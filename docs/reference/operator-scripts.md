---
type: reference
---

# Operator scripts

The helper scripts under `scripts/` back up Postgres, check DuckLake catalogs, review the assistant's answers and wait
for the stack to start.

They run on the host, next to Docker and Postgres, for jobs the API does not cover. For everything the API does, use
the [`dh` CLI](cli.md) instead.

## Requirements

The scripts are in the repository, not in the `deploy/` download that [Install](../deployment/install.md) uses. Clone
the repository on the host and run them from its root:

- **Shell scripts** find the Compose stack at `deploy/docker-compose.yml` next to them, so the stack must run from that
  checkout's `deploy/` directory. The systemd backup timer expects the checkout at `/opt/duckhaven`.
- **Python scripts** import the API's code, so they need its environment: run `uv sync` once, then start them with
  `uv run --package duckhaven-api`. They connect to Postgres directly, so the database must be reachable from where they
  run; the bundled stack does not publish Postgres to the host.

| Script | Kind | Changes anything |
|---|---|---|
| [`pg-backup.sh`](#pg-backupsh) | Shell | Writes backup files |
| [`enable-ducklake.sh`](#enable-ducklakesh) | Shell | Creates the `ducklake` database |
| [`ducklake-check.py`](#ducklake-checkpy) | Python | No, read-only |
| [`assistant-mine-feedback.py`](#assistant-mine-feedbackpy) | Python | Writes a candidate file; reads the database only |
| [`wait-for-stack.sh`](#wait-for-stacksh) | Shell | No |

## `pg-backup.sh`

Dumps the `duckhaven` database (application state), the `polaris` database (Iceberg metastore) and, when it exists, the
`ducklake` database (DuckLake catalog metadata), each to a timestamped, gzipped file.

```bash
DUCKHAVEN_BACKUP_DIR=/mnt/nas/duckhaven ./scripts/pg-backup.sh
```

```text
Backup written to /mnt/nas/duckhaven/duckhaven_20261019_031500.sql.gz
Backup written to /mnt/nas/duckhaven/polaris_20261019_031500.sql.gz
```

| Variable | Default | Meaning |
|---|---|---|
| `DUCKHAVEN_BACKUP_DIR` | `/var/duckhaven/backups` | Where the dumps go. Use a separate disk or NAS in production |

With a `ducklake` database it first prints a reminder to snapshot object storage at the same moment, because a
DuckLake catalog's metadata and data can otherwise be restored to different points in time. A systemd timer that runs
the script ships under `deploy/systemd/`. See [Backup & restore](../deployment/backup-restore.md) for restoring.

## `enable-ducklake.sh`

Creates the `ducklake` database and its permissions on a deployment whose Postgres was initialized before DuckLake
existed. A new install creates it on first start, so this is for existing deployments only. It is safe to run twice.

```bash
./scripts/enable-ducklake.sh
```

It ends by printing the next steps; [DuckLake](../deployment/ducklake.md) walks through them.

## `ducklake-check.py`

Reports where a [DuckLake](../data/ducklake.md) catalog and its object storage disagree. Read-only: it lists, compares
and prints, and deletes nothing, so it is safe against a live deployment.

```bash
DATABASE_URL=postgresql+asyncpg://… DUCKLAKE_DATABASE_URL=postgresql+asyncpg://… \
  uv run --package duckhaven-api ./scripts/ducklake-check.py
DATABASE_URL=… DUCKLAKE_DATABASE_URL=… uv run --package duckhaven-api ./scripts/ducklake-check.py --catalog raw
```

| Option | Meaning |
|---|---|
| `--catalog ⟨slug⟩` | Check only this catalog |

An Iceberg table cannot disagree with its metadata, which sits beside the data in object storage. A DuckLake catalog
keeps its metadata in Postgres, so a restore can put the two at different moments. The report has two directions per
catalog:

- **missing**: the catalog references a file that storage does not have, and queries on that table fail. The script
  exits non-zero when it finds any, so it can gate a restore.
- **orphaned**: an object nothing live references. It costs storage and nothing else. Time travel creates these
  legitimately, because a file that only older snapshots reference is still needed, so never treat the list as files to
  delete.

It also counts rows with absolute paths, which do not follow a change to the catalog's data path. Run it after
restoring a deployment that has DuckLake catalogs.

## `assistant-mine-feedback.py`

Reads the [assistant's](../ai/assistant.md) audit trail, reports the turns worth a person's review, and drafts them as
candidate cases for the [eval set](../developer/testing.md). It opens a read-only transaction and writes nothing to the
database.

```bash
DATABASE_URL=postgresql+asyncpg://… uv run --package duckhaven-api ./scripts/assistant-mine-feedback.py --days 30
```

| Option | Default | Meaning |
|---|---|---|
| `--days` | `30` | How far back to look |
| `--out` | `api/tests/evals/cases.candidate.yaml` | Where to write candidate cases |
| `--no-out` | – | Print the report only |

Nothing is promoted to the eval set automatically. Each candidate says what to confirm before you keep it, because a
mined case is a lead, not a finding.

It reports four signals, the most reliable first:

| Signal | What it means |
|---|---|
| A documentation search found nothing | Someone asked what the documentation does not cover. Exact, and the strongest signal |
| A documentation tool failed or was denied | A bad path, a missing page, an incomplete image |
| The turn produced no answer | It ran out of steps or failed; the user got nothing |
| A product question answered without opening the docs | A heuristic that over-reports: it matches the question's wording against product vocabulary |

The last is the weakest, but it is the only one that can catch a confidently wrong answer. Expect to discard most of
what it finds.

!!! note "These signals are not satisfaction"
    DuckHaven records no rating or correction from users, so nothing measures whether an answer helped. The four
    signals point at turns that went badly, which is why a person reviews them instead of a job acting on them.

## `wait-for-stack.sh`

Waits until the `api` and `agent` containers report healthy, checking every 5 seconds for up to 5 minutes each. The
end-to-end CI job uses it, and it is handy after `make compose-up`.

```bash
./scripts/wait-for-stack.sh
```

```text
api is healthy
agent is healthy
Stack is ready.
```

If a container does not become healthy in time, it prints the container's last 80 log lines and exits non-zero.

## Related

- [Backup & restore](../deployment/backup-restore.md)
- [DuckLake](../deployment/ducklake.md)
- [CLI reference](cli.md)
