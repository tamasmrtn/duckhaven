"""The read_doc_page tool: what the model gets back, and how it fails.

Failure behaviour is most of the surface here. A tool that raises on a wrong path
ends the turn; one that hands back the nearest real paths lets the model correct
itself, which is why every error below is a ``ModelRetry`` and not an exception.
"""

from types import SimpleNamespace

import pytest
from pydantic_ai import ModelRetry

from api.config import settings
from api.services.assistant.knowledge import generate
from api.services.assistant.knowledge.loader import load_index
from api.services.assistant.tools import ALL_TOOLS, DOCS_TOOLS, build_toolset, read_doc_page

DOCS_DIR = generate._repo_root() / "docs"


@pytest.fixture(autouse=True)
def _docs_from_the_checkout(monkeypatch):
    monkeypatch.setattr(settings, "assistant_docs_dir", DOCS_DIR)
    load_index.cache_clear()
    yield
    load_index.cache_clear()


def _ctx():
    return SimpleNamespace(deps=SimpleNamespace())


async def test_reading_a_page_returns_its_text_and_the_running_version():
    result = await read_doc_page(_ctx(), "guides/snapshots-time-travel.md")

    assert result["path"] == "guides/snapshots-time-travel.md"
    assert result["title"] == "Snapshots & time travel"
    assert "AT (TIMESTAMP =>" in result["text"]
    assert result["version"] == settings.app_version


async def test_an_unknown_path_suggests_the_nearest_real_ones():
    """A retryable nudge, not a dead end: the model can fix its own guess."""
    with pytest.raises(ModelRetry) as exc:
        await read_doc_page(_ctx(), "reference/sql-supported.md")

    assert "reference/sql-support.md" in str(exc.value)


async def test_a_path_outside_the_index_is_refused():
    for path in ("../../../etc/passwd", "/etc/passwd", "developer/testing.md"):
        with pytest.raises(ModelRetry):
            await read_doc_page(_ctx(), path)


async def test_a_deployment_without_docs_says_so(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "assistant_docs_dir", tmp_path / "absent")

    with pytest.raises(ModelRetry) as exc:
        await read_doc_page(_ctx(), "reference/sql-support.md")

    assert "not available" in str(exc.value)


async def test_an_oversized_page_comes_back_truncated(monkeypatch):
    monkeypatch.setattr(settings, "assistant_docs_max_page_chars", 1_000)

    result = await read_doc_page(_ctx(), "concepts/architecture.md")

    assert result["truncated"] is True
    assert "[truncated — full page at" in result["text"]


# ── The toolset ───────────────────────────────────────────────────────────────


def test_docs_tools_are_exposed_by_default():
    assert read_doc_page in build_toolset()


def test_disabling_product_knowledge_withholds_the_tool(monkeypatch):
    """A half-revert would leave the tool callable with nothing telling the model
    not to; the schema is the reachable surface, not the prompt."""
    monkeypatch.setattr(settings, "assistant_docs_enabled", False)

    toolset = build_toolset()

    assert read_doc_page not in toolset
    assert len(toolset) == len(ALL_TOOLS) - len(DOCS_TOOLS)
    assert all(tool in ALL_TOOLS for tool in toolset)
