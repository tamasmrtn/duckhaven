"""Idempotency tests for deploy/polaris-bootstrap.sh (BUG-6).

The wrapper runs the Polaris admin-tool bootstrap and must treat the
"realm already exists" exit (3) as success so a repeated `compose up` is a
no-op, while still propagating genuine failures.
"""

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "polaris-bootstrap.sh"


def _run_wrapper(inner_exit_code: int) -> int:
    """Invoke the wrapper around a stub command that exits with the given code."""
    result = subprocess.run(
        ["/bin/sh", str(SCRIPT), "sh", "-c", f"exit {inner_exit_code}"],
        check=False,
    )
    return result.returncode


def test_successful_bootstrap_succeeds():
    assert _run_wrapper(0) == 0


def test_realm_already_exists_is_treated_as_success():
    # Exit 3 = "realm already exists"; re-running must not block dependents.
    assert _run_wrapper(3) == 0


def test_genuine_failures_propagate():
    assert _run_wrapper(1) == 1
    assert _run_wrapper(2) == 2
