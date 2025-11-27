from __future__ import annotations

import asyncio
import contextlib
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import logging
import nats
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from qasimodo_agent.browser import ensure_chromium_installed
from qasimodo_agent.config import AgentConfig, LLMConfig
from qasimodo_agent.proto import AgentHeartbeat, AgentResult, AgentResultKind
from qasimodo_agent.runtime import AgentRuntime
from qasimodo_agent.state import get_agent_version, get_or_create_agent_id

console = Console()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AgentEvent:
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=_utc_now_iso)


@dataclass
class AgentState:
    nats_url: str
    agent_id: str
    status: str = "starting"
    version: str = ""
    last_heartbeat: datetime | None = None
    runs: dict[str, dict[str, Any]] = field(default_factory=dict)
    steps: list[str] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)


class AgentController:
    def __init__(self) -> None:
        self.runtime_task: asyncio.Task | None = None
        self.stop_event: asyncio.Event | None = None
        self.monitor_task: asyncio.Task | None = None
        self.monitor_nc: nats.NATS | None = None
        self.event_queue: asyncio.Queue[AgentEvent] = asyncio.Queue()

    async def start(self, *, nats_url: str, agent_id: str) -> None:
        await self.stop()
        version = get_agent_version()
        heartbeat_interval = int(os.environ.get("QASIMODO_AGENT_HEARTBEAT_INTERVAL", "30"))
        browser_headless = str(os.environ.get("QASIMODO_AGENT_BROWSER_HEADLESS", "true")).lower() == "true"
        chromium_sandbox = str(os.environ.get("QASIMODO_AGENT_CHROMIUM_SANDBOX", "true")).lower() == "true"
        max_steps = int(os.environ.get("QASIMODO_AGENT_MAX_STEPS", "60"))
        config = AgentConfig(
            agent_id=agent_id,
            nats_url=nats_url,
            heartbeat_interval=heartbeat_interval,
            browser_headless=browser_headless,
            chromium_sandbox=chromium_sandbox,
            max_steps=max_steps,
            version=version,
        )
        llm_config = LLMConfig.from_env()
        ensure_chromium_installed()
        runtime = AgentRuntime(config=config, llm_config=llm_config)
        self.stop_event = asyncio.Event()
        self.runtime_task = asyncio.create_task(runtime.start(self.stop_event))
        await self._start_monitor(nats_url=nats_url, agent_id=agent_id)
        await self.event_queue.put(AgentEvent(kind="log", payload={"message": "Agent started"}))

    async def _start_monitor(self, *, nats_url: str, agent_id: str) -> None:
        if self.monitor_task:
            self.monitor_task.cancel()
        self.monitor_nc = await nats.connect(nats_url)
        heartbeat_subject = f"agents.{agent_id}.heartbeat"
        result_subject = f"agents.{agent_id}.results"

        async def _on_heartbeat(msg: nats.aio.msg.Msg) -> None:
            heartbeat = AgentHeartbeat()
            heartbeat.ParseFromString(msg.data)
            await self.event_queue.put(
                AgentEvent(
                    kind="heartbeat",
                    payload={
                        "agent_id": heartbeat.metadata.agent_id,
                        "timestamp": heartbeat.timestamp,
                        "version": heartbeat.metadata.agent_version,
                        "status": heartbeat.status,
                    },
                )
            )

        async def _on_result(msg: nats.aio.msg.Msg) -> None:
            result = AgentResult()
            result.ParseFromString(msg.data)
            await self.event_queue.put(
                AgentEvent(
                    kind="result",
                    payload={
                        "run_id": result.metadata.run_id,
                        "status": result.status,
                        "kind": result.kind,
                        "message": result.message,
                        "step": {
                            "index": result.step.step_index,
                            "status": result.step.status,
                            "action": result.step.action_name,
                        }
                        if result.kind == AgentResultKind.AGENT_RESULT_KIND_STEP
                        else None,
                        "started_at": result.started_at,
                        "finished_at": result.finished_at,
                    },
                )
            )

        async def _monitor() -> None:
            assert self.monitor_nc is not None
            await self.monitor_nc.subscribe(heartbeat_subject, cb=_on_heartbeat)
            await self.monitor_nc.subscribe(result_subject, cb=_on_result)
            while True:
                await asyncio.sleep(1)

        self.monitor_task = asyncio.create_task(_monitor())

    async def stop(self) -> None:
        if self.stop_event and not self.stop_event.is_set():
            self.stop_event.set()
        if self.runtime_task:
            self.runtime_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.runtime_task
        if self.monitor_task:
            self.monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.monitor_task
        if self.monitor_nc:
            await self.monitor_nc.drain()
            await self.monitor_nc.close()
        self.runtime_task = None
        self.stop_event = None
        self.monitor_task = None
        self.monitor_nc = None
        await self.event_queue.put(AgentEvent(kind="log", payload={"message": "Agent stopped"}))


def render(state: AgentState) -> Panel:
    status = "online"
    if state.last_heartbeat is None or (datetime.now(timezone.utc) - state.last_heartbeat) > timedelta(minutes=5):
        status = "offline"

    agent_table = Table.grid(padding=0)
    agent_table.add_column(justify="left")
    agent_table.add_column(justify="left")
    agent_table.add_row("Agent", state.agent_id)
    agent_table.add_row("Version", state.version or "—")
    agent_table.add_row("NATS", state.nats_url)
    agent_table.add_row("Tasks", f"agents.{state.agent_id}.tasks")
    agent_table.add_row("Status", "[green]online[/green]" if status == "online" else "[red]offline[/red]")
    if state.last_heartbeat:
        agent_table.add_row("Last heartbeat", state.last_heartbeat.isoformat())

    logs = state.logs[-10:]
    if not logs:
        logs = ["Waiting for events…"]
    while len(logs) < 10:
        logs.append("")
    logs_panel = Panel("\n".join(logs), title="Logs", border_style="magenta", height=12)

    body = Table.grid(expand=True)
    body.add_row(Panel(agent_table, title="Agent", border_style="yellow"))
    body.add_row(logs_panel)

    return Panel(body, border_style="white")


async def _drain_events(controller: AgentController, state: AgentState) -> None:
    while not controller.event_queue.empty():
        event = await controller.event_queue.get()
        if event.kind == "log":
            state.logs.append(f"{_utc_now_iso()} {event.payload.get('message', '')}")
        elif event.kind == "heartbeat":
            ts = datetime.fromtimestamp(event.payload["timestamp"], tz=timezone.utc)
            state.last_heartbeat = ts
            state.version = event.payload.get("version", state.version)
            state.status = event.payload.get("status", state.status)
            state.logs.append(f"{ts.isoformat()} heartbeat v{state.version}")
        elif event.kind == "result":
            run_id = event.payload.get("run_id") or "unknown"
            state.logs.append(
                f"{_utc_now_iso()} result {run_id} {event.payload.get('status', '')} {event.payload.get('message', '')}"
            )


async def _main_async() -> None:
    logging.getLogger("qasimodo.agent.runtime").setLevel(logging.ERROR)
    logging.getLogger("qasimodo.agent.runtime").propagate = False
    agent_id = os.environ.get("QASIMODO_AGENT_ID") or get_or_create_agent_id()
    nats_url = os.environ.get("QASIMODO_NATS_URL", "nats://localhost:4222")
    state = AgentState(nats_url=nats_url, agent_id=agent_id)
    controller = AgentController()

    console.print(f"Starting agent {agent_id} on {nats_url}")
    try:
        await controller.start(nats_url=nats_url, agent_id=agent_id)
        with Live(render(state), refresh_per_second=4, console=console) as live:
            while True:
                await _drain_events(controller, state)
                live.update(render(state))
                await asyncio.sleep(0.25)
    except KeyboardInterrupt:
        console.print("Stopping agent…")
    finally:
        await controller.stop()


def main() -> None:
    asyncio.run(_main_async())


__all__ = ["main"]
