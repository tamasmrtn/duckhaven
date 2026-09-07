"""Generate the assistant's documentation index from ``docs/`` and ``mkdocs.yml``.

Run with ``make docs-index``. Emits two files from one source of truth:

- ``docs_index.yaml`` beside this module — what the assistant sees, and the only
  part of the corpus committed to the repository. The page *bodies* are never
  copied; they are read from ``docs/`` at runtime.
- ``docs/llms.txt`` — the same data in the llms.txt convention, replacing a
  hand-maintained file that had drifted to listing 31 of 69 pages.

Titles, sections and ordering come from ``mkdocs.yml``'s nav rather than from the
files, because the nav is what a reader actually sees. That does mean a page
absent from the nav is absent from the index, and mkdocs will not complain —
``validation.nav.omitted_files`` defaults to ``info``, which ``--strict`` does
not promote — so the drift test reads ``docs/`` directly rather than trusting
this walk. Summaries are derived from each page's opening paragraph and then
**preserved across regeneration**, so a hand-tuned one is never clobbered.

Deliberately records no version or commit: the index would then change on every
commit and the drift gate would fire constantly. The running version is stamped
at read time from ``settings.app_version`` instead.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from api.services.assistant.knowledge.loader import Page

# Contributor documentation answers questions about working *on* DuckHaven, not
# about using it, and the release notes date instantly. Excluding them keeps the
# resident index roughly 300 tokens smaller and stops the assistant citing the
# testing guide at someone asking about time travel.
EXCLUDED_PREFIXES = ("developer/", "release-notes/")
EXCLUDED_PATHS = frozenset({"index.md"})

INDEX_PATH = Path(__file__).with_name("docs_index.yaml")


def _repo_root() -> Path:
    """Walk up to the checkout. Only the generator and its tests need this; the
    running image reads the index from the package and the bodies from /app/docs."""
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "mkdocs.yml").is_file():
            return candidate
    raise RuntimeError("not inside a DuckHaven checkout: no mkdocs.yml found above this file")


_LINK = re.compile(r"\[([^\]]+)\]\([^)]*\)")
# Emphasis only where Markdown itself would see it: a delimiter flanked by word
# characters is part of an identifier, and `assistant_docs_dir` must not come out
# as "assistantdocsdir" in a documentation set full of snake_case settings.
_INLINE_MARKUP = re.compile(r"(?<!\w)([*_]{1,2})(?=\S)(.+?)(?<=\S)\1(?!\w)")
# A sentence ends at .!? followed by space — unless what precedes it is an
# abbreviation or an initial, which would cut "See e.g. the guide" to "See e.g."
_SENTENCE_END = re.compile(
    r"(?<![A-Z]\.)(?<!\be\.g\.)(?<!\bi\.e\.)(?<!\betc\.)(?<!\bvs\.)(?<=[.!?])\s+"
)
_SUMMARY_MAX = 180
# Lines that are structure rather than prose. Bullets are a marker *followed by
# space*: "**DuckHaven** is…" is a lead paragraph, not a list.
_SKIP_PREFIXES = ("#", "---", "!!!", "|", "<", ">", "```")
_BULLETS = ("- ", "* ", "+ ")


class _TagIgnoringLoader(yaml.SafeLoader):
    """Reads ``mkdocs.yml``, whose ``!!python/name:`` tags SafeLoader rejects.

    The tagged values are Material extension hooks; nothing here needs them, so
    they resolve to ``None`` rather than requiring an unsafe load of a file that
    can execute arbitrary imports.
    """


_TagIgnoringLoader.add_multi_constructor("tag:yaml.org,2002:python/", lambda *_: None)
_TagIgnoringLoader.add_multi_constructor("!", lambda *_: None)


def _is_indexed(path: str) -> bool:
    return path not in EXCLUDED_PATHS and not path.startswith(EXCLUDED_PREFIXES)


def summarise(body: str) -> str:
    """The page's opening paragraph, reduced to one plain-text line.

    A starting point for a human, not a finished description: ``merge`` keeps any
    summary already in the index, so editing one here is permanent.
    """
    paragraph: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not paragraph:
            # Skip everything before the lead prose: the H1, front matter, lists,
            # tables, code fences, and admonitions — including an admonition's
            # *body*, which is indented and would otherwise read as the lead.
            if (
                not stripped
                or line[:1].isspace()
                or stripped.startswith(_SKIP_PREFIXES)
                or stripped.startswith(_BULLETS)
            ):
                continue
        if not stripped:
            break
        paragraph.append(stripped)
    text = " ".join(paragraph)
    text = _LINK.sub(r"\1", text)
    text = _INLINE_MARKUP.sub(r"\2", text)
    text = text.replace("`", "").strip()
    if not text:
        return ""
    first = _SENTENCE_END.split(text)[0]
    if len(first) <= _SUMMARY_MAX:
        return first
    return text[:_SUMMARY_MAX].rsplit(" ", 1)[0].rstrip(",;:") + "…"


_H1 = re.compile(r"^#\s+(.+)$", re.MULTILINE)


def _walk(nav: list, section: str) -> list[tuple[str, str | None, str]]:
    """Flatten a nav to ``(path, title, section)``, at any nesting depth.

    Every shape MkDocs accepts, because the alternatives are worse than the few
    lines: a bare ``- guides/foo.md`` entry used to raise ``AttributeError`` from
    the middle of ``make docs-index``, and a third nav level was skipped whole,
    silently taking its pages out of the index.
    """
    found: list[tuple[str, str | None, str]] = []
    for entry in nav:
        if isinstance(entry, str):
            found.append((entry, None, section))
        elif isinstance(entry, dict):
            for title, value in entry.items():
                if isinstance(value, str):
                    found.append((value, title, section or title))
                elif isinstance(value, list):
                    found += _walk(value, section or title)
    return found


def discover(docs_dir: Path, mkdocs_yml: Path) -> list[Page]:
    config = yaml.load(mkdocs_yml.read_text(encoding="utf-8"), Loader=_TagIgnoringLoader)
    pages: list[Page] = []
    for path, title, section in _walk(config["nav"], ""):
        if not _is_indexed(path):
            continue
        body = (docs_dir / path).read_text(encoding="utf-8")
        # A bare nav entry carries no title; MkDocs falls back to the page's H1.
        heading = title or (m.group(1).strip() if (m := _H1.search(body)) else Path(path).stem)
        pages.append(Page(path=path, title=heading, section=section, summary=summarise(body)))
    return pages


def merge(discovered: list[Page], existing: dict) -> list[Page]:
    """Keep hand-edited summaries; take everything else from the docs tree."""
    kept = {p["path"]: p.get("summary", "") for p in existing.get("pages", [])}
    return [Page(p.path, p.title, p.section, kept.get(p.path) or p.summary) for p in discovered]


class _IndexDumper(yaml.SafeDumper):
    pass


_IndexDumper.add_representer(
    str,
    lambda dumper, data: dumper.represent_scalar(
        "tag:yaml.org,2002:str", data, style="|" if "\n" in data else None
    ),
)


def render_index(pages: list[Page], intro: str) -> str:
    payload = {
        "intro": intro,
        "pages": [
            {"path": p.path, "title": p.title, "section": p.section, "summary": p.summary}
            for p in pages
        ],
    }
    header = (
        "# Generated by `make docs-index` from docs/ and mkdocs.yml — do not add pages\n"
        "# by hand. Summaries ARE hand-editable: regeneration preserves them.\n"
    )
    return header + yaml.dump(
        payload,
        Dumper=_IndexDumper,
        sort_keys=False,
        allow_unicode=True,
        width=96,
        default_flow_style=False,
    )


def render_llms_txt(pages: list[Page], intro: str, site_url: str) -> str:
    out = ["# DuckHaven", ""]
    out += [f"> {line}" for line in intro.strip().splitlines()]
    section = None
    for page in pages:
        if page.section != section:
            section = page.section
            out += ["", f"## {section}", ""]
        url = f"{site_url.rstrip('/')}/{page.path.removesuffix('.md')}/"
        out.append(f"- [{page.title}]({url}): {page.summary}")
    return "\n".join(out) + "\n"


def main() -> None:
    root = _repo_root()
    docs_dir = root / "docs"
    mkdocs_yml = root / "mkdocs.yml"
    existing = yaml.safe_load(INDEX_PATH.read_text()) or {} if INDEX_PATH.exists() else {}
    intro = existing.get("intro") or ""
    pages = merge(discover(docs_dir, mkdocs_yml), existing)

    INDEX_PATH.write_text(render_index(pages, intro))
    site_url = yaml.load(mkdocs_yml.read_text(), Loader=_TagIgnoringLoader)["site_url"]
    (docs_dir / "llms.txt").write_text(render_llms_txt(pages, intro, site_url))
    print(f"{INDEX_PATH.relative_to(root)}: {len(pages)} pages")


if __name__ == "__main__":
    main()
