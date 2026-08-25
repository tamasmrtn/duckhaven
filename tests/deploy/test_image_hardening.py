"""Hardening invariants in the published container images.

Both runtime images drop `pip` and purge `perl-base`, then run as the
unprivileged `duckhaven` user. The purge is the load-bearing one: `perl-base` is
marked Essential by Debian and carries eight unfixable CRITICAL/HIGH CVEs that
nothing in either image actually needs, so a well-meaning cleanup of the "weird"
force flags would silently reintroduce them.

Scope note: this asserts the *Dockerfiles*, not the built images. Whether the
purge really removes those CVEs is measured by the weekly Trivy scan in
`.github/workflows/security.yml`; what this guards is that the instructions
never quietly disappear from the build.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

DOCKERFILES = {
    "api": (ROOT / "api" / "Dockerfile").read_text(),
    "agent": (ROOT / "agent" / "Dockerfile").read_text(),
}


def _runtime_stage(text: str) -> str:
    """The final stage: everything after the last `FROM`."""
    return text[text.rindex("FROM ") :]


def test_pip_is_dropped_from_both_runtimes():
    for name, text in DOCKERFILES.items():
        assert "pip uninstall -y pip" in _runtime_stage(text), name


def test_perl_base_is_purged_from_both_runtimes():
    for name, text in DOCKERFILES.items():
        stage = _runtime_stage(text)
        assert "--purge" in stage and "perl-base" in stage, name
        # Essential packages need the force flag; without it dpkg refuses.
        assert "--force-remove-essential" in stage, name


def test_both_runtimes_run_as_the_duckhaven_user():
    for name, text in DOCKERFILES.items():
        stage = _runtime_stage(text)
        assert "useradd" in stage and "duckhaven" in stage, name
        assert "USER duckhaven" in stage, name


def test_no_compiler_toolchain_in_the_runtime_stages():
    """The runtime stage copies a built venv; it must never gain a toolchain."""
    for name, text in DOCKERFILES.items():
        stage = _runtime_stage(text)
        for pkg in ("build-essential", "gcc", "g++", "cmake", "ninja"):
            assert pkg not in stage, f"{name}: {pkg}"


def test_dockerignore_excludes_large_local_only_directories():
    """`.gitignore` does not feed the build context; BuildKit walks the tree.

    `benchmarks/` alone is ~10 GB, and leaving it in the context stalls any
    build started from the repository root.
    """
    entries = {
        line.strip()
        for line in (ROOT / ".dockerignore").read_text().splitlines()
        if line.strip() and not line.startswith("#")
    }
    for required in ("benchmarks", "deploy/terraform"):
        assert required in entries, required
