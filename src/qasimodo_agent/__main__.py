from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
import os
import signal
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

# Unix-only modules for terminal control
try:
    import termios
    import tty
except ImportError:
    termios = None  # type: ignore
    tty = None  # type: ignore

import nats
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table

from qasimodo_agent.browser import ensure_chromium_installed
from qasimodo_agent.config import AgentConfig, LLMConfig
from qasimodo_agent.proto import AgentHeartbeat, AgentResult, AgentResultKind
from qasimodo_agent.runtime import AgentRuntime

LOGGER = logging.getLogger("qasimodo.agent")
console = Console()


def health_check() -> bool:
    print("=" * 60)
    print("Qasimodo Agent Health Check")
    print("=" * 60)
    print(f"Python: {sys.version}")
    print(f"Platform: {os.name}")
    try:
        ensure_chromium_installed()
        print("[OK] Chromium check passed")
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] Chromium check failed: {exc}")
        return False
    print("=" * 60)
    return True


@dataclass
class AgentEvent:
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class AgentState:
    nats_url: str
    agent_id: str
    status: str = "starting"
    version: str = ""
    last_heartbeat: datetime | None = None
    logs: list[str] = field(default_factory=list)


class AgentController:
    def __init__(self) -> None:
        self.runtime_task: asyncio.Task | None = None
        self.stop_event: asyncio.Event | None = None
        self.monitor_task: asyncio.Task | None = None
        self.monitor_nc: nats.NATS | None = None
        self.event_queue: asyncio.Queue[AgentEvent] = asyncio.Queue()

    async def start_runtime(self, *, agent_config: AgentConfig, llm_config: LLMConfig) -> None:
        await self.stop()
        config = agent_config
        ensure_chromium_installed()
        runtime = AgentRuntime(config=config, llm_config=llm_config)
        self.stop_event = asyncio.Event()
        self.runtime_task = asyncio.create_task(runtime.start(self.stop_event))
        await self._start_monitor(nats_url=config.nats_url, agent_id=config.agent_id)
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
            try:
                await self.monitor_nc.drain()
            except Exception:
                # Swallow drain errors on shutdown; not critical on exit.
                pass
            with contextlib.suppress(Exception):
                await self.monitor_nc.close()
        self.runtime_task = None
        self.stop_event = None
        self.monitor_task = None
        self.monitor_nc = None
        await self.event_queue.put(AgentEvent(kind="log", payload={"message": "Agent stopped"}))


def render_tui(state: AgentState) -> Panel:
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

    logs = state.logs[-10:] or ["Waiting for events…"]
    while len(logs) < 10:
        logs.append("")
    logs_panel = Panel("\n".join(logs), title="Logs", border_style="magenta", height=12)

    body = Table.grid(expand=True)
    body.add_row(Panel(agent_table, title="Agent", border_style="yellow"))
    body.add_row(logs_panel)

    return Panel(body, border_style="white")


async def drain_events(controller: AgentController, state: AgentState) -> None:
    while not controller.event_queue.empty():
        event = await controller.event_queue.get()
        if event.kind == "log":
            state.logs.append(f"{datetime.now(timezone.utc).isoformat()} {event.payload.get('message', '')}")
        elif event.kind == "heartbeat":
            ts = datetime.fromtimestamp(event.payload["timestamp"], tz=timezone.utc)
            state.last_heartbeat = ts
            state.version = event.payload.get("version", state.version)
            state.status = event.payload.get("status", state.status)
            state.logs.append(f"{ts.isoformat()} heartbeat v{state.version}")
        elif event.kind == "result":
            run_id = event.payload.get("run_id") or "unknown"
            state.logs.append(
                f"{datetime.now(timezone.utc).isoformat()} result {run_id} {event.payload.get('status', '')} {event.payload.get('message', '')}"
            )


async def run_tui(
    state: AgentState, controller: AgentController, llm_config: LLMConfig, agent_config: AgentConfig
) -> None:
    console.print(f"Starting agent {state.agent_id} on {state.nats_url}")
    await controller.start_runtime(agent_config=agent_config, llm_config=llm_config)
    quit_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def on_keypress() -> None:
        try:
            ch = sys.stdin.read(1)
        except Exception:  # noqa: BLE001
            return
        if ch and ch.lower() == "q":
            quit_event.set()

    original_term_settings = None
    try:
        if sys.stdin.isatty():
            original_term_settings = termios.tcgetattr(sys.stdin.fileno())
            tty.setcbreak(sys.stdin.fileno())
        loop.add_reader(sys.stdin, on_keypress)
    except Exception:  # noqa: BLE001
        pass

    with Live(render_tui(state), refresh_per_second=4, console=console, screen=True) as live:
        while not quit_event.is_set():
            await drain_events(controller, state)
            live.update(render_tui(state))
            await asyncio.sleep(0.25)

    try:
        loop.remove_reader(sys.stdin)
    except Exception:  # noqa: BLE001
        pass
    if original_term_settings:
        with contextlib.suppress(Exception):
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, original_term_settings)

    console.print("Shutting down…")
    await controller.stop()


async def run_headless(
    state: AgentState, controller: AgentController, llm_config: LLMConfig, agent_config: AgentConfig
) -> None:
    console.print(f"Starting agent {state.agent_id} on {state.nats_url}")
    await controller.start_runtime(agent_config=agent_config, llm_config=llm_config)

    while True:
        await drain_events(controller, state)
        await asyncio.sleep(0.25)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Qasimodo agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join(
            [
                "Quick start:",
                "  1) export QASIMODO_AGENT_LLM_API_KEY=your_key",
                "  2) run: uv run qasimodo-agent   (default TUI; pass --mode headless for non-interactive)",
                "  3) press 'q' in the TUI for a safe shutdown",
            ]
        ),
    )
    parser.add_argument("--heartbeat-interval", type=int, help="Heartbeat interval in seconds")
    parser.add_argument("--health", action="store_true", help="Run health check and exit")
    parser.add_argument("--log-level", default=os.environ.get("QASIMODO_AGENT_LOG_LEVEL", "INFO"))
    parser.add_argument("--mode", choices=["tui", "headless"], default="tui")
    parser.add_argument("--llm-api-key", help="LLM API key")
    parser.add_argument("--llm-model", help="LLM model (default google/gemini-2.0-flash-exp)")
    parser.add_argument("--llm-base-url", help="LLM base URL (default https://openrouter.ai/api/v1)")
    parser.add_argument(
        "--browser-headless",
        choices=["true", "false"],
        help="Run Chromium headless (default true; env QASIMODO_AGENT_BROWSER_HEADLESS)",
    )
    parser.add_argument(
        "--chromium-sandbox",
        choices=["true", "false"],
        help="Enable Chromium sandbox (default true; env QASIMODO_AGENT_CHROMIUM_SANDBOX)",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        help="Maximum browser steps per task (default 60; env QASIMODO_AGENT_MAX_STEPS)",
    )
    return parser.parse_args()


async def _async_main(config: AgentConfig, mode: str, llm_config: LLMConfig) -> None:
    agent_id = config.agent_id
    nats_url = config.nats_url
    state = AgentState(
        nats_url=nats_url,
        agent_id=agent_id,
    )
    controller = AgentController()

    def _handle_shutdown() -> None:
        LOGGER.info("Shutdown signal received")
        if controller.stop_event and not controller.stop_event.is_set():
            controller.stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_shutdown)
        except NotImplementedError:
            signal.signal(sig, lambda *_: _handle_shutdown())

    ensure_chromium_installed()

    if mode == "tui":
        await run_tui(state, controller, llm_config, config)
    else:
        await run_headless(state, controller, llm_config, config)


def cli() -> None:
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))
    # Keep NATS internal shutdown noise out of user logs.
    logging.getLogger("nats").setLevel(logging.CRITICAL)
    logging.getLogger("nats.aio.client").setLevel(logging.CRITICAL)
    if args.health:
        success = health_check()
        sys.exit(0 if success else 1)
    config = AgentConfig.from_args(args)
    try:
        llm_config = LLMConfig.from_args(args)
        asyncio.run(_async_main(config, args.mode, llm_config))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    cli()
