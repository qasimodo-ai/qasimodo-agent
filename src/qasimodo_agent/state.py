from __future__ import annotations

import contextlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


STATE_DIR = Path.home() / ".qasimodo-agent"
STATE_FILE = STATE_DIR / "agents.json"
DEFAULT_AGENT_KEY = "__default__"


@dataclass(slots=True)
class AgentState:
    agents: dict[str, str]
    version: str | None = None
    nats_jwt: str | None = None  # NATS JWT for authentication
    nats_jwt_expires_at: str | None = None  # JWT expiry timestamp


def _load_state() -> AgentState:
    if not STATE_FILE.exists():
        return AgentState(
            agents={},
            version=None,
            nats_jwt=None,
            nats_jwt_expires_at=None,
        )
    try:
        with STATE_FILE.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except Exception:  # noqa: BLE001
        return AgentState(
            agents={},
            version=None,
            nats_jwt=None,
            nats_jwt_expires_at=None,
        )
    if isinstance(data, dict):
        if "agents" in data or "version" in data:
            raw_agents = data.get("agents", {})
            agents = _normalize_agents(raw_agents)
            version = data.get("version")
            if version is not None:
                version = str(version)
            nats_jwt = data.get("nats_jwt")
            nats_jwt_expires_at = data.get("nats_jwt_expires_at")
            return AgentState(
                agents=agents,
                version=version,
                nats_jwt=nats_jwt,
                nats_jwt_expires_at=nats_jwt_expires_at,
            )
        return AgentState(
            agents=_normalize_agents(data),
            version=None,
            nats_jwt=None,
            nats_jwt_expires_at=None,
        )
    return AgentState(agents={}, version=None, nats_jwt=None, nats_jwt_expires_at=None)


def _normalize_agents(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, str] = {}
    for key, val in value.items():
        normalized[str(key)] = str(val)
    return normalized


def _save_state(state: AgentState) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "agents": state.agents,
        "version": state.version,
        "nats_jwt": state.nats_jwt,
        "nats_jwt_expires_at": state.nats_jwt_expires_at,
    }
    fd = os.open(STATE_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    with contextlib.suppress(OSError):
        os.chmod(STATE_FILE, 0o600)


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
    version = _read_pyproject_version()
    if state.version != version:
        state.version = version
        _save_state(state)
    return version


def get_or_create_agent_id(project_id: str | None = None) -> str:
    state = _load_state()
    if project_id:
        existing = state.agents.get(project_id)
        if existing:
            return existing
        default_agent = state.agents.get(DEFAULT_AGENT_KEY)
        if default_agent:
            if project_id and state.agents.get(project_id) != default_agent:
                state.agents[project_id] = default_agent
                _save_state(state)
            return default_agent
    agent_id = str(uuid.uuid4())
    state.agents[project_id or DEFAULT_AGENT_KEY] = agent_id
    try:
        _save_state(state)
    except Exception as exc:
        print(f"ERROR: Failed to save agent_id: {exc}")
    return agent_id


def remember_project_agent(project_id: str | None, agent_id: str) -> None:
    if not project_id:
        return
    state = _load_state()
    if state.agents.get(project_id) == agent_id:
        return
    state.agents[project_id] = agent_id
    if DEFAULT_AGENT_KEY not in state.agents:
        state.agents[DEFAULT_AGENT_KEY] = agent_id
    _save_state(state)


def save_nats_jwt(jwt: str, expires_at: str) -> None:
    """Save NATS JWT for authentication."""
    state = _load_state()
    state.nats_jwt = jwt
    state.nats_jwt_expires_at = expires_at
    _save_state(state)


def get_nats_jwt() -> str | None:
    """
    Get NATS JWT if it exists and is valid.

    Returns None if JWT doesn't exist or is expired.
    """
    state = _load_state()
    if not state.nats_jwt or not state.nats_jwt_expires_at:
        return None

    # Check expiry
    try:
        exp = datetime.fromisoformat(state.nats_jwt_expires_at)
    except (TypeError, ValueError):
        clear_nats_jwt()
        return None

    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    if exp <= now:
        # Expired
        clear_nats_jwt()
        return None

    return state.nats_jwt


def clear_nats_jwt() -> None:
    """Clear stored NATS JWT."""
    state = _load_state()
    state.nats_jwt = None
    state.nats_jwt_expires_at = None
    _save_state(state)


def is_nats_jwt_valid() -> bool:
    """Check if a valid NATS JWT exists."""
    return get_nats_jwt() is not None


__all__ = [
    "clear_nats_jwt",
    "get_agent_version",
    "get_nats_jwt",
    "get_or_create_agent_id",
    "is_nats_jwt_valid",
    "remember_project_agent",
    "save_nats_jwt",
]
