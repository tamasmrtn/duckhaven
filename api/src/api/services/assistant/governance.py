"""The governance capability: audit every tool call via a wrapping hook.

This is the **audit and UX** layer, not the enforcement layer. Authorization is
enforced server-side by the REST chokepoint the tools call (``sql_guard`` +
``assert_query_access``); this hook records what the assistant attempted and its
outcome, and never widens access. It is the only module that imports Pydantic AI
hook APIs, so any V2 churn is contained here.

A single ``wrap_tool_execute`` hook is used (rather than before/after/error) because
it is the only variant that observes *all* outcomes — including the control-flow
signals ``ModelRetry`` (a refused call) and ``ApprovalRequired`` (a write awaiting
human confirmation), which never reach the discrete error hook.

Write access is governed by the tool itself (``run_sql`` refuses non-SELECT when the
service account lacks write access, and defers confirmed writes to human approval),
so there is no separate write tool to hide — a ``prepare_tools`` filter would have
nothing to remove.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic_ai import ApprovalRequired
from pydantic_ai.capabilities import Hooks
from pydantic_ai.exceptions import ToolRetryError
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import RunContext, ToolDefinition

from api.services.assistant.deps import AssistantDeps, ToolCallRecord


async def _audit(
    ctx: RunContext[AssistantDeps],
    *,
    call: ToolCallPart,
    tool_def: ToolDefinition,
    args: Any,
    handler,
) -> Any:
    record = ToolCallRecord(
        tool=call.tool_name,
        args=args if isinstance(args, dict) else None,
        started=time.monotonic(),
    )
    ctx.deps.records[call.tool_call_id] = record
    try:
        result = await handler(args)
    except ApprovalRequired:
        record.status = "approval_required"
        record.latency_ms = int((time.monotonic() - record.started) * 1000)
        raise
    except ToolRetryError as exc:
        # A ModelRetry raised inside a tool (e.g. a refused write or a governed
        # error the tool translated) reaches the wrap hook wrapped as this.
        record.status = "denied"
        record.detail = str(exc)
        record.latency_ms = int((time.monotonic() - record.started) * 1000)
        raise
    except Exception as exc:
        record.status = "error"
        record.detail = str(exc)
        record.latency_ms = int((time.monotonic() - record.started) * 1000)
        raise
    record.status = "ok"
    if isinstance(result, dict):
        if result.get("query_id"):
            record.query_id = str(result["query_id"])
        # A successful call can still carry a finding worth keeping. The one that
        # matters is hand-written SQL recomputing something a published metric
        # already defines: the query ran and the answer stands, so this is not an
        # error — but "how often are the agreed definitions worked around?" is
        # only answerable later if the warning is on the row, not just in the
        # reply the model saw and then forgot.
        warning = result.get("semantic_warning")
        if warning:
            record.detail = str(warning)
    record.latency_ms = int((time.monotonic() - record.started) * 1000)
    return result


def build_governance() -> Hooks:
    """Return the audit-hooks capability attached to the assistant agent."""
    return Hooks(
        tool_execute=_audit,
        id="duckhaven-governance",
        description="Audit and outcome recording for every assistant tool call.",
    )
