"""Per-run dependencies and the in-memory tool-call audit accumulator."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from api.services.assistant.gateway import Gateway


@dataclass
class ToolCallRecord:
    tool: str
    args: dict | None
    status: str = "ok"
    detail: str | None = None
    query_id: str | None = None
    latency_ms: int | None = None
    # Monotonic start time, set by the before-hook; not persisted.
    started: float | None = None


@dataclass
class AssistantDeps:
    """Injected into every tool via ``RunContext.deps``."""

    gateway: Gateway
    catalog: str | None
    can_write: bool
    query_timeout_s: float
    # The service account this turn acts as — stamped on the conversation for
    # audit attribution.
    service_account_id: uuid.UUID
    # The SQL currently in the user's worksheet editor, sent with the turn so the
    # assistant can read and propose edits to it. None when no editor is open.
    editor_sql: str | None = None
    # Tool-call audit records for this run, keyed by the SDK tool_call_id. Populated
    # by the governance hooks; drained by the runner and persisted after the turn.
    records: dict[str, ToolCallRecord] = field(default_factory=dict)
