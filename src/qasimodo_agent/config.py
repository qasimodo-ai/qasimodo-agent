from __future__ import annotations

import argparse
import os
from dataclasses import dataclass

from qasimodo_agent.state import get_agent_version, get_or_create_agent_id


@dataclass(slots=True)
class LLMConfig:
    model: str
    api_key: str
    base_url: str

    @classmethod
    def from_env(cls) -> "LLMConfig":
        model = os.environ.get("QASIMODO_AGENT_LLM_MODEL", "google/gemini-2.0-flash-exp")
        base_url = os.environ.get("QASIMODO_AGENT_LLM_BASE_URL", "https://openrouter.ai/api/v1")
        api_key = os.environ.get("QASIMODO_AGENT_LLM_API_KEY")
        if not api_key:
            raise RuntimeError("Missing QASIMODO_AGENT_LLM_API_KEY environment variable")
        return cls(model=model, api_key=api_key, base_url=base_url)


@dataclass(slots=True)
class AgentConfig:
    agent_id: str
    project_id: str
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
        project_id = args.project_id or os.environ.get("QASIMODO_AGENT_PROJECT_ID")
        if not project_id:
            raise SystemExit("Project ID missing. Use --project-id or set QASIMODO_AGENT_PROJECT_ID")
        agent_id = args.agent_id or os.environ.get("QASIMODO_AGENT_ID")
        if not agent_id:
            agent_id = get_or_create_agent_id(project_id)
        nats_url = args.nats_url or os.environ.get("QASIMODO_NATS_URL", "nats://localhost:4222")
        heartbeat_interval = int(args.heartbeat_interval or os.environ.get("QASIMODO_AGENT_HEARTBEAT_INTERVAL", "30"))
        browser_headless = str(os.environ.get("QASIMODO_AGENT_BROWSER_HEADLESS", "true")).lower() == "true"
        chromium_sandbox = str(os.environ.get("QASIMODO_AGENT_CHROMIUM_SANDBOX", "true")).lower() == "true"
        max_steps = int(os.environ.get("QASIMODO_AGENT_MAX_STEPS", "60"))
        version = get_agent_version()
        return cls(
            agent_id=agent_id,
            project_id=project_id,
            nats_url=nats_url,
            heartbeat_interval=heartbeat_interval,
            browser_headless=browser_headless,
            chromium_sandbox=chromium_sandbox,
            max_steps=max_steps,
            version=version,
        )


__all__ = ["AgentConfig", "LLMConfig"]
