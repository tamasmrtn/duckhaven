"""Hardening invariants in the published container images.

Both images build on a Chainguard `-dev` stage (which has a shell) and ship a
**distroless** runtime that does not: no shell, no package manager, no apt/dpkg,
no pip. That is the hardening posture, and it replaces the one these tests used
to guard — dropping `pip` and force-purging Debian's Essential `perl-base` to be
rid of unfixable CVEs. Those steps are gone because there is nothing left to
remove, not because anyone relaxed the standard.

What the tests protect is that the property keeps holding. Reintroducing a
`RUN` in the runtime stage cannot work (there is no shell to run it), so the
realistic regressions are the quiet ones: a runtime stage that slips back to the
`-dev` base to make some build step convenient, an unpinned `:latest` that moves
under the deployment, or a `USER` line dropped so the process runs as root.

Scope note: this asserts the *Dockerfiles*, not the built images. Whether the
runtime is genuinely free of those CVEs is measured by the weekly Trivy scan in
`.github/workflows/security.yml`; what this guards is that the build never
quietly stops being distroless, pinned, and non-root.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

DOCKERFILES = {
    "api": (ROOT / "api" / "Dockerfile").read_text(),
    "agent": (ROOT / "agent" / "Dockerfile").read_text(),
}

#: The uid:gid the Chainguard images' `nonroot` user has. Numeric rather than a
#: name because there is no shell to add a named user with, and no /etc/passwd
#: entry to resolve one against.
NONROOT = "65532:65532"


def _runtime_stage(text: str) -> str:
    """The final stage: everything after the last `FROM`."""
    return text[text.rindex("FROM ") :]


def _runtime_base(text: str) -> str:
    """The final stage's `FROM` line."""
    return _runtime_stage(text).splitlines()[0]


def test_both_runtimes_are_distroless():
    """The runtime must not fall back to the `-dev` base, which ships a shell.

    A shell in the runtime is what made the old pip/perl-base purge necessary in
    the first place; keeping it out is the whole reason those steps could go.
    """
    for name, text in DOCKERFILES.items():
        base = _runtime_base(text)
        assert "cgr.dev/chainguard/python" in base, name
        assert "-dev" not in base, f"{name}: runtime stage is on the -dev base"


def test_runtime_stages_run_no_commands():
    """No `RUN` in the runtime stage — there is no shell to execute one.

    Asserted rather than left implicit because it is the structural reason this
    file no longer checks for cleanup commands: anything that would need to run
    has to happen in the builder and arrive by `COPY --from`.
    """
    for name, text in DOCKERFILES.items():
        offenders = [
            line for line in _runtime_stage(text).splitlines() if line.strip().startswith("RUN ")
        ]
        assert not offenders, f"{name}: {offenders}"


def test_both_runtime_bases_are_pinned_by_digest():
    """`:latest` rebuilds continuously, so only a digest is a fixed input.

    Dependabot moves these; an unpinned tag would let the base change under a
    deployment between two builds of the same commit.
    """
    for name, text in DOCKERFILES.items():
        assert "@sha256:" in _runtime_base(text), name


def test_both_runtimes_run_as_a_non_root_user():
    for name, text in DOCKERFILES.items():
        stage = _runtime_stage(text)
        assert f"USER {NONROOT}" in stage, name
        assert "USER root" not in stage, name


def test_everything_copied_into_the_runtime_is_owned_by_the_non_root_user():
    """A root-owned file in a non-root runtime is unwritable at best.

    The runtime has no shell to `chown` with, so ownership has to be declared on
    every `COPY`; one that forgets is only discovered when something tries to
    write there.
    """
    for name, text in DOCKERFILES.items():
        for line in _runtime_stage(text).splitlines():
            if line.strip().startswith("COPY "):
                assert f"--chown={NONROOT}" in line, f"{name}: {line.strip()}"


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
