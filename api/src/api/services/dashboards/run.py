"""Running a dashboard for one viewer, as a stream of events.

A view is planned while the request can still be refused: the viewer's parameter
values are resolved (``resolve.py``), each metric query is compiled against the
models the viewer can see, and widgets that ask the same question of the same
model share one statement, which is then fanned back out to each of them. Then
the view runs:

1. Every statement is submitted as the viewer through the path a worksheet's run
   takes (``query_submit``), with ``origin="dashboard"``, so the viewer's grants
   apply to each one. A cache hit is answered at once and never starts compute; a
   statement with no compute up parks until an agent takes it.
2. The misses bound for a connected agent go out together, in up to
   ``dashboard_max_inflight`` lanes per agent: a lane is one batch where the agent
   runs batches (``query.dispatch_batch``), or single queries one after another
   where it does not.
3. Each widget is sent as soon as its statements finish.

Closing the stream cancels every statement still queued or running.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import math
import time
import uuid
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.config import settings
from api.metrics import record_dashboard_run, record_dashboard_widget
from api.models.agent import Agent
from api.models.catalog import Catalog
from api.models.query import Query
from api.models.user import User
from api.models.workspace import Workspace
from api.schemas.query import QueryCreate, RowsPageOut
from api.services import delegation, query_submit
from api.services import query as query_service
from api.services.agent_capabilities import agent_supports_feature
from api.services.dashboards.chart_spec import MAX_SERIES, ChartSpec, chart_spec
from api.services.dashboards.resolve import (
    ParameterError,
    Resolved,
    ResolvedWidget,
    Window,
    resolve,
)
from api.services.dashboards.spec import (
    TABLE_LIMIT,
    DashboardSpec,
    DataWidget,
    DimensionParameter,
    TextWidget,
)
from api.services.grants import GrantDenied
from api.services.runtimes import BATCH_DISPATCH
from api.services.semantic import store as semantic_store
from api.services.semantic.compile import CompiledQuery, compile_metric_query, legal_dimensions
from api.services.semantic.errors import SemanticError
from api.services.semantic.model import LoadedModel, load_model
from api.services.sql_sessions.service import await_query_done

logger = logging.getLogger(__name__)

__all__ = ["ParameterError", "Plan", "UnknownWidget", "plan", "stream"]

#: How often an idle stream says it is still there, so proxies keep it open.
PING_S = 15.0
#: How long past its own timeout a widget's query is waited for: time for
#: stopped compute to start.
WAIT_MARGIN_S = 300.0
#: What a viewer is told about data they may not read. It names nothing.
NO_ACCESS = "You don't have access to the data this widget reads."

_TERMINAL = ("done", "failed", "cancelled")


class UnknownWidget(ValueError):
    """The request names a widget the dashboard does not have."""


# ── Planning ──────────────────────────────────────────────────────────────────


@dataclass(eq=False)
class _Statement:
    """One query the view sends, and the widgets it answers."""

    sql: str
    parameters: Mapping[str, object] | None
    limit: int
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    widgets: list[str] = field(default_factory=list)
    #: The agent it is to be sent to, once submitted and not yet sent.
    agent_id: uuid.UUID | None = None
    #: Copied from its run when it ends: a session's rollback expires the row.
    cache: str | None = None
    duration_ms: int | None = None
    row_count: int = 0
    page: RowsPageOut | None = None
    error: str | None = None
    no_access: bool = False
    finished: bool = False


@dataclass(eq=False)
class _Widget:
    widget: DataWidget
    resolved: ResolvedWidget
    chart: ChartSpec | None = None
    compiled: CompiledQuery | None = None
    current: _Statement | None = None
    previous: _Statement | None = None
    #: Known before anything runs: ``error`` or ``no_access``.
    state: str | None = None
    error: str | None = None
    sent: bool = False

    @property
    def id(self) -> str:
        return self.widget.id

    def statements(self) -> list[_Statement]:
        return [s for s in (self.current, self.previous) if s is not None]


@dataclass
class Plan:
    run_id: uuid.UUID
    workspace_id: uuid.UUID
    user_id: uuid.UUID
    delegation: delegation.Delegation | None
    dashboard: dict[str, object]
    today: date
    timezone: str
    resolved: Resolved
    widgets: list[_Widget]
    statements: list[_Statement]
    use_cache: bool
    client: tuple[str | None, str | None]


async def plan(
    db: AsyncSession,
    *,
    workspace: Workspace,
    catalogs: list[Catalog],
    user: User,
    spec: DashboardSpec,
    dashboard: Mapping[str, object],
    today: date,
    values: Mapping[str, object],
    widget_ids: list[str] | None = None,
    use_cache: bool = True,
    client: tuple[str | None, str | None] = (None, None),
) -> Plan:
    """Everything a view of ``spec`` will run, as ``user``. Nothing is run yet.

    Raises :class:`ParameterError` for a value that does not fit its parameter,
    and :class:`UnknownWidget` for an id in ``widget_ids`` the dashboard lacks."""
    data = [w for _, w in spec.widgets() if not isinstance(w, TextWidget)]
    if widget_ids is not None:
        missing = sorted(set(widget_ids) - {w.id for w in data})
        if missing:
            raise UnknownWidget(f"This dashboard has no data widget {missing[0]!r}.")
        data = [w for w in data if w.id in widget_ids]

    models, unpublished = await _models(db, spec, workspace, catalogs, user)

    def reachable(slug: str, metric: str) -> list[str]:
        try:
            return legal_dimensions(models[slug], metric) if slug in models else []
        except SemanticError:
            return []

    resolved = resolve(spec, values, today=today, reachable=reachable)
    widgets = [_Widget(widget=w, resolved=resolved.widgets[w.id]) for w in data]
    for w in widgets:
        _compile(w, models, unpublished, today)
    statements = _share(widgets, models, today)
    return Plan(
        run_id=uuid.uuid4(),
        workspace_id=workspace.id,
        user_id=user.id,
        delegation=delegation.bound(db),
        dashboard=dict(dashboard),
        today=today,
        timezone=workspace.timezone,
        resolved=resolved,
        widgets=widgets,
        statements=statements,
        use_cache=use_cache,
        client=client,
    )


async def _models(
    db: AsyncSession, spec: DashboardSpec, workspace: Workspace, catalogs: list[Catalog], user
) -> tuple[dict[str, LoadedModel], dict[str, str]]:
    """The published models the file reads that ``user`` can see, and why each
    other visible one cannot be read. A model the viewer cannot see is in neither."""
    slugs = {p.model for p in spec.parameters.values() if isinstance(p, DimensionParameter)}
    slugs |= {w.query.model for _, w in spec.widgets() if getattr(w, "query", None)}
    models: dict[str, LoadedModel] = {}
    unpublished: dict[str, str] = {}
    for slug in sorted(slugs):
        try:
            row = await semantic_store.get_model(
                db, workspace_id=workspace.id, slug=slug, user_id=user.id, catalogs=catalogs
            )
        except semantic_store.SemanticNotFound:
            continue
        if row.status != "published":
            unpublished[slug] = (
                f"Semantic model {slug!r} is {row.status}; dashboards read published models only."
            )
            continue
        models[slug] = await load_model(db, row)
    return models, unpublished


def _compile(
    w: _Widget, models: dict[str, LoadedModel], unpublished: dict[str, str], today: date
) -> None:
    query = w.widget.query
    if w.resolved.error:
        w.state, w.error = "error", w.resolved.error
    elif query is None:
        w.chart = chart_spec(w.widget)
    elif query.model in unpublished:
        w.state, w.error = "error", unpublished[query.model]
    elif query.model not in models:
        w.state, w.error = "no_access", NO_ACCESS
    else:
        model = models[query.model]
        w.chart = chart_spec(w.widget, model.metrics)
        try:
            w.compiled = compile_metric_query(model, w.resolved.query, today=today)
        except SemanticError as exc:
            w.state, w.error = "error", str(exc)


def _share(widgets: list[_Widget], models: dict[str, LoadedModel], today: date) -> list[_Statement]:
    """The statements ``widgets`` need, each once.

    Query widgets that differ only in their metrics, on one model's dataset, are
    asked as one query with all of their metrics. Identical SQL is sent once."""
    by_sql: dict[str, _Statement] = {}

    def statement(sql: str, parameters, limit: int, owner: _Widget) -> _Statement:
        key = json.dumps([sql, parameters], default=_jsonable, sort_keys=True)
        found = by_sql.get(key)
        if found is None:
            found = by_sql[key] = _Statement(sql=sql, parameters=parameters, limit=limit)
        found.limit = max(found.limit, limit)
        found.widgets.append(owner.id)
        return found

    groups: dict[object, list[_Widget]] = {}
    for w in widgets:
        if w.state is None and w.compiled is not None:
            model = models[w.widget.query.model]
            dataset = model.metrics[w.resolved.query.metrics[0]].dataset
            question = replace(w.resolved.query, metrics=())
            groups.setdefault((model.slug, dataset, question), []).append(w)

    for (slug, _, question), group in groups.items():
        compiled = group[0].compiled
        if len(group) > 1:
            metrics = tuple(dict.fromkeys(m for w in group for m in w.resolved.query.metrics))
            try:
                compiled = compile_metric_query(
                    models[slug], replace(question, metrics=metrics), today=today
                )
            except SemanticError:
                compiled = None  # each asks on its own, as compiled
        for w in group:
            asked = compiled or w.compiled
            limit = _limit(w)
            w.current = statement(asked.sql, None, limit, w)
            if asked.compare is not None:
                w.previous = statement(asked.compare["sql"], None, limit, w)

    for w in widgets:
        if w.state is None and w.widget.sql is not None:
            w.current = statement(w.resolved.sql, dict(w.resolved.sql_params or {}), _limit(w), w)
    return list(by_sql.values())


def _limit(w: _Widget) -> int:
    rows = w.chart.limits.rows if w.chart is not None else None
    return min(rows or TABLE_LIMIT, TABLE_LIMIT)


def _jsonable(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    raise TypeError(f"{type(value).__name__} is not JSON")


# ── Running ───────────────────────────────────────────────────────────────────


async def stream(
    run: Plan, factory: async_sessionmaker[AsyncSession], polaris: Any
) -> AsyncIterator[str]:
    """The view as server-sent events. Closing it cancels what is still running."""
    events: asyncio.Queue[dict | None] = asyncio.Queue()
    task = asyncio.create_task(_Runner(run, factory, polaris, events.put_nowait).run())
    try:
        while True:
            try:
                event = await asyncio.wait_for(events.get(), PING_S)
            except TimeoutError:
                yield ": ping\n\n"
                continue
            if event is None:
                break
            yield f"data: {json.dumps(event, default=_jsonable)}\n\n"
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


class _Runner:
    def __init__(
        self,
        run: Plan,
        factory: async_sessionmaker[AsyncSession],
        polaris: Any,
        emit: Callable[[dict | None], None],
    ) -> None:
        self.plan = run
        self.factory = factory
        self.polaris = polaris
        self.emit = emit
        self.started = time.monotonic()
        self.batches = 0
        self.parked: list[_Statement] = []

    @contextlib.asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """A session of the run's own, holding the viewer's delegation if any."""
        async with self.factory() as db:
            delegation.bind(db, self.plan.delegation)
            yield db

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self.started) * 1000)

    async def run(self) -> None:
        outcome = "failed"
        try:
            self.emit(_start(self.plan))
            for w in self.plan.widgets:
                if w.state is not None:
                    self._send(w)
            await self._execute()
            outcome = "done"
            self.emit(self._done())
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except Exception:
            logger.exception("Dashboard run %s failed", self.plan.run_id)
            self.emit({"type": "error", "message": "The dashboard could not be run."})
        finally:
            await asyncio.shield(self._cancel_unfinished())
            record_dashboard_run(outcome, time.monotonic() - self.started)
            self.emit(None)

    async def _execute(self) -> None:
        statements = self.plan.statements
        limit = asyncio.Semaphore(max(1, settings.dashboard_max_inflight))

        async def submit(statement: _Statement) -> None:
            async with limit:
                await self._submit(statement)

        async with asyncio.TaskGroup() as group:
            for statement in statements:
                group.create_task(submit(statement))

        if self.parked:
            waiting = sorted({w for s in self.parked for w in s.widgets})
            self.emit({"type": "compute", "state": "starting", "widgets": waiting})

        sending: dict[uuid.UUID, list[_Statement]] = {}
        for statement in statements:
            if statement.agent_id is not None:
                sending.setdefault(statement.agent_id, []).append(statement)
        async with asyncio.TaskGroup() as group:
            for agent_id, waiting in sending.items():
                group.create_task(self._send_to(agent_id, waiting))
            for statement in self.parked:
                group.create_task(self._await_parked(statement))

    async def _submit(self, statement: _Statement) -> None:
        widgets = ",".join(statement.widgets)
        tags = {
            "dashboard": str(self.plan.dashboard["name"]),
            "widget": widgets[:256],
            "dashboard.run": str(self.plan.run_id),
        }
        if self.plan.dashboard.get("preview"):
            tags["dashboard.preview"] = str(self.plan.dashboard["preview"])
        body = QueryCreate(
            id=statement.id,
            sql=statement.sql,
            parameters=statement.parameters or None,
            use_cache=self.plan.use_cache,
            tags=tags,
        )
        async with self.session() as db:
            workspace = await db.get(Workspace, self.plan.workspace_id)
            user = await db.get(User, self.plan.user_id)
            try:
                query = await query_submit.submit_query(
                    db,
                    workspace,
                    body,
                    user,
                    self.polaris,
                    origin="dashboard",
                    client=self.plan.client,
                    dispatch=False,
                )
            except HTTPException as exc:
                self._fail(statement, _refusal(exc))
                return
            if query.status == "done":
                await self._read(db, statement, None)
            elif query.agent_id is None:
                self.parked.append(statement)
            else:
                statement.agent_id = query.agent_id

    async def _send_to(self, agent_id: uuid.UUID, statements: list[_Statement]) -> None:
        async with self.session() as db:
            agent = await db.get(Agent, agent_id)
            batched = agent is not None and agent_supports_feature(
                agent.capabilities, BATCH_DISPATCH
            )
        lanes = max(1, settings.dashboard_max_inflight)
        if batched:
            most = min(settings.dashboard_batch_max_queries, query_service.BATCH_MAX_QUERIES)
            count = max(min(lanes, len(statements)), math.ceil(len(statements) / max(1, most)))
            groups = _deal(statements, count)
        else:
            groups = [[s] for s in statements]
        async with asyncio.TaskGroup() as group:
            for lane in _deal(groups, min(lanes, len(groups))):
                group.create_task(self._lane(lane, batched))

    async def _lane(self, groups: list[list[_Statement]], batched: bool) -> None:
        """Send each group once the one before it has finished: a group is one
        batch where the agent runs batches, otherwise one query."""
        async with self.session() as db:
            for group in groups:
                queries = [await db.get(Query, s.id) for s in group]
                # Read now: each wait rolls the session back, which expires the rows.
                waits = [_wait_s(q) for q in queries]
                with contextlib.ExitStack() as stack:
                    waiters = [
                        stack.enter_context(query_service.completion_waiter(q.id)) for q in queries
                    ]
                    outcomes = await query_service.dispatch_batch(db, queries)
                    if batched:
                        self.batches += 1
                    for statement, query, outcome, waiter, wait in zip(
                        group, queries, outcomes, waiters, waits, strict=True
                    ):
                        if outcome is not None:
                            await self._refuse(db, statement, query, outcome)
                            continue
                        await await_query_done(db, query, wait, done=waiter)
                        await self._read(db, statement, waiter.result_parquet)

    async def _await_parked(self, statement: _Statement) -> None:
        async with self.session() as db:
            query = await db.get(Query, statement.id)
            await await_query_done(db, query, _wait_s(query))
            await self._read(db, statement, None)

    async def _refuse(
        self, db: AsyncSession, statement: _Statement, query: Query, refusal: ValueError
    ) -> None:
        """Record a run its dispatch refused, as the REST path records one."""
        query.status, query.error = "failed", str(refusal)
        query.finished_at = datetime.now(UTC)
        await db.commit()
        self._fail(statement, None if isinstance(refusal, GrantDenied) else str(refusal))

    async def _read(self, db: AsyncSession, statement: _Statement, inline: bytes | None) -> None:
        query = await db.get(Query, statement.id)
        statement.cache, statement.duration_ms = query.cache_status, query.duration_ms
        statement.row_count = query.row_count or 0
        if query.status == "done":
            try:
                if inline is not None:
                    statement.page = query_service.page_from_parquet(
                        query, inline, limit=statement.limit
                    )
                else:
                    statement.page = await query_service.fetch_result_page(
                        db, query, limit=statement.limit
                    )
            except HTTPException as exc:
                self._fail(statement, _refusal(exc))
                return
            self._finish(statement)
        elif query.status == "failed":
            self._fail(statement, query.error or "The query failed.")
        elif query.status == "cancelled":
            self._fail(statement, "The query was cancelled.")
        else:
            await query_service.cancel_query(db, query)
            self._fail(statement, "The query did not finish in time.")

    def _fail(self, statement: _Statement, message: str | None) -> None:
        """``message`` None: the viewer may not read what it reads."""
        statement.error = message or NO_ACCESS
        statement.no_access = message is None
        self._finish(statement)

    def _finish(self, statement: _Statement) -> None:
        statement.finished = True
        for w in self.plan.widgets:
            if not w.sent and statement in w.statements():
                if all(s.finished for s in w.statements()):
                    self._send(w)

    def _send(self, w: _Widget) -> None:
        w.sent = True
        event = _widget_event(w, self.plan)
        event["timing"] = {"elapsed_ms": self.elapsed_ms()}
        if w.current is not None and w.current.duration_ms is not None:
            event["timing"]["query_ms"] = w.current.duration_ms
        record_dashboard_widget(event["state"], event.get("cache"))
        self.emit(event)

    def _done(self) -> dict:
        states: dict[str, int] = {}
        for w in self.plan.widgets:
            state = w.state or _state_of(w)
            states[state] = states.get(state, 0) + 1
        hits = sum(1 for s in self.plan.statements if s.cache == "hit")
        return {
            "type": "done",
            "elapsed_ms": self.elapsed_ms(),
            "widgets": states,
            "statements": len(self.plan.statements),
            "cache_hits": hits,
            "batch": {"used": self.batches > 0, "batches": self.batches},
        }

    async def _cancel_unfinished(self) -> None:
        unfinished = [s for s in self.plan.statements if not s.finished]
        if not unfinished:
            return
        try:
            async with self.session() as db:
                for statement in unfinished:
                    query = await db.get(Query, statement.id)
                    if query is not None and query.status not in _TERMINAL:
                        await query_service.cancel_query(db, query)
        except Exception:
            logger.exception("Could not cancel the queries of dashboard run %s", self.plan.run_id)


def _deal(items: list, count: int) -> list[list]:
    """``items`` dealt in turn into ``count`` parts, so the top of the page comes
    first in every part."""
    return [items[i::count] for i in range(count)]


def _wait_s(query: Query) -> float:
    return (query.timeout_s or QueryCreate.model_fields["timeout_s"].default) + WAIT_MARGIN_S


def _refusal(exc: HTTPException) -> str | None:
    """What to tell the viewer about a refused run; None when it is a grant."""
    detail = exc.detail
    if isinstance(detail, dict):
        if detail.get("error") == "grant_denied":
            return None
        return str(detail.get("detail") or detail.get("error"))
    return str(detail)


# ── Events ────────────────────────────────────────────────────────────────────


def _window(window: Window | None) -> dict | None:
    if window is None:
        return None
    return {"start": window.start, "end": window.end, "text": window.text}


def _start(run: Plan) -> dict:
    return {
        "type": "start",
        "run_id": run.run_id,
        "dashboard": run.dashboard,
        "today": run.today,
        "timezone": run.timezone,
        "parameters": [
            {
                "name": p.name,
                "type": p.type,
                "value": p.value,
                "window": _window(p.window),
            }
            for p in run.resolved.parameters.values()
        ],
        "widgets": [w.id for w in run.widgets],
    }


def _state_of(w: _Widget) -> str:
    current = w.current
    if current is None or current.no_access:
        return "no_access"
    if current.error:
        return "error"
    return "ok" if current.page is not None and current.page.rows else "empty"


def _widget_event(w: _Widget, run: Plan) -> dict:
    resolved = w.resolved
    event: dict[str, Any] = {
        "type": "widget",
        "id": w.id,
        "state": w.state or _state_of(w),
        "scope": {
            "window": _window(resolved.window),
            "compare_window": _window(resolved.compare),
            "applied": list(resolved.applied),
            "skipped": [{"parameter": s.parameter, "reason": s.reason} for s in resolved.skipped],
        },
    }
    if event["state"] in ("error", "no_access"):
        event["error"] = w.error or (w.current.error if w.current else None)
        return event

    current = w.current
    page = current.page
    columns = _columns(w, page)
    names = [c["name"] for c in columns]
    chart = w.chart.model_copy(deep=True)
    warnings = list(w.compiled.warnings) if w.compiled else []
    data = [[row.get(name) for name in names] for row in page.rows]
    if chart.series is not None:
        chart.series.keys, dropped = _series(chart, names, data)
        chart.colors = _colors(chart.series.field, chart.series.keys)
        if dropped:
            warnings.append(
                f"Showing the {MAX_SERIES} largest of {MAX_SERIES + dropped} series; "
                "the table view has them all."
            )
    event.update(
        rows={
            "columns": columns,
            "data": data,
            "truncated": current.row_count > len(page.rows),
            "query_id": current.id,
        },
        chart=chart.model_dump(mode="json"),
        cache=current.cache,
        sql=current.sql,
        warnings=warnings,
    )
    if w.compiled is not None:
        event["definitions"] = w.compiled.definitions_used
    if w.previous is not None:
        if w.previous.page is None:
            warnings.append(f"The comparison could not be read: {w.previous.error}")
        else:
            event["compare"] = _compare(w, data, names, w.previous.page)
    return event


def _columns(w: _Widget, page: RowsPageOut) -> list[dict]:
    """The result columns the widget reads, with what each is for and its format."""
    types = {c.name: c.type for c in page.column_schema or []}
    measures = {m.field: m for m in w.chart.y}
    roles: dict[str, str] = {}
    query = w.widget.query
    if query is not None:
        if query.grain:
            roles[query.grain] = "time"
        for dimension in query.dimensions:
            roles[dimension.rsplit(".", 1)[-1]] = "dimension"
        for metric in query.metrics:
            roles[metric] = "measure"
        names = [c for c in page.columns if c in roles]
    else:
        names = list(page.columns)
        if w.chart.x is not None:
            roles[w.chart.x.field] = "time" if w.chart.x.kind == "time" else "dimension"
        if w.chart.series is not None:
            roles[w.chart.series.field] = "dimension"
        for measure in w.chart.y:
            roles[measure.field] = "measure"
    out = []
    for name in names:
        column: dict[str, Any] = {"name": name, "type": types.get(name), "role": roles.get(name)}
        measure = measures.get(name)
        if measure is not None:
            column.update(
                label=measure.label,
                format=measure.format,
                unit=measure.unit,
                currency=measure.currency,
            )
        out.append(column)
    return out


def _number(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _series(chart: ChartSpec, names: list[str], data: list[list]) -> tuple[list[str], int]:
    """The series drawn, largest first when there are too many, and how many were left out."""
    at = names.index(chart.series.field) if chart.series.field in names else None
    if at is None:
        return [], 0
    measure = names.index(chart.y[0].field) if chart.y and chart.y[0].field in names else None
    totals: dict[str, Decimal] = {}
    for row in data:
        key = "" if row[at] is None else str(row[at])
        value = _number(row[measure]) if measure is not None else None
        totals[key] = totals.get(key, Decimal(0)) + (value or 0)
    keys = list(totals)
    if len(keys) <= MAX_SERIES:
        return keys, 0
    largest = sorted(keys, key=lambda k: totals[k], reverse=True)[:MAX_SERIES]
    return [k for k in keys if k in largest], len(keys) - MAX_SERIES


def _colors(field_name: str, keys: list[str]) -> dict[str, int]:
    """A colour slot per series key, chosen by the key itself rather than its rank,
    so a filter that removes some series leaves the others' colours alone. Two
    keys whose slots collide take the next free one, in key order."""
    taken: set[int] = set()
    slots: dict[str, int] = {}
    for key in sorted(keys):
        digest = hashlib.sha256(f"{field_name}\0{key}".encode()).digest()
        slot = int.from_bytes(digest[:4], "big") % MAX_SERIES
        while slot in taken:
            slot = (slot + 1) % MAX_SERIES
        taken.add(slot)
        slots[key] = slot
    return slots


def _compare(w: _Widget, data: list[list], names: list[str], previous: RowsPageOut) -> dict:
    measure = w.chart.y[0]
    at = names.index(measure.field)
    current = data[0][at] if data else None
    before = previous.rows[0].get(measure.field) if previous.rows else None
    now, then = _number(current), _number(before)
    delta = delta_pct = good = None
    if now is not None and then is not None:
        exact = now - then
        if isinstance(current, str) or isinstance(before, str):
            delta = format(exact, "f")
        elif isinstance(current, int) and isinstance(before, int):
            delta = int(exact)
        else:
            delta = float(exact)
        if then != 0:
            delta_pct = round(float(exact / abs(then)), 6)
        if exact != 0 and measure.polarity in ("up_good", "down_good"):
            good = (exact > 0) == (measure.polarity == "up_good")
    return {
        "current": current,
        "previous": before,
        "delta": delta,
        "delta_pct": delta_pct,
        "good": good,
        "window_text": w.resolved.compare.text if w.resolved.compare else None,
    }
