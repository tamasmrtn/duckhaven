"""The documentation index: does it ship, does it match docs/, does it read back.

The index is generated, so the interesting failures are not logic errors but
correspondence ones — a page added to ``docs/`` and never indexed, an indexed path
that no longer resolves, a summary silently clobbered by regeneration. Those are
what these tests are for. The drift hook catches them at commit time; these catch
them for anyone who bypasses it, and they run with no Postgres and no provider.
"""

import shutil

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from api.config import settings
from api.models.docs import DocsCorpusMeta, DocsPage
from api.services.assistant.knowledge import generate, sync
from api.services.assistant.knowledge.loader import (
    INDEX_PATH,
    DocsUnavailableError,
    PageNotIndexed,
    docs_available,
    load_index,
    page_url,
    read_page,
)

REPO_ROOT = generate._repo_root()
DOCS_DIR = REPO_ROOT / "docs"


@pytest.fixture(autouse=True)
def _docs_from_the_checkout(monkeypatch):
    """Point the loader at the repo's docs/, which is what the image copies."""
    monkeypatch.setattr(settings, "assistant_docs_dir", DOCS_DIR)
    load_index.cache_clear()
    yield
    load_index.cache_clear()


# ── Integrity: the index and the docs tree agree ──────────────────────────────


def test_the_index_ships_with_the_package():
    """It lives inside api/src, so it travels in the wheel and into the image."""
    assert INDEX_PATH.is_file()
    assert load_index().pages


def test_every_indexed_path_resolves_to_a_real_page():
    missing = [p.path for p in load_index().pages if not (DOCS_DIR / p.path).is_file()]

    assert missing == []


def test_every_page_in_the_docs_tree_is_indexed():
    """The other direction, and it must read ``docs/`` rather than the nav.

    The index is derived from ``mkdocs.yml``'s nav, so comparing it against
    another nav walk compares the generator with itself and passes no matter what
    is on disk. A page written but never added to nav is the drift this whole
    feature exists to prevent, and mkdocs will not catch it either: its
    ``nav.omitted_files`` default is ``info``, which ``--strict`` does not
    promote to an error.
    """
    on_disk = {
        str(p.relative_to(DOCS_DIR))
        for p in DOCS_DIR.rglob("*.md")
        if generate._is_indexed(str(p.relative_to(DOCS_DIR)))
    }

    assert on_disk == set(load_index().paths)


def test_contributor_docs_are_left_out():
    """They answer questions about working on DuckHaven, not about using it."""
    paths = load_index().paths

    assert not [p for p in paths if p.startswith(("developer/", "release-notes/"))]
    assert "reference/sql-support.md" in paths


def test_every_page_has_a_title_and_a_summary():
    thin = [p.path for p in load_index().pages if not p.title or len(p.summary) < 20]

    assert thin == []


def test_the_committed_index_matches_what_the_generator_produces():
    """The drift gate, as a test — the hook is bypassable, this is not."""
    committed = yaml.safe_load(INDEX_PATH.read_text())
    regenerated = generate.render_index(
        generate.merge(generate.discover(DOCS_DIR, REPO_ROOT / "mkdocs.yml"), committed),
        committed.get("intro", ""),
    )

    assert regenerated == INDEX_PATH.read_text()


# ── The generator ─────────────────────────────────────────────────────────────


def test_regeneration_preserves_a_hand_written_summary():
    """Summaries are the one part a human owns; a mechanical guess must not win."""
    discovered = generate.discover(DOCS_DIR, REPO_ROOT / "mkdocs.yml")
    existing = {"pages": [{"path": discovered[0].path, "summary": "Hand-written."}]}

    merged = generate.merge(discovered, existing)

    assert merged[0].summary == "Hand-written."
    assert merged[1].summary == discovered[1].summary


def test_a_page_missing_from_the_index_gets_a_derived_summary():
    discovered = generate.discover(DOCS_DIR, REPO_ROOT / "mkdocs.yml")

    merged = generate.merge(discovered, {"pages": []})

    assert merged[0].summary == discovered[0].summary
    assert merged[0].summary


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("# T\n\nA plain lead sentence. And a second one.", "A plain lead sentence."),
        ("# T\n\nSee [the guide](x.md) for more.", "See the guide for more."),
        ("# T\n\nUse `DESCRIBE` instead.", "Use DESCRIBE instead."),
        ("# T\n\n!!! note\n    Skipped.\n\nThe real lead.", "The real lead."),
        ("# T\n\n## Straight to a heading", ""),
        # A bold or italic lead is prose, not a list item.
        ("# T\n\n**DuckHaven** is a platform. More.", "DuckHaven is a platform."),
        ("# T\n\n*Note* this describes storage.", "Note this describes storage."),
        ("# T\n\n- a list item\n\nThe real lead.", "The real lead."),
        # snake_case is an identifier, not emphasis — this docs set is full of them.
        ("# T\n\nSet `assistant_docs_dir` first.", "Set assistant_docs_dir first."),
        ("# T\n\nThe ASSISTANT_DOCS_ENABLED flag. Next.", "The ASSISTANT_DOCS_ENABLED flag."),
        # An abbreviation is not the end of a sentence.
        ("# T\n\nSee e.g. the guide. Next.", "See e.g. the guide."),
        (
            "# T\n\nSnapshots, tables, etc. are covered. Next.",
            "Snapshots, tables, etc. are covered.",
        ),
    ],
)
def test_summaries_are_reduced_to_one_plain_line(body, expected):
    assert generate.summarise(body) == expected


def test_llms_txt_matches_what_the_generator_produces():
    """The published index for other people's models. It has no unit of its own
    otherwise, and the pre-commit hook that regenerates it does not run in CI."""
    committed = yaml.safe_load(INDEX_PATH.read_text())
    pages = generate.merge(generate.discover(DOCS_DIR, REPO_ROOT / "mkdocs.yml"), committed)
    site_url = yaml.load(
        (REPO_ROOT / "mkdocs.yml").read_text(), Loader=generate._TagIgnoringLoader
    )["site_url"]

    rendered = generate.render_llms_txt(pages, committed.get("intro", ""), site_url)

    assert rendered == (DOCS_DIR / "llms.txt").read_text()


def test_a_nav_entry_with_no_title_takes_the_pages_h1(tmp_path):
    """MkDocs accepts a bare `- guides/foo.md`; this used to raise AttributeError
    from the middle of `make docs-index`."""
    docs = tmp_path / "docs"
    (docs / "guides").mkdir(parents=True)
    (docs / "guides" / "foo.md").write_text("# Foo the page\n\nA lead sentence.\n")
    mkdocs = tmp_path / "mkdocs.yml"
    mkdocs.write_text("site_url: https://x/\nnav:\n  - Guides:\n    - guides/foo.md\n")

    (page,) = generate.discover(docs, mkdocs)

    assert (page.title, page.section) == ("Foo the page", "Guides")


def test_a_third_nav_level_is_not_dropped(tmp_path):
    """A nested subtree used to be skipped whole, silently taking its pages out."""
    docs = tmp_path / "docs"
    (docs / "guides").mkdir(parents=True)
    (docs / "guides" / "deep.md").write_text("# Deep\n\nA lead sentence.\n")
    mkdocs = tmp_path / "mkdocs.yml"
    mkdocs.write_text(
        "site_url: https://x/\nnav:\n  - Guides:\n    - Sub:\n      - Deep: guides/deep.md\n"
    )

    assert [p.path for p in generate.discover(docs, mkdocs)] == ["guides/deep.md"]


# ── Reading a page ────────────────────────────────────────────────────────────


def test_reading_a_page_returns_its_markdown_and_the_running_version():
    result = read_page("reference/sql-support.md")

    assert result["title"] == "SQL support"
    assert "AT (VERSION =>" in result["text"]
    assert result["version"] == settings.app_version
    assert result["truncated"] is False


def test_an_oversized_page_is_truncated_with_a_pointer(monkeypatch):
    monkeypatch.setattr(settings, "assistant_docs_max_page_chars", 500)

    result = read_page("concepts/architecture.md")

    assert result["truncated"] is True
    assert "characters are not shown" in result["text"]
    assert page_url("concepts/architecture.md") in result["text"]


def test_an_unindexed_path_is_refused():
    """The index is the allowlist, which is what makes traversal unreachable."""
    for path in ("developer/testing.md", "../../etc/passwd", "/etc/passwd", "nope.md"):
        with pytest.raises(PageNotIndexed):
            read_page(path)


def test_a_near_miss_suggests_real_paths():
    nearest = load_index().nearest("reference/sql-supported.md")

    assert "reference/sql-support.md" in nearest


def test_a_missing_docs_tree_is_reported_not_crashed(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "assistant_docs_dir", tmp_path / "absent")

    with pytest.raises(DocsUnavailableError):
        read_page("reference/sql-support.md")


def test_a_page_indexed_but_absent_from_the_build_is_reported(monkeypatch, tmp_path):
    """A half-copied image should say so rather than raise a bare FileNotFound."""
    partial = tmp_path / "docs"
    (partial / "reference").mkdir(parents=True)
    shutil.copy(DOCS_DIR / "reference" / "cli.md", partial / "reference" / "cli.md")
    monkeypatch.setattr(settings, "assistant_docs_dir", partial)

    with pytest.raises(DocsUnavailableError):
        read_page("reference/sql-support.md")


def test_pages_are_cited_at_the_published_url():
    assert page_url("reference/sql-support.md") == (
        "https://tamasmrtn.github.io/duckhaven/reference/sql-support/"
    )


def test_an_index_that_parses_but_lists_nothing_is_an_error(monkeypatch, tmp_path):
    """A truncated write or a bad merge parses fine and yields zero pages. Read
    as success it produces an instructions block telling the model to call
    read_doc_page "with one of these exact paths:" and then listing none."""
    empty = tmp_path / "docs_index.yaml"
    empty.write_text("intro: hello\npages: []\n")
    monkeypatch.setattr("api.services.assistant.knowledge.loader.INDEX_PATH", empty)
    load_index.cache_clear()

    with pytest.raises(DocsUnavailableError):
        load_index()


def test_an_empty_index_takes_the_docs_feature_out_rather_than_half_on(monkeypatch, tmp_path):
    empty = tmp_path / "docs_index.yaml"
    empty.write_text("pages: []\n")
    monkeypatch.setattr("api.services.assistant.knowledge.loader.INDEX_PATH", empty)
    load_index.cache_clear()

    assert docs_available() is False


# ── Loading the corpus into the search tables ─────────────────────────────────
#
# Ranking needs Postgres and is scored in api/tests/integration/. What is
# checked here is the loading contract, which is dialect-independent: load once,
# skip when unchanged, and replace wholesale so a deleted page really goes.


def _fake_corpus(pages, monkeypatch, hash_="h1"):
    monkeypatch.setattr(sync, "_corpus", lambda: (hash_, pages))


def _page(path, body="body text"):
    return {"path": path, "title": path, "section": "Concepts", "summary": "s", "body": body}


async def test_the_corpus_loads_once_and_then_skips(db_session, monkeypatch):
    _fake_corpus([_page("a.md"), _page("b.md")], monkeypatch)

    assert await sync.sync_corpus(db_session) is True
    assert await sync.sync_corpus(db_session) is False

    rows = (await db_session.execute(select(DocsPage))).scalars().all()
    assert {r.path for r in rows} == {"a.md", "b.md"}
    meta = (await db_session.execute(select(DocsCorpusMeta))).scalar_one()
    assert meta.page_count == 2
    assert meta.app_version == settings.app_version


async def test_a_new_release_replaces_the_corpus_wholesale(db_session, monkeypatch):
    """A page removed from docs/ must leave search too. Diffing would leave the
    assistant citing a page its own build no longer ships."""
    _fake_corpus([_page("a.md"), _page("gone.md")], monkeypatch, hash_="old")
    await sync.sync_corpus(db_session)

    _fake_corpus([_page("a.md"), _page("new.md")], monkeypatch, hash_="new")
    assert await sync.sync_corpus(db_session) is True

    rows = (await db_session.execute(select(DocsPage))).scalars().all()
    assert {r.path for r in rows} == {"a.md", "new.md"}


async def test_a_deployment_without_docs_starts_anyway(db_session, monkeypatch):
    """A missing corpus must never stop a replica booting."""

    def absent():
        raise DocsUnavailableError("no docs here")

    monkeypatch.setattr(sync, "_corpus", absent)

    assert await sync.sync_corpus(db_session) is False
    assert (await db_session.execute(select(DocsPage))).scalars().all() == []


async def test_a_database_that_refuses_the_write_still_lets_the_replica_boot(
    db_session, monkeypatch
):
    """The other half of best-effort, and the half that takes the API down with
    it: this runs inside the lifespan, so an unapplied migration here means the
    replica does not start at all rather than starting without search."""
    _fake_corpus([_page("a.md")], monkeypatch)

    async def refuse(*args, **kwargs):
        raise OperationalError("no such table: docs_corpus_meta", None, Exception())

    monkeypatch.setattr(type(db_session), "execute", refuse)

    assert await sync.sync_corpus(db_session) is False
