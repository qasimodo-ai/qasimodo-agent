from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
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
from qasimodo_agent.state import clear_core_token, get_core_token, is_core_token_valid, save_core_token

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
    status: str = "disconnected"
    version: str = ""
    last_heartbeat: datetime | None = None
    logs: list[str] = field(default_factory=list)
    auth_url: str | None = None
    authenticated: bool = False


def _core_base_url() -> str:
    raw = os.environ.get("QASIMODO_CORE_BASE_URL") or "http://localhost:8000"
    return raw.rstrip("/")


def _build_auth_url(agent_id: str) -> str:
    return f"{_core_base_url()}/agent_auth/{agent_id}"


def _get_valid_cached_token(agent_id: str) -> str | None:
    if not is_core_token_valid(agent_id):
        return None
    return get_core_token(agent_id)


def _record_auth_prompt(state: AgentState) -> None:
    if state.auth_url:
        return
    state.auth_url = _build_auth_url(state.agent_id)
    msg = f"{datetime.now(timezone.utc).isoformat()} Authenticate at {state.auth_url}"
    state.status = "disconnected"
    state.logs.append(msg)
    console.print(f"Authenticate the agent: {state.auth_url}")


async def _wait_for_core_token(
    controller: AgentController,
    agent_id: str,
    nats_url: str,
    state: AgentState | None = None,
    cancel_event: asyncio.Event | None = None,
) -> str | None:
    await controller.ensure_control_listener(agent_id=agent_id, nats_url=nats_url)
    cached = _get_valid_cached_token(agent_id)
    if cached:
        return cached

    if state:
        _record_auth_prompt(state)
    else:
        console.print(f"Authenticate the agent: {_build_auth_url(agent_id)}")

    loop = asyncio.get_running_loop()
    if controller.token_future is None or controller.token_future.done():
        controller.token_future = loop.create_future()

    wait_tasks: set[asyncio.Task[Any]] = {asyncio.ensure_future(controller.token_future)}
    cancel_task: asyncio.Task[Any] | None = None
    if cancel_event is not None:
        cancel_task = asyncio.create_task(cancel_event.wait())
        wait_tasks.add(cancel_task)

    try:
        done, _ = await asyncio.wait(wait_tasks, return_when=asyncio.FIRST_COMPLETED)
        if cancel_task and cancel_task in done:
            return None
        if controller.token_future in done:
            return controller.token_future.result()
        return None
    finally:
        if cancel_task:
            cancel_task.cancel()


class AgentController:
    def __init__(self) -> None:
        self.runtime_task: asyncio.Task | None = None
        self.stop_event: asyncio.Event | None = None
        self.monitor_task: asyncio.Task | None = None
        self.monitor_nc: nats.NATS | None = None
        self.event_queue: asyncio.Queue[AgentEvent] = asyncio.Queue()
        self.control_nc: nats.NATS | None = None
        self.control_task: asyncio.Task | None = None
        self.logout_event: asyncio.Event = asyncio.Event()
        self.token_future: asyncio.Future[str] | None = None
        self._log_handler: logging.Handler | None = None

    async def ensure_control_listener(self, *, agent_id: str, nats_url: str) -> None:
        if self.control_task:
            return
        self.control_nc = await nats.connect(nats_url)
        subject = f"agents.{agent_id}.auth"
        loop = asyncio.get_running_loop()
        self.token_future = loop.create_future()

        async def _on_control(msg: nats.aio.msg.Msg) -> None:
            try:
                payload = json.loads(msg.data.decode("utf-8"))
            except Exception:  # noqa: BLE001
                LOGGER.warning("Invalid auth payload")
                return
            action = payload.get("action")
            token = payload.get("token")
            expires_at = payload.get("expires_at") or ""
            if action == "logout":
                clear_core_token(agent_id)
                self.logout_event.set()
                self.token_future = loop.create_future()
                await self.event_queue.put(
                    AgentEvent(kind="log", payload={"message": "Received logout request; stopping agent"})
                )
                return
            if token:
                save_core_token(agent_id, token, expires_at)
                if self.token_future and not self.token_future.done():
                    self.token_future.set_result(token)
                await self.event_queue.put(AgentEvent(kind="log", payload={"message": "Received auth token"}))

        async def _runner() -> None:
            assert self.control_nc is not None
            await self.control_nc.subscribe(subject, cb=_on_control)
            while True:
                await asyncio.sleep(1)

        self.control_task = asyncio.create_task(_runner())

    async def start_runtime(self, *, agent_config: AgentConfig, llm_config: LLMConfig) -> None:
        await self.stop()
        ensure_chromium_installed()
        config = agent_config
        await self._start_monitor(nats_url=config.nats_url, agent_id=config.agent_id)
        self._ensure_log_forwarder()
        await self.event_queue.put(AgentEvent(kind="log", payload={"message": "Agent started"}))
        runtime = AgentRuntime(config=config, llm_config=llm_config)
        self.stop_event = asyncio.Event()
        self.runtime_task = asyncio.create_task(runtime.start(self.stop_event))

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
                            "observation": result.step.observation,
                            "error": result.step.error,
                            "url": result.step.url,
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
        had_runtime = any([self.stop_event, self.runtime_task, self.monitor_task, self.monitor_nc])
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
        if had_runtime:
            await self.event_queue.put(AgentEvent(kind="log", payload={"message": "Agent stopped"}))
            await self.event_queue.put(
                AgentEvent(
                    kind="status", payload={"status": "offline", "timestamp": datetime.now(timezone.utc).timestamp()}
                )
            )

    async def shutdown(self) -> None:
        await self.stop()
        if self.control_task:
            self.control_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.control_task
        if self.control_nc:
            with contextlib.suppress(Exception):
                await self.control_nc.drain()
            with contextlib.suppress(Exception):
                await self.control_nc.close()
        self.control_task = None
        self.control_nc = None
        self._detach_log_forwarder()

    def _ensure_log_forwarder(self) -> None:
        if self._log_handler:
            return
        loop = asyncio.get_running_loop()

        class EventQueueLogHandler(logging.Handler):
            def __init__(self, queue: asyncio.Queue[AgentEvent], running_loop: asyncio.AbstractEventLoop) -> None:
                super().__init__(level=logging.INFO)
                self.queue = queue
                self.loop = running_loop

            def emit(self, record: logging.LogRecord) -> None:
                try:
                    message = self.format(record)
                    event = AgentEvent(kind="log", payload={"message": message})
                    self.loop.call_soon_threadsafe(self.queue.put_nowait, event)
                except Exception:  # noqa: BLE001
                    self.handleError(record)

        handler = EventQueueLogHandler(self.event_queue, loop)
        formatter = logging.Formatter("%(levelname)s [%(name)s] %(message)s")
        handler.setFormatter(formatter)
        self._log_handler = handler

        targets = ["qasimodo.agent.runtime", "Agent", "BrowserSession", "service"]
        for name in targets:
            logger = logging.getLogger(name)
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)

    def _detach_log_forwarder(self) -> None:
        if not self._log_handler:
            return
        targets = ["qasimodo.agent.runtime", "Agent", "BrowserSession", "service"]
        for name in targets:
            logger = logging.getLogger(name)
            with contextlib.suppress(ValueError):
                logger.removeHandler(self._log_handler)
        self._log_handler = None


def render_tui(state: AgentState) -> Panel:
    status = "online"
    if state.last_heartbeat is None or (datetime.now(timezone.utc) - state.last_heartbeat) > timedelta(minutes=5):
        status = "offline"
    if state.status.lower() == "disconnected":
        status = "disconnected"

    agent_table = Table.grid(padding=(0, 1))
    agent_table.add_column(justify="left")
    agent_table.add_column(justify="left")
    agent_table.add_row("Agent", state.agent_id)
    agent_table.add_row("Version", state.version or "—")
    agent_table.add_row("NATS", state.nats_url)
    agent_table.add_row("Tasks", f"agents.{state.agent_id}.tasks")
    if status == "online":
        status_cell = "[green]online[/green]"
    elif status == "disconnected":
        status_cell = "[red]disconnected[/red]"
    else:
        status_cell = "[red]offline[/red]"
    agent_table.add_row("Status", status_cell)
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
            if not state.authenticated:
                continue
            ts = datetime.fromtimestamp(event.payload["timestamp"], tz=timezone.utc)
            state.last_heartbeat = ts
            state.version = event.payload.get("version", state.version)
            state.status = event.payload.get("status", state.status)
            state.logs.append(f"{ts.isoformat()} heartbeat v{state.version}")
        elif event.kind == "status":
            status = event.payload.get("status", "offline")
            ts_raw = event.payload.get("timestamp")
            ts = (
                datetime.fromtimestamp(ts_raw, tz=timezone.utc).isoformat()
                if ts_raw
                else datetime.now(timezone.utc).isoformat()
            )
            state.status = status
            state.last_heartbeat = None
            state.logs.append(f"{ts} agent status: {status}")
        elif event.kind == "result":
            run_id = event.payload.get("run_id") or "unknown"
            step = event.payload.get("step")
            if step:
                obs = (step.get("observation") or "").strip()
                err = (step.get("error") or "").strip()
                url = (step.get("url") or "").strip()
                detail_parts = []
                if obs:
                    detail_parts.append(f"obs={obs}")
                if err:
                    detail_parts.append(f"err={err}")
                if url:
                    detail_parts.append(f"url={url}")
                detail = " | ".join(detail_parts)
                state.logs.append(
                    f"{datetime.now(timezone.utc).isoformat()} run {run_id} step {step.get('index')} "
                    f"{step.get('status', '').lower()} {step.get('action', '')} {detail}".strip()
                )
            else:
                state.logs.append(
                    f"{datetime.now(timezone.utc).isoformat()} result {run_id} {event.payload.get('status', '')} "
                    f"{event.payload.get('message', '')}"
                )


async def run_tui(
    state: AgentState,
    controller: AgentController,
    llm_config: LLMConfig,
    agent_config: AgentConfig,
    shutdown_event: asyncio.Event,
) -> None:
    console.print(f"Starting agent {state.agent_id} on {state.nats_url}")
    await controller.ensure_control_listener(agent_id=state.agent_id, nats_url=state.nats_url)
    quit_event = asyncio.Event()
    cancel_event = asyncio.Event()

    async def _watch_shutdown() -> None:
        await shutdown_event.wait()
        cancel_event.set()

    asyncio.create_task(_watch_shutdown())
    loop = asyncio.get_running_loop()

    def on_keypress() -> None:
        try:
            ch = sys.stdin.read(1)
        except Exception:  # noqa: BLE001
            return
        if ch and ch.lower() == "q":
            quit_event.set()
            cancel_event.set()

    original_term_settings = None
    try:
        if sys.stdin.isatty() and termios is not None and tty is not None:
            original_term_settings = termios.tcgetattr(sys.stdin.fileno())
            tty.setcbreak(sys.stdin.fileno())
        loop.add_reader(sys.stdin, on_keypress)
    except Exception:  # noqa: BLE001
        pass

    with Live(render_tui(state), refresh_per_second=4, console=console, screen=True) as live:
        while not quit_event.is_set() and not shutdown_event.is_set():
            await drain_events(controller, state)
            live.update(render_tui(state))
            # Re-auth if needed
            token = _get_valid_cached_token(state.agent_id)
            if not token:
                state.authenticated = False
                _record_auth_prompt(state)
                state.status = "offline"
                state.last_heartbeat = None
                live.update(render_tui(state))
                await controller.stop()
                token_result = await _wait_for_core_token(
                    controller, state.agent_id, state.nats_url, state, cancel_event=cancel_event
                )
                if token_result is None:
                    break
                continue
            state.authenticated = True
            if controller.runtime_task is None or controller.runtime_task.done():
                await controller.start_runtime(agent_config=agent_config, llm_config=llm_config)

            if controller.logout_event.is_set():
                await controller.stop()
                controller.logout_event.clear()
                clear_core_token(state.agent_id)
                state.authenticated = False
                state.status = "offline"
                state.last_heartbeat = None
                state.logs.append(f"{datetime.now(timezone.utc).isoformat()} Agent set offline after logout")
                _record_auth_prompt(state)
                live.update(render_tui(state))
                continue

            await asyncio.sleep(0.25)

    try:
        loop.remove_reader(sys.stdin)
    except Exception:  # noqa: BLE001
        pass
    if original_term_settings:
        with contextlib.suppress(Exception):
            termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, original_term_settings)

    console.print("Shutting down…")
    await controller.shutdown()


async def run_headless(
    state: AgentState,
    controller: AgentController,
    llm_config: LLMConfig,
    agent_config: AgentConfig,
    shutdown_event: asyncio.Event,
) -> None:
    console.print(f"Starting agent {state.agent_id} on {state.nats_url}")
    await controller.ensure_control_listener(agent_id=state.agent_id, nats_url=state.nats_url)

    while not shutdown_event.is_set():
        await drain_events(controller, state)
        token = _get_valid_cached_token(state.agent_id)
        if not token:
            state.authenticated = False
            _record_auth_prompt(state)
            state.status = "disconnected"
            state.last_heartbeat = None
            await controller.stop()
            await _wait_for_core_token(controller, state.agent_id, state.nats_url, state, cancel_event=shutdown_event)
            continue
        state.authenticated = True
        if controller.runtime_task is None or controller.runtime_task.done():
            await controller.start_runtime(agent_config=agent_config, llm_config=llm_config)
        if controller.logout_event.is_set():
            await controller.stop()
            controller.logout_event.clear()
            clear_core_token(state.agent_id)
            state.authenticated = False
            state.status = "disconnected"
            state.last_heartbeat = None
            state.logs.append(f"{datetime.now(timezone.utc).isoformat()} Agent set offline after logout")
            _record_auth_prompt(state)
            continue
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
                "  3) open the authentication link shown at startup, then return to the agent",
                "  4) press 'q' in the TUI for a safe shutdown",
                "  (Use --logout to clear saved auth and re-login)",
            ]
        ),
    )
    parser.add_argument("--heartbeat-interval", type=int, help="Heartbeat interval in seconds")
    parser.add_argument("--health", action="store_true", help="Run health check and exit")
    parser.add_argument("--log-level", default=os.environ.get("QASIMODO_AGENT_LOG_LEVEL", "DEBUG"))
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
        "--send-screenshots",
        choices=["true", "false"],
        help="Send screenshots to core (default true; env QASIMODO_AGENT_SEND_SCREENSHOTS)",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        help="Maximum browser steps per task (default 60; env QASIMODO_AGENT_MAX_STEPS)",
    )
    parser.add_argument(
        "--logout",
        action="store_true",
        help="Disconnect: clear saved core token for this agent and force re-authentication at startup",
    )
    return parser.parse_args()


async def _async_main(config: AgentConfig, mode: str, llm_config: LLMConfig, force_logout: bool) -> None:
    agent_id = config.agent_id
    nats_url = config.nats_url
    state = AgentState(
        nats_url=nats_url,
        agent_id=agent_id,
        version=config.version,
    )
    controller = AgentController()
    shutdown_event = asyncio.Event()

    def _handle_shutdown() -> None:
        LOGGER.info("Shutdown signal received")
        if not shutdown_event.is_set():
            shutdown_event.set()
        if controller.stop_event and not controller.stop_event.is_set():
            controller.stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_shutdown)
        except NotImplementedError:
            signal.signal(sig, lambda *_: _handle_shutdown())

    if force_logout:
        clear_core_token(agent_id)
    ensure_chromium_installed()

    if mode == "tui":
        await run_tui(state, controller, llm_config, config, shutdown_event=shutdown_event)
    else:
        await run_headless(state, controller, llm_config, config, shutdown_event=shutdown_event)


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
        asyncio.run(_async_main(config, args.mode, llm_config, args.logout))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    cli()
