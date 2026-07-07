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
