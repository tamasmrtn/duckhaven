"""Reference pages that restate code must agree with it.

A reference table is the one place a reader looks a fact up, so a permission or
tier that exists in the code but not on the page (or the reverse) is worse than
no page. These tests compare each table's first column with the definition it
documents.
"""

import re

from api.openapi import _DERIVED_ERROR_CODES
from api.services.agent_access import AGENT_TIER_ORDER
from api.services.assistant.knowledge import generate
from api.services.grants import TIER_SCALE
from api.services.permissions import Permission
from api.services.workspace import ROLE_ORDER

DOCS_DIR = generate._repo_root() / "docs"
API_SRC = generate._repo_root() / "api" / "src" / "api"

# The two ways a handler names its code: an ``{"error": ...}`` detail, or the
# first argument of the dispatch check's exception.
_RAISED_CODE = re.compile(r'(?:"error": *|AgentNotDispatchable\(\s*)"([a-z]+(?:_[a-z]+)*)"')


def _first_column(page: str, heading: str) -> set[str]:
    """The backticked names in the first column of the table under ``heading``."""
    text = (DOCS_DIR / page).read_text(encoding="utf-8")
    section = text.split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]
    return set(re.findall(r"^\| `([^`]+)` \|", section, flags=re.MULTILINE))


def test_every_global_permission_is_documented():
    assert _first_column("reference/permissions.md", "Global permissions") == {
        p.value for p in Permission
    }


def test_every_workspace_role_is_documented():
    assert _first_column("reference/permissions.md", "Workspace roles") == set(ROLE_ORDER)


def test_every_data_grant_tier_is_documented():
    assert _first_column("reference/permissions.md", "Data-grant tiers") == set(TIER_SCALE)


def test_every_agent_tier_is_documented():
    assert _first_column("reference/permissions.md", "Agent tiers") == set(AGENT_TIER_ORDER)


def _documented_error_codes() -> set[str]:
    """Every code in the first column of the code tables on the errors page."""
    text = (DOCS_DIR / "reference/errors.md").read_text(encoding="utf-8")
    tables = text.split("## General codes\n", 1)[1]
    codes: set[str] = set()
    for cell in re.findall(r"^\| ((?:`[a-z_]+`(?:, )?)+) \|", tables, flags=re.MULTILINE):
        codes |= set(re.findall(r"`([a-z_]+)`", cell))
    return codes


def _api_source() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in API_SRC.rglob("*.py"))


def test_every_error_code_the_api_raises_is_documented():
    raised = set(_RAISED_CODE.findall(_api_source())) | set(_DERIVED_ERROR_CODES.values())
    raised.add("internal_error")

    assert raised - _documented_error_codes() == set()


def test_every_documented_error_code_exists_in_the_api():
    source = _api_source()

    assert {c for c in _documented_error_codes() if f'"{c}"' not in source} == set()
