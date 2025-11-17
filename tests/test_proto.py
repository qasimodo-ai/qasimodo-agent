from __future__ import annotations

import base64

from qasimodo_agent.proto import AgentHeartbeat, AgentResult, AgentTask


def test_agent_task_roundtrip() -> None:
    task = AgentTask(
        agent_id="agent-123",
        project_id="proj-789",
        run_id="run-001",
        testbook_id="tbk-456",
        environment_id="env-abc",
        instructions="Do something",
        core_base_url="https://core.local",
        core_token="token-xyz",
    )
    encoded = task.SerializeToString()
    parsed = AgentTask()
    parsed.ParseFromString(encoded)
    assert parsed.agent_id == "agent-123"
    assert parsed.project_id == "proj-789"
    assert parsed.run_id == "run-001"
    assert parsed.instructions == "Do something"
    # Snapshot for cross-language compatibility (dashboard tests decode the same bytes)
    assert base64.b64encode(encoded).decode() == (
        "CglhZ2VudC0xMjMSCHByb2otNzg5GgdydW4tMDAxIgd0YmstNDU2KgdlbnYtYWJjM"
        "gxEbyBzb21ldGhpbmc6Emh0dHBzOi8vY29yZS5sb2NhbEIJdG9rZW4teHl6"
    )


def test_agent_result_roundtrip() -> None:
    result = AgentResult(
        agent_id="agent-123",
        project_id="proj-789",
        run_id="run-001",
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


def test_agent_heartbeat_roundtrip() -> None:
    heartbeat = AgentHeartbeat(
        agent_id="agent-123",
        status="online",
        timestamp=42,
        version="dev",
        capabilities=["browser_use"],
    )
    encoded = heartbeat.SerializeToString()
    parsed = AgentHeartbeat()
    parsed.ParseFromString(encoded)
    assert parsed.capabilities == ["browser_use"]
