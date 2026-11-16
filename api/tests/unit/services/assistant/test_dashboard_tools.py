"""The dashboard tools, called directly over a fake gateway."""

import uuid
from types import SimpleNamespace

import pytest
from pydantic_ai import ModelRetry

from api.schemas.assistant import DashboardContext, WorksheetContext
from api.services.assistant.deps import AssistantDeps
from api.services.assistant.gateway import GatewayError
from api.services.assistant.tools import (
    get_dashboard,
    list_dashboards,
    preview_widgets,
    propose_dashboard_changes,
    run_sql,
    run_widget,
    show_chart,
)


def _widget_event(widget_id: str = "revenue", data=None) -> dict:
    return {
        "type": "widget",
        "id": widget_id,
        "state": "ok",
        "scope": {"window": {"text": "Last 30 days"}, "applied": ["period"], "skipped": []},
        "rows": {
            "columns": [{"name": "revenue", "role": "measure"}],
            "data": data if data is not None else [["510"]],
            "truncated": False,
            "query_id": f"q-{widget_id}",
        },
        "chart": {"type": "kpi", "y": [{"field": "revenue"}]},
        "cache": "miss",
    }


class FakeGateway:
    row_cap = 100

    def __init__(self, *, events=None, result=None, fail=None):
        self.calls: list[tuple[str, dict]] = []
        self._events = events
        self._result = result
        self._fail = fail
        self._previews = 0

    def cap_rows(self, rows):
        return rows, False

    def _record(self, tool, **kw):
        self.calls.append((tool, kw))
        if self._fail:
            raise GatewayError(self._fail)

    async def list_dashboards(self, **kw):
        self._record("list_dashboards", **kw)
        return {"dashboards": [], "more": False}

    async def get_dashboard(self, name, **kw):
        self._record("get_dashboard", name=name, **kw)
        return {"name": name}

    async def run_dashboard(self, name, **kw):
        self._record("run_dashboard", name=name, **kw)
        return self._events or [
            {
                "type": "start",
                "parameters": [
                    {"name": "period", "value": "last_30_days", "window": {"text": "…"}}
                ],
            },
            _widget_event(),
        ]

    async def hold_preview(self, spec, *, preview_id):
        self._record("hold_preview", spec=spec, preview_id=preview_id)
        if preview_id is None:
            self._previews += 1
            return f"p-{self._previews}"
        return preview_id

    async def run_preview(self, preview_id, **kw):
        self._record("run_preview", preview_id=preview_id, **kw)
        return self._events or []

    async def own_result(self, query_id, *, limit):
        self._record("own_result", query_id=query_id, limit=limit)
        return self._result

    async def run_sql(self, sql, **kw):
        self._record("run_sql", sql=sql, **kw)
        return {"query_id": "q-1"}

    async def propose_dashboard_changes(self, name, **kw):
        self._record("propose_dashboard_changes", name=name, **kw)
        return {
            "id": "p-1",
            "dashboard": name,
            "title": "Sales",
            "summary": kw["summary"],
            "changes": [{"path": "widgets.orders", "change": "added"}],
            "managed": name == "git-sales",
            "expires_at": "2026-11-16T10:00:00Z",
            "result_spec": {"version": 1},
            "result_source": "version: 1\n",
        }


def _ctx(gateway, page=None, *, can_write=False):
    deps = AssistantDeps(
        gateway=gateway,
        catalog=None,
        can_write=can_write,
        query_timeout_s=30.0,
        service_account_id=uuid.uuid4(),
        page=page,
    )
    return SimpleNamespace(deps=deps, tool_call_id="call-1", tool_call_approved=False)


ON_SALES = DashboardContext(
    page="dashboard", name="sales", version=3, params={"period": "last_7_days", "country": "NL"}
)


# ── Reading dashboards ────────────────────────────────────────────────────────


async def test_the_dashboard_on_screen_is_read_when_none_is_named():
    gw = FakeGateway()
    assert await get_dashboard(_ctx(gw, ON_SALES)) == {"name": "sales"}
    assert await get_dashboard(_ctx(gw, ON_SALES), name="ops", draft=True) == {"name": "ops"}
    assert gw.calls[1] == ("get_dashboard", {"name": "ops", "draft": True})


async def test_away_from_a_dashboard_one_must_be_named():
    page = WorksheetContext(page="worksheet", sql="SELECT 1")
    with pytest.raises(ModelRetry, match="list_dashboards"):
        await get_dashboard(_ctx(FakeGateway(), page))


async def test_a_gateway_failure_is_for_the_model_to_retry():
    with pytest.raises(ModelRetry, match="Not found"):
        await list_dashboards(_ctx(FakeGateway(fail="Not found: no dashboards")), query="x")


# ── run_widget ────────────────────────────────────────────────────────────────


async def test_a_widget_on_screen_runs_with_the_values_on_screen():
    gw = FakeGateway()
    out = await run_widget(_ctx(gw, ON_SALES), widget="revenue")

    call = gw.calls[0][1]
    assert call["name"] == "sales" and call["widgets"] == ["revenue"]
    assert call["params"] == {"period": "last_7_days", "country": "NL"}
    assert call["compare"] is None and call["timeout_s"] == 30.0
    assert out["dashboard"] == "sales" and out["rows"] == [["510"]]
    assert out["window"] == "Last 30 days" and out["filtered_by"] == ["period"]
    assert out["parameters"] == [{"name": "period", "value": "last_30_days", "window": "…"}]


async def test_values_the_model_gives_replace_the_pages_and_another_dashboard_takes_its_defaults():
    gw = FakeGateway()
    await run_widget(_ctx(gw, ON_SALES), widget="revenue", params={"country": "DE"})
    await run_widget(_ctx(gw, ON_SALES), widget="tickets", dashboard="ops")

    assert gw.calls[0][1]["params"] == {"country": "DE"}
    assert gw.calls[1][1]["name"] == "ops" and gw.calls[1][1]["params"] is None


async def test_a_widget_can_be_compared_with_an_earlier_window():
    gw = FakeGateway()
    await run_widget(_ctx(gw, ON_SALES), widget="revenue", compare_to="previous_year")
    assert gw.calls[0][1]["compare"] == "previous_year"

    with pytest.raises(ModelRetry, match="previous_period or previous_year"):
        await run_widget(_ctx(gw, ON_SALES), widget="revenue", compare_to="last_week")


async def test_a_text_widget_has_no_numbers():
    gw = FakeGateway(events=[{"type": "start"}, {"type": "done"}])
    out = await run_widget(_ctx(gw, ON_SALES), widget="about")
    assert out["state"] == "text"


async def test_rows_past_the_byte_cap_are_cut_and_said_to_be():
    gw = FakeGateway()
    gw.cap_rows = lambda rows: (rows[:1], True)
    out = await run_widget(_ctx(gw, ON_SALES), widget="revenue")
    assert out["truncated"] is True


# ── preview_widgets ───────────────────────────────────────────────────────────


def _kpi(widget_id: str, title: str = "Revenue") -> dict:
    return {"id": widget_id, "type": "kpi", "title": title, "sql": "SELECT 1 AS revenue"}


async def test_widgets_are_previewed_each_in_a_row_of_its_own_in_one_preview():
    gw = FakeGateway(events=[_widget_event("a"), _widget_event("b", data=[["1"], ["2"]])])
    ctx = _ctx(gw)
    parameters = {"period": {"type": "time_range", "default": "last_30_days"}}

    out = await preview_widgets(
        ctx, [_kpi("a"), _kpi("b", "Total 42")], parameters=parameters, params={"period": "ytd"}
    )

    spec = gw.calls[0][1]["spec"]
    assert spec["parameters"] == parameters
    assert [row["widgets"][0]["width"] for row in spec["rows"]] == [12, 12]
    assert gw.calls[1] == (
        "run_preview",
        {"preview_id": "p-1", "params": {"period": "ytd"}, "timeout_s": 30.0},
    )
    a, b = out["widgets"]
    assert a["chart"]["type"] == "kpi" and a["lint"] == []
    # Each lint names what reads badly.
    assert any("title holds a number" in n for n in b["lint"])
    assert any("one number" in n for n in b["lint"])

    # A second call in the same turn reuses the preview.
    await preview_widgets(ctx, [_kpi("a")])
    assert gw.calls[2][1]["preview_id"] == "p-1"


async def test_a_widget_the_preview_did_not_run_is_a_text_widget():
    gw = FakeGateway(events=[])
    out = await preview_widgets(_ctx(gw), [{"id": "note", "type": "text", "markdown": "Hi"}])
    assert out["widgets"][0]["state"] == "text"


@pytest.mark.parametrize("count", [0, 9])
async def test_too_few_or_too_many_widgets_are_refused(count):
    with pytest.raises(ModelRetry, match="between 1 and 8"):
        await preview_widgets(_ctx(FakeGateway()), [_kpi(f"w{i}") for i in range(count)])


async def test_a_preview_the_server_refuses_is_for_the_model_to_fix():
    gw = FakeGateway(fail="Not allowed: rows.0.widgets.0.query: unknown metric")
    with pytest.raises(ModelRetry, match="unknown metric"):
        await preview_widgets(_ctx(gw), [_kpi("a")])


# ── show_chart ────────────────────────────────────────────────────────────────


def _result(rows: list[dict]) -> dict:
    return {
        "query_id": "q-7",
        "sql": "SELECT month, revenue FROM t",
        "row_count": len(rows),
        "columns": ["month", "revenue"],
        "column_schema": None,
        "rows": rows,
        "total": len(rows),
    }


TREND = {
    "id": "trend",
    "type": "line",
    "title": "Revenue",
    "columns": {"x": "month", "y": ["revenue"]},
}


async def test_a_chart_is_kept_for_the_chat_and_not_shown_to_the_model():
    rows = [{"month": "2026-09-01", "revenue": 4}, {"month": "2026-10-01", "revenue": 6}]
    gw = FakeGateway(result=_result(rows))

    out = await show_chart(_ctx(gw), query_id="q-7", widget=TREND)

    assert gw.calls[0] == ("own_result", {"query_id": "q-7", "limit": 1000})
    # What the model reads: that it was drawn, and how many rows, not the rows.
    told = out.return_value
    assert told["shown"] is True and told["rows"] == 2 and told["lint"] == []
    frame = out.metadata["chart"]
    assert frame["type"] == "chart" and frame["tool_call_id"] == "call-1"
    assert frame["result_hash"] == told["result_hash"]
    assert frame["widget"]["sql"] == "SELECT month, revenue FROM t"
    assert frame["event"]["rows"]["data"] == [["2026-09-01", 4], ["2026-10-01", 6]]


async def test_a_chart_that_cannot_be_drawn_is_for_the_model_to_fix():
    gw = FakeGateway(result=_result([{"month": 1, "revenue": 2}]))
    with pytest.raises(ModelRetry, match="not a query or SQL"):
        await show_chart(_ctx(gw), query_id="q-7", widget={**TREND, "sql": "SELECT 1"})


async def test_a_query_someone_else_ran_is_not_drawn():
    gw = FakeGateway(fail="I can only draw results of queries I ran in this conversation.")
    with pytest.raises(ModelRetry, match="only draw results"):
        await show_chart(_ctx(gw), query_id="q-x", widget=TREND)


# ── run_sql from a dashboard ──────────────────────────────────────────────────


async def test_from_a_dashboard_a_write_is_refused_even_with_write_access():
    gw = FakeGateway()
    with pytest.raises(ModelRetry, match="From a dashboard only SELECT"):
        await run_sql(_ctx(gw, ON_SALES, can_write=True), sql="DELETE FROM t")
    assert gw.calls == []

    # Reads still run.
    assert await run_sql(_ctx(gw, ON_SALES, can_write=True), sql="SELECT 1") == {"query_id": "q-1"}


# ── propose_dashboard_changes ─────────────────────────────────────────────────

ADD = [{"op": "add_widget", "widget": {"id": "orders", "type": "text", "markdown": "x"}}]


async def test_a_proposal_goes_to_the_dashboard_on_screen_and_the_chat_gets_its_card():
    gw = FakeGateway()

    out = await propose_dashboard_changes(
        _ctx(gw, ON_SALES, can_write=True), operations=ADD, summary="Add orders."
    )

    assert gw.calls == [
        (
            "propose_dashboard_changes",
            {"name": "sales", "operations": ADD, "summary": "Add orders."},
        )
    ]
    told = out.return_value
    assert told["proposed"] is True and told["proposal_id"] == "p-1"
    assert "accept it as the draft" in told["note"]
    # The model is not handed the whole resulting file back.
    assert "result_spec" not in told and "result_source" not in told
    frame = out.metadata["proposal"]
    assert frame["type"] == "dashboard_proposal" and frame["tool_call_id"] == "call-1"
    assert frame["proposal"]["id"] == "p-1" and frame["proposal"]["summary"] == "Add orders."
    assert "result_spec" not in frame["proposal"]


async def test_a_proposal_for_a_dashboard_in_git_says_the_file_goes_to_the_repository():
    out = await propose_dashboard_changes(
        _ctx(FakeGateway(), can_write=True), operations=ADD, summary="x", dashboard="git-sales"
    )
    assert out.return_value["managed"] is True and "repository" in out.return_value["note"]


async def test_without_write_access_nothing_is_proposed():
    gw = FakeGateway()
    with pytest.raises(ModelRetry, match="writer role"):
        await propose_dashboard_changes(_ctx(gw, ON_SALES), operations=ADD, summary="x")
    assert gw.calls == []


async def test_a_proposal_the_server_refuses_is_for_the_model_to_fix():
    gw = FakeGateway(fail="The resulting file has problems:\n- rows[0]: too wide")
    with pytest.raises(ModelRetry, match="too wide"):
        await propose_dashboard_changes(
            _ctx(gw, ON_SALES, can_write=True), operations=ADD, summary="x"
        )
