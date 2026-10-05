from enum import StrEnum
from typing import Any

from pydantic import BaseModel


class FrameType(StrEnum):
    AUTH = "auth"
    AUTH_OK = "auth_ok"
    DISPATCH_QUERY = "dispatch_query"
    QUERY_PROGRESS = "query_progress"
    # Terminal outcome for a query or session statement. Its payload carries
    # `result_schema` — `[{"name", "type"}, ...]` where `type` is DuckDB's own
    # logical-type spelling — captured off the relation *before* materialization,
    # because the Parquet write is lossy (HUGEINT -> DOUBLE, ENUM -> VARCHAR).
    # Null for DDL/DML, and absent from an agent older than this field, in which
    # case the control plane reports no schema rather than deriving a wrong one.
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
    # Result cache (control plane -> agent). RETAIN_RESULT asks the agent to keep a
    # query's result file past its retention window, until `retain_until` (epoch
    # seconds), because a cache entry serves rows from it; RELEASE_RESULT ends that.
    # Both keyed by `query_id`. An agent too old to know them ignores the frame,
    # and its normal retention removes the file: the cache then finds the file
    # gone and drops the entry, so the worst case is a miss.
    RETAIN_RESULT = "retain_result"
    RELEASE_RESULT = "release_result"
    # On-demand attach. The control plane sends only the catalogs a statement
    # names; when one turns out to need another (a view or macro reading it), the
    # agent asks with CATALOG_REQUEST `{request_id, query_id, catalog}` and the
    # control plane answers CATALOG_RESPONSE `{request_id, catalog, error}`, where
    # `catalog` is the attach descriptor, or null when the catalog is not in the
    # query's workspace or the principal may not reach it. Only sent by agents
    # advertising the "on_demand_attach" protocol feature, and only for work that
    # the control plane dispatched that way.
    CATALOG_REQUEST = "catalog_request"
    CATALOG_RESPONSE = "catalog_response"


class Frame(BaseModel):
    type: FrameType
    payload: dict[str, Any] = {}
    # W3C Trace Context carrier ("traceparent"/"tracestate"); None when the
    # sender has no active trace. Compatible in both directions: an old peer
    # drops the unknown key on parse (pydantic's default extra="ignore"), and
    # a frame from an old sender leaves it None here.
    trace_context: dict[str, str] | None = None
