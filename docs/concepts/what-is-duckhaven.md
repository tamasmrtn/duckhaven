# What is DuckHaven?

DuckHaven is a self-hosted data lakehouse. Your data, your hardware, your bill.

The pieces were already open. [Apache Iceberg](https://iceberg.apache.org/) brought a transactional table format.
[Apache Polaris](https://polaris.apache.org/) brought a catalog that governs those tables and vends short-lived storage
credentials. [DuckDB](https://duckdb.org/) was already quick enough that most teams never need a cluster. What nobody
had built was the part that holds the three together — something that knows who your users are, what they may touch,
which engine runs their query, and what happened afterwards.

That's DuckHaven. One Docker Compose stack, and those three projects become a system a team can actually work in.

## The problem it solves

Running a lakehouse has usually meant adopting somebody else's. The open components are all there, but wiring them into
something you'd let colleagues loose on is a platform project most teams cannot justify — so they rent Snowflake's or
Databricks' instead, and hand over the data and the bill along with it.

That trade is rarely revisited once it is made. DuckHaven is the argument that you do not have to make it.

## What you get

Data arrives through it and leaves through it. Tables are Iceberg, governed by Polaris, sitting on object storage you
own — the bundled object store, S3, or ADLS Gen 2 — and a catalog can move between backends without losing a snapshot.
Compute is DuckDB [agents](agents.md) you choose per query, which can scale to zero between runs. Access is governed
down to the table, with single sign-on, machine identities, and a record of who ran what. And the layers that usually
get skipped on a self-hosted stack are in the box: column-level [lineage](lineage.md), a
[semantic layer](semantic-layer.md), a [maintenance advisor](maintenance.md), and
[governed access for AI agents](mcp-server.md).

Everything runs on your network. Nothing phones home.

## Who it is for

Teams that want their lakehouse to be theirs — a homelab running on one box, or a company that would rather not put its
data in someone else's account. The deployment grows with you: start with the bundled agent, add hosts when queries
outgrow them, turn on [elastic compute](elastic-compute.md) when you would rather not pay for idle ones.

It is deliberately **not** a Spark replacement or a notebook platform, and it is not built to face the public internet.
[Architecture](architecture.md) lists the boundaries in full.

## Where to go next

- New here? Start with the [Quickstart](../getting-started/quickstart.md).
- Want the mental model first? Read [Architecture](architecture.md), then [Workspaces](workspaces.md).
