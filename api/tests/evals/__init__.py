"""The assistant's eval harness: cases, metrics, and the runners that score them.

Tier 1 runs free on every pull request; the judged tier costs money and is
env-gated. See docs/developer/testing.md.

Importing this package loads ``.env``. The eval-only variables are read with
``os.getenv`` and by the Makefile, neither of which sees a file pydantic parsed
into a settings object — so without this, putting them in ``.env`` looks like it
should work and instead scores against the wrong provider. An explicit export
still wins.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import dotenv_values

from api.config import settings


def _load_dotenv() -> None:
    root = next((p for p in Path(__file__).resolve().parents if (p / "mkdocs.yml").is_file()), None)
    if root is None:
        return
    for key, value in dotenv_values(root / ".env").items():
        if value is not None and key not in os.environ:
            os.environ[key] = value

    # settings was constructed before the file was read, so a value arriving now
    # has to be applied to the object. Only the key; the rest belongs to the arm.
    key = os.environ.get("ASSISTANT_EVAL_API_KEY")
    if key and not settings.assistant_api_key:
        settings.assistant_api_key = key

    # The default is the image path /app/docs. Missing it fails silently, since
    # the corpus load is best-effort: search returns nothing and the documented
    # arm is scored as if it had no documentation.
    if (root / "docs").is_dir():
        settings.assistant_docs_dir = root / "docs"


_load_dotenv()
