from __future__ import annotations

import argparse
import os
from dataclasses import dataclass

from qasimodo_agent.state import get_agent_version, get_or_create_agent_id

DEFAULT_NATS_URL = "nats://localhost:4222"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).lower() in {"1", "true", "yes", "y", "on"}


@dataclass(slots=True)
class LLMConfig:
    model: str
    api_key: str
    base_url: str

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "LLMConfig":
        model = args.llm_model or os.environ.get("QASIMODO_AGENT_LLM_MODEL", "google/gemini-2.0-flash-exp")
        base_url = args.llm_base_url or os.environ.get("QASIMODO_AGENT_LLM_BASE_URL", "https://openrouter.ai/api/v1")
        api_key = args.llm_api_key or os.environ.get("QASIMODO_AGENT_LLM_API_KEY")
        if not api_key:
            raise RuntimeError("Missing LLM API key (provide --llm-api-key or QASIMODO_AGENT_LLM_API_KEY)")
        return cls(model=model, api_key=api_key, base_url=base_url)


@dataclass(slots=True)
class AgentConfig:
    agent_id: str
    nats_url: str
    heartbeat_interval: int
    stream_name: str = "AGENTS"
    subject_prefix: str = "agents"
    durable_prefix: str = "agent"
    browser_headless: bool = True
    chromium_sandbox: bool = True
    max_steps: int = 60
    version: str = "dev"

    @property
    def task_subject(self) -> str:
        return f"{self.subject_prefix}.{self.agent_id}.tasks"

    @property
    def result_subject(self) -> str:
        return f"{self.subject_prefix}.{self.agent_id}.results"

    @property
    def heartbeat_subject(self) -> str:
        return f"{self.subject_prefix}.{self.agent_id}.heartbeat"

    @property
    def durable_name(self) -> str:
        return f"{self.durable_prefix}-{self.agent_id}"

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "AgentConfig":
        agent_id = get_or_create_agent_id()
        nats_url = DEFAULT_NATS_URL
        heartbeat_interval = int(args.heartbeat_interval or os.environ.get("QASIMODO_AGENT_HEARTBEAT_INTERVAL", "30"))
        browser_headless = _env_bool("QASIMODO_AGENT_BROWSER_HEADLESS", True)
        chromium_sandbox = _env_bool("QASIMODO_AGENT_CHROMIUM_SANDBOX", True)
        if args.browser_headless:
            browser_headless = args.browser_headless.lower() == "true"
        if args.chromium_sandbox:
            chromium_sandbox = args.chromium_sandbox.lower() == "true"
        max_steps = int(args.max_steps or os.environ.get("QASIMODO_AGENT_MAX_STEPS", "60"))
        version = get_agent_version()
        return cls(
            agent_id=agent_id,
            nats_url=nats_url,
            heartbeat_interval=heartbeat_interval,
            browser_headless=browser_headless,
            chromium_sandbox=chromium_sandbox,
            max_steps=max_steps,
            version=version,
        )


__all__ = ["AgentConfig", "LLMConfig"]
