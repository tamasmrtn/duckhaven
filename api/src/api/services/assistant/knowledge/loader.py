"""Read the documentation index, and read pages out of the docs tree.

Two separate things travel by two separate routes, on purpose. The **index** is a
committed artefact inside this package, so it ships in the wheel and is reviewable
in a diff. The **page bodies** are the real ``docs/`` tree, copied into the image
by the Dockerfile — never duplicated into the repository, which is what keeps them
from drifting the way ``llms.txt`` did.

The snapshot is pinned to the release that built the image. An assistant that
answered from newer documentation than its own code would describe features it
does not have, which is the sharpest failure this whole feature risks.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from functools import lru_cache
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
    intro: str
    pages: tuple[Page, ...]

    @property
    def paths(self) -> tuple[str, ...]:
        return tuple(p.path for p in self.pages)

    def get(self, path: str) -> Page | None:
        return next((p for p in self.pages if p.path == path), None)

    def nearest(self, path: str, n: int = 3) -> list[str]:
        """Indexed paths closest to a miss, so a wrong guess can be corrected."""
        return difflib.get_close_matches(path, self.paths, n=n, cutoff=0.3)

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
    raw = yaml.safe_load(INDEX_PATH.read_text()) or {}
    return DocsIndex(
        intro=raw.get("intro", ""),
        pages=tuple(
            Page(p["path"], p["title"], p["section"], p.get("summary", ""))
            for p in raw.get("pages", [])
        ),
    )


class DocsUnavailableError(RuntimeError):
    """The docs tree is not present in this deployment."""


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
    page = load_index().get(path)
    if page is None:
        raise KeyError(path)

    resolved = (docs_dir() / path).resolve()
    # Belt and braces: the index is the allowlist, but a symlink inside docs/
    # could still point out of the tree, and a page that escapes is not a page.
    if not resolved.is_relative_to(docs_dir().resolve()) or not resolved.is_file():
        raise DocsUnavailableError(f"Documentation page is missing from this build: {path}")

    text = resolved.read_text()
    cap = settings.assistant_docs_max_page_chars
    truncated = len(text) > cap
    if truncated:
        text = text[:cap].rsplit("\n", 1)[0] + f"\n\n[truncated — full page at {page_url(path)}]"
    return {
        "path": path,
        "title": page.title,
        "text": text,
        "version": settings.app_version,
        "truncated": truncated,
    }


def page_url(path: str) -> str:
    """The published URL for a page, at the version this build shipped with."""
    return f"{settings.docs_site_url.rstrip('/')}/{path.removesuffix('.md')}/"
