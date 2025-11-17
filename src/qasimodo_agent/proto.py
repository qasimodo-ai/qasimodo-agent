"""Thin wrapper around the generated qasimodo.agent protobuf messages."""

from __future__ import annotations

from qasimodo_agent.protos import agent_pb2 as _agent_pb2

AgentTask = _agent_pb2.AgentTask
AgentResult = _agent_pb2.AgentResult
AgentHeartbeat = _agent_pb2.AgentHeartbeat

__all__ = ["AgentTask", "AgentResult", "AgentHeartbeat"]
