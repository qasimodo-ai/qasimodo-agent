from __future__ import annotations

import json
import uuid
from pathlib import Path


STATE_DIR = Path.home() / ".qasimodo-agent"
STATE_FILE = STATE_DIR / "agents.json"


def _load_state() -> dict[str, str]:
    if not STATE_FILE.exists():
        return {}
    try:
        with STATE_FILE.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
            if isinstance(data, dict):
                return {str(key): str(value) for key, value in data.items()}
    except Exception:  # noqa: BLE001
        pass
    return {}


def _save_state(entries: dict[str, str]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    with STATE_FILE.open("w", encoding="utf-8") as handle:
        json.dump(entries, handle, indent=2)


def get_or_create_agent_id(project_id: str) -> str:
    state = _load_state()
    existing = state.get(project_id)
    if existing:
        return existing
    agent_id = str(uuid.uuid4())
    state[project_id] = agent_id
    _save_state(state)
    return agent_id


__all__ = ["get_or_create_agent_id"]
