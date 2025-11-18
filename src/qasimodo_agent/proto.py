"""Thin wrapper around the generated qasimodo.agent protobuf messages."""

from __future__ import annotations

from qasimodo_agent.protos import agent_pb2 as _agent_pb2

AgentMetadata = _agent_pb2.AgentMetadata
AgentTask = _agent_pb2.AgentTask
AgentStepResult = _agent_pb2.AgentStepResult
AgentResult = _agent_pb2.AgentResult
AgentResultKind = _agent_pb2.AgentResultKind
AgentHeartbeat = _agent_pb2.AgentHeartbeat

__all__ = [
    "AgentMetadata",
    "AgentTask",
    "AgentStepResult",
    "AgentResult",
    "AgentResultKind",
    "AgentHeartbeat",
]
