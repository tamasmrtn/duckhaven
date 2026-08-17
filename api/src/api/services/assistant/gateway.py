"""Thin, authenticated loopback to DuckHaven's own REST API.

Every metadata/SQL action the assistant takes is an HTTP call to the same routes
the SPA calls, authenticated as the assistant's service account. This is the
load-bearing property of the design: enforcement (``assert_workspace_member`` →
``sql_guard`` → ``assert_query_access``) stays server-side in the existing
chokepoints, so a harness bug or a prompt-injected tool call can never exceed the
service account's grants.

Router-body checks (membership, the SQL allowlist) live *above* the service layer,
so a direct service call would skip them — hence the loopback rather than a direct
``dispatch_query`` call.
"""

from __future__ import annotations

import asyncio
import contextlib
import json

import httpx

# Query statuses that mean the run has stopped (mirrors the scheduler's terminal set).
_TERMINAL = {"done", "failed", "cancelled"}
_POLL_INTERVAL_S = 0.25


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


class GatewayError(Exception):
    """A governed REST call failed. The message is safe to surface to the model."""


def _translate(exc: httpx.HTTPStatusError) -> GatewayError:
    """Map a governed error response to a concise, model-friendly message."""
    resp = exc.response
    detail: object = None
    try:
        body = resp.json()
        detail = body.get("detail", body) if isinstance(body, dict) else body
    except ValueError:
        detail = resp.text
    # sql_guard / grants wrap detail as {"error": ..., "detail": ...}.
    if isinstance(detail, dict):
        detail = detail.get("detail") or detail.get("error") or str(detail)
    code = resp.status_code
    if code == 403:
        return GatewayError(f"Access denied: {detail}")
    if code == 404:
        return GatewayError(f"Not found (or not accessible): {detail}")
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
    ) -> None:
        self._client = client
        self._ws = workspace_slug
        self._row_cap = row_cap
        self._byte_cap = byte_cap
        # The id the assistant's queries are attributed to; used to refuse paging
        # results of queries run by *other* principals.
        self._service_account_id = service_account_id

    async def _get(self, path: str, **kwargs) -> httpx.Response:
        resp = await self._client.get(path, **kwargs)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise _translate(exc) from exc
        return resp

    # ── Catalog browse ────────────────────────────────────────────────────────
    async def list_catalogs(self) -> list[dict]:
        resp = await self._get(f"/workspaces/{self._ws}/catalogs")
        return [{"slug": c["slug"], "name": c.get("name")} for c in resp.json()]

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
    async def _pick_agent(self) -> str:
        resp = await self._get("/agents")
        for agent in resp.json():
            if agent.get("status") == "healthy":
                return agent["id"]
        raise GatewayError("No compute agent is currently available to run SQL.")

    async def run_sql(self, sql: str, *, catalog: str | None, timeout_s: float) -> dict:
        """Submit a query, poll to completion, and return a capped result sample.

        Returns a dict with ``query_id``, ``status``, ``columns``, capped ``rows``,
        ``total`` row count, and a ``truncated`` flag. Raises :class:`GatewayError`
        with a model-friendly message on any governed rejection or failure.
        """
        agent_id = await self._pick_agent()
        body = {"sql": sql, "agent_id": agent_id, "timeout_s": timeout_s}
        if catalog:
            body["catalog"] = catalog
        resp = await self._client.post(f"/workspaces/{self._ws}/queries", json=body)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise _translate(exc) from exc
        query = resp.json()
        query_id = query["id"]

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_s
        status = query["status"]
        try:
            while status not in _TERMINAL:
                if loop.time() > deadline:
                    # Don't leave the query running on the agent to stack up.
                    await self._cancel_query(query_id)
                    raise GatewayError(f"Query timed out after {timeout_s:.0f}s.")
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
        # Governance: the shared rows endpoint authorizes on workspace membership
        # only, so restrict paging to queries this service account actually ran —
        # otherwise the assistant could read data from a more-privileged member's
        # query id, exceeding its own grants.
        record = (await self._get(f"/queries/{query_id}")).json()
        if str(record.get("user_id")) != self._service_account_id:
            raise GatewayError("I can only page results of queries I ran in this conversation.")
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
        return {
            "columns": data.get("columns", []),
            "rows": rows,
            "total": data.get("total", len(rows)),
            "cursor": data.get("cursor"),
            "truncated": truncated or len(rows) < len(data.get("rows", [])),
        }

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
