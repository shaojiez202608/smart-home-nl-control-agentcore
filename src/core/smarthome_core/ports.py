"""Interfaces between the orchestration logic and the outside world.

The Lambda handler wires AWS-backed adapters (ElastiCache, AgentCore
Runtime, the platform API, AgentCore Memory, CloudWatch EMF). Local mode and
unit tests wire in-memory fakes. Keeping the pipeline behind these ports is
what makes it testable without an AWS account.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .memory_policy import PreferenceRecord
from .models import Device, DispatchResult, PlannedAction


class Cache(Protocol):
    def get_json(self, key: str) -> Any | None: ...
    def mget_json(self, keys: list[str]) -> list[Any | None]: ...
    def set_json(self, key: str, value: Any, ttl_seconds: int) -> None: ...
    def add_json(self, key: str, value: Any, ttl_seconds: int) -> bool:
        """Set only if the key does not exist (SET NX); True if this call created it."""
        ...

    def pop_json(self, key: str) -> Any | None:
        """Atomically read and delete (single-use tokens)."""
        ...


class DeviceDirectory(Protocol):
    def list_user_devices(self, user_id: str) -> list[Device]: ...


class CommandDispatcher(Protocol):
    def dispatch(self, user_id: str, action: PlannedAction, request_id: str) -> DispatchResult: ...


@dataclass
class AgentRequest:
    user_id: str
    session_id: str
    utterance: str
    channel: str
    devices: list[dict[str, Any]] = field(default_factory=list)
    preferences: list[str] = field(default_factory=list)


class AgentClient(Protocol):
    def plan(self, request: AgentRequest) -> str:
        """Return the raw model output (expected to be a JSON Plan)."""
        ...


class PreferenceStore(Protocol):
    def upsert(self, user_id: str, records: list[PreferenceRecord]) -> None: ...
    def list_records(self, user_id: str) -> list[PreferenceRecord]:
        """Newest record per (device, attribute) for this user, including aliases."""
        ...


class Metrics(Protocol):
    def put(self, name: str, value: float, unit: str = "Milliseconds") -> None: ...
    def flush(self, dimensions: dict[str, str] | None = None) -> None: ...


class NullMetrics:
    def put(self, name: str, value: float, unit: str = "Milliseconds") -> None:
        pass

    def flush(self, dimensions: dict[str, str] | None = None) -> None:
        pass
