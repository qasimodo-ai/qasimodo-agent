from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import mimetypes
from contextlib import suppress
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import nats
from browser_use import Agent as BrowserUseAgent
from browser_use import Browser, ChatOpenAI
from google.protobuf.message import DecodeError
from nats.errors import DrainTimeoutError

from qasimodo_agent.config import AgentConfig, LLMConfig
from qasimodo_agent.browser import find_cached_chromium, ensure_chromium_installed, find_bundled_chromium
from qasimodo_agent.proto import (
    AgentHeartbeat,
    AgentMetadata,
    AgentResult,
    AgentResultKind,
    AgentStepResult,
    AgentTask,
)
from qasimodo_agent.state import remember_project_agent

LOGGER = logging.getLogger("qasimodo.agent.runtime")


class AgentRuntime:
    def __init__(self, config: AgentConfig, llm_config: LLMConfig) -> None:
        self.config = config
        self.llm_config = llm_config
        self._nc: nats.NATS | None = None
        self._js = None
        self._heartbeat_task: asyncio.Task[None] | None = None

    async def start(self, stop_event: asyncio.Event) -> None:
        LOGGER.info("Connecting to NATS at %s", self.config.nats_url)
        self._nc = await nats.connect(self.config.nats_url)
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
                    await self._nc.drain()
                except DrainTimeoutError:
                    LOGGER.warning("NATS drain timed out; forcing close")
                await self._nc.close()

    async def _ensure_stream(self) -> None:
        assert self._js is not None
        try:
            await self._js.stream_info(self.config.stream_name)
        except Exception:  # noqa: BLE001
            await self._js.add_stream(name=self.config.stream_name, subjects=[f"{self.config.subject_prefix}.>"])

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
        LOGGER.info("Executing run %s for project %s", run_id or "unknown", project_id or "unknown")
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
            history = await self._run_browser_use(task, started_at)
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
            testbook_id=task.testbook_id,
            environment_id=task.environment_id,
            partial_history_json=self._serialize_history(partial_history) if partial_history else "",
        )
        if step:
            result.step.CopyFrom(step)
        await self._js.publish(self.config.result_subject, result.SerializeToString())

    def _build_result_metadata(self, task: AgentTask) -> AgentMetadata:
        task_metadata = task.metadata
        return AgentMetadata(
            agent_id=self.config.agent_id,
            project_id=task_metadata.project_id or "",
            run_id=task_metadata.run_id or "",
            agent_version=self.config.version,
        )

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

    async def _run_browser_use(self, task: AgentTask, started_at: datetime) -> dict[str, Any]:
        instructions = task.instructions or "Run testbook"
        llm = ChatOpenAI(
            model=self.llm_config.model, api_key=self.llm_config.api_key, base_url=self.llm_config.base_url
        )
        ensure_chromium_installed()
        chromium_path = find_bundled_chromium() or find_cached_chromium()
        if not chromium_path:
            raise RuntimeError("Chromium executable not found. Run `playwright install chromium`.")
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
            LOGGER.exception("Failed to prepare step payload for run %s", task.metadata.run_id or "unknown")
            return
        if step_result is None:
            return
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
            return self._json_safe(history_obj)
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
        return self._json_safe(run_history)

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
            screenshots.append(self._to_data_url(str(raw_value), mime_type))
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

    @staticmethod
    def _to_data_url(raw_value: str, mime_type: str) -> str:
        data = raw_value.strip()
        if data.startswith("data:"):
            return data
        return f"data:{mime_type};base64,{data}"

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


__all__ = ["AgentRuntime"]
