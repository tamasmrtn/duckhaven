# Run queries

Worksheets are DuckHaven's primary surface — a browser SQL editor backed by the [agent](../concepts/agents.md) you
choose.

## Worksheets and tabs

Open multiple worksheets as tabs. Each worksheet remembers its selected agent, so you can run different work against
different compute.

## Pick an agent

The engine picker lists every connected agent with its DuckDB version, memory ceiling, and the storage backends it can
serve. An agent that lacks the extension required by the workspace's [storage backend](../concepts/storage-backends.md)
is shown disabled — picking it would fail fast.

## Run a statement

Press **Ctrl+Enter** (or the Run button) to execute the statement under the cursor — runs are statement-aware, not
whole-buffer. While a query runs you can **Cancel** it; a wall-clock timeout also applies.

## Read results

- Results appear in a grid below the editor, paged on demand so large results never load whole.
- Export the current result to **CSV**.
- After a run, open the **Profile** tab to inspect performance — see [Read query profiles](query-profiles.md).

## Allowed SQL

Worksheets accept `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `MERGE` and catalog DDL (`CREATE`, `ALTER`, `DROP`). Sandbox
escapes (`ATTACH`, `COPY`, `LOAD`, `SET`, `PRAGMA`, …) are rejected. See [SQL support](../reference/sql-support.md).

## Save for later

Save a frequently used query with a name and an optional default agent — see [Saved queries](saved-queries.md). Every
run is recorded in the workspace [history and audit log](../operations/monitoring.md).
