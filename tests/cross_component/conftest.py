"""Cross-component (Layer 2) harness: real API + real agent over the live
WebSocket control channel, against real Postgres + Polaris + MinIO.

Unlike the per-service integration suites this boots both processes for real:

- the API via ``uvicorn api.main:app`` (the *outer* app, so the agent can dial
  ``/agents/connect`` and the API can call back to the agent's result server),
- the agent via ``python -m agent.main``, dialing the live API.

A live uvicorn server (not ASGI transport) is mandatory here: ASGI transport
cannot host a real cross-process WebSocket nor the reverse HTTP fetch
(`proxy_rows`). The whole suite is env-gated and skips cleanly without
``DATABASE_URL`` / ``POLARIS_BASE_URL`` / ``POLARIS_S3_BUCKET``.

Orchestration/readiness uses a synchronous client (simpler than session-scoped
async fixtures); the tests themselves use ``httpx.AsyncClient``.
"""

from __future__ import annotations

import os
import socket
import subprocess
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

REPO_ROOT = Path(__file__).resolve().parents[2]

BOOTSTRAP_TOKEN = "dh_xc_bootstrap_token"
SETUP_TOKEN = "dh_xc_setup_token"
ADMIN_EMAIL = "admin@xc.test"
ADMIN_PASSWORD = "xc-password-123"

_STARTUP_TIMEOUT_S = 90.0


@dataclass
class Stack:
    base_url: str  # http://127.0.0.1:<port> (the /api prefix is added by callers)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_until(predicate, timeout: float, what: str, proc: subprocess.Popen, log: Path) -> None:
    """Poll ``predicate`` until truthy or timeout; on failure include the
    subprocess's captured output so CI logs explain the startup failure."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"{what}: process exited early\n{log.read_text()[-4000:]}")
        try:
            if predicate():
                return
        except Exception:  # noqa: BLE001 - not ready yet
            pass
        time.sleep(0.5)
    raise RuntimeError(f"{what}: not ready within {timeout}s\n{log.read_text()[-4000:]}")


def _api_env(db_url: str, setup_token_file: Path) -> dict[str, str]:
    env = os.environ.copy()
    bucket = os.environ["POLARIS_S3_BUCKET"]
    env.update(
        {
            "DATABASE_URL": db_url,
            "POLARIS_BASE_URL": os.environ["POLARIS_BASE_URL"],
            "POLARIS_REALM": os.getenv("POLARIS_REALM", "POLARIS"),
            "POLARIS_CLIENT_ID": os.getenv("POLARIS_CLIENT_ID", "root"),
            "POLARIS_CLIENT_SECRET": os.getenv("POLARIS_CLIENT_SECRET", "s3cr3t"),
            "S3_BUCKET": bucket.split("://", 1)[-1].strip("/"),
            "S3_REGION": os.getenv("POLARIS_S3_REGION", "us-east-1"),
            "AGENT_BOOTSTRAP_TOKEN": BOOTSTRAP_TOKEN,
            "SETUP_TOKEN_PATH": str(setup_token_file),
            "SECRET_KEY": "xc-test-secret",
            "COOKIE_SECURE": "false",
            # Exercise the SQL session layer end to end (off by default in prod).
            "SQL_SESSIONS_ENABLED": "true",
        }
    )
    if endpoint := os.getenv("POLARIS_S3_ENDPOINT"):
        env["S3_ENDPOINT"] = endpoint
    if internal := os.getenv("POLARIS_S3_ENDPOINT_INTERNAL"):
        env["S3_ENDPOINT_INTERNAL"] = internal
    return env


def _agent_env(api_port: int, results_dir: Path, result_port: int) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "CONTROL_PLANE_URL": f"ws://127.0.0.1:{api_port}/agents/connect",
            "BOOTSTRAP_TOKEN": BOOTSTRAP_TOKEN,
            "POLARIS_BASE_URL": os.environ["POLARIS_BASE_URL"],
            "POLARIS_CLIENT_ID": os.getenv("POLARIS_CLIENT_ID", "root"),
            "POLARIS_CLIENT_SECRET": os.getenv("POLARIS_CLIENT_SECRET", "s3cr3t"),
            "RESULTS_DIR": str(results_dir),
            "RESULTS_HTTP_HOST": "127.0.0.1",
            "RESULTS_HTTP_PORT": str(result_port),
        }
    )
    return env


@pytest.fixture(scope="session", autouse=True)
def _require_env() -> None:
    missing = [
        v for v in ("DATABASE_URL", "POLARIS_BASE_URL", "POLARIS_S3_BUCKET") if not os.getenv(v)
    ]
    if missing:
        pytest.skip(f"cross-component tests need {', '.join(missing)}; skipping")


def _preinstall_agent_extensions() -> None:
    """Install the DuckDB extensions the agent advertises into the shared
    ``~/.duckdb`` cache, mirroring what the agent Docker image bakes in at build
    time. The agent here runs from the uv env (not the image), and its capability
    probe only ``LOAD``s (relying on pre-installed extensions), so without this
    ``httpfs`` is never advertised and dispatch is rejected as agent_incompatible.
    """
    subprocess.run(
        [
            "uv",
            "run",
            "--package",
            "duckhaven-agent",
            "python",
            "-c",
            "import duckdb; c = duckdb.connect(); "
            "[c.execute(f'INSTALL {e}') for e in ('httpfs', 'azure', 'iceberg')]; "
            "[c.execute(f'LOAD {e}') for e in ('httpfs', 'azure', 'iceberg')]",
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
    )


@pytest.fixture(scope="session")
def stack(_require_env, tmp_path_factory) -> Iterator[Stack]:
    """Migrate the DB, boot the API + agent subprocesses, create the first
    admin, and wait for the agent to register healthy. Tears both down."""
    tmp = tmp_path_factory.mktemp("xc")
    db_url = os.environ["DATABASE_URL"]
    setup_token_file = tmp / "setup_token"
    setup_token_file.write_text(SETUP_TOKEN)

    api_env = _api_env(db_url, setup_token_file)

    # Pre-install agent DuckDB extensions (the image bakes these in; the uv-run
    # agent needs them cached so its LOAD-only capability probe advertises httpfs).
    _preinstall_agent_extensions()

    # 1. Apply migrations to the target database.
    subprocess.run(
        [
            "uv",
            "run",
            "--package",
            "duckhaven-api",
            "alembic",
            "-c",
            "api/alembic.ini",
            "upgrade",
            "head",
        ],
        cwd=REPO_ROOT,
        env=api_env,
        check=True,
        capture_output=True,
    )

    api_port = _free_port()
    api_log = tmp / "api.log"
    agent_log = tmp / "agent.log"
    procs: list[subprocess.Popen] = []
    base_url = f"http://127.0.0.1:{api_port}"
    try:
        # 2. Start the API (outer app: REST under /api + agent WS at /agents/connect).
        with api_log.open("w") as fh:
            api = subprocess.Popen(
                [
                    "uv",
                    "run",
                    "--package",
                    "duckhaven-api",
                    "uvicorn",
                    "api.main:app",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(api_port),
                ],
                cwd=REPO_ROOT,
                env=api_env,
                stdout=fh,
                stderr=subprocess.STDOUT,
            )
        procs.append(api)
        _wait_until(
            lambda: httpx.get(f"{base_url}/api/healthz", timeout=2.0).status_code == 200,
            _STARTUP_TIMEOUT_S,
            "api",
            api,
            api_log,
        )

        # 3. Create the first admin (consumes the setup token). 409 = an admin
        #    already exists (a reused database from a prior local run) — fine,
        #    we log in with the same credentials below. CI gets a fresh DB (200).
        with httpx.Client(base_url=base_url, timeout=10.0) as c:
            r = c.post(
                "/api/setup/admin",
                json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD, "name": "XC Admin"},
                headers={"X-Setup-Token": SETUP_TOKEN},
            )
            if r.status_code not in (200, 409):
                r.raise_for_status()

        # 4. Start the agent and wait until the API reports it healthy.
        results_dir = tmp / "agent-results"
        results_dir.mkdir()
        result_port = _free_port()
        with agent_log.open("w") as fh:
            agent = subprocess.Popen(
                ["uv", "run", "--package", "duckhaven-agent", "python", "-m", "agent.main"],
                cwd=REPO_ROOT,
                env=_agent_env(api_port, results_dir, result_port),
                stdout=fh,
                stderr=subprocess.STDOUT,
            )
        procs.append(agent)
        _wait_until(
            lambda: _agent_healthy(base_url),
            _STARTUP_TIMEOUT_S,
            "agent",
            agent,
            agent_log,
        )

        yield Stack(base_url=base_url)
    finally:
        for proc in reversed(procs):
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


def _agent_healthy(base_url: str) -> bool:
    with httpx.Client(base_url=base_url, timeout=5.0) as c:
        login = c.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        login.raise_for_status()
        agents = c.get("/api/agents").json()
        return any(a["status"] == "healthy" for a in agents)


@pytest_asyncio.fixture
async def api_client(stack: Stack):
    """An admin-authenticated async client against the live API."""
    async with httpx.AsyncClient(base_url=stack.base_url, timeout=30.0) as c:
        r = await c.post("/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        r.raise_for_status()
        yield c


@pytest_asyncio.fixture
async def healthy_agent(api_client) -> dict:
    """The registered, healthy agent as the API reports it."""
    agents = (await api_client.get("/api/agents")).json()
    healthy = [a for a in agents if a["status"] == "healthy"]
    assert healthy, "expected a healthy agent in the live stack"
    return healthy[0]


@pytest.fixture
def spawn_agent(
    stack: Stack, tmp_path_factory
) -> Iterator[Callable[[Mapping[str, str] | None], subprocess.Popen]]:
    """Start an *extra*, disposable agent against the live API (so disconnect
    tests never disturb the session-scoped agent). ``extra_env`` overrides agent
    settings (e.g. a short ``SESSION_IDLE_TIMEOUT_S``). All spawned agents are
    terminated on teardown."""
    api_port = int(stack.base_url.rsplit(":", 1)[1])
    started: list[subprocess.Popen] = []

    def _mint_bootstrap_token() -> str:
        """Mint a fresh single-use bootstrap token via the admin API — the
        seeded AGENT_BOOTSTRAP_TOKEN was already consumed by the session agent."""
        with httpx.Client(base_url=stack.base_url, timeout=10.0) as c:
            c.post(
                "/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
            ).raise_for_status()
            resp = c.post("/api/admin/agents/bootstrap")
            resp.raise_for_status()
            return resp.json()["token"]

    def _spawn(extra_env: Mapping[str, str] | None = None) -> subprocess.Popen:
        d = tmp_path_factory.mktemp("xc-agent")
        results_dir = d / "results"
        results_dir.mkdir()
        log = d / "agent.log"
        env = _agent_env(api_port, results_dir, _free_port())
        env["BOOTSTRAP_TOKEN"] = _mint_bootstrap_token()
        if extra_env:
            env.update(extra_env)
        with log.open("w") as fh:
            proc = subprocess.Popen(
                ["uv", "run", "--package", "duckhaven-agent", "python", "-m", "agent.main"],
                cwd=REPO_ROOT,
                env=env,
                stdout=fh,
                stderr=subprocess.STDOUT,
            )
        started.append(proc)
        return proc

    yield _spawn

    for proc in started:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest_asyncio.fixture
async def workspace(api_client) -> str:
    """A freshly created workspace slug with a default catalog.

    Workspaces no longer auto-create a catalog, so attach one (which provisions
    a real Polaris catalog + default namespace) for queries to run against."""
    import uuid

    slug = f"xc-{uuid.uuid4().hex[:8]}"
    r = await api_client.post("/api/workspaces", json={"slug": slug, "name": "XC"})
    r.raise_for_status()
    c = await api_client.post(
        f"/api/workspaces/{slug}/catalogs", json={"name": f"c_{slug.replace('-', '_')}"}
    )
    c.raise_for_status()
    return slug
