"""The strict YAML loader: what it refuses, and where it says the problem is."""

import re
import textwrap
from pathlib import Path

import pytest

from api.services.dashboards.loader import (
    MAX_BYTES,
    MAX_DEPTH,
    DashboardInvalid,
    UnsupportedSpecVersion,
    load,
    parse,
)

SOURCE = textwrap.dedent(
    """\
    version: 1
    name: sales
    title: Sales
    parameters:
      country:
        type: dimension
        model: orders
        dimension: country
        default: NO
    rows:
      - widgets:
          - id: revenue
            type: kpi
            title: Revenue
            query:
              model: orders
              metrics: [revenue]
    """
)


def refused(text) -> list[tuple]:
    with pytest.raises(DashboardInvalid) as caught:
        load(text)
    return [(d.path, d.line, d.col, d.message, d.hint) for d in caught.value.diagnostics]


def test_a_valid_file_loads():
    loaded = load(SOURCE)
    assert loaded.spec.name == "sales"
    assert loaded.warnings == []


@pytest.mark.parametrize("word", ["NO", "no", "yes", "on", "off", "y", "True", "FALSE"])
def test_only_true_and_false_are_booleans(word):
    # The Norway problem: YAML 1.1 reads the country code NO as false.
    loaded = load(SOURCE.replace("default: NO", f"default: {word}"))
    assert loaded.spec.parameters["country"].default == word


@pytest.mark.parametrize(
    ("text", "value"), [("true", True), ("false", False), ("12", 12), ("-3", -3), ("2.5", 2.5)]
)
def test_plain_scalars_still_read_as_values(text, value):
    loaded = load(SOURCE.replace("default: NO", f"default: {text}"))
    assert loaded.spec.parameters["country"].default == value


@pytest.mark.parametrize("text", ["1:30", "0x1F", "1_000", ".inf", "0o17"])
def test_yaml_1_1_number_forms_stay_text(text):
    # 1:30 is 90 to YAML 1.1; here it is the text it looks like.
    loaded = load(SOURCE.replace("default: NO", f"default: {text}"))
    assert loaded.spec.parameters["country"].default == text


def test_a_number_written_as_text_is_refused_where_a_number_belongs():
    text = SOURCE.replace(
        "        title: Revenue\n", "        title: Revenue\n        width: 1_000\n"
    )
    assert refused(text) == [
        ("rows[0].widgets[0].width", 15, 16, "Input should be a valid integer, not '1_000'.", None)
    ]


def test_a_duplicate_key_is_refused_with_both_lines():
    text = SOURCE.replace("title: Sales\n", "title: Sales\ntitle: Again\n")
    assert refused(text) == [
        ("", 4, 1, "'title' appears twice in the same mapping; it was first set on line 3.", None)
    ]


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (("query:\n", "query: &q\n"), "Anchors (&name) are not allowed; write each value out."),
        (("metrics: [revenue]", "metrics: *revenue"), "Aliases (*name) are not allowed"),
        (
            ("    query:\n", "    query:\n          <<: {limit: 5}\n"),
            "Merge keys (<<) are not allowed",
        ),
    ],
)
def test_references_inside_yaml_are_refused(change, message):
    text = SOURCE.replace(*change)
    assert any(message in m for _, _, _, m, _ in refused(text))


def test_keys_are_text():
    text = SOURCE.replace("title: Sales\n", "title: Sales\n1: one\n")
    assert refused(text) == [("", 4, 1, "Keys are plain text.", None)]


def test_tags_that_build_objects_are_refused():
    text = SOURCE.replace("title: Sales", "title: !!python/object:os.system ls")
    assert refused(text)[0][1:3] == (3, 8)


def test_the_file_size_and_depth_are_capped():
    too_big = SOURCE + "# " + "x" * MAX_BYTES + "\n"
    assert refused(too_big)[0][3] == "A dashboard file is at most 256 KB."

    deep = SOURCE.replace(
        "default: NO", "default: " + "[" * (MAX_DEPTH + 1) + "]" * (MAX_DEPTH + 1)
    )
    assert f"deeper than {MAX_DEPTH}" in refused(deep)[0][3]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("", "The file is empty."),
        ("- a\n- b\n", "A dashboard is a mapping of keys."),
        (
            SOURCE + "---\nversion: 1\n",
            "Expected a single document in the stream, but found another document.",
        ),
        ("title: [\n", "While parsing a flow node, expected the node content"),
        (b"\xff\xfe", "A dashboard file is UTF-8 text."),
    ],
)
def test_documents_that_are_not_one_mapping_are_refused(text, message):
    assert message in refused(text)[0][3]


def test_an_unknown_key_points_at_the_key_and_suggests_the_near_one():
    text = SOURCE.replace("        title: Revenue\n", "        title: Revenue\n        widht: 4\n")
    assert refused(text) == [
        ("rows[0].widgets[0].widht", 15, 9, "Unknown key 'widht'.", "Did you mean 'width'?")
    ]


def test_a_missing_key_points_at_the_mapping_it_belongs_in():
    text = SOURCE.replace("        title: Revenue\n", "")
    assert refused(text) == [("rows[0].widgets[0]", 12, 9, "'title' is required.", None)]


def test_an_unknown_widget_type_points_at_its_type():
    assert refused(SOURCE.replace("type: kpi", "type: pie")) == [
        (
            "rows[0].widgets[0].type",
            13,
            15,
            "Unknown type 'pie'; use one of 'kpi', 'line', 'bar', 'table', 'text'.",
            None,
        )
    ]


def test_a_shape_error_points_at_the_widget_and_says_what_to_do():
    text = SOURCE.replace("metrics: [revenue]", "metrics: [revenue]\n          grain: month")
    assert refused(text) == [
        (
            "rows[0].widgets[0]",
            12,
            9,
            "A kpi takes no grain; use a line to show it over time.",
            None,
        )
    ]


def test_a_reference_error_points_at_the_reference():
    text = SOURCE.replace("metrics: [revenue]", "metrics: [revenue]\n          time_range: $period")
    assert refused(text) == [
        (
            "rows[0].widgets[0].query.time_range",
            18,
            23,
            "$period is not a parameter of this dashboard (declared: $country).",
            None,
        )
    ]


def test_a_bad_name_gets_a_hint_about_the_allowed_form():
    assert refused(SOURCE.replace("name: sales", "name: Sales Overview")) == [
        (
            "name",
            2,
            7,
            "'Sales Overview' is not allowed here.",
            "Use lowercase letters, digits, - and _, starting with a letter.",
        )
    ]


def test_every_problem_is_reported_at_once():
    text = SOURCE.replace("title: Sales", "title: 5").replace(
        "type: kpi", "type: kpi\n        colour: red"
    )
    assert [p for p, *_ in refused(text)] == ["title", "rows[0].widgets[0].colour"]


def test_warnings_come_back_with_positions():
    text = SOURCE.replace("model: orders\n    dimension", "model: web\n    dimension")
    loaded = load(text)
    assert [(w.path, w.line, w.col) for w in loaded.warnings] == [("parameters.country", 6, 5)]
    assert "filters nothing" in loaded.warnings[0].message


@pytest.mark.parametrize(
    ("version", "message"),
    [
        ("2", "written for dashboard format version 2; this server reads 1. Upgrade DuckHaven"),
        ("0", "format version 0 is no longer read"),
    ],
)
def test_a_version_this_server_does_not_read_is_its_own_error(version, message):
    with pytest.raises(UnsupportedSpecVersion) as caught:
        load(SOURCE.replace("version: 1", f"version: {version}"))
    (diagnostic,) = caught.value.diagnostics
    assert caught.value.code == "unsupported_spec_version"
    assert (diagnostic.path, diagnostic.line, diagnostic.col) == ("version", 1, 10)
    assert message in diagnostic.message


def test_a_newer_file_is_refused_before_its_new_keys_are():
    # A v2 file may use keys v1 has never heard of; the version is the useful error.
    text = SOURCE.replace("version: 1", "version: 2").replace(
        "title: Sales", "title: Sales\nlayout: grid"
    )
    with pytest.raises(UnsupportedSpecVersion):
        load(text)


@pytest.mark.parametrize(
    ("text", "message"), [("", "version is required."), ("version: '1'\n", "whole number")]
)
def test_the_version_is_required_and_numeric(text, message):
    assert message in refused(text + "name: x\n" if text else "name: x\n")[0][3]


def test_a_json_body_is_validated_the_same_way_without_positions():
    with pytest.raises(DashboardInvalid) as caught:
        parse({"version": 1, "name": "x", "title": "X", "rows": [], "extra": 1})
    assert {(d.path, d.line) for d in caught.value.diagnostics} == {("rows", None), ("extra", None)}
    assert (
        parse(load(SOURCE).spec.model_dump(mode="json", exclude_defaults=True)).spec
        == load(SOURCE).spec
    )


def test_the_reference_page_example_loads_cleanly():
    """The example on the reference page is the one people copy; keep it valid."""
    page = Path(__file__).parents[5] / "docs" / "reference" / "dashboard-spec.md"
    blocks = re.findall(r"```yaml\n(.*?)```", page.read_text(encoding="utf-8"), flags=re.DOTALL)
    example = next(b for b in blocks if b.lstrip().startswith("# yaml-language-server"))
    loaded = load(example)
    assert loaded.warnings == []
    assert [w.id for _, w in loaded.spec.widgets()] == [
        "revenue",
        "revenue_trend",
        "by_region",
        "refunds",
        "notes",
    ]
