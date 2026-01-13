from __future__ import annotations

import asyncio
import base64
import contextlib
import io
import json
import logging
import math
import mimetypes
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import nats
from nats import errors as nats_errors
from browser_use import Agent as BrowserUseAgent
from browser_use import Browser, ChatOpenAI
from google.protobuf.message import DecodeError
from nats.errors import DrainTimeoutError
from PIL import Image

from qasimodo_agent.config import AgentConfig, LLMConfig

from qasimodo_agent.browser import (
    find_system_chromium,
    find_bundled_chromium,
    find_cached_chromium,
    ensure_chromium_installed,
)
from qasimodo_agent.proto import (
    AgentHeartbeat,
    AgentMetadata,
    AgentResult,
    AgentResultKind,
    AgentStepResult,
    AgentTask,
)
from qasimodo_agent.nkey_manager import get_user_seed
from qasimodo_agent.state import (
    get_nats_jwt,
    get_or_create_agent_id,
    remember_project_agent,
)
import nkeys

LOGGER = logging.getLogger("qasimodo.agent.runtime")


class AgentRuntime:
    def __init__(self, config: AgentConfig, llm_config: LLMConfig) -> None:
        self.config = config
        self.llm_config = llm_config
        self._nc: nats.NATS | None = None
        self._js = None
        self._heartbeat_task: asyncio.Task[None] | None = None

    async def start(self, stop_event: asyncio.Event) -> None:
        # Ensure we have an agent_id
        if not self.config.agent_id:
            self.config.agent_id = get_or_create_agent_id()
            remember_project_agent(None, self.config.agent_id)
            LOGGER.info("Generated new agent_id: %s", self.config.agent_id)

        LOGGER.info("Connecting to NATS at %s", self.config.nats_url)

        # Try to connect with JWT authentication if available
        jwt_token = get_nats_jwt()
        if jwt_token:
            try:
                user_seed = get_user_seed()
                kp = nkeys.from_seed(user_seed.encode())

                def signature_cb(nonce: bytes) -> bytes:
                    return kp.sign(nonce)

                self._nc = await asyncio.wait_for(
                    nats.connect(
                        self.config.nats_url,
                        user_jwt_cb=lambda: jwt_token,
                        signature_cb=signature_cb,
                    ),
                    timeout=5.0,
                )
                LOGGER.info("Connected to NATS with JWT authentication")
            except Exception as exc:
                LOGGER.warning("JWT auth failed, falling back to unauthenticated: %s", exc)
                self._nc = await asyncio.wait_for(nats.connect(self.config.nats_url), timeout=5.0)
        else:
            # Fallback to unauthenticated connection (dev mode without JWT auth on NATS)
            self._nc = await asyncio.wait_for(nats.connect(self.config.nats_url), timeout=5.0)
            LOGGER.info("Connected to NATS without authentication")

        self._js = self._nc.jetstream()
        await self._ensure_stream()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(stop_event))
        try:
            await self._consume_tasks(stop_event)
        finally:
            if self._heartbeat_task:
                self._heartbeat_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._heartbeat_task
            if self._nc:
                try:
                    await asyncio.wait_for(self._nc.drain(), timeout=2.0)
                except (DrainTimeoutError, asyncio.TimeoutError):
                    LOGGER.warning("NATS drain timed out; forcing close")
                except Exception as exc:
                    LOGGER.warning("NATS drain failed: %s; forcing close", exc)
                try:
                    await asyncio.wait_for(self._nc.close(), timeout=1.0)
                except asyncio.TimeoutError:
                    LOGGER.warning("NATS close timed out")
                except Exception as exc:
                    LOGGER.warning("NATS close failed: %s", exc)

    async def _ensure_stream(self) -> None:
        assert self._js is not None
        try:
            await self._js.stream_info(self.config.stream_name)
        except Exception:  # noqa: BLE001
            await self._js.add_stream(
                name=self.config.stream_name,
                subjects=[f"{self.config.subject_prefix}.>"],
            )

    async def _consume_tasks(self, stop_event: asyncio.Event) -> None:
        assert self._js is not None
        subscription = await self._js.pull_subscribe(self.config.task_subject, durable=self.config.durable_name)
        LOGGER.info("Listening for tasks at subject %s", self.config.task_subject)
        while not stop_event.is_set():
            try:
                messages = await subscription.fetch(batch=1, timeout=1.0)
            except asyncio.TimeoutError:
                continue
            for msg in messages:
                try:
                    task = AgentTask()
                    task.ParseFromString(msg.data)
                    await self._execute_task(task)
                    await msg.ack()
                except DecodeError as exc:
                    LOGGER.error("Unable to decode task payload: %s", exc)
                    await msg.ack()
                except Exception as exc:  # noqa: BLE001
                    LOGGER.exception("Failed to handle task: %s", exc)
                    await msg.nak()

    async def _execute_task(self, task: AgentTask) -> None:
        assert self._js is not None
        metadata = task.metadata
        project_id = metadata.project_id
        run_id = metadata.run_id
        if project_id:
            remember_project_agent(project_id, self.config.agent_id)
        started_at = datetime.now(timezone.utc)
        LOGGER.info(
            "Executing run %s for project %s",
            run_id or "unknown",
            project_id or "unknown",
        )
        await self._publish_result_message(
            task=task,
            status="STATUS_RUNNING",
            message="Run started",
            started_at=started_at,
        )
        status = "STATUS_FAILED"
        error_message = ""
        history: dict[str, Any] | None = None
        try:
            instructions = self._compose_instructions_from_proto(task)

            history = await self._run_browser_use(task, instructions, started_at)
            status = "STATUS_PASSED"
        except Exception as exc:  # noqa: BLE001
            error_message = str(exc)
            LOGGER.exception("Browser execution failed for run %s", run_id or "unknown")
        finished_at = datetime.now(timezone.utc)
        await self._publish_result_message(
            task=task,
            status=status,
            message="Run completed" if status == "STATUS_PASSED" else "Run failed",
            error=error_message,
            started_at=started_at,
            finished_at=finished_at,
            history=history or {},
        )
        LOGGER.info("Published result for run %s", run_id or "unknown")

    async def _publish_result_message(
        self,
        *,
        task: AgentTask,
        status: str,
        message: str,
        started_at: datetime,
        finished_at: datetime | None = None,
        history: dict[str, Any] | None = None,
        partial_history: dict[str, Any] | None = None,
        step: AgentStepResult | None = None,
        kind: AgentResultKind = AgentResultKind.AGENT_RESULT_KIND_STATUS,
        error: str = "",
    ) -> None:
        assert self._js is not None
        metadata = self._build_result_metadata(task)
        result = AgentResult(
            metadata=metadata,
            kind=kind,
            status=status,
            message=message,
            error=error,
            started_at=started_at.isoformat(),
            finished_at=finished_at.isoformat() if finished_at else "",
            history_json=self._serialize_history(history) if history else "",
            testbook_id=task.testbook.id if task.HasField("testbook") else "",
            environment_id=task.environment.id if task.HasField("environment") else "",
            partial_history_json=self._serialize_history(partial_history) if partial_history else "",
        )
        if step:
            result.step.CopyFrom(step)
        max_bytes = 800_000
        result = self._shrink_result_payload(result, max_bytes)
        payload = result.SerializeToString()
        LOGGER.info(
            "Publishing result kind=%s status=%s size=%sB history_len=%s partial_len=%s step=%s",
            result.kind,
            result.status,
            len(payload),
            len(result.history_json or ""),
            len(result.partial_history_json or ""),
            bool(result.step and result.kind == AgentResultKind.AGENT_RESULT_KIND_STEP),
        )
        if LOGGER.isEnabledFor(logging.DEBUG):
            try:
                parsed_history = json.loads(result.history_json or "{}") if result.history_json else {}
            except Exception:
                parsed_history = "<unparseable>"
            try:
                parsed_partial = json.loads(result.partial_history_json or "{}") if result.partial_history_json else {}
            except Exception:
                parsed_partial = "<unparseable>"
            LOGGER.debug(
                "Result payload detail: metadata=%s history=%s partial=%s step=%s",
                result.metadata,
                parsed_history,
                parsed_partial,
                result.step if result.kind == AgentResultKind.AGENT_RESULT_KIND_STEP else None,
            )
        try:
            await self._js.publish(self.config.result_subject, payload)
        except nats_errors.MaxPayloadError:
            LOGGER.warning("Payload still above NATS max_payload, sending minimal result")
            minimal = self._minimal_result(result)
            try:
                await self._js.publish(self.config.result_subject, minimal.SerializeToString())
            except Exception:
                LOGGER.exception("Failed to publish even minimal result")
        except Exception:
            LOGGER.exception("Failed to publish result message")

    def _build_result_metadata(self, task: AgentTask) -> AgentMetadata:
        task_metadata = task.metadata
        return AgentMetadata(
            agent_id=self.config.agent_id,
            project_id=task_metadata.project_id or "",
            run_id=task_metadata.run_id or "",
            agent_version=self.config.version,
        )

    def _compose_instructions_from_proto(self, task: AgentTask) -> str:
        """Compose instructions from protobuf testbook and environment data."""
        base_instruction = task.instructions or "Run testbook"

        parts: list[str] = []

        # Extract environment details from protobuf
        if task.HasField("environment") and task.environment.id:
            env_name = task.environment.name.strip()
            env_url = task.environment.url.strip()
            if env_name or env_url:
                details = env_name or "Environment"
                if env_url:
                    details = f"{details} ({env_url})" if details else env_url
                parts.append(f"Target environment: {details}")

        # Extract testbook tasks from protobuf
        if task.HasField("testbook") and task.testbook.id:
            tasks = list(task.testbook.tasks)
            version = task.testbook.version.strip()
            if tasks:
                if version:
                    parts.append(f"Execute testbook version {version} with the following steps:")
                else:
                    parts.append("Execute the following steps:")
                parts.extend(f"- {step}" for step in tasks)
            elif version:
                parts.append(f"Execute testbook version {version}.")

        if not parts:
            return base_instruction
        return "\n".join(parts)

    async def _heartbeat_loop(self, stop_event: asyncio.Event) -> None:
        assert self._nc is not None
        while not stop_event.is_set():
            heartbeat = AgentHeartbeat(
                metadata=AgentMetadata(
                    agent_id=self.config.agent_id,
                    project_id="",
                    run_id="",
                    agent_version=self.config.version,
                ),
                status="online",
                timestamp=int(datetime.now(timezone.utc).timestamp()),
                capabilities=["browser_use"],
            )
            await self._nc.publish(self.config.heartbeat_subject, heartbeat.SerializeToString())
            await asyncio.sleep(self.config.heartbeat_interval)

    async def _run_browser_use(self, task: AgentTask, instructions: str, started_at: datetime) -> dict[str, Any]:
        llm = ChatOpenAI(
            model=self.llm_config.model,
            api_key=self.llm_config.api_key,
            base_url=self.llm_config.base_url,
        )
        if self.config.chromium_executable:
            chromium_path = self.config.chromium_executable
        else:
            ensure_chromium_installed()
            chromium_path = find_system_chromium() or find_bundled_chromium() or find_cached_chromium()
        if not chromium_path:
            raise RuntimeError(
                "Chromium executable not found. Set QASIMODO_AGENT_CHROMIUM_EXECUTABLE environment variable "
                "or use --chromium-executable flag."
            )
        browser = Browser(
            headless=self.config.browser_headless,
            chromium_sandbox=self.config.chromium_sandbox,
            executable_path=chromium_path,
        )
        agent = BrowserUseAgent(llm=llm, task=instructions, browser=browser)

        async def _on_step_end(active_agent: BrowserUseAgent) -> None:
            await self._handle_step_completion(active_agent, task, started_at)

        history = await agent.run(max_steps=self.config.max_steps, on_step_end=_on_step_end)
        return self._build_history_snapshot(history)

    async def _handle_step_completion(self, agent: BrowserUseAgent, task: AgentTask, started_at: datetime) -> None:
        try:
            step_result, partial_history = await self._prepare_step_payload(agent)
        except Exception:  # noqa: BLE001
            LOGGER.exception(
                "Failed to prepare step payload for run %s",
                task.metadata.run_id or "unknown",
            )
            return
        if step_result is None:
            return
        # NATS payloads are size-limited; compress screenshots to stay under the limit.
        max_bytes = 800_000
        if step_result.screenshot_bytes:
            step_result.screenshot_bytes = self._compress_image_bytes(step_result.screenshot_bytes, max_bytes)
        await self._publish_result_message(
            task=task,
            status="STATUS_RUNNING",
            message=f"Step {step_result.step_index} completed",
            started_at=started_at,
            partial_history=partial_history,
            step=step_result,
            kind=AgentResultKind.AGENT_RESULT_KIND_STEP,
        )

    async def _prepare_step_payload(self, agent: BrowserUseAgent) -> tuple[AgentStepResult | None, dict[str, Any]]:
        history_obj = getattr(agent, "history", None)
        steps = getattr(history_obj, "history", None) if history_obj is not None else None
        if not history_obj or not steps:
            return None, {}
        step_index = len(steps)
        last_entry = steps[-1]

        screenshot_bytes, mime_type = await self._load_screenshot_bytes(
            getattr(last_entry.state, "screenshot_path", None)
        )
        if screenshot_bytes:
            screenshot_bytes = self._compress_image_bytes(screenshot_bytes, max_bytes=300_000)
        if not self.config.send_screenshots:
            screenshot_bytes = b""
        model_actions_payload: list[Any] = []
        model_output_payload: dict[str, Any] | None = None
        if last_entry.model_output:
            model_output_payload = last_entry.model_output.model_dump(exclude_none=True)
            for action in last_entry.model_output.action:
                with suppress(Exception):
                    model_actions_payload.append(action.model_dump(exclude_none=True))
        action_results_payload: list[dict[str, Any]] = []
        for result in last_entry.result:
            with suppress(Exception):
                action_results_payload.append(result.model_dump(exclude_none=True))

        state_payload: dict[str, Any] = {}
        url_value = ""
        screenshot_path = ""
        if last_entry.state:
            url_value = getattr(last_entry.state, "url", "") or ""
            screenshot_path = getattr(last_entry.state, "screenshot_path", "") or ""
            state_payload = self._safe_state_dump(last_entry.state)
        error_message = next((str(res.error) for res in last_entry.result if res.error), "")
        status = "STEP_COMPLETED" if not error_message else "STEP_FAILED"
        observation = last_entry.state_message or ""
        timestamp = ""
        if last_entry.metadata and getattr(last_entry.metadata, "step_end_time", None):
            timestamp = datetime.fromtimestamp(last_entry.metadata.step_end_time, tz=timezone.utc).isoformat()
        else:
            timestamp = datetime.now(timezone.utc).isoformat()
        action_name = ""
        if model_actions_payload:
            first_action = model_actions_payload[0]
            if isinstance(first_action, dict):
                action_name = next(iter(first_action.keys()), "")

        step_payload = AgentStepResult(
            step_index=step_index,
            status=status,
            action_name=action_name,
            model_actions_json=self._json_dumps(model_actions_payload),
            model_outputs_json=self._json_dumps(model_output_payload),
            action_results_json=self._json_dumps(action_results_payload),
            observation=observation,
            error=error_message,
            url=url_value,
            screenshot_mime_type=mime_type,
            screenshot_bytes=screenshot_bytes,
            screenshot_path=screenshot_path,
            timestamp=timestamp,
            state_json=self._json_dumps(state_payload),
        )
        partial_history = self._build_history_snapshot(history_obj)
        return step_payload, partial_history

    async def _load_screenshot_bytes(self, path_value: str | None) -> tuple[bytes, str]:
        if not path_value:
            return b"", ""
        path = Path(path_value)
        if not path.exists():
            return b"", ""
        data = await asyncio.to_thread(path.read_bytes)
        mime_type, _ = mimetypes.guess_type(path_value)
        return data, mime_type or "image/png"

    def _safe_state_dump(self, state: Any) -> dict[str, Any]:
        if hasattr(state, "to_dict"):
            with suppress(Exception):
                return state.to_dict()
        if hasattr(state, "__dict__"):
            return {str(key): value for key, value in vars(state).items()}
        return {}

    def _json_dumps(self, value: Any) -> str:
        if value is None:
            return ""
        return json.dumps(value, default=self._json_default, ensure_ascii=False)

    def _json_default(self, obj: Any) -> Any:
        if isinstance(obj, datetime):
            return obj.isoformat()
        if hasattr(obj, "model_dump") and callable(obj.model_dump):
            with suppress(Exception):
                return obj.model_dump()
        if hasattr(obj, "__dict__"):
            return vars(obj)
        return str(obj)

    def _build_history_snapshot(self, history_obj: Any) -> dict[str, Any]:
        if history_obj is None:
            return self._empty_history()
        if isinstance(history_obj, dict):
            return self._compress_history_images(self._json_safe(history_obj))
        run_history = self._empty_history()
        with suppress(Exception):
            run_history["urls"] = self._string_list(history_obj.urls())
        run_history["screenshots"] = self._build_screenshot_list(history_obj)
        with suppress(Exception):
            run_history["action_names"] = self._string_list(history_obj.action_names())
        with suppress(Exception):
            run_history["extracted_content"] = self._string_list(history_obj.extracted_content())
        with suppress(Exception):
            run_history["errors"] = self._nullable_string_list(history_obj.errors())
        with suppress(Exception):
            run_history["model_actions"] = self._string_list(history_obj.model_actions())
        with suppress(Exception):
            run_history["model_outputs"] = self._string_list(history_obj.model_outputs())
        with suppress(Exception):
            run_history["model_thoughts"] = self._string_list(history_obj.model_thoughts())
        with suppress(Exception):
            run_history["action_results"] = self._string_list(history_obj.action_results())
        with suppress(Exception):
            run_history["action_history"] = self._string_list(history_obj.action_history())
        final_result = None
        with suppress(Exception):
            final_result = history_obj.final_result()
        run_history["final_result"] = self._stringify(final_result) if final_result else ""
        with suppress(Exception):
            run_history["is_done"] = bool(history_obj.is_done())
        with suppress(Exception):
            run_history["is_successful"] = history_obj.is_successful()
        with suppress(Exception):
            run_history["has_errors"] = bool(history_obj.has_errors())
        with suppress(Exception):
            steps_count = history_obj.number_of_steps()
            if steps_count is not None:
                run_history["number_of_steps"] = int(steps_count)
        with suppress(Exception):
            duration = history_obj.total_duration_seconds()
            if duration is not None:
                run_history["duration_seconds"] = int(duration)
        structured_output = None
        with suppress(Exception):
            structured_output = history_obj.structured_output
        if structured_output is not None and not isinstance(structured_output, str):
            run_history["structured_output"] = self._stringify(structured_output)
        else:
            run_history["structured_output"] = structured_output
        run_history["screenshots"] = self._string_list(run_history.get("screenshots"))
        run_history["model_actions"] = self._string_list(run_history.get("model_actions"))
        run_history["model_outputs"] = self._string_list(run_history.get("model_outputs"))
        run_history["model_thoughts"] = self._string_list(run_history.get("model_thoughts"))
        run_history["action_results"] = self._string_list(run_history.get("action_results"))
        run_history["action_history"] = self._string_list(run_history.get("action_history"))
        run_history["urls"] = self._string_list(run_history.get("urls"))
        run_history["action_names"] = self._string_list(run_history.get("action_names"))
        run_history["extracted_content"] = self._string_list(run_history.get("extracted_content"))
        run_history["errors"] = self._nullable_string_list(run_history.get("errors"))
        return self._compress_history_images(self._json_safe(run_history))

    @staticmethod
    def _empty_history() -> dict[str, Any]:
        return {
            "urls": [],
            "screenshots": [],
            "action_names": [],
            "extracted_content": [],
            "errors": [],
            "model_actions": [],
            "model_outputs": [],
            "final_result": "",
            "is_done": False,
            "is_successful": None,
            "has_errors": False,
            "model_thoughts": [],
            "action_results": [],
            "action_history": [],
            "number_of_steps": 0,
            "duration_seconds": 0,
            "structured_output": None,
        }

    def _build_screenshot_list(self, history_obj: Any) -> list[str]:
        if not self.config.send_screenshots:
            return []
        try:
            raw_screenshots = history_obj.screenshots(return_none_if_not_screenshot=True)
        except Exception:  # noqa: BLE001
            return []
        entries = getattr(history_obj, "history", [])
        screenshots: list[str] = []
        for idx, raw_value in enumerate(raw_screenshots):
            if not raw_value:
                continue
            mime_type = self._guess_screenshot_mime(entries, idx)
            compressed = self._compress_data_url(str(raw_value), mime_type, max_bytes=300_000)
            screenshots.append(compressed)
        return screenshots

    def _guess_screenshot_mime(self, entries: Any, index: int) -> str:
        try:
            entry = entries[index]
            path = getattr(entry.state, "screenshot_path", None)
        except Exception:  # noqa: BLE001
            path = None
        if isinstance(path, str) and path:
            mime_type, _ = mimetypes.guess_type(path)
            if mime_type:
                return mime_type
        return "image/png"

    def _compress_data_url(self, raw_value: str, mime_type: str, max_bytes: int) -> str:
        data = raw_value.strip()
        prefix = f"data:{mime_type};base64,"
        if data.startswith("data:"):
            if data.startswith(prefix):
                try:
                    decoded = base64.b64decode(data[len(prefix) :], validate=True)
                    compressed = self._compress_image_bytes(decoded, max_bytes)
                    return prefix + base64.b64encode(compressed).decode("ascii")
                except Exception:  # noqa: BLE001
                    return data
            return data
        try:
            decoded = base64.b64decode(data, validate=True)
        except Exception:
            return f"data:{mime_type};base64,{data}"
        compressed = self._compress_image_bytes(decoded, max_bytes)
        return f"{prefix}{base64.b64encode(compressed).decode('ascii')}"

    def _json_safe(self, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, (str, bool)):
            return value
        if isinstance(value, (int,)):
            return value
        if isinstance(value, float):
            if math.isnan(value) or math.isinf(value):
                return self._stringify(value)
            return value
        if isinstance(value, dict):
            return {str(key): self._json_safe(val) for key, val in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [self._json_safe(item) for item in value]
        if hasattr(value, "model_dump") and callable(value.model_dump):
            with suppress(Exception):
                return self._json_safe(value.model_dump())
        if hasattr(value, "dict") and callable(value.dict):
            with suppress(Exception):
                return self._json_safe(value.dict())
        with suppress(TypeError, ValueError):
            json.dumps(value, ensure_ascii=False, allow_nan=False)
            return value
        return self._stringify(value)

    def _string_list(self, value: Any) -> list[str]:
        result: list[str] = []
        for item in self._listify(value):
            if item is None:
                continue
            result.append(self._stringify(item))
        return result

    def _nullable_string_list(self, value: Any) -> list[str | None]:
        result: list[str | None] = []
        for item in self._listify(value):
            if item is None:
                result.append(None)
            else:
                result.append(self._stringify(item))
        return result

    def _listify(self, value: Any) -> list[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            return value
        if isinstance(value, tuple):
            return list(value)
        if isinstance(value, set):
            return list(value)
        if isinstance(value, str):
            return [value]
        try:
            return list(value)
        except TypeError:
            return [value]

    def _stringify(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        try:
            return json.dumps(value, ensure_ascii=False)
        except TypeError:
            return str(value)

    def _serialize_history(self, history: dict[str, Any]) -> str:
        if not history:
            return ""
        return json.dumps(history, default=self._json_default)

    def _compress_image_bytes(self, data: bytes, max_bytes: int) -> bytes:
        if len(data) <= max_bytes:
            return data
        try:
            with Image.open(io.BytesIO(data)) as img:
                img = img.convert("RGB")
                target_width = 480
                if img.width > target_width:
                    scale = target_width / img.width
                    new_size = (
                        max(1, int(img.width * scale)),
                        max(1, int(img.height * scale)),
                    )
                    img = img.resize(new_size)
                quality = 50
                best = data
                for _ in range(8):
                    buffer = io.BytesIO()
                    img.save(buffer, format="JPEG", quality=quality, optimize=True)
                    compressed = buffer.getvalue()
                    if len(compressed) <= max_bytes:
                        return compressed
                    if len(compressed) < len(best):
                        best = compressed
                    quality = max(30, quality - 5)
                return best if len(best) < len(data) else data
        except Exception:
            LOGGER.exception("Screenshot compression failed; sending original bytes")
            return data

    def _compress_history_images(
        self, history_json: str, max_bytes_per_image: int = 200_000, max_items: int = 5
    ) -> str:
        if not history_json:
            return ""
        try:
            data = json.loads(history_json)
        except Exception:
            return history_json
        if isinstance(data, dict) and "screenshots" in data and isinstance(data["screenshots"], list):
            compressed_list: list[str] = []
            limited = self._limit_screenshots(data["screenshots"], max_items=max_items)
            for item in limited:
                if isinstance(item, str):
                    mime = "image/png"
                    if item.startswith("data:"):
                        parts = item.split(";")
                        if parts and parts[0].startswith("data:"):
                            mime = parts[0].split("data:")[-1]
                    if self.config.send_screenshots:
                        compressed_list.append(self._compress_data_url(item, mime, max_bytes=max_bytes_per_image))
                    else:
                        compressed_list.append("")
                else:
                    compressed_list.append(item)
            data["screenshots"] = compressed_list
        try:
            return json.dumps(data, default=self._json_default)
        except Exception:
            return history_json

    def _limit_screenshots(self, items: list[str], max_items: int) -> list[str]:
        if len(items) <= max_items:
            return items
        if max_items <= 2:
            return items[:max_items]
        head = items[:2]
        tail = items[-2:]
        middle_needed = max_items - 4
        middle = items[2 : 2 + middle_needed]
        return head + middle + tail

    def _shrink_result_payload(self, result: AgentResult, max_bytes: int) -> AgentResult:
        updated = AgentResult()
        updated.CopyFrom(result)

        def current_size(obj: AgentResult) -> int:
            return len(obj.SerializeToString())

        if updated.kind == AgentResultKind.AGENT_RESULT_KIND_STEP and updated.step.screenshot_bytes:
            updated.step.screenshot_bytes = self._compress_image_bytes(updated.step.screenshot_bytes, max_bytes=150_000)

        updated.history_json = self._compress_history_images(updated.history_json)
        updated.partial_history_json = self._compress_history_images(updated.partial_history_json)

        if current_size(updated) > max_bytes:
            LOGGER.warning(
                "Result payload still above limit after compression (size=%s)",
                current_size(updated),
            )
        return updated

    def _minimal_result(self, result: AgentResult) -> AgentResult:
        minimal = AgentResult()
        minimal.CopyFrom(result)
        minimal.history_json = ""
        minimal.partial_history_json = ""
        if minimal.kind == AgentResultKind.AGENT_RESULT_KIND_STEP:
            minimal.step.screenshot_bytes = b""
            minimal.step.model_actions_json = ""
            minimal.step.model_outputs_json = ""
            minimal.step.action_results_json = ""
            minimal.step.state_json = ""
        return minimal


__all__ = ["AgentRuntime"]
