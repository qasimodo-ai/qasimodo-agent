from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path


STATE_DIR = Path.home() / ".qasimodo-agent"
STATE_FILE = STATE_DIR / "agents.json"


@dataclass(slots=True)
class AgentState:
    agents: dict[str, str]
    version: str | None = None


def _load_state() -> AgentState:
    if not STATE_FILE.exists():
        return AgentState(agents={}, version=None)
    try:
        with STATE_FILE.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:  # noqa: BLE001
        return AgentState(agents={}, version=None)
    if isinstance(data, dict):
        if "agents" in data or "version" in data:
            raw_agents = data.get("agents", {})
            agents = _normalize_agents(raw_agents)
            version = data.get("version")
            if version is not None:
                version = str(version)
            return AgentState(agents=agents, version=version)
        return AgentState(agents=_normalize_agents(data), version=None)
    return AgentState(agents={}, version=None)


def _normalize_agents(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, str] = {}
    for key, val in value.items():
        normalized[str(key)] = str(val)
    return normalized


def _save_state(state: AgentState) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"agents": state.agents, "version": state.version}
    with STATE_FILE.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def _read_pyproject_version() -> str:
    try:
        root = Path(__file__).resolve().parents[2]
    except IndexError:  # pragma: no cover - defensive
        return "dev"
    pyproject_file = root / "pyproject.toml"
    if not pyproject_file.exists():
        return "dev"
    try:
        content = pyproject_file.read_text(encoding="utf-8")
    except OSError:
        return "dev"
    match = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
    if match:
        return match.group(1)
    return "dev"


def get_agent_version() -> str:
    state = _load_state()
    version = os.environ.get("QASIMODO_AGENT_VERSION") or _read_pyproject_version()
    if state.version != version:
        state.version = version
        _save_state(state)
    return version


def get_or_create_agent_id(project_id: str) -> str:
    state = _load_state()
    existing = state.agents.get(project_id)
    if existing:
        return existing
    if state.agents:
        agent_id = next(iter(state.agents.values()))
    else:
        agent_id = str(uuid.uuid4())
    state.agents[project_id] = agent_id
    _save_state(state)
    return agent_id


__all__ = ["get_agent_version", "get_or_create_agent_id"]
