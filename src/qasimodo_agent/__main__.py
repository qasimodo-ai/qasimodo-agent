from __future__ import annotations

import argparse
import asyncio
import logging
import os
import signal
import sys

from qasimodo_agent.browser import ensure_chromium_installed, find_bundled_chromium
from qasimodo_agent.config import AgentConfig, LLMConfig
from qasimodo_agent.runtime import AgentRuntime

LOGGER = logging.getLogger("qasimodo.agent")


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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Qasimodo agent")
    parser.add_argument("--agent-id", help="Identifier for this agent instance")
    parser.add_argument("--nats-url", help="NATS connection string")
    parser.add_argument("--heartbeat-interval", type=int, help="Heartbeat interval in seconds")
    parser.add_argument("--health", action="store_true", help="Run health check and exit")
    parser.add_argument("--log-level", default=os.environ.get("QASIMODO_AGENT_LOG_LEVEL", "INFO"))
    return parser.parse_args()


async def _async_main(config: AgentConfig) -> None:
    llm_config = LLMConfig.from_env()
    ensure_chromium_installed()
    chromium_path = find_bundled_chromium() or os.environ.get("CHROMIUM_PATH")
    if not chromium_path:
        raise SystemExit("Chromium executable not found. Set CHROMIUM_PATH or bundle the browser.")
    runtime = AgentRuntime(config=config, llm_config=llm_config, chromium_path=chromium_path)
    stop_event = asyncio.Event()

    def _handle_shutdown() -> None:
        LOGGER.info("Shutdown signal received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_shutdown)
        except NotImplementedError:
            signal.signal(sig, lambda *_: stop_event.set())
    await runtime.start(stop_event)


def cli() -> None:
    args = parse_args()
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))
    if args.health:
        success = health_check()
        sys.exit(0 if success else 1)
    config = AgentConfig.from_args(args)
    try:
        asyncio.run(_async_main(config))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    cli()
