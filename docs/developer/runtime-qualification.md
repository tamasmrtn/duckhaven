# Runtime qualification

A [runtime](../concepts/runtimes.md) is one DuckDB line baked into an agent image. The agent runs on every runtime
from the same source, so each new DuckDB line has to be *qualified*: shown to work with DuckHaven, and its differences
recorded. This page is the checklist for that, and the record of each runtime's results.

Run it when adding a runtime, when moving a non-default runtime's pin (`agent/runtimes/<id>.in`) to another build,
and before promoting a runtime from beta. A pre-release build is not the same as its release. Between two alphas of
DuckDB 2.0, a benchmark conclusion reversed completely, so results never carry over from one build to the next.

## Checklist

1. **Pin and build.** Put the exact DuckDB in `agent/runtimes/<id>.in` and run `make runtimes-lock`. Add the runtime
   to `shared/src/duckhaven_shared/runtimes.py`. `make build-agent RUNTIME=<id>` fails unless the image is on the
   right DuckDB line and every extension in the manifest loads, which also proves the extensions are published for
   that exact build.
2. **Agent unit tests on the line.** `make test-agent-runtime RUNTIME=<id>`. These use a real engine for the sandbox,
   profiling, `EXPLAIN` estimates, statement classification and result materialization.
3. **Agent integration tests on the line.** Against Polaris, the object store and DuckLake:
   `make test-agent-runtime RUNTIME=<id> AGENT_TESTS=agent/tests/integration PYTEST_ARGS="-m integration"`.
4. **The sandbox locks.** A capability probe of the image must report `sandbox: verified`. The allowed-settings list
   is derived per engine, but any setting a new extension changes while the lock is on still has to be allowed by
   name.
5. **DuckLake formats.** Measure which format the runtime creates a catalog in and which formats it opens without
   migrating. Record them as `ducklake_format` and `ducklake_formats`. Check that DuckHaven's own SQL against the
   `ducklake_*` tables (`api/.../catalog_backends/ducklake.py`, `agent/.../runner.py`) still reads a catalog in the
   new format.
6. **Across runtimes.** `make test-runtime-compat` covers five checks against the default runtime:
   - Iceberg tables written by either runtime read on the other.
   - A DuckLake catalog the default runtime created is read and written by the new runtime without being migrated.
   - Whether the default runtime can open a catalog the new runtime creates matches the manifest.
   - Result pages written on the new runtime decode on the default one.
   - Result pages written on the default runtime decode on the new one.

   This also runs nightly.
7. **End to end with the image.** Run an agent from the image against a live stack. It should report the runtime,
   serve an explicitly targeted query, open a SQL session, and be skipped by auto-pick while in beta.
8. **Benchmark** it against the default runtime (`benchmarks/tpch`), on the release build.

## Changing the default runtime

The default runtime is the one the API itself runs, the bundled agent runs, and auto-provisioned compute runs. Moving
it is a release, not a setting. Do it only once the new runtime has passed every check above **on its release
build**, including the benchmark. For a new line `N` replacing `O`:

1. Move `uv.lock` to line `N`. Change the `duckdb` pin in both `api/pyproject.toml` and `agent/pyproject.toml`, then
   run `uv lock`. The API parses SQL and decodes result pages with this DuckDB, so it moves too.
2. Give `O` its own pin: `agent/runtimes/O.in` with its exact version, then `make runtimes-lock`. Remove `N.in` and
   `N.txt`, since `N` now comes from the lock.
3. In `shared/src/duckhaven_shared/runtimes.py`, set `DEFAULT_RUNTIME_ID = "N"`, make `N` `ga`, and mark `O`
   `deprecated`.
4. Change the defaults that name the runtime: `DEFAULT_RUNTIME` in `deploy/docker-compose.yml` and
   `deploy/docker-compose.ha.yml`, `default_runtime` and `agent_runtimes` in Terraform, and the `-duckdbN` e2e tag in
   `.github/workflows/ci.yml`. `tests/deploy/test_compose_runtimes.py` fails until they agree with the manifest.
5. Check DuckLake. If `N` creates a format `O` can't open, then once `N` is the default, `O` agents lose any catalog
   `N` creates. That is acceptable only because `O` is on its way out. Existing catalogs keep their format, since
   nothing migrates them.
6. In the release notes, say that the unsuffixed `:latest` and `:X.Y.Z` agent tags now mean `N`. That affects any
   static agent run from them.

One DuckHaven minor release later, retire `O`: set it to `retired` for one release, so an agent still running it is
refused with that reason, and remove it from the manifest in the release after.

## DuckDB 2.0 (beta)

Qualified on 2026-09-26 against the pre-release `v2.0.0-alpha43385` (PyPI `duckdb==2.0.0.dev2609250715`), since 2.0
is not released yet (planned for 2026-10-21).

| Check | Result |
|---|---|
| Build | Every extension loads (`httpfs`, `azure`, `iceberg`, `ducklake`, `postgres`). Image 391 MB against 346 MB for 1.5. |
| Unit tests | All pass. |
| Integration tests | All pass except two expected failures, see *vended credentials* below. |
| Sandbox | `verified`. |
| DuckLake | Creates format `1.1-dev1`, opens `1.0` and `1.1-dev1`. It opens and writes a 1.0 catalog without migrating it. 1.5 cannot open a 1.1-dev1 catalog. The dispatch gate therefore keeps 2.0 agents from creating catalogs while 1.5 is the default. DuckHaven's metadata SQL reads 1.1-dev1 unchanged: the format only adds columns, and renames columns in tables DuckHaven never reads directly. |
| Across runtimes | All pass. |
| End to end | Passes, including a query and a session on the 2.0 agent and the DuckLake refusal. |
| Benchmark | **Not run on this build.** Must be re-run on the 2.0 release before any promotion. |

Differences the agent absorbs, so they need no action from users:

- **`CREATE SECRET` refuses bind parameters.** The agent detects this and inlines escaped values instead, scrubbing
  them from any error.
- **Profiling moved to `tracked_metrics` with glob patterns, and the profile is regrouped.** Both are detected, and
  the profile is mapped back to the 1.5 shape.
- **`custom_profiling_settings` is gone.** A fixed allowed-settings list would have left the sandbox unlocked, so the
  list is derived from the engine.
- **`ducklake` sets `current_transaction_invalidation_policy` around every statement.** Left locked, every DuckLake
  query fails as an aborted transaction, so it is allowed.
- **A `SELECT`'s runtime error surfaces when its rows are fetched, not when it runs.** The agent drains a script's
  final result.

Open items. These don't block beta, since beta agents are only used when named, but they block promotion:

- **Vended credentials are not exposed as a DuckDB secret.** On 1.5 the `iceberg` extension registers the storage
  credentials Polaris vends as an S3 secret. On 2.0 it keeps them internal, so reading a table's files directly gets
  a 403. That breaks the table-health probes that size data files from their Parquet footers and list the storage
  prefix for orphans. They run only on auto-picked agents, which are never beta, so today nothing reaches them.
- **The API parses SQL with 1.5.** SQL only 2.0 understands is refused before it reaches a 2.0 agent.
- **Performance.** On the August and September alphas, reads through DuckHaven were 6% slower with warm caches,
  while writes and cold reads were 30% faster. The release build decides whether 2.0 becomes the default.
