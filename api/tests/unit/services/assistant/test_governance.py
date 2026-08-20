"""Unit tests for the governance audit hook, in particular the deep-link
table extraction it attaches to a successful run_sql call."""

from types import SimpleNamespace

from pydantic_ai.messages import ToolCallPart

from api.services.assistant.governance import _audit, _linked_tables


def _ctx(**deps_kwargs):
    deps_kwargs.setdefault("records", {})
    return SimpleNamespace(deps=SimpleNamespace(**deps_kwargs))


async def _run_audit(ctx, *, tool_name, args, handler):
    call = ToolCallPart(tool_name=tool_name, args=args, tool_call_id="call-1")
    result = await _audit(ctx, call=call, tool_def=None, args=args, handler=handler)
    return result, ctx.deps.records["call-1"]


class TestLinkedTables:
    def test_resolves_a_fully_qualified_table(self):
        tables = _linked_tables("SELECT * FROM c.raw.events", default_catalog=None)
        assert tables == [{"catalog": "c", "schema_name": "raw", "table": "events"}]

    def test_defaults_catalog_when_unqualified(self):
        tables = _linked_tables("SELECT * FROM raw.events", default_catalog="acme")
        assert tables == [{"catalog": "acme", "schema_name": "raw", "table": "events"}]

    def test_drops_refs_with_no_resolvable_schema(self):
        # Bare `events`, no schema and no way to guess one — no confidence, no chip.
        assert _linked_tables("SELECT * FROM events", default_catalog="acme") is None

    def test_drops_refs_with_no_resolvable_catalog_or_schema(self):
        assert _linked_tables("SELECT * FROM events", default_catalog=None) is None

    def test_excludes_metadata_only_refs(self):
        assert _linked_tables("DESCRIBE c.raw.events", default_catalog=None) is None

    def test_includes_write_targets(self):
        tables = _linked_tables(
            "INSERT INTO c.raw.events SELECT * FROM c.raw.staging", default_catalog=None
        )
        assert {"catalog": "c", "schema_name": "raw", "table": "events"} in tables
        assert {"catalog": "c", "schema_name": "raw", "table": "staging"} in tables

    def test_dedupes_repeated_refs(self):
        tables = _linked_tables(
            "SELECT * FROM c.raw.events a JOIN c.raw.events b ON a.id = b.id",
            default_catalog=None,
        )
        assert tables == [{"catalog": "c", "schema_name": "raw", "table": "events"}]

    def test_caps_at_three(self):
        sql = "SELECT * FROM c.raw.a, c.raw.b, c.raw.c, c.raw.d"
        assert len(_linked_tables(sql, default_catalog=None)) == 3

    def test_unparseable_sql_yields_no_chips(self):
        assert _linked_tables("SELECT FROM WHERE (((", default_catalog=None) is None


class TestAuditRecordsTables:
    async def test_run_sql_success_records_tables(self):
        ctx = _ctx(catalog="acme")

        async def handler(_args):
            return {"query_id": "q-1"}

        _, record = await _run_audit(
            ctx,
            tool_name="run_sql",
            args={"sql": "SELECT * FROM raw.events"},
            handler=handler,
        )
        assert record.tables == [{"catalog": "acme", "schema_name": "raw", "table": "events"}]

    async def test_non_run_sql_tool_never_gets_tables(self):
        ctx = _ctx(catalog="acme")

        async def handler(_args):
            return {"catalogs": ["acme"]}

        _, record = await _run_audit(ctx, tool_name="list_catalogs", args={}, handler=handler)
        assert record.tables is None

    async def test_run_sql_with_no_resolvable_table_leaves_tables_none(self):
        ctx = _ctx(catalog=None)

        async def handler(_args):
            return {"query_id": "q-2"}

        _, record = await _run_audit(
            ctx,
            tool_name="run_sql",
            args={"sql": "SELECT * FROM events"},
            handler=handler,
        )
        assert record.tables is None

    async def test_run_sql_failure_never_reaches_extraction(self):
        ctx = _ctx(catalog="acme")

        async def handler(_args):
            raise RuntimeError("boom")

        call = ToolCallPart(tool_name="run_sql", args={"sql": "SELECT 1"}, tool_call_id="call-1")
        try:
            await _audit(
                ctx,
                call=call,
                tool_def=None,
                args={"sql": "SELECT 1"},
                handler=handler,
            )
        except RuntimeError:
            pass
        record = ctx.deps.records["call-1"]
        assert record.status == "error"
        assert record.tables is None
