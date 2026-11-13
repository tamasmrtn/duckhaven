"""The published JSON Schema is a contract: it changes only on purpose."""

import json
import os
from pathlib import Path

from api.services.dashboards.spec import json_schema

SNAPSHOT = Path(__file__).with_name("dashboard-schema-v1.json")


def test_the_schema_matches_its_snapshot():
    """Editors, the CLI and AI clients validate against this schema.

    If this fails, the format changed. Make sure the change is one a file written
    for the last release still passes, then regenerate with
    ``UPDATE_SNAPSHOTS=1 make test-api`` and say so in the pull request.
    """
    schema = json.dumps(json_schema(), indent=2, sort_keys=True) + "\n"
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        SNAPSHOT.write_text(schema, encoding="utf-8")
    assert schema == SNAPSHOT.read_text(encoding="utf-8")


def test_every_object_in_the_schema_refuses_unknown_keys():
    """An editor flags a typo, as the server does, instead of silently passing it."""
    schema = json_schema()
    objects = [schema, *schema["$defs"].values()]
    open_ = [
        d.get("title")
        for d in objects
        if d.get("type") == "object"
        and d.get("additionalProperties") is not False
        and "properties" in d
    ]
    assert open_ == []
