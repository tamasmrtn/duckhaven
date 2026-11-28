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


#: A metric line and a ranked bar, with labels, widths and an ignored parameter.
TRENDS = {
    "version": 1,
    "name": "trends",
    "title": "Trends",
    "description": "Weekly revenue and the top countries.",
    "labels": ["sales"],
    "parameters": {
        "period": {"type": "time_range", "label": "Period", "default": "last_90_days"},
        "channel": {
            "type": "dimension",
            "model": "orders",
            "dimension": "channel",
            "default": "web",
        },
    },
    "rows": [
        {
            "widgets": [
                {
                    "id": "weekly",
                    "type": "line",
                    "title": "Revenue by week",
                    "width": 8,
                    "query": {
                        "model": "orders",
                        "metrics": ["revenue"],
                        "dimensions": ["region"],
                        "grain": "week",
                        "time_range": "$period",
                    },
                },
                {
                    "id": "top",
                    "type": "bar",
                    "title": "Top countries",
                    "width": 4,
                    "ignore": ["channel"],
                    "query": {
                        "model": "orders",
                        "metrics": ["revenue"],
                        "dimensions": ["country"],
                        "order_by": [{"field": "revenue", "descending": True}],
                        "limit": 10,
                    },
                },
            ]
        }
    ],
}
#: SQL widgets of each charted type, one split into series.
LAPS = {
    "version": 1,
    "name": "laps",
    "title": "Lap times",
    "parameters": {"race": {"type": "dimension", "model": "f1", "dimension": "race"}},
    "rows": [
        {
            "widgets": [
                {
                    "id": "laps",
                    "type": "line",
                    "title": "Lap times",
                    "description": "Seconds per lap, one line per driver.",
                    "sql": "SELECT lap, driver, seconds FROM f1.marts.laps WHERE race = $race",
                    "params": {"race": "$race"},
                    "columns": {"x": "lap", "y": ["seconds"], "series": "driver"},
                },
                {
                    "id": "best",
                    "type": "kpi",
                    "title": "Fastest lap",
                    "sql": "SELECT min(seconds) AS best FROM f1.marts.laps WHERE race = $race",
                    "params": {"race": "$race"},
                    "columns": {"value": "best"},
                },
                {
                    "id": "teams",
                    "type": "bar",
                    "title": "Laps by team",
                    "sql": "SELECT team, count(*) AS laps FROM f1.marts.laps GROUP BY team",
                    "columns": {"x": "team", "y": ["laps"]},
                },
            ]
        }
    ],
}


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (BASE, "a538f8271fdf16c9de113ddc2d2c2623b42bd02ebd2891eabbf0f1621bf94902"),
        (TRENDS, "9da07399172c9c2228cd959c9861a6d9d1631f438c2f57436ce1649c9c1da0f7"),
        (LAPS, "cafd979e1aab406101172c29712b89b8b853a007becb1e7c9c225fb9c1943324"),
    ],
    ids=["base", "trends", "laps"],
)
def test_stored_files_keep_their_hash(data, expected):
    """A version 1 file means what it meant when it was written, and hashes the same.

    These hashes were recorded before version 1 gained optional keys. A key added
    with a default the stored form leaves out keeps them; if one changes, the
    addition changed what an existing file means."""
    assert spec_hash(_spec(data)) == expected


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
