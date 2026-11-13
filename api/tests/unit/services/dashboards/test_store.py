"""The stored form of a dashboard: canonical YAML, hashes, what it reads, and the write guard."""

import copy

import pytest
from fastapi import HTTPException

from api.models.dashboard import Dashboard
from api.services.dashboards.loader import load, parse
from api.services.dashboards.store import assert_writable, export, refs, spec_hash, stored

BASE = {
    "version": 1,
    "name": "sales-overview",
    "title": "Sales",
    "parameters": {
        "period": {"type": "time_range", "default": "last_30_days"},
        "region": {"type": "dimension", "model": "orders", "dimension": "region"},
    },
    "rows": [
        {
            "widgets": [
                {
                    "title": "Revenue",
                    "type": "kpi",
                    "id": "revenue",
                    "query": {
                        "model": "orders",
                        "metrics": ["revenue"],
                        "time_range": "$period",
                        "filters": [{"dimension": "status", "op": "eq", "values": ["NO"]}],
                    },
                },
                {
                    "id": "refunds",
                    "type": "table",
                    "title": "Refunds",
                    "sql": "SELECT *\nFROM sales.analytics.refunds\nWHERE d >= $since\n",
                    "params": {"since": "$period.start"},
                },
            ]
        },
        {"widgets": [{"id": "notes", "type": "text", "markdown": "Net of refunds."}]},
    ],
}


def _spec(data=None):
    return parse(copy.deepcopy(data or BASE)).spec


def test_the_export_loads_back_to_the_same_dashboard():
    spec = _spec()

    assert load(export(spec)).spec == spec


def test_the_export_reads_like_a_file_someone_wrote():
    text = export(_spec())

    assert "  - widgets:\n      - id: revenue\n        type: kpi\n        title: Revenue\n" in text
    assert "metrics: [revenue]" in text
    assert "sql: |\n          SELECT *\n          FROM sales.analytics.refunds\n" in text
    assert "values: ['NO']" in text  # stays text when read back


def test_the_hash_ignores_key_order_and_spelled_out_defaults():
    reordered = copy.deepcopy(BASE)
    reordered["parameters"]["region"]["multiple"] = False
    reordered = dict(reversed(list(reordered.items())))

    assert spec_hash(_spec(reordered)) == spec_hash(_spec())
    assert spec_hash(_spec({**BASE, "title": "Other"})) != spec_hash(_spec())


def test_the_stored_form_leaves_defaults_out():
    assert "multiple" not in stored(_spec())["parameters"]["region"]


def test_refs_name_every_model_metric_dimension_and_table():
    assert refs(_spec()) == [
        ("dimension", "orders", "region"),
        ("dimension", "orders", "status"),
        ("metric", "orders", "revenue"),
        ("model", None, "orders"),
        ("table", None, "sales.analytics.refunds"),
    ]


def test_sql_that_does_not_parse_has_no_table_refs():
    data = copy.deepcopy(BASE)
    data["rows"][0]["widgets"][1]["sql"] = "SELEC nonsense FROM"

    assert not [r for r in refs(_spec(data)) if r[0] == "table"]


def test_only_a_dashboard_managed_in_git_is_refused():
    assert_writable(Dashboard(name="a", source="ui"))
    with pytest.raises(HTTPException) as caught:
        assert_writable(Dashboard(name="a", source="git", source_id="acme/bi"))

    assert caught.value.status_code == 409
    assert caught.value.detail["error"] == "managed_dashboard"
    assert "acme/bi" in caught.value.detail["detail"]
