"""Print the agent runtimes CI builds, as a GitHub Actions matrix.

    python scripts/runtime_matrix.py
    {"include": [{"runtime": "1.5", "default": true}]}

Every runtime that isn't retired gets an image; the default one also gets the
unsuffixed tags (`latest`, `1.4.0`) that pre-runtime deployments pull. Read from
duckhaven_shared.runtimes so the build matrix cannot drift from the manifest the
agent and API use. Standard library only, so it runs on a bare CI runner.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "shared" / "src"))

from duckhaven_shared.runtimes import DEFAULT_RUNTIME_ID, RUNTIMES  # noqa: E402

print(
    json.dumps(
        {
            "include": [
                {"runtime": r.id, "default": r.id == DEFAULT_RUNTIME_ID}
                for r in RUNTIMES.values()
                if r.status != "retired"
            ]
        }
    )
)
