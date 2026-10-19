"""The docs follow the page-type templates in docs/developer/writing-docs.md.

A page declares its type in front matter (``type: how-to``). Each type has a
shape a reader can rely on — a how-to always says what you need before you
start and what to do when a step fails — and these tests hold typed pages to
it. Untyped pages are not checked until their subject folder is rewritten and
added to ``MIGRATED_DIRS``, which turns the type into a requirement there.

Two more invariants protect page moves: every redirect points from a path that
is gone to a page that exists, and nothing outside ``docs/`` still names a moved
page — the code comments, compose files and README links that ``mkdocs build``
never sees.
"""

import re
from pathlib import Path

import pytest
import yaml

from api.services.assistant.knowledge import generate

REPO_ROOT = generate._repo_root()
DOCS_DIR = REPO_ROOT / "docs"

DOC_TYPES = frozenset({"tutorial", "how-to", "concept", "reference", "troubleshooting"})
REQUIRED_H2 = {
    "tutorial": ("Before you begin", "Next steps"),
    "how-to": ("Before you begin", "If it goes wrong"),
}
# Generated in duckhaven-clients from the CLI's command tree; edits are overwritten.
GENERATED = frozenset({"reference/cli.md"})
# Subject folders whose pages have been rewritten to the templates. Every page in
# one must declare a type. Emptied and removed once every folder is migrated.
MIGRATED_DIRS: frozenset[str] = frozenset({"getting-started", "access", "querying"})

# Where a moved page's old path could still be named, outside the docs tree.
REFERENCE_ROOTS = (
    "api/src",
    "web/src",
    "web/tests",
    "deploy",
    "tests",
    "agent",
    "README.md",
    "Makefile",
    "CLAUDE.md",
)
# The generated index records old paths as aliases on purpose.
REFERENCE_EXEMPT = frozenset({"api/src/api/services/assistant/knowledge/docs_index.yaml"})
SKIP_DIRS = frozenset(
    {"node_modules", "dist", "__pycache__", ".venv", "htmlcov", "test-results", "playwright-report"}
)

_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_FENCE = re.compile(r"^(```|~~~)")
_NOT_PROSE = ("!!!", "???", "|", "- ", "* ", "+ ", "```", "<", ">", "#")


def front_matter(text: str) -> dict:
    match = _FRONT_MATTER.match(text)
    return (yaml.safe_load(match.group(1)) or {}) if match else {}


def _body(text: str) -> str:
    return _FRONT_MATTER.sub("", text, count=1)


def _outside_fences(text: str) -> list[str]:
    lines, fenced = [], False
    for line in text.splitlines():
        if _FENCE.match(line.strip()):
            fenced = not fenced
            continue
        if not fenced:
            lines.append(line)
    return lines


def _sections(lines: list[str]) -> list[tuple[str, str]]:
    """``(heading, body)`` for every H2."""
    sections: list[tuple[str, list[str]]] = []
    for line in lines:
        if line.startswith("## "):
            sections.append((line[3:].strip(), []))
        elif sections:
            sections[-1][1].append(line)
    return [(heading, "\n".join(body)) for heading, body in sections]


def problems(text: str) -> list[str]:
    """What a typed page gets wrong about its template; empty when it conforms."""
    doc_type = front_matter(text).get("type")
    if doc_type is None:
        return []
    if doc_type not in DOC_TYPES:
        return [f"type {doc_type!r} is not one of {sorted(DOC_TYPES)}"]

    found: list[str] = []
    lines = _outside_fences(_body(text))
    h1s = [i for i, line in enumerate(lines) if line.startswith("# ")]
    if len(h1s) != 1:
        found.append(f"has {len(h1s)} H1 headings; a page has exactly one")
    else:
        lead = next((line.strip() for line in lines[h1s[0] + 1 :] if line.strip()), "")
        if not lead or lead.startswith(_NOT_PROSE):
            found.append(
                "the first block after the H1 must be a prose paragraph"
                " that answers the page's question"
            )
        else:
            summary = generate.summarise(_body(text))
            if summary.endswith("…"):
                found.append(
                    f"the lead sentence is over {generate._SUMMARY_MAX} characters,"
                    " so its summary is cut off"
                )

    sections = _sections(lines)
    headings = {heading.lower() for heading, _ in sections}
    for required in REQUIRED_H2.get(doc_type, ()):
        if required.lower() not in headings:
            found.append(f"a {doc_type} needs a '## {required}' section")
    if doc_type == "troubleshooting":
        for heading, body in sections:
            if heading.lower() == "related":
                continue
            for part in ("What it means", "What to do"):
                if part not in body:
                    found.append(f"symptom '{heading}' is missing '{part}'")
    return found


def _pages() -> list[str]:
    # Underscored files and folders (docs/_snippets/) are fragments, not pages.
    paths = (p.relative_to(DOCS_DIR) for p in DOCS_DIR.rglob("*.md"))
    return sorted(str(p) for p in paths if not any(part.startswith("_") for part in p.parts))


def _redirect_maps(root: Path = REPO_ROOT) -> dict[str, str]:
    config = yaml.load(
        (root / "mkdocs.yml").read_text(encoding="utf-8"), Loader=generate._TagIgnoringLoader
    )
    for plugin in config.get("plugins", []):
        if isinstance(plugin, dict) and "redirects" in plugin:
            return (plugin["redirects"] or {}).get("redirect_maps") or {}
    return {}


def _reference_files():
    for root in REFERENCE_ROOTS:
        base = REPO_ROOT / root
        candidates = [base] if base.is_file() else base.rglob("*")
        for path in candidates:
            if not path.is_file() or SKIP_DIRS.intersection(path.parts):
                continue
            rel = str(path.relative_to(REPO_ROOT))
            if rel in REFERENCE_EXEMPT:
                continue
            try:
                yield rel, path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue


# ── The real docs tree ────────────────────────────────────────────────────────


@pytest.mark.parametrize("path", [p for p in _pages() if p not in GENERATED])
def test_typed_pages_follow_their_template(path):
    assert problems((DOCS_DIR / path).read_text(encoding="utf-8")) == []


def test_pages_in_migrated_folders_declare_a_type():
    untyped = [
        path
        for path in _pages()
        if path.split("/", 1)[0] in MIGRATED_DIRS
        and "type" not in front_matter((DOCS_DIR / path).read_text(encoding="utf-8"))
    ]
    assert untyped == []


def test_every_redirect_leaves_a_removed_page_for_an_existing_one():
    maps = _redirect_maps()
    pages = set(_pages())
    assert [src for src in maps if src in pages] == [], (
        "a redirect source still exists, so it shadows a real page"
    )
    missing = [
        dst
        for dst in maps.values()
        if not dst.startswith("http") and dst.split("#")[0] not in pages
    ]
    assert missing == [], "a redirect points at a page that does not exist"
    assert [dst for dst in maps.values() if dst.split("#")[0] in maps] == [], (
        "redirects must not chain"
    )


def test_nothing_outside_docs_names_a_moved_page():
    needles = {old: (old, old.removesuffix(".md") + "/") for old in _redirect_maps()}
    stale = [
        f"{rel} names {old}"
        for rel, text in _reference_files()
        for old, forms in needles.items()
        if any(form in text for form in forms)
    ]
    assert stale == []


# ── The checker itself ────────────────────────────────────────────────────────

GOOD_HOW_TO = """---
type: how-to
---

# Rotate a token

Rotate a service-account token without downtime by issuing a new one before you revoke the old.

## Before you begin

You need `service_accounts:manage`.

## If it goes wrong

`401 Unauthorized` means the client still sends the old token.
"""


def test_an_untyped_page_is_not_checked():
    assert problems("No heading, no structure.") == []


def test_a_conforming_how_to_passes():
    assert problems(GOOD_HOW_TO) == []


def test_an_unknown_type_is_named():
    assert "is not one of" in problems(GOOD_HOW_TO.replace("how-to", "guide"))[0]


def test_a_missing_recovery_section_is_flagged():
    page = GOOD_HOW_TO.split("## If it goes wrong")[0]
    assert problems(page) == ["a how-to needs a '## If it goes wrong' section"]


def test_a_lead_that_is_not_prose_is_flagged():
    page = GOOD_HOW_TO.replace(
        "Rotate a service-account token", '!!! note "Hi"\n    Rotate a service-account token'
    )
    assert any("first block after the H1" in p for p in problems(page))


def test_a_lead_too_long_to_summarise_is_flagged():
    page = GOOD_HOW_TO.replace("without downtime", "without downtime " + "and keep going " * 15)
    assert any("over 180 characters" in p for p in problems(page))


def test_headings_inside_code_fences_do_not_count():
    page = GOOD_HOW_TO.replace("## Before you begin", "```sh\n# Rotate\n```\n\n## Before you begin")
    assert problems(page) == []


def test_a_troubleshooting_entry_needs_meaning_and_action():
    page = """---
type: troubleshooting
---

# Troubleshoot queries

Find the message you see, then follow what to do.

## Agent not connected

**What it means.** The agent dropped its connection.

## Related

- Nothing.
"""
    assert problems(page) == ["symptom 'Agent not connected' is missing 'What to do'"]


def test_redirect_maps_are_read_from_the_plugin_block(tmp_path):
    (tmp_path / "mkdocs.yml").write_text(
        "plugins:\n  - search\n  - redirects:\n      redirect_maps:\n        old.md: new.md\n",
        encoding="utf-8",
    )
    assert _redirect_maps(tmp_path) == {"old.md": "new.md"}


def test_front_matter_tolerates_its_absence():
    assert front_matter("# Title\n\nText.") == {}
    assert front_matter("---\ntype: concept\n---\n# T\n") == {"type": "concept"}
