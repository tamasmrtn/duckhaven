---
type: concept
---

# Runtimes

A **runtime** is the DuckDB an agent runs, together with the extensions baked into its image. Each runtime is one
agent image that DuckHaven builds and tests, named after its DuckDB line: `1.5` is DuckDB 1.5.x with the standard
extensions (`httpfs`, `azure`, `iceberg`, `ducklake`, `postgres`).

| Runtime | Status | Notes |
|---|---|---|
| DuckDB 1.5 | Generally available, the default | Upstream support ends 2027-03-01. |
| DuckDB 2.0 | Beta | Upstream support ends 2027-10-22. See [performance](#performance-on-duckdb-20), [SQL on DuckDB 2.0](#sql-on-duckdb-20) and its [qualification record](../developer/runtime-qualification.md#duckdb-20-beta). |

Runtimes exist because DuckDB versions are not interchangeable. A new line changes performance (sometimes for the
better and sometimes not, depending on the workload), changes behaviour the agent relies on, and changes what it can
read and write. Pinning the version per agent lets you try a new line on one agent while the rest of the fleet stays
where it is, and roll back by stopping that agent.

## A runtime belongs to compute

The runtime is a property of an agent, not of a workspace or a query. It is chosen for each piece of compute and stays
with it:

- **Elastic compute** runs the runtime it was created with. An admin picks it in **Compute → New compute**; a beta
  runtime has to be acknowledged before the agent is created. A restart keeps it, but picks up the newest build of that
  runtime that the control plane ships, the way a maintenance update would. An agent never moves to a different DuckDB
  line on its own.
- **A static agent** is whatever image its operator runs. The *Add an agent* snippet names the runtime's image
  (`duckhaven-agent:<tag>-duckdb<runtime>`). Changing it later means re-running the agent from another image.
- **The default runtime** (`DEFAULT_RUNTIME`) is what compute runs when nobody chose. The elastic pool provisions
  it when work arrives and nothing is up, and the create-compute dialog preselects it. The bundled agent in the
  compose stack runs it too.

To run work on a particular runtime, send it to an agent on that runtime. A worksheet, a saved query, or a schedule
that names an elastic agent keeps running on that agent's runtime, because a restart never changes it.

## What gets picked when nobody chooses

Some work doesn't name an agent: a pool run, a background maintenance probe, the assistant, a query sent without an
`agent_id`. For that work DuckHaven picks a connected agent that can serve the workspace, and prefers:

1. the default runtime,
2. then other generally available runtimes,
3. then deprecated ones.

An agent on a **beta** runtime is never picked this way. Work reaches it only by naming it, so trying a new DuckDB
line never changes where anyone else's queries run.

## Lifecycle

| Status | Offered for new compute | Picked automatically | Existing agents |
|---|---|---|---|
| **Beta** | Yes, with an explicit opt-in | Never | Run work that names them |
| **Generally available** | Yes | Yes | Run normally |
| **Deprecated** | No | Yes, after the others | Run normally, flagged in the UI |
| **Retired** | No | No | Refused, and can't be restarted |

A runtime is deprecated some time after upstream DuckDB stops supporting its line. It is retired one DuckHaven
release later, when it stops being built. Its entry stays in that last release so that an agent still running it is
refused with a clear reason rather than an unknown one.

## Which agents are trusted with work

Every agent reports its runtime and its exact DuckDB version when it connects, and work is routed on that report. An
agent is refused work, with the reason shown, when:

- it reports a runtime the control plane doesn't know, or a DuckDB version its runtime doesn't have
  (`runtime_unsupported`);
- it is elastic compute running something other than the runtime it was started as (also `runtime_unsupported`).
  In the elastic pool such an agent is terminated at once, since it would otherwise hold the pool's slot while
  being refused every query;
- its runtime is retired (`runtime_retired`).

An agent built before runtimes existed doesn't report one. It is matched to a runtime by its DuckDB line, so an
existing fleet keeps working through an upgrade.

For a **SQL session**, the agent must also confirm that DuckDB's configuration lock really applied
(`agent_sandbox_unverified` otherwise). A session runs under a relaxed statement policy on the strength of that lock,
and on some DuckDB versions the lock can fail silently. An operator who turned the lock off on purpose
(`SANDBOX_LOCK_CONFIGURATION=false`) is not refused.

Every query and session records the runtime that ran it. History shows it next to the agent's name. The Compute page
shows each agent's runtime and exact DuckDB version, and flags one that is beta, deprecated, or refused work. An
agent's own page also says whether its configuration lock applied.

## Mixing runtimes on the same data

Agents on different runtimes can read and write the same catalogs, with limits:

- **The API parses SQL with the default runtime's DuckDB.** Syntax that only a newer line understands is refused
  (`sql_not_allowed`) even when it is sent to an agent on that newer line. This goes away once that line becomes
  the default.
- **DuckLake catalog formats move one way.** A DuckLake catalog's metadata format is tied to the `ducklake`
  extension's version. A newer extension can upgrade an older catalog, but the upgrade can't be undone, and an older
  runtime can't open the result. DuckHaven never asks for that upgrade. It also checks, on every dispatch, that the
  agent's runtime opens the catalog's format as-is (`ducklake_format_unsupported` otherwise). That check has a
  consequence for new catalogs: whichever agent attaches a catalog first creates it, in that agent's format, and
  any statement may attach any catalog of its workspace. So in a workspace with a DuckLake catalog that has no
  metadata, only a runtime whose format the default runtime can open may run work. DuckDB 1.5 creates
  and opens format `1.0`. DuckDB 2.0 opens `1.0` without changing it, but creates `1.1`, which 1.5 can't open. So a
  2.0 agent works on existing DuckLake catalogs but isn't allowed to create new ones.
- **A beta runtime may run background checks differently.** Background work only runs on agents the server picks,
  which are never beta, so such a difference doesn't reach you. Each runtime's qualification record lists them.

## Performance on DuckDB 2.0

Measured on TPC-H at scale factor 10, against DuckDB 1.5.6 on an agent of the same size, reading and writing Iceberg
tables through DuckHaven:

- **Writes are faster.** `CREATE TABLE … AS SELECT` takes about 29% less time.
- **A first read is faster.** A query that reads its tables for the first time on a session is about 16% faster.
- **A repeat read is slower.** A query whose data is already in the agent's file cache is about 12% slower. This is
  DuckDB 2.0 itself: a plain 2.0 connection outside DuckHaven is just as slow.

On that workload, where most reads are repeats, queries are about 11% slower overall. So 2.0 favors writes and work
that reads many different tables, and 1.5 favors queries that rerun over the same data, such as dashboards.

## SQL on DuckDB 2.0

A few statements that work on DuckDB 1.5 fail or return something else on a 2.0 agent:

| On 1.5 | On 2.0 | Write instead |
|---|---|---|
| `list_transform([1, 2], x -> x + 1)` works. | Fails with `Binder Error: Deprecated lambda arrow (->) detected.` | `list_transform([1, 2], lambda x: x + 1)`, which both lines accept. |
| Integer division `7 // 0` and modulo `7 % 0` return `NULL`. | Fail with `Invalid Input Error: Division by zero in expression (7 // 0).` | `TRY(7 // 0)` returns `NULL` on both lines. |

DuckDB has settings that restore the 1.5 behavior (`lambda_syntax`, `error_on_division_by_zero`), but no query or
SQL session can change them on an agent, so change the SQL instead. Division with `/` returns `inf` on both lines.

## Related

- [Agents](agents.md): what an agent is and what it reports.
- [Elastic compute](elastic-compute.md): how compute is created, pooled and restarted.
- [Updating](../deployment/updating.md): changing the default runtime.
- [Configuration](../reference/configuration.md#agent-images-and-runtimes): `DEFAULT_RUNTIME` and the agent image
  settings.
