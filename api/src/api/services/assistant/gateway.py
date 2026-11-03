"""Thin, authenticated loopback to DuckHaven's own REST API.

Every metadata/SQL action the assistant takes is an HTTP call to the same routes
the SPA calls, authenticated as the assistant's service account. This is the
load-bearing property of the design: enforcement (``assert_workspace_member`` →
``sql_guard`` → ``assert_query_access``) stays server-side in the existing
chokepoints, so a harness bug or a prompt-injected tool call can never exceed the
service account's grants.

Shared with :mod:`api.services.mcp`, which is the same design pointed at a
different principal: there the loopback carries the MCP caller's own access token
rather than the assistant's, so the grants it cannot exceed are theirs.

Router-body checks (membership, the SQL allowlist) live *above* the service layer,
so a direct service call would skip them — hence the loopback rather than a direct
``dispatch_query`` call.
"""

from __future__ import annotations

import asyncio
import contextlib
import json

import httpx

from api.config import settings

# Query statuses that mean the run has stopped (mirrors the scheduler's terminal set).
_TERMINAL = {"done", "failed", "cancelled"}
_POLL_INTERVAL_S = 0.25
# A query that sat queued at least this long gets its wait reported to the model.
_WAITED_NOTE_AFTER_S = 10.0


def _aggregated_columns(sql: str) -> dict[tuple[str | None, str, str], set[str]]:
    """Which columns this statement aggregates, grouped by the table they come from.

    Keyed by ``(catalog, schema, table)``; the catalog is ``None`` when the query
    left it implicit.

    Used only to notice that hand-written SQL is recomputing something the
    semantic layer already defines authoritatively. Best-effort by design: an
    unqualified column in a multi-table query is skipped rather than guessed at,
    because a nudge pointing at the wrong metric is worse than no nudge.
    """
    import sqlglot
    from sqlglot import exp

    try:
        statements = sqlglot.parse(sql, read="duckdb")
    except Exception:  # sqlglot.errors.ParseError et al
        return {}

    found: dict[tuple[str | None, str, str], set[str]] = {}
    for statement in statements:
        if statement is None:
            continue
        tables = {
            t.alias_or_name.lower(): (t.catalog or None, t.db, t.name)
            for t in statement.find_all(exp.Table)
            if t.name
        }
        if not tables:
            continue
        sole = next(iter(tables.values())) if len(tables) == 1 else None

        for agg in statement.find_all(exp.AggFunc):
            for column in agg.find_all(exp.Column):
                if column.table:
                    origin = tables.get(column.table.lower())
                elif sole is not None:
                    origin = sole
                else:
                    # Ambiguous without resolving the whole query. Skip it.
                    continue
                # No schema means the reverse index cannot be addressed, since it
                # is keyed by (catalog, schema, table).
                if origin is None or not origin[1]:
                    continue
                found.setdefault(origin, set()).add(column.name.lower())
    return found


def metric_tags(model: str, metrics: list[str]) -> dict[str, str]:
    """Tags naming the semantic model and metrics a compiled query answers.

    Cut to the server's 256-character value limit: a long metric list must not
    turn a good query into a 422.
    """
    return {"semantic.model": model[:256], "semantic.metrics": ",".join(metrics)[:256]}


class GatewayError(Exception):
    """A governed REST call failed. The message is safe to surface to the model.

    ``kind`` is how the audit trail records the failure: ``denied`` when the platform
    refused on policy (access, the SQL guard), ``error`` for anything else, such as a
    query that failed, a timeout, or no compute to run on. They answer different
    questions, "what was this assistant stopped from doing?" and "what went wrong?",
    so a compute outage must not read as a denial.
    """

    def __init__(self, message: str = "", *, kind: str = "error") -> None:
        super().__init__(message)
        self.kind = kind


def _error_parts(resp: httpx.Response) -> tuple[str | None, str]:
    """The ``error`` code and the human message of an error response.

    The API answers in one envelope (``error``, ``message``, ``details``), but a
    FastAPI-native ``{"detail": ...}`` and the older ``{"error", "detail"}`` pair both
    still turn up, so all three are read. Without the envelope case the model would
    be handed the bare code in place of the sentence that explains it.
    """
    try:
        body = resp.json()
    except ValueError:
        return None, resp.text
    if not isinstance(body, dict):
        return None, str(body)
    nested = body.get("detail")
    if isinstance(nested, dict):  # {"detail": {"error": ..., "detail": ...}}
        body = nested
    code = body.get("error") if isinstance(body.get("error"), str) else None
    message = body.get("message") or body.get("detail") or code or str(body)
    return code, str(message)


# Error codes that mean the platform refused the request on policy. Anything else the
# API answers is some other kind of failure.
_POLICY_REFUSALS = frozenset(
    {
        "sql_not_allowed",
        "statement_not_allowed",
        "grant_denied",
        "agent_forbidden",
        "catalog_read_only",
    }
)


def _translate(exc: httpx.HTTPStatusError) -> GatewayError:
    """Map a governed error response to a model-friendly message, and classify it.

    A 404 counts as a refusal because the API answers it for objects the caller may
    not see, so "missing" and "hidden" cannot be told apart from here.
    """
    error, _ = _error_parts(exc.response)
    translated = _describe(exc)
    if exc.response.status_code in (403, 404) or error in _POLICY_REFUSALS:
        translated.kind = "denied"
    return translated


def _describe(exc: httpx.HTTPStatusError) -> GatewayError:
    """The concise sentence the model is shown for a governed error response."""
    resp = exc.response
    error, detail = _error_parts(resp)
    code = resp.status_code
    if code == 403:
        return GatewayError(f"Access denied: {detail}")
    if code == 404:
        return GatewayError(f"Not found (or not accessible): {detail}")
    if error == "agent_required":
        return GatewayError("No compute agent is currently available to run SQL.")
    if error == "no_compute_available":
        return GatewayError(
            "No compute is running for this workspace and I cannot start any. Tell the user "
            "that a workspace owner can set a default compute in Settings, or that compute "
            "needs starting."
        )
    if error == "default_compute_unavailable":
        return GatewayError(
            f"{detail}. I cannot choose a different engine; tell the user which compute is "
            "unavailable and why."
        )
    if error == "compute_queue_full":
        return GatewayError(
            "Compute is saturated and not accepting more waiting queries. Ask the user to "
            "try again shortly."
        )
    if code == 422:
        return GatewayError(f"Not allowed: {detail}")
    if code == 409:
        return GatewayError(f"Conflict: {detail}")
    if code == 503:
        return GatewayError(f"Service unavailable: {detail}")
    return GatewayError(f"Request failed ({code}): {detail}")


class Gateway:
    """Per-turn wrapper over the loopback ``httpx`` client, scoped to a workspace."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        workspace_slug: str,
        *,
        row_cap: int,
        byte_cap: int,
        service_account_id: str,
        tags: dict[str, str] | None = None,
        queue_wait_s: float | None = None,
    ) -> None:
        self._client = client
        self._ws = workspace_slug
        self._row_cap = row_cap
        self._byte_cap = byte_cap
        # The id the assistant's queries are attributed to; used to refuse paging
        # results of queries run by *other* principals.
        self._service_account_id = service_account_id
        # Sent with every query this gateway runs, so History can tell whose
        # conversation or which MCP client a run came from.
        self._tags = tags or {}
        # How long a query may wait for compute to start before it runs. Added to the
        # execution timeout rather than carved out of it: the agent starts that clock
        # only once the run is bound, so a cold start must not eat the query's budget.
        self._queue_wait_s = (
            queue_wait_s
            if queue_wait_s is not None
            else (settings.compute_queue_wait_s if settings.elastic_compute_enabled else 0.0)
        )

    async def _get(self, path: str, **kwargs) -> httpx.Response:
        resp = await self._client.get(path, **kwargs)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise _translate(exc) from exc
        return resp

    # ── Catalog browse ────────────────────────────────────────────────────────
    async def list_workspaces(self) -> list[dict]:
        """The workspaces this principal is a member of.

        Not workspace-scoped, unlike everything else here: an MCP client has no
        ambient workspace the way a worksheet does, so its agent has to be able to
        discover which ones its token actually reaches.
        """
        resp = await self._get("/workspaces")
        return [
            {
                "workspace": w["slug"],
                "name": w.get("name"),
                "description": w.get("description"),
                "default_catalog": w.get("default_catalog"),
            }
            for w in resp.json()
        ]

    async def list_catalogs(self) -> list[dict]:
        resp = await self._get(f"/workspaces/{self._ws}/catalogs")
        return [{"slug": c["slug"], "name": c.get("name")} for c in resp.json()]

    async def storage_kinds(self) -> tuple[str, ...]:
        """Distinct storage-backend kinds behind this workspace's catalogs.

        Its own call rather than a wider ``list_catalogs``: that one is a tool
        result the model reads on most turns, and the backend kind is prompt
        context, not something worth spending tokens on every time it browses.
        """
        resp = await self._get(f"/workspaces/{self._ws}/catalogs")
        kinds = {c.get("storage_backend_kind") for c in resp.json()}
        return tuple(sorted(k for k in kinds if k))

    async def catalog_kinds(self) -> tuple[str, ...]:
        """Distinct catalog kinds attached to this workspace, for prompt context."""
        resp = await self._get(f"/workspaces/{self._ws}/catalogs")
        kinds = {c.get("kind") for c in resp.json()}
        return tuple(sorted(k for k in kinds if k))

    async def workspace_compute(self) -> dict:
        """The compute this workspace runs on, for prompt context.

        ``{"name": <agent name or None>, "elastic": <can it be started on demand>}``.
        Read from the workspace itself, not from ``GET /agents``: that list is
        filtered by the service account's own agent grants and says nothing about
        which agent a query will actually be sent to.
        """
        resp = await self._get(f"/workspaces/{self._ws}")
        agent = resp.json().get("default_agent")
        if not agent:
            return {"name": None, "elastic": False}
        return {"name": agent["name"], "elastic": agent.get("provider") is not None}

    async def list_schemas(self, catalog: str) -> list[str]:
        resp = await self._get(f"/workspaces/{self._ws}/catalogs/{catalog}/schemas")
        return [s["name"] for s in resp.json()]

    async def list_tables(self, catalog: str, schema: str) -> list[str]:
        resp = await self._get(f"/workspaces/{self._ws}/catalogs/{catalog}/schemas/{schema}/tables")
        return [t["name"] for t in resp.json()]

    async def describe_table(self, catalog: str, schema: str, table: str) -> dict:
        resp = await self._get(
            f"/workspaces/{self._ws}/catalogs/{catalog}/schemas/{schema}/tables/{table}"
        )
        t = resp.json()
        return {
            "name": t.get("name"),
            "row_count": t.get("row_count"),
            "size_bytes": t.get("size_bytes"),
            "columns": [
                {"name": c["name"], "type": c["type"], "nullable": c.get("nullable")}
                for c in t.get("columns", [])
            ],
        }

    async def table_lineage(
        self,
        catalog: str,
        schema: str,
        table: str,
        *,
        direction: str = "both",
        depth: int = 2,
        columns_for: list[str] | None = None,
    ) -> dict:
        """The bounded lineage graph around a table, trimmed for an agent.

        Passed through rather than reshaped in two respects that matter. A
        ``redacted`` node keeps its key — a hash, not a name — so the graph keeps
        its shape and an agent can see that *something* it may not read sits
        there. And all three truncation flags survive, because a partial graph
        that looks complete is the one failure this endpoint's own design goes
        out of its way to prevent.

        What is dropped is the per-provider freshness block: each edge carries a
        name, two timestamps, an observation count and two more flags per
        producer, which is most of the payload and answers a question ("should I
        trust this claim?") that belongs to a person looking at the graph rather
        than to an agent traversing it. The provider *names* stay, since which
        producer asserted an edge is the part that changes what an agent says.
        """
        params: dict[str, object] = {"direction": direction, "depth": depth}
        if columns_for:
            params["columns_for"] = columns_for
        resp = await self._get(
            f"/workspaces/{self._ws}/catalogs/{catalog}/schemas/{schema}/tables/{table}/lineage",
            params=params,
        )
        graph = resp.json()
        return {
            "root": graph.get("root"),
            "nodes": [
                {
                    key: value
                    for key, value in (
                        ("key", n["key"]),
                        ("kind", n["kind"]),
                        ("catalog", n.get("catalog")),
                        ("schema", n.get("schema_name")),
                        ("table", n.get("table")),
                        ("system", n.get("system")),
                        ("distance", n.get("distance")),
                        ("column_count", n.get("column_count")),
                    )
                    # A redacted node carries no names; omitting the empty keys
                    # says that more plainly than a row of nulls.
                    if value is not None
                }
                for n in graph.get("nodes", [])
            ],
            "edges": [
                {
                    "source_key": e["source_key"],
                    "target_key": e["target_key"],
                    "operation": e.get("operation"),
                    "confidence": e.get("confidence"),
                    "stale": e.get("stale", False),
                    "providers": [p["name"] for p in e.get("providers", [])],
                    "column_lineage": e.get("column_lineage", "unknown"),
                    "columns": [
                        {"source_column": c["source_column"], "target_column": c["target_column"]}
                        for c in e.get("columns", [])
                    ],
                }
                for e in graph.get("edges", [])
            ],
            "truncated": graph.get("truncated", False),
            "hidden": graph.get("hidden", False),
            "columns_truncated": graph.get("columns_truncated", False),
        }

    # ── Semantic layer ────────────────────────────────────────────────────────
    async def search_semantic(self, query: str, *, limit: int = 10) -> dict:
        resp = await self._get(
            f"/workspaces/{self._ws}/semantic/search", params={"q": query, "limit": limit}
        )
        return resp.json()

    async def list_semantic_models(self) -> list[dict]:
        """Published models only, and only the summary — this runs every turn."""
        resp = await self._get(
            f"/workspaces/{self._ws}/semantic/models", params={"status": "published"}
        )
        return [
            {
                "model": m["slug"],
                "name": m["name"],
                "description": m.get("description"),
                "metrics": m.get("metric_count", 0),
            }
            for m in resp.json()
        ]

    async def get_semantic_model(self, model: str) -> dict:
        """One model, trimmed to what is useful for choosing and querying.

        Trimmed rather than passed through because the raw record carries ids,
        timestamps and validation detail that cost context and answer no question
        the assistant is asking.
        """
        resp = await self._get(f"/workspaces/{self._ws}/semantic/models/{model}")
        body = resp.json()
        payload = {
            "model": body["slug"],
            "name": body["name"],
            "description": body.get("description"),
            "datasets": [
                {
                    "name": d["name"],
                    "description": d.get("description"),
                    "table": f"{d.get('catalog')}.{d['schema_name']}.{d['table_name']}",
                }
                for d in body.get("datasets", [])
                if d.get("validation_state") != "broken"
            ],
            "dimensions": [
                {
                    "name": d["name"],
                    "dataset": d.get("dataset"),
                    "kind": d["kind"],
                    "description": d.get("description"),
                    "synonyms": d.get("synonyms") or [],
                    "grains": d.get("time_grains") or [],
                    "sample_values": (d.get("sample_values") or [])[:5],
                }
                for d in body.get("dimensions", [])
                if d.get("validation_state") != "broken"
            ],
            "metrics": [
                {
                    "name": m["name"],
                    "dataset": m.get("dataset"),
                    "description": m.get("description"),
                    "synonyms": m.get("synonyms") or [],
                    "calculation": m.get("expression"),
                    "measured_on": m.get("time_dimension"),
                    "caveat": m.get("caveat"),
                }
                for m in body.get("metrics", [])
                if m.get("status") == "published" and m.get("validation_state") != "broken"
            ],
            "joins": [
                f"{r['left_dataset']} -> {r['right_dataset']} ({r['cardinality']})"
                for r in body.get("relationships", [])
                if r.get("validation_state") != "broken"
            ],
        }
        return self._cap_payload(payload)

    def _cap_payload(self, payload: dict) -> dict:
        """Trim the longest collection until the whole thing fits the byte cap.

        A model too big for the context window degrades to a truncated one with a
        flag saying so, rather than silently blowing the turn's budget.
        """
        for _ in range(20):
            if len(json.dumps(payload, default=str)) <= self._byte_cap:
                return payload
            longest = max(
                ("dimensions", "metrics", "datasets"),
                key=lambda key: len(payload.get(key, [])),
            )
            if not payload.get(longest):
                break
            payload[longest] = payload[longest][:-1]
            payload["truncated"] = (
                "This model is too large to show in full; some definitions are omitted. "
                "Use search_semantic to find a specific one."
            )
        return payload

    async def compile_metric_query(self, body: dict) -> dict:
        resp = await self._client.post(f"/workspaces/{self._ws}/semantic/compile", json=body)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise _translate(exc) from exc
        return resp.json()

    async def metric_definition(self, model: str, metric: str) -> dict:
        """One metric's full definition, for explaining rather than querying."""
        body = (await self._get(f"/workspaces/{self._ws}/semantic/models/{model}")).json()
        for m in body.get("metrics", []):
            if m["name"] == metric:
                dataset = next(
                    (d for d in body.get("datasets", []) if d["name"] == m.get("dataset")), None
                )
                return {
                    "model": body["slug"],
                    "metric": m["name"],
                    "label": m.get("display_name") or m["name"],
                    "description": m.get("description"),
                    "calculation": m.get("expression"),
                    "measured_on": m.get("time_dimension"),
                    "caveat": m.get("caveat"),
                    "status": m.get("status"),
                    "synonyms": m.get("synonyms") or [],
                    "table": (
                        f"{dataset.get('catalog')}.{dataset['schema_name']}.{dataset['table_name']}"
                        if dataset
                        else None
                    ),
                    "validation_state": m.get("validation_state"),
                    "validation_detail": m.get("validation_detail"),
                }
        known = ", ".join(sorted(m["name"] for m in body.get("metrics", [])))
        raise GatewayError(f"{model!r} has no metric called {metric!r}. Available: {known}.")

    async def semantic_conflicts(self, sql: str) -> list[dict]:
        """Published metrics that already define what this SQL is aggregating.

        Best-effort and never fatal: this is a nudge attached to a result, not a
        gate. If anything about it fails the query still ran and the answer still
        stands, so a failure here must not surface as a query failure.
        """
        try:
            refs = _aggregated_columns(sql)
            if not refs:
                return []
            found: list[dict] = []
            for (catalog, schema, table), columns in refs.items():
                # The canonical path when the query named a catalog; the legacy
                # shim, which resolves the workspace default, when it did not.
                path = (
                    f"/workspaces/{self._ws}/catalogs/{catalog}/schemas/{schema}"
                    f"/tables/{table}/semantic"
                    if catalog
                    else f"/workspaces/{self._ws}/schemas/{schema}/tables/{table}/semantic"
                )
                resp = await self._client.get(path)
                if resp.status_code != 200:
                    continue
                for dep in resp.json().get("dependents", []):
                    if dep.get("kind") != "metric" or dep.get("model_status") != "published":
                        continue
                    overlap = columns & {c.lower() for c in dep.get("columns", [])}
                    if overlap:
                        found.append(
                            {
                                "metric": dep["name"],
                                "model": dep["model"],
                                "columns": sorted(overlap),
                            }
                        )
            return found
        except Exception:  # noqa: BLE001 — advisory only; never fail a good answer
            return []

    # ── SQL execution ─────────────────────────────────────────────────────────
    async def run_sql(
        self,
        sql: str,
        *,
        catalog: str | None,
        timeout_s: float,
        tags: dict[str, str] | None = None,
    ) -> dict:
        """Submit a query, wait for it to finish, and return a capped result sample.

        Returns a dict with ``query_id``, ``status``, ``columns``, ``column_types``,
        capped ``rows``, ``total`` row count, and a ``truncated`` flag. ``tags`` are
        added to the gateway's own for this query. Raises :class:`GatewayError`
        with a model-friendly message on any governed rejection or failure.
        """
        # No agent_id: the server runs it on the workspace's default compute, or
        # picks a connected agent when there is none, and starts a stopped default.
        # `scale_out: false` is the rule that the assistant never adds an agent to
        # the elastic pool; the server enforces it for this account regardless.
        body: dict[str, object] = {
            "sql": sql,
            "timeout_s": timeout_s,
            "scale_out": False,
            # The server holds the call until the query finishes, up to its cap,
            # so most queries cost this one request; slower ones are polled below.
            "wait_timeout_s": min(timeout_s, settings.sql_statement_max_wait_timeout_s),
        }
        if catalog:
            body["catalog"] = catalog
        if self._tags or tags:
            body["tags"] = {**self._tags, **(tags or {})}
        loop = asyncio.get_running_loop()
        submitted_at = loop.time()
        deadline = submitted_at + timeout_s + self._queue_wait_s
        # A turn cancelled during this call cannot cancel the query, whose id is
        # not known yet: it runs on to its timeout_s, and its result goes unread.
        resp = await self._client.post(f"/workspaces/{self._ws}/queries", json=body)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise _translate(exc) from exc
        query = resp.json()
        query_id = query["id"]

        status = query["status"]
        parked = status == "queued"
        try:
            while status not in _TERMINAL:
                if loop.time() > deadline:
                    # Don't leave the query running on the agent to stack up.
                    await self._cancel_query(query_id)
                    raise GatewayError(f"Query timed out after {loop.time() - submitted_at:.0f}s.")
                await asyncio.sleep(_POLL_INTERVAL_S)
                query = (await self._get(f"/queries/{query_id}")).json()
                status = query["status"]
        except asyncio.CancelledError:
            # The turn was cancelled while polling — best-effort cancel, detached
            # so the cancellation still propagates promptly.
            asyncio.ensure_future(self._cancel_query(query_id))  # noqa: RUF006
            raise

        if status == "failed":
            raise GatewayError(f"Query failed: {query.get('error') or 'unknown error'}")
        if status == "cancelled":
            raise GatewayError("Query was cancelled.")

        page = await self._fetch_page(query_id, cursor=None, limit=self._row_cap)
        page["query_id"] = query_id
        page["status"] = status
        waited = loop.time() - submitted_at
        if parked and waited >= _WAITED_NOTE_AFTER_S:
            # Compute was probably starting. Said so the model can tell the user why
            # this answer was slow instead of leaving them to wonder.
            page["queued_for_s"] = round(waited)

        # Advisory, not a gate. Free SQL is a legitimate use of this assistant, so
        # hand-written aggregation is never refused — but when the workspace has
        # already settled what a number means, saying so is the difference between
        # one authoritative answer and two plausible ones that disagree. Recorded
        # on the tool-call row too, so how often the layer gets bypassed is a
        # number somebody can look at rather than a hope.
        conflicts = await self.semantic_conflicts(sql)
        if conflicts:
            named = "; ".join(
                f"{c['metric']} (model {c['model']}, over {', '.join(c['columns'])})"
                for c in conflicts
            )
            page["semantic_warning"] = (
                f"This aggregates column(s) already defined by published metric(s): {named}. "
                "Those definitions may carry filters this query does not. Prefer query_metric "
                "so the authoritative definition applies, or say explicitly that you are "
                "computing something different."
            )
        return page

    async def _cancel_query(self, query_id: str) -> None:
        """Best-effort cancel of a running query; never raises."""
        with contextlib.suppress(Exception):
            await self._client.delete(f"/queries/{query_id}")

    async def get_query_result(self, query_id: str, *, cursor: str | None, limit: int) -> dict:
        # The rows endpoint re-checks the reader's grants, but paging stays limited
        # to queries this principal ran: a query id from a colleague's History is
        # not something a conversation should be able to pull results from.
        record = (await self._get(f"/queries/{query_id}")).json()
        if str(record.get("user_id")) != self._service_account_id:
            raise GatewayError(
                "I can only page results of queries I ran in this conversation.", kind="denied"
            )
        page = await self._fetch_page(query_id, cursor=cursor, limit=min(limit, self._row_cap))
        page["query_id"] = query_id
        return page

    async def _fetch_page(self, query_id: str, *, cursor: str | None, limit: int) -> dict:
        params: dict[str, object] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        resp = await self._get(f"/queries/{query_id}/rows", params=params)
        data = resp.json()
        rows, truncated = self._cap_bytes(data.get("rows", []))
        page: dict[str, object] = {
            "columns": data.get("columns", []),
            "rows": rows,
            "total": data.get("total", len(rows)),
            "cursor": data.get("cursor"),
            "truncated": truncated or len(rows) < len(data.get("rows", [])),
        }
        # DECIMAL and the wide integers arrive as exact strings, so without the
        # types a count of "2" reads like text. Absent for an agent too old to
        # report them.
        if data.get("column_schema"):
            page["column_types"] = {c["name"]: c["type"] for c in data["column_schema"]}
        return page

    def _cap_bytes(self, rows: list[dict]) -> tuple[list[dict], bool]:
        """Trim rows so the JSON-serialized sample stays under the byte cap."""
        kept: list[dict] = []
        size = 0
        for row in rows:
            size += len(json.dumps(row, default=str))
            if size > self._byte_cap and kept:
                return kept, True
            kept.append(row)
        return kept, False
