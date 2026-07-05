"""Governed AI data assistant.

A model-agnostic chat assistant built on Pydantic AI. It browses catalog metadata
and runs SQL as a service-account principal, with every action flowing through
DuckHaven's existing REST enforcement chokepoints (``assert_workspace_member`` →
``sql_guard`` → ``assert_query_access``). The harness supplies the loop, provider
abstraction, tool-schema generation, hooks, and message serialization; this package
supplies the thin tools, the governance policy, the persistence, and the run
orchestration.
"""

from api.services.assistant.runner import (
    AssistantDisabledError,
    resume_turn,
    run_turn,
    stream_turn,
)

__all__ = [
    "AssistantDisabledError",
    "resume_turn",
    "run_turn",
    "stream_turn",
]
