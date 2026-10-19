"""Read the documentation index, and read pages out of the docs tree.

The index and the page bodies travel by different routes: the index is committed
inside this package and ships in the wheel, the bodies are the real ``docs/``
tree copied into the image. So they can go missing independently, and both are
pinned to the release that built the image — an assistant answering from newer
documentation than its own code would describe features it does not have.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from functools import cached_property, lru_cache
from pathlib import Path

import yaml

from api.config import settings

INDEX_PATH = Path(__file__).with_name("docs_index.yaml")


@dataclass(frozen=True)
class Page:
    path: str
    title: str
    section: str
    summary: str


@dataclass(frozen=True)
class DocsIndex:
    pages: tuple[Page, ...]
    # Old path -> current path, from the site's redirect map. A page that moved
    # keeps answering to its old path, because old paths outlive the move: in
    # stored conversations' citations, and in a model's guess.
    aliases: dict[str, str] = field(default_factory=dict)

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(p.path for p in self.pages)

    def get(self, path: str) -> Page | None:
        return next((p for p in self.pages if p.path == path), None)

    def resolve(self, path: str) -> Page | None:
        """The page at ``path``, or the page it moved to."""
        return self.get(path) or self.get(self.aliases.get(path, ""))

    def nearest(self, path: str, n: int = 3) -> list[str]:
        """Indexed paths closest to a miss, so a wrong guess can be corrected.

        Old paths count as candidates too, answered with where the page lives
        now: a model that learned the docs before a move guesses in the old
        layout, and its nearest old path is the best clue to the page it means.
        """
        candidates = self.paths + tuple(self.aliases)
        found: list[str] = []
        for match in difflib.get_close_matches(path, candidates, n=2 * n, cutoff=0.3):
            target = self.aliases.get(match, match)
            if target not in found:
                found.append(target)
        return found[:n]

    @cached_property
    def prompt_block(self) -> str:
        """The always-resident index: section, path and title, nothing more.

        Summaries are deliberately excluded. They are the useful half of a search
        *result*, but resident they would cost roughly 2,500 tokens instead of
        750 — and the model does not need to know what a page says in order to
        decide it is worth opening.
        """
        lines: list[str] = []
        section = None
        for page in self.pages:
            if page.section != section:
                section = page.section
                lines.append(f"  {section}:")
            lines.append(f"    {page.path} — {page.title}")
        return "\n".join(lines)


@lru_cache(maxsize=1)
def load_index() -> DocsIndex:
    raw = yaml.safe_load(INDEX_PATH.read_text(encoding="utf-8")) or {}
    pages = raw.get("pages") or []
    if not pages:
        # A file that parses but lists nothing would otherwise render an index
        # block that names no pages, leaving the model told to call read_doc_page
        # with "one of these exact paths:" and then shown none.
        raise DocsUnavailableError(f"Documentation index is empty or malformed: {INDEX_PATH}")
    return DocsIndex(
        pages=tuple(Page(p["path"], p["title"], p["section"], p.get("summary", "")) for p in pages),
        aliases=dict(raw.get("aliases") or {}),
    )


_FRONT_MATTER = re.compile(r"\A---\n.*?\n---\n", re.DOTALL)
_SNIPPET = re.compile(r'^([ \t]*)--8<--[ \t]+"([^"]+)"[ \t]*$', re.MULTILINE)
SNIPPETS_DIR = "_snippets"


def strip_front_matter(text: str) -> str:
    """A page's body without its YAML header (``type: how-to`` and the like).

    The header is for the site and the docs checks; to a model it is noise, and
    to the summariser it would read as the page's opening line.
    """
    return _FRONT_MATTER.sub("", text, count=1)


def expand_snippets(text: str, root: Path) -> str:
    """Inline ``--8<-- "name.md"`` includes the way MkDocs renders them.

    MkDocs expands these at build time, but the assistant reads the Markdown
    source, where an unexpanded include is a line of punctuation in place of the
    fact it stands for. An include that does not resolve inside the snippets
    directory is left as written rather than read from elsewhere.
    """
    base = (root / SNIPPETS_DIR).resolve()

    def include(match: re.Match) -> str:
        target = (base / match.group(2)).resolve()
        if not target.is_relative_to(base) or not target.is_file():
            return match.group(0)
        indent = match.group(1)
        body = target.read_text(encoding="utf-8").rstrip("\n")
        return "\n".join(indent + line if line else line for line in body.splitlines())

    return _SNIPPET.sub(include, text)


def page_body(text: str, root: Path) -> str:
    """What a reader of the rendered page sees, as Markdown."""
    return expand_snippets(strip_front_matter(text), root)


class DocsUnavailableError(RuntimeError):
    """The documentation is not usable in this deployment."""


class PageNotIndexed(LookupError):
    """The requested path is not in the index — a model guess, not a fault."""


def docs_available() -> bool:
    """Whether both halves of the corpus are present: the index and the bodies."""
    try:
        load_index()
    except Exception:  # noqa: BLE001 — any unreadable index means "not available"
        return False
    return settings.assistant_docs_dir.is_dir()


def docs_dir() -> Path:
    directory = settings.assistant_docs_dir
    if not directory.is_dir():
        raise DocsUnavailableError("Documentation is not available in this deployment.")
    return directory


def read_page(path: str) -> dict:
    """Return one indexed page's full Markdown, truncated to the configured cap.

    ``path`` must be in the index. That is the security boundary as much as the
    usability one: the index is a fixed allowlist, so no traversal, symlink or
    absolute path can reach a file outside the docs tree even if the model is
    talked into asking for one.
    """
    page = load_index().resolve(path)
    if page is None:
        raise PageNotIndexed(path)
    path = page.path

    root = docs_dir().resolve()
    resolved = (root / path).resolve()
    # Belt and braces: the index is the allowlist, but a symlink inside docs/
    # could still point out of the tree.
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise DocsUnavailableError(f"Documentation page is missing from this build: {path}")

    text = page_body(resolved.read_text(encoding="utf-8"), root)
    cap = settings.assistant_docs_max_page_chars
    truncated = len(text) > cap
    if truncated:
        kept = text[:cap].rsplit("\n", 1)[0]
        # Say how much is missing, in the model's own units. A bare "[truncated]"
        # reads as a formality on a page that otherwise looks complete, and the
        # honest-but-wrong answer that follows is "the documentation does not
        # cover that" — from a page where it does, further down.
        withheld = len(text) - len(kept)
        text = (
            f"{kept}\n\n[This page was cut off here: {withheld:,} of {len(text):,} characters "
            f"are not shown, including everything after this point. If the answer is not "
            f"above, say the page continues beyond what you can read and point the user at "
            f"{page_url(path)} — do not conclude the documentation is silent.]"
        )
    return {
        "path": path,
        "title": page.title,
        "text": text,
        "version": settings.app_version,
        "truncated": truncated,
    }


def page_url(path: str) -> str:
    return f"{settings.docs_site_url.rstrip('/')}/{path.removesuffix('.md')}/"
