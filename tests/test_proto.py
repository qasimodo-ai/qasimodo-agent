from __future__ import annotations

import base64

from qasimodo_agent.proto import (
    AgentHeartbeat,
    AgentMetadata,
    AgentResult,
    AgentResultKind,
    AgentTask,
)


def test_agent_task_roundtrip() -> None:
    task = AgentTask(
        metadata=AgentMetadata(
            agent_id="agent-123",
            project_id="proj-789",
            run_id="run-001",
            agent_version="dev",
        ),
        testbook_id="tbk-456",
        environment_id="env-abc",
        instructions="Do something",
        core_base_url="https://core.local",
        core_token="token-xyz",
    )
    encoded = task.SerializeToString()
    parsed = AgentTask()
    parsed.ParseFromString(encoded)
    assert parsed.metadata.agent_id == "agent-123"
    assert parsed.metadata.project_id == "proj-789"
    assert parsed.metadata.run_id == "run-001"
    assert parsed.metadata.agent_version == "dev"
    assert parsed.instructions == "Do something"
    # Snapshot for cross-language compatibility (dashboard tests decode the same bytes)
    assert base64.b64encode(encoded).decode() == (
        "CiMKCWFnZW50LTEyMxIIcHJvai03ODkaB3J1bi0wMDEiA2RldhIHdGJrLTQ1NhoHZW52"
        "LWFiYyIMRG8gc29tZXRoaW5nKhJodHRwczovL2NvcmUubG9jYWwyCXRva2VuLXh5eg=="
    )


def test_agent_result_roundtrip() -> None:
    result = AgentResult(
        metadata=AgentMetadata(
            agent_id="agent-123",
            project_id="proj-789",
            run_id="run-001",
            agent_version="dev",
        ),
        kind=AgentResultKind.AGENT_RESULT_KIND_STATUS,
        status="STATUS_PASSED",
        message="ok",
        error="",
        started_at="2025-01-01T00:00:00Z",
        finished_at="2025-01-01T00:10:00Z",
        history_json="{}",
    )
    encoded = result.SerializeToString()
    parsed = AgentResult()
    parsed.ParseFromString(encoded)
    assert parsed.status == "STATUS_PASSED"
    assert parsed.history_json == "{}"
    assert parsed.kind == AgentResultKind.AGENT_RESULT_KIND_STATUS
    assert parsed.metadata.agent_version == "dev"


def test_agent_heartbeat_roundtrip() -> None:
    heartbeat = AgentHeartbeat(
        metadata=AgentMetadata(
            agent_id="agent-123",
            project_id="proj-789",
            run_id="",
            agent_version="dev",
        ),
        status="online",
        timestamp=42,
        capabilities=["browser_use"],
    )
    encoded = heartbeat.SerializeToString()
    parsed = AgentHeartbeat()
    parsed.ParseFromString(encoded)
    assert parsed.capabilities == ["browser_use"]
    assert parsed.metadata.agent_version == "dev"
