---
type: how-to
---

# Load data with dlt

Load data from any dlt source into DuckHaven tables with the `dlt-duckhaven` destination.

dlt writes each load as Parquet files. The destination uploads them straight to the catalog's object storage and then
loads them with a statement through a [SQL session](sql-sessions.md), so bulk data never passes through the DuckHaven
API, and every statement is checked and recorded like any query.

## Before you begin

- **You need:** a [service-account token](../access/service-accounts.md) (`dh_pat_…`) whose account is a `writer` in
  the workspace, and Python 3.11 or later on the machine that runs dlt.
- **Enabled by:** `SQL_SESSIONS_ENABLED=true` on the DuckHaven server.
- **Network:** the machine that runs dlt must reach the object store to upload files. On the bundled stack that is
  the address in `S3_ENDPOINT_PUBLIC`, `http://localhost:9000` by default, which works only on the DuckHaven host
  itself. From another machine, set `S3_ENDPOINT_PUBLIC` to an address it can reach and run `docker compose up -d`.

This page loads a `customers` table into the schema `crm` of the catalog `sales`, in the workspace `analytics`.

## 1. Install the destination

```bash
pip install dlt-duckhaven "dlt[parquet]"
```

`dlt[parquet]` adds pyarrow, which dlt needs to write the Parquet files the destination stages.

## 2. Configure it

In your project, `.dlt/config.toml`:

```toml
[destination.duckhaven]
host = "https://duckhaven.example.com"
workspace = "analytics"
catalog = "sales"
```

and the token in `.dlt/secrets.toml`, which you keep out of version control:

```toml
[destination.duckhaven.credentials]
token = "dh_pat_…"
```

| Field | Meaning |
|---|---|
| `host` | The DuckHaven address |
| `workspace` | The workspace slug |
| `catalog` | The catalog tables are loaded into |
| `credentials.token` | The service account's token |
| `agent` | Optional agent id. Leave it out to let DuckHaven choose |

The pipeline's `dataset_name` is the schema, created if it does not exist.

## 3. Run a pipeline

```python
# load_customers.py
import dlt


@dlt.resource(primary_key="id", write_disposition="merge")
def customers():
    yield [
        {"id": 1, "name": "acme", "country": "NL"},
        {"id": 2, "name": "globex", "country": "DE"},
        {"id": 3, "name": "initech", "country": "US"},
    ]


pipeline = dlt.pipeline(
    pipeline_name="customers",
    destination="duckhaven",
    dataset_name="crm",
)
print(pipeline.run(customers()))
```

```bash
python load_customers.py
```

```text
Pipeline customers load step finished in 3.92 seconds
1 load package(s) were loaded to destination duckhaven and into dataset crm
The duckhaven destination used https://duckhaven.example.com/analytics location to store data
Load package 1792434944.2186713 is LOADED and contains no failed jobs
```

## Check that it worked

Query the table in a worksheet:

```sql
SELECT id, name, country FROM sales.crm.customers ORDER BY id;
```

| id | name | country |
|---|---|---|
| 1 | acme | NL |
| 2 | globex | DE |
| 3 | initech | US |

Run the script again: with `write_disposition="merge"` and a `primary_key`, the rows are updated in place and the table
still has three rows.

## Write dispositions

- **`append`** adds the loaded rows.
- **`replace`** swaps the table's contents. On Iceberg, the `truncate-and-insert` strategy deletes every row first,
  which writes delete files rather than being a cheap truncate.
- **`merge`** updates rows by `primary_key` or `merge_key`, and inserts the rest.

New columns in later loads are added to the table. The destination's README lists every option and type mapping:
[`dlt-duckhaven`](https://github.com/tamasmrtn/duckhaven-clients/tree/main/packages/dlt-duckhaven).

## If it goes wrong

### `You must install additional dependencies to run dlt pyarrow helpers`

pyarrow is missing. Run `pip install "dlt[parquet]"` and run the pipeline again; dlt retries the load it kept.

### `[404] SQL sessions are not enabled`

The server has sessions off. An operator sets `SQL_SESSIONS_ENABLED=true` in `.env` and runs `docker compose up -d`.

### `ConnectionRefusedError` or `Failed to establish a new connection` while loading a job

dlt could not upload a file to the object store: the address in the presigned URL is not reachable from this machine.
Set `S3_ENDPOINT_PUBLIC` on the server to an address the dlt machine can reach, run `docker compose up -d`, and run
the pipeline again. dlt keeps the failed package and retries it on the next run.

### `[503] No connected agent available`

No agent is connected, for example just after the server restarted. Wait until an agent shows **Running** under
**Compute**, then run again.

### `[401] Unauthorized`

The token is wrong, expired or revoked. Check `.dlt/secrets.toml`; see
[Service accounts & tokens](../access/service-accounts.md).

## Related

- [SQL sessions](sql-sessions.md#staging-files) — how files are staged.
- [Connect dbt](connect-dbt.md) — transform what you loaded.
- [Session audit trail](session-audit.md) — the load's statements, as one session.
