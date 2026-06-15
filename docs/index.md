---
title: Home
template: home.html
hide:
  - navigation
  - toc
---

<div class="grid cards" markdown>

-   :material-table-edit:{ .lg .middle } __Browser worksheets__

    ---

    A Monaco SQL editor with tabs, a results grid, and CSV export. Write and run queries together without
    emailing `.sql` snippets around.

-   :material-file-tree:{ .lg .middle } __Shared Iceberg catalog__

    ---

    Browse schemas and tables with sample rows. Every table is Iceberg-native, with snapshot history and
    "query at this snapshot" time travel.

-   :material-shield-account:{ .lg .middle } __Governed workspaces__

    ---

    Per-workspace permissions, Apache Polaris catalog integration, and a full audit trail of who ran what,
    where, and when.

-   :material-server-network:{ .lg .middle } __Transparent compute__

    ---

    You pick the DuckDB agent per query. No opaque optimizer, no surprise costs, no hidden resource
    allocation. Scale by adding agents.

-   :material-memory:{ .lg .middle } __Right-sized memory__

    ---

    Each query's memory reservation is estimated from DuckDB's `EXPLAIN` plan, so cheap queries pack in and
    heavy ones queue instead of OOM-ing the agent.

-   :material-chart-timeline-variant:{ .lg .middle } __Query profiles__

    ---

    After each run, inspect an interactive operator graph with rows, bytes, and timing per step, plus flags
    for spills, scan blow-ups, and bad estimates.

</div>

## Explore the docs

<div class="grid cards" markdown>

-   :material-rocket-launch:{ .lg .middle } __Quickstart__

    ---

    Stand up the full stack with one `docker compose` command and run your first query.

    [:octicons-arrow-right-24: Get started](getting-started/quickstart.md)

-   :material-sitemap:{ .lg .middle } __Architecture__

    ---

    The control-plane / compute split, invariants, and data flow — the defining idea behind DuckHaven.

    [:octicons-arrow-right-24: Understand the design](concepts/architecture.md)

-   :material-server:{ .lg .middle } __Deployment__

    ---

    Install, add agents, front the stack with TLS, update, and back up your data.

    [:octicons-arrow-right-24: Self-host DuckHaven](deployment/install.md)

-   :material-tools:{ .lg .middle } __Administration__

    ---

    Day-2 operations: query queueing and concurrency, agent management, and the operator runbook.

    [:octicons-arrow-right-24: Operate a cluster](administration/runbook.md)

-   :material-cog:{ .lg .middle } __Configuration__

    ---

    Every control-plane and agent environment variable, consolidated into one reference.

    [:octicons-arrow-right-24: Configure DuckHaven](reference/configuration.md)

-   :material-source-pull:{ .lg .middle } __Contributing__

    ---

    Local development, the four-layer test architecture, the design system, and cutting a release.

    [:octicons-arrow-right-24: Start contributing](contributing/development.md)

</div>
