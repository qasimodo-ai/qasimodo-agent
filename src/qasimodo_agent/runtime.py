from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

import nats
from browser_use import Agent as BrowserUseAgent
from browser_use import Browser, ChatOpenAI
from google.protobuf.message import DecodeError

from qasimodo_agent.config import AgentConfig, LLMConfig
from qasimodo_agent.proto import AgentHeartbeat, AgentResult, AgentTask

LOGGER = logging.getLogger("qasimodo.agent.runtime")


class AgentRuntime:
    def __init__(self, config: AgentConfig, llm_config: LLMConfig, chromium_path: str) -> None:
        self.config = config
        self.llm_config = llm_config
        self.chromium_path = chromium_path
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
                await self._nc.drain()
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
        if task.project_id and task.project_id != self.config.project_id:
            LOGGER.warning(
                "Skipping run %s: project mismatch agent=%s task=%s",
                task.run_id,
                self.config.project_id,
                task.project_id,
            )
            await self._publish_result_message(
                task=task,
                status="STATUS_FAILED",
                message="Project mismatch",
                started_at=datetime.now(timezone.utc),
                error="Agent is registered to a different project",
            )
            return
        started_at = datetime.now(timezone.utc)
        LOGGER.info("Executing run %s for project %s", task.run_id, task.project_id)
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
            history = await self._run_browser_use(task.instructions or "Run testbook")
            status = "STATUS_PASSED"
        except Exception as exc:  # noqa: BLE001
            error_message = str(exc)
            LOGGER.exception("Browser execution failed for run %s", task.run_id)
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
        LOGGER.info("Published result for run %s", task.run_id)

    async def _publish_result_message(
        self,
        *,
        task: AgentTask,
        status: str,
        message: str,
        started_at: datetime,
        finished_at: datetime | None = None,
        history: dict[str, Any] | None = None,
        error: str = "",
    ) -> None:
        assert self._js is not None
        result = AgentResult(
            agent_id=self.config.agent_id,
            project_id=task.project_id or self.config.project_id,
            run_id=task.run_id,
            status=status,
            message=message,
            error=error,
            started_at=started_at.isoformat(),
            finished_at=finished_at.isoformat() if finished_at else "",
            history_json=self._serialize_history(history or {}),
            testbook_id=task.testbook_id,
            environment_id=task.environment_id,
        )
        await self._js.publish(self.config.result_subject, result.SerializeToString())

    async def _heartbeat_loop(self, stop_event: asyncio.Event) -> None:
        assert self._nc is not None
        while not stop_event.is_set():
            heartbeat = AgentHeartbeat(
                agent_id=self.config.agent_id,
                status="online",
                timestamp=int(datetime.now(timezone.utc).timestamp()),
                version=self.config.version,
                project_id=self.config.project_id,
                capabilities=["browser_use"],
            )
            await self._nc.publish(self.config.heartbeat_subject, heartbeat.SerializeToString())
            await asyncio.sleep(self.config.heartbeat_interval)

    async def _run_browser_use(self, instructions: str) -> dict[str, Any]:
        llm = ChatOpenAI(
            model=self.llm_config.model, api_key=self.llm_config.api_key, base_url=self.llm_config.base_url
        )
        browser = Browser(
            headless=self.config.browser_headless,
            chromium_sandbox=self.config.chromium_sandbox,
            executable_path=self.chromium_path,
        )
        agent = BrowserUseAgent(llm=llm, task=instructions, browser=browser)
        history = await agent.run(max_steps=self.config.max_steps)
        if hasattr(history, "model_dump"):
            return history.model_dump()
        if isinstance(history, dict):
            return history
        return {"result": str(history)}

    @staticmethod
    def _serialize_history(history: dict[str, Any]) -> str:
        def _default(obj: Any) -> Any:
            if isinstance(obj, datetime):
                return obj.isoformat()
            return str(obj)

        return json.dumps(history, default=_default)


__all__ = ["AgentRuntime"]
