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


class Frame(BaseModel):
    type: FrameType
    payload: dict[str, Any] = {}
