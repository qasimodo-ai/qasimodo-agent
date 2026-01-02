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
    core_tokens: dict[str, dict[str, str]] | None = None  # Legacy REST tokens (deprecated)
    nats_jwt: str | None = None  # NATS JWT for authentication
    nats_jwt_expires_at: str | None = None  # JWT expiry timestamp


def _load_state() -> AgentState:
    if not STATE_FILE.exists():
        return AgentState(
            agents={},
            version=None,
            core_tokens={},
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
            core_tokens={},
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
            core_tokens = data.get("core_tokens") or {}
            nats_jwt = data.get("nats_jwt")
            nats_jwt_expires_at = data.get("nats_jwt_expires_at")
            return AgentState(
                agents=agents,
                version=version,
                core_tokens=core_tokens,
                nats_jwt=nats_jwt,
                nats_jwt_expires_at=nats_jwt_expires_at,
            )
        return AgentState(
            agents=_normalize_agents(data),
            version=None,
            core_tokens={},
            nats_jwt=None,
            nats_jwt_expires_at=None,
        )
    return AgentState(agents={}, version=None, core_tokens={}, nats_jwt=None, nats_jwt_expires_at=None)


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
        "core_tokens": state.core_tokens or {},
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
    _save_state(state)
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


def get_core_token(agent_id: str) -> str | None:
    state = _load_state()
    tokens = state.core_tokens or {}
    record = tokens.get(agent_id)
    if not record:
        return None
    return record.get("token")


def save_core_token(agent_id: str, token: str, expires_at: str | None = None) -> None:
    state = _load_state()
    if state.core_tokens is None:
        state.core_tokens = {}
    state.core_tokens[agent_id] = {"token": token, "expires_at": expires_at or ""}
    _save_state(state)


def clear_core_token(agent_id: str) -> None:
    state = _load_state()
    tokens = state.core_tokens or {}
    if agent_id in tokens:
        tokens.pop(agent_id, None)
        state.core_tokens = tokens
        _save_state(state)


def get_core_token_record(agent_id: str) -> dict[str, str] | None:
    state = _load_state()
    tokens = state.core_tokens or {}
    return tokens.get(agent_id)


def is_core_token_valid(agent_id: str, now: datetime | None = None) -> bool:
    record = get_core_token_record(agent_id)
    if not record:
        return False
    token = record.get("token")
    if not token:
        clear_core_token(agent_id)
        return False
    expires_at = record.get("expires_at") or ""
    if not expires_at:
        return True
    try:
        expiry_dt = datetime.fromisoformat(expires_at)
    except (TypeError, ValueError):
        clear_core_token(agent_id)
        return False
    if expiry_dt.tzinfo is None:
        expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    if now < expiry_dt:
        return True
    clear_core_token(agent_id)
    return False


def save_nats_jwt(jwt: str, expires_at: str) -> None:
    """Save NATS JWT for authentication."""
    state = _load_state()
    state.nats_jwt = jwt
    state.nats_jwt_expires_at = expires_at
    _save_state(state)


def get_nats_jwt() -> str | None:
    """
    Get the NATS JWT if it exists and is valid.

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
    """Clear the stored NATS JWT."""
    state = _load_state()
    state.nats_jwt = None
    state.nats_jwt_expires_at = None
    _save_state(state)


def is_nats_jwt_valid() -> bool:
    """Check if a valid NATS JWT exists."""
    return get_nats_jwt() is not None


__all__ = [
    "clear_core_token",
    "clear_nats_jwt",
    "get_agent_version",
    "get_core_token",
    "get_core_token_record",
    "get_nats_jwt",
    "get_or_create_agent_id",
    "is_core_token_valid",
    "is_nats_jwt_valid",
    "remember_project_agent",
    "save_core_token",
    "save_nats_jwt",
]
