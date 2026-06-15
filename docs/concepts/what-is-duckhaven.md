# What is DuckHaven?

DuckHaven is a **self-hosted, browser-based SQL workspace** for teams that run [DuckDB](https://duckdb.org/) over
Apache Iceberg tables governed by [Apache Polaris](https://polaris.apache.org/). It gives a small team (roughly 2–10
users) collaborative worksheets, a shared catalog, per-workspace permissions, and a full audit trail — without a cloud
warehouse, Kubernetes, or a platform team.

## The problem it solves

Teams that love DuckDB end up sharing `.duckdb` files over chat. DuckHaven provides the worksheet and collaboration
experience of MotherDuck or Databricks while keeping data on your own infrastructure — no SaaS lock-in, no opaque
billing, and no surprise costs.

## What you get

- **Browser worksheets** — a Monaco SQL editor with tabs, a results grid, and CSV export.
- **A shared catalog** — browse schemas and tables, with sample rows and Iceberg snapshot history.
- **Governed workspaces** — per-workspace roles and a complete audit log of who ran what.
- **Transparent compute** — you pick the DuckDB [agent](agents.md) per query; nothing is hidden behind an optimizer.
- **Self-hosting** — one Docker Compose stack on your own network.

## Who it is for

DuckHaven targets a homelab or a small team that wants collaborative, governed SQL over DuckDB on its own
infrastructure. It is intentionally **not** a Spark/Databricks replacement, a notebook platform, or an
internet-exposed service — see [Architecture](architecture.md) for the explicit non-goals.

## Where to go next

- New here? Start with the [Quickstart](../getting-started/quickstart.md).
- Want the mental model first? Read [Architecture](architecture.md), then [Workspaces](workspaces.md).
