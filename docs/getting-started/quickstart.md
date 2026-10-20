---
type: tutorial
---

# Quickstart

Install DuckHaven with Docker Compose, create the first admin, and query a table you created yourself in about fifteen
minutes.

By the end you will have the full stack running on one host: the web app and API, Postgres, Apache Polaris, the bundled
object store, and one DuckDB agent. [How DuckHaven works](how-duckhaven-works.md) explains what each piece does.

## Before you begin

- A Linux host with Docker Engine 24 or later, Docker Compose v2, and 8 GB of RAM.
- Network access to GitHub and the GitHub Container Registry, to download the stack and its images.
- Ports 8000 (web app and API), 9000 and 9001 (bundled object store) and 3000 (Grafana) free on the host.

DuckHaven is built to run on a private network, such as Tailscale, rather than on the public internet.

## 1. Download and start the stack

In an empty directory, run:

```bash
curl -fsSL https://github.com/tamasmrtn/duckhaven/archive/refs/heads/main.tar.gz \
  | tar xz --strip-components=2 duckhaven-main/deploy
docker compose up -d
```

The first command unpacks the `deploy/` directory: the Compose file and the scripts and configuration it mounts. The
second downloads the images, which takes a few minutes the first time, and starts the stack. It ends with lines like
these, where the prefix is the name of your directory:

```text
 Container duckhaven-api-1 Healthy
 Container duckhaven-agent-1 Starting
 Container duckhaven-agent-1 Started
```

On first boot the stack generates its secret key and a one-time setup token, and prepares the database. No `.env` file
is needed.

!!! warning "Set `POSTGRES_PASSWORD` before exposing the host"
    Without it, Postgres uses the published default password `duckhaven`. That is acceptable on a private machine you
    trust; anywhere else, start the stack with `POSTGRES_PASSWORD=⟨a long random value⟩ docker compose up -d`, or put
    the variable in a `.env` file beside `docker-compose.yml`, before the first boot.

## 2. Create the first admin

Read the setup token on the host:

--8<-- "setup-token.md"

It prints a single line of about 44 characters.

Open `http://⟨host⟩:8000` in a browser. While no admin exists, DuckHaven opens the **Create the first admin account**
screen. Paste the token into **Setup token**, fill in **Your name**, **Email** and **Password**, and click **Create
admin**.

You are signed in and see **Create your first workspace**. The setup token is used up; from here on, you sign in
with the email and password you chose.

## 3. Create a workspace

A [workspace](../access/workspaces.md) is where a team works: its members, its worksheets, and the catalogs it uses.

1. Click **Get started**.
2. Enter `Analytics` as the **Workspace name**. The slug below it reads `analytics`.
3. Click **Create workspace**.

A worksheet opens. Beside it, the catalog tree says **No catalogs attached.**, and the agent picker in the toolbar shows
the bundled agent with a green status dot.

## 4. Create a catalog

A [catalog](../data/catalogs.md) holds your schemas and tables, on a storage backend.

1. In the catalog tree's header, click **Create** → **Create catalog**.
2. Enter `sales` as the **Name**.
3. Leave the defaults: **Apache Iceberg + Polaris**, **Bundled object storage**, and **Everyone in this workspace**.
4. Click **Create**.

`sales` appears in the tree, marked **default**, and the toolbar's **Active catalog** switches to it. Every new catalog
starts with one schema, `analytics`.

## 5. Create a table and query it

**Run** (or **Ctrl+Enter**, **⌘+Enter** on macOS) runs the statement under the cursor, so run these one at a time.
Replace the worksheet's contents with each statement in turn, and run it.

Create a table:

```sql
CREATE TABLE sales.analytics.orders (
    order_id INTEGER,
    customer VARCHAR,
    amount DECIMAL(10, 2),
    ordered_on DATE
);
```

The status above the results reads **done**, with **0 rows**.

Add four orders:

```sql
INSERT INTO sales.analytics.orders VALUES
    (1, 'acme', 120.00, DATE '2026-10-01'),
    (2, 'globex', 75.50, DATE '2026-10-01'),
    (3, 'acme', 42.25, DATE '2026-10-02'),
    (4, 'initech', 310.00, DATE '2026-10-03');
```

The status reads **done**, with **4 rows**: the number of rows inserted.

Ask a question of the data:

```sql
SELECT customer, count(*) AS orders, sum(amount) AS revenue
FROM sales.analytics.orders
GROUP BY customer
ORDER BY revenue DESC;
```

The **Results** tab shows three rows, each column headed with its type:

| customer `VARCHAR` | orders `BIGINT` | revenue `DECIMAL(38,2)` |
|---|---|---|
| initech | 1 | 310.00 |
| acme | 2 | 162.25 |
| globex | 1 | 75.50 |

Which SQL statements DuckHaven runs is listed in [SQL support](../reference/sql-support.md).

## 6. See how the query ran

Open the **Profile** tab. It shows the query's latency, its peak memory against the memory the agent reserved for it,
and the tree of operators DuckDB ran, with rows, bytes and time for each. **Open full profile ↗** opens the same profile
on its own page. [Read query profiles](../querying/query-profiles.md) explains each figure.

## If it goes wrong

### `docker compose up -d` cannot pull an image

The host cannot reach `ghcr.io` or Docker Hub. Check the host's network and proxy settings, then run the command again.

### The setup screen says "Invalid or missing setup token."

The token was mistyped, or it is from a different stack. Run the `docker compose cp` command again in the same
directory and paste the new output.

### The browser shows the sign-in page instead of the setup screen

An admin already exists, so the token was used up. Sign in with that account. To start again from nothing, see
[Start over](#start-over).

### A query fails with "Agent not connected"

The bundled agent is not running. Check it with `docker compose ps agent` and `docker compose logs agent`. See
[Troubleshooting](../operations/troubleshooting.md) for more.

## Start over

To remove the stack and everything in it — Postgres, the object store, the secret key and the setup token — run:

```bash
docker compose down -v
```

The next `docker compose up -d` starts from nothing, with a new setup token.

## What you built

A complete DuckHaven stack on one host, with an admin account, an `Analytics` workspace, a `sales` Iceberg catalog on
the bundled object store, and an `orders` table you queried through the bundled DuckDB agent.

## Next steps

- [Run queries](../querying/run-queries.md) — everything a worksheet does.
- [Manage users & access](../access/manage-users.md) — invite your team and give them roles.
- [Command line (dh)](cli-quickstart.md) — the same work from a terminal or a CI job.
- [Install DuckHaven](../deployment/install.md) — before production: TLS, external storage, backups.
- [Add an agent](../compute/add-agent.md) — more compute, on another host.
