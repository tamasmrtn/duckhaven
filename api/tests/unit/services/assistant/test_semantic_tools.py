"""The semantic tools, and how the assistant is meant to behave around them.

Driven through the real ``Agent`` with a scripted model and a stub gateway, so
what is under test is the wiring — tool schemas, error translation, the shape of
what comes back — rather than a language model's judgement.

The behaviours that matter most are the refusals. A compiler error must reach the
model as something it can act on, an ambiguous term must not silently resolve,
and a broken definition must not quietly fall back to hand-written SQL.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic_ai import Agent, capture_run_messages
from pydantic_ai.messages import ToolReturnPart

from api.services.assistant.deps import AssistantDeps
from api.services.assistant.gateway import GatewayError, _aggregated_columns
from api.services.assistant.prompts import BASE_PROMPT, build_instructions, format_summary
from api.services.assistant.tools import ALL_TOOLS
from tests.unit.services.assistant.conftest import scripted_model, text_step, tool_step

COMPILED_SQL = (
    "SELECT SUM(orders.total_amount) FILTER(WHERE orders.status <> 'test') AS revenue\n"
    "FROM warehouse.analytics.orders AS orders"
)


class StubGateway:
    """Records what the tools asked for and returns canned governed responses."""

    def __init__(self, **overrides) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.overrides = overrides
        self._row_cap = 100

    def _record(self, name, args, kwargs):
        self.calls.append((name, args, kwargs))

    def _maybe_raise(self, name):
        error = self.overrides.get(f"{name}_error")
        if error is not None:
            raise error

    async def search_semantic(self, query, *, limit=10):
        self._record("search_semantic", (query,), {})
        self._maybe_raise("search_semantic")
        return self.overrides.get(
            "search_result",
            {
                "hits": [
                    {
                        "kind": "metric",
                        "model": "sales",
                        "name": "revenue",
                        "label": "Revenue",
                        "description": "Net booked revenue.",
                        "synonyms": ["turnover"],
                        "status": "published",
                        "expression": "SUM(total_amount) FILTER (WHERE status <> 'test')",
                        "time_dimension": "order_date",
                        "caveat": "Excludes internal test orders.",
                    }
                ],
                "ambiguous": [],
            },
        )

    async def get_semantic_model(self, model):
        self._record("get_semantic_model", (model,), {})
        self._maybe_raise("get_semantic_model")
        return {"model": model, "metrics": [{"name": "revenue"}]}

    async def compile_metric_query(self, body):
        self._record("compile_metric_query", (body,), {})
        self._maybe_raise("compile_metric_query")
        return self.overrides.get(
            "compiled",
            {
                "sql": COMPILED_SQL,
                "definitions_used": [{"kind": "metric", "model": "sales", "name": "revenue"}],
                "warnings": ["Revenue: Excludes internal test orders."],
            },
        )

    async def metric_definition(self, model, metric):
        self._record("metric_definition", (model, metric), {})
        self._maybe_raise("metric_definition")
        return {
            "model": model,
            "metric": metric,
            "calculation": "SUM(total_amount) FILTER (WHERE status <> 'test')",
            "measured_on": "order_date",
            "caveat": "Excludes internal test orders.",
            "status": "published",
        }

    async def run_sql(self, sql, *, catalog, timeout_s):
        self._record("run_sql", (sql,), {"catalog": catalog})
        self._maybe_raise("run_sql")
        return dict(
            self.overrides.get(
                "run_result",
                {
                    "query_id": "q1",
                    "status": "done",
                    "columns": ["revenue"],
                    "rows": [{"revenue": 100}],
                    "total": 1,
                    "truncated": False,
                },
            )
        )

    async def list_semantic_models(self):
        self._record("list_semantic_models", (), {})
        return self.overrides.get("models", [])


def deps(gateway: StubGateway, **kwargs) -> AssistantDeps:
    import uuid

    return AssistantDeps(
        gateway=gateway,
        catalog="warehouse",
        can_write=False,
        query_timeout_s=30.0,
        service_account_id=uuid.uuid4(),
        **kwargs,
    )


def agent_with(steps):
    return Agent(
        scripted_model(steps),
        deps_type=AssistantDeps,
        instructions=build_instructions,
        tools=ALL_TOOLS,
    )


def tool_returns(messages) -> dict[str, object]:
    """Map tool name to what the tool actually returned to the model."""
    out: dict[str, object] = {}
    for message in messages:
        for part in message.parts:
            if isinstance(part, ToolReturnPart):
                out[part.tool_name] = part.content
    return out


# ── The happy path ────────────────────────────────────────────────────────────


async def test_a_metric_question_routes_through_the_semantic_layer():
    gateway = StubGateway()
    agent = agent_with(
        [
            tool_step("search_semantic", {"query": "revenue last month"}),
            tool_step(
                "query_metric",
                {
                    "model": "sales",
                    "metrics": ["revenue"],
                    "grain": "month",
                    "time_window": {"kind": "last_complete", "grain": "month", "n": 1},
                },
            ),
            text_step("Revenue last month was 100."),
        ]
    )

    result = await agent.run("what was revenue last month?", deps=deps(gateway))

    assert result.output == "Revenue last month was 100."
    called = [c[0] for c in gateway.calls]
    assert called == ["search_semantic", "compile_metric_query", "run_sql"]


async def test_query_metric_executes_the_compiled_sql_not_the_models_own():
    """The core guarantee: the model chooses concepts, the compiler writes SQL."""
    gateway = StubGateway()
    agent = agent_with(
        [
            tool_step("query_metric", {"model": "sales", "metrics": ["revenue"]}),
            text_step("done"),
        ]
    )

    await agent.run("revenue?", deps=deps(gateway))

    executed = next(c for c in gateway.calls if c[0] == "run_sql")[1][0]
    assert executed == COMPILED_SQL


async def test_query_metric_returns_the_sql_and_the_definitions_it_used():
    gateway = StubGateway()
    agent = agent_with(
        [
            tool_step("query_metric", {"model": "sales", "metrics": ["revenue"]}),
            text_step("done"),
        ]
    )

    with capture_run_messages() as messages:
        await agent.run("revenue?", deps=deps(gateway))

    returned = tool_returns(messages)["query_metric"]
    assert returned["sql"] == COMPILED_SQL
    assert returned["definitions_used"][0]["name"] == "revenue"
    assert any("test orders" in n for n in returned["notes"])


async def test_explain_metric_answers_from_the_definition():
    gateway = StubGateway()
    agent = agent_with(
        [
            tool_step("explain_metric", {"model": "sales", "metric": "revenue"}),
            text_step("Revenue sums order totals, excluding test orders."),
        ]
    )

    with capture_run_messages() as messages:
        await agent.run("how is revenue calculated?", deps=deps(gateway))

    returned = tool_returns(messages)["explain_metric"]
    assert returned["calculation"] == "SUM(total_amount) FILTER (WHERE status <> 'test')"
    assert returned["measured_on"] == "order_date"


# ── Refusals reach the model as something it can act on ───────────────────────


async def test_a_compiler_refusal_comes_back_as_a_retryable_message():
    """The compiler names the legal alternatives; the model must see them."""
    gateway = StubGateway(
        compile_metric_query_error=GatewayError(
            "Not allowed: 'sales' has no metric called 'profit'. Available: revenue."
        )
    )
    agent = agent_with(
        [
            tool_step("query_metric", {"model": "sales", "metrics": ["profit"]}),
            text_step("There is no profit metric; sales defines revenue."),
        ]
    )

    with capture_run_messages() as messages:
        result = await agent.run("profit?", deps=deps(gateway))

    assert "no profit metric" in result.output
    # It never fell through to hand-written SQL.
    assert not any(c[0] == "run_sql" for c in gateway.calls)
    rendered = str(messages)
    assert "Available: revenue" in rendered


async def test_an_ambiguous_term_is_surfaced_rather_than_resolved():
    """Two authoritative metrics fit the words; the answer is a question."""
    gateway = StubGateway(
        search_result={
            "hits": [
                {
                    "kind": "metric",
                    "model": "sales",
                    "name": "total_customers",
                    "label": "Total customers",
                    "description": "Every customer row.",
                    "synonyms": ["customers"],
                    "status": "published",
                },
                {
                    "kind": "metric",
                    "model": "sales",
                    "name": "active_customers",
                    "label": "Active customers",
                    "description": "Customers with an order in the period.",
                    "synonyms": ["customers"],
                    "status": "published",
                },
            ],
            "ambiguous": [
                {
                    "kind": "metric",
                    "model": "sales",
                    "name": "total_customers",
                    "label": "Total customers",
                    "status": "published",
                    "synonyms": [],
                },
                {
                    "kind": "metric",
                    "model": "sales",
                    "name": "active_customers",
                    "label": "Active customers",
                    "status": "published",
                    "synonyms": [],
                },
            ],
        }
    )
    agent = agent_with(
        [
            tool_step("search_semantic", {"query": "how many customers"}),
            text_step("Do you mean total customers or active customers?"),
        ]
    )

    with capture_run_messages() as messages:
        result = await agent.run("how many customers do we have?", deps=deps(gateway))

    returned = tool_returns(messages)["search_semantic"]
    assert len(returned["ambiguous"]) == 2
    assert "?" in result.output
    assert not any(c[0] == "compile_metric_query" for c in gateway.calls)


async def test_a_broken_definition_is_not_silently_replaced():
    gateway = StubGateway(
        compile_metric_query_error=GatewayError(
            "Not allowed: Metric 'revenue' references column(s) that no longer exist: total_amount."
        )
    )
    agent = agent_with(
        [
            tool_step("query_metric", {"model": "sales", "metrics": ["revenue"]}),
            text_step("The revenue definition is broken: it reads total_amount, which is gone."),
        ]
    )

    result = await agent.run("revenue?", deps=deps(gateway))

    assert "broken" in result.output
    assert not any(c[0] == "run_sql" for c in gateway.calls)


# ── Falling back, and staying out of the way ──────────────────────────────────


async def test_with_no_semantic_models_the_semantic_section_is_absent():
    """The deployment-safety property, stated as a test.

    Asserts the absence of the semantic paragraph rather than equality with
    ``BASE_PROMPT``: the instructions legitimately carry other blocks now, so
    equality would only restate how ``build_instructions`` happens to be built.
    The baseline itself is pinned in ``test_prompts.py``.
    """
    ctx = SimpleNamespace(deps=deps(StubGateway()))

    instructions = build_instructions(ctx)

    assert instructions.startswith(BASE_PROMPT)
    assert "curated semantic models" not in instructions
    assert "search_semantic FIRST" not in instructions


async def test_with_models_the_instructions_name_them():
    ctx = SimpleNamespace(
        deps=deps(
            StubGateway(),
            semantic_summary=format_summary(
                [{"model": "sales", "metrics": 12, "description": "Orders and revenue."}]
            ),
        )
    )

    instructions = build_instructions(ctx)

    assert "sales (12 metrics)" in instructions
    assert "Orders and revenue." in instructions
    assert "search_semantic FIRST" in instructions
    assert instructions.startswith(BASE_PROMPT)


async def test_the_ordinary_discovery_path_still_works():
    """A question the semantic layer does not cover must not be blocked by it."""
    gateway = StubGateway()
    agent = agent_with(
        [
            tool_step("run_sql", {"sql": "SELECT 1"}),
            text_step("1"),
        ]
    )

    result = await agent.run("select 1", deps=deps(gateway))

    assert result.output == "1"
    assert gateway.calls[0][0] == "run_sql"


async def test_free_sql_over_a_defined_column_is_warned_about_not_blocked():
    gateway = StubGateway(
        run_result={
            "query_id": "q1",
            "status": "done",
            "columns": ["s"],
            "rows": [{"s": 5}],
            "total": 1,
            "truncated": False,
            "semantic_warning": (
                "This aggregates column(s) already defined by published metric(s): "
                "revenue (model sales, over total_amount)."
            ),
        }
    )
    agent = agent_with(
        [
            tool_step("run_sql", {"sql": "SELECT SUM(total_amount) FROM analytics.orders"}),
            text_step("That is 5, but note there is a curated revenue metric."),
        ]
    )

    with capture_run_messages() as messages:
        result = await agent.run("sum total_amount", deps=deps(gateway))

    returned = tool_returns(messages)["run_sql"]
    assert "already defined" in returned["semantic_warning"]
    # The query still ran and still answered.
    assert returned["rows"] == [{"s": 5}]
    assert "5" in result.output


async def test_compiled_sql_does_not_warn_about_itself():
    """A nudge toward query_metric on the output of query_metric would be noise."""
    gateway = StubGateway(
        run_result={
            "query_id": "q1",
            "status": "done",
            "columns": ["revenue"],
            "rows": [{"revenue": 100}],
            "total": 1,
            "truncated": False,
            "semantic_warning": "would be circular",
        }
    )
    agent = agent_with(
        [
            tool_step("query_metric", {"model": "sales", "metrics": ["revenue"]}),
            text_step("100"),
        ]
    )

    with capture_run_messages() as messages:
        await agent.run("revenue?", deps=deps(gateway))

    assert "semantic_warning" not in tool_returns(messages)["query_metric"]


# ── The conflict detector itself ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("sql", "expected"),
    [
        (
            "SELECT SUM(total_amount) FROM analytics.orders",
            {(None, "analytics", "orders"): {"total_amount"}},
        ),
        (
            "SELECT SUM(o.total_amount) FROM warehouse.analytics.orders o "
            "JOIN warehouse.analytics.customers c ON o.customer_id = c.id",
            {("warehouse", "analytics", "orders"): {"total_amount"}},
        ),
        # Unqualified column across two tables: ambiguous, so no claim is made.
        (
            "SELECT SUM(total_amount) FROM analytics.orders o "
            "JOIN analytics.customers c ON o.customer_id = c.id",
            {},
        ),
        # Not an aggregate, so nothing is being redefined.
        ("SELECT total_amount FROM analytics.orders", {}),
        # Unparseable input must not raise.
        ("SELECT SUM(((", {}),
    ],
)
def test_aggregated_columns(sql, expected):
    assert _aggregated_columns(sql) == expected


async def test_the_bypass_warning_lands_on_the_audit_row():
    """Otherwise "how often is the semantic layer worked around?" is unanswerable.

    The warning reaching the model is what lets it correct itself; the warning
    reaching the audit row is what lets somebody count it afterwards. Those are
    different requirements and only the first is satisfied by the tool result.
    """
    from pydantic_ai.messages import ToolCallPart

    from api.services.assistant.governance import _audit

    gateway = StubGateway(
        run_result={
            "query_id": "q1",
            "status": "done",
            "columns": ["s"],
            "rows": [{"s": 5}],
            "total": 1,
            "truncated": False,
            "semantic_warning": "revenue (model sales, over total_amount)",
        }
    )
    ctx = SimpleNamespace(deps=deps(gateway))

    async def handler(_args):
        return await gateway.run_sql("SELECT SUM(total_amount) FROM s.t", catalog=None, timeout_s=1)

    await _audit(
        ctx,
        call=ToolCallPart("run_sql", {}, tool_call_id="c1"),
        tool_def=None,
        args={},
        handler=handler,
    )

    record = ctx.deps.records["c1"]
    assert record.status == "ok"  # the query ran; this is not a failure
    assert record.query_id == "q1"
    assert "revenue" in (record.detail or "")
