"""The assistant's eval harness: cases, metrics, and the runners that score them.

Tier 1 lives here and runs free on every pull request. The judged tier (tier 2)
is env-gated and costs money; see docs/developer/testing.md.

Importing this package loads the repository's ``.env``. The application reads it
through pydantic-settings, but the eval-only variables — the API key, the judge
model, the judge endpoint — are read with ``os.getenv`` and by the Makefile, and
neither sees a file pydantic parsed into a settings object. Without this, putting
them in ``.env`` looks like it should work and instead produces either a refusal
or a run silently scored against the wrong provider.

Nothing already in the environment is overridden: an explicit ``export`` still
wins over the file, which is what anyone would expect.
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

    # settings was constructed at import, before the file was read, so a value
    # that arrives this late has to be applied to the object rather than the
    # environment. Only the key: the model and endpoint belong to the arm.
    key = os.environ.get("ASSISTANT_EVAL_API_KEY")
    if key and not settings.assistant_api_key:
        settings.assistant_api_key = key

    # Point at the checkout's docs. The default is the image path /app/docs,
    # which does not exist here — and the failure is silent rather than loud:
    # the corpus load is best-effort by design, so search would simply return
    # nothing and the documented arm would be scored with no documentation.
    if (root / "docs").is_dir():
        settings.assistant_docs_dir = root / "docs"


_load_dotenv()
