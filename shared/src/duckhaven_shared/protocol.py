from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class FrameType(StrEnum):
    AUTH = "auth"
    AUTH_OK = "auth_ok"
    DISPATCH_QUERY = "dispatch_query"
    QUERY_PROGRESS = "query_progress"
    QUERY_DONE = "query_done"
    CANCEL_QUERY = "cancel_query"
    HEARTBEAT = "heartbeat"
    AGENT_STATUS = "agent_status"
    METRICS_SAMPLE = "metrics_sample"
    SET_CONCURRENCY = "set_concurrency"
    # SQL session layer: the control plane opens a session bound to one agent,
    # which holds a persistent DuckDB connection; statements run against it, then
    # the session is closed. OPEN_SESSION/EXEC_STATEMENT/CLOSE_SESSION are sent by
    # the API; SESSION_OPENED/SESSION_CLOSED are the agent's lifecycle acks.
    # A statement's completion reuses QUERY_DONE/QUERY_PROGRESS (keyed by the
    # statement's query_id), so no new completion frame is needed.
    OPEN_SESSION = "open_session"
    SESSION_OPENED = "session_opened"
    EXEC_STATEMENT = "exec_statement"
    CLOSE_SESSION = "close_session"
    SESSION_CLOSED = "session_closed"
    # Receipt (not outcome) for EXEC_STATEMENT, keyed by the statement's query_id.
    # Sent the moment the agent takes the frame off the wire, before the session
    # lock, so a statement that never arrives is distinguishable from one that is
    # merely slow: it flips the row queued -> running, and the reaper fails rows
    # left queued past the short ack deadline. An old agent never sends it, so the
    # reaper only applies that deadline to agents advertising the "statement_ack"
    # protocol feature (see AgentCapabilities.protocol_features).
    STATEMENT_ACK = "statement_ack"


class Frame(BaseModel):
    type: FrameType
    payload: dict[str, Any] = {}
    # W3C Trace Context carrier ("traceparent"/"tracestate"); None when the
    # sender has no active trace. Compatible in both directions: an old peer
    # drops the unknown key on parse (pydantic's default extra="ignore"), and
    # a frame from an old sender leaves it None here.
    trace_context: dict[str, str] | None = None
