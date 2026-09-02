# Operator scripts

DuckHaven ships a few small helper scripts under `scripts/` for operators. There is no separate `duckhaven` CLI — these
are the supported command-line helpers.

## `gen-token.sh` — mint an agent bootstrap token

Generates a one-time [bootstrap token](../deployment/add-agent.md) via the API, as an alternative to the admin UI.

```bash
SESSION_COOKIE=<your session cookie> ./scripts/gen-token.sh
```

`API_URL` defaults to `http://localhost:8000`. The token is printed as JSON.

## `pg-backup.sh` — back up Postgres

Dumps the DuckHaven database (app state plus the Polaris metastore) to a timestamped, gzipped file.

```bash
DUCKHAVEN_BACKUP_DIR=/mnt/nas/duckhaven ./scripts/pg-backup.sh
```

`DUCKHAVEN_BACKUP_DIR` defaults to `/var/duckhaven/backups`; point it at a separate disk or NAS in production. A systemd
timer wrapping this script ships under `deploy/systemd/` — see [Backup & restore](../deployment/backup-restore.md).

## `assistant-mine-feedback.py` — find questions the assistant answered badly

Reads the [assistant's](../concepts/assistant.md) own audit trail and reports the turns worth a person's attention,
then drafts them as candidate cases for the [eval set](../developer/testing.md). Read-only — it opens a read-only
transaction and writes nothing to the database.

```bash
DATABASE_URL=postgresql+asyncpg://… ./scripts/assistant-mine-feedback.py --days 30
```

Candidate cases go to `api/tests/evals/cases.candidate.yaml`; pass `--no-out` to print the report only. **Nothing is
promoted automatically.** Each candidate carries a note saying what to confirm before keeping it, because a mined case
is a lead rather than a finding — and a guess promoted into the golden set becomes a standard.

It reports four signals, in descending order of confidence:

| Signal | What it means |
|---|---|
| Documentation search found nothing | Somebody asked what the documentation does not cover. The strongest signal, and an exact one. |
| A documentation tool errored or was denied | A bad path, a missing page, an incomplete image. |
| The turn produced no answer | It ran out of its step limit, or failed. The user got nothing either way. |
| A product question answered without opening docs | **Heuristic**, and it over-reports. It matches the question's wording against product vocabulary. |

The fourth earns its place despite being the weakest: it is the only one that can catch a *confidently wrong* answer,
which leaves no trace anywhere else. Expect to discard most of what it surfaces.

!!! note "There is no satisfaction signal, and these are not one"
    Nothing in the schema records whether a user was happy with an answer — no rating, no correction, no thumbs-up.
    These four are proxies for a turn that went badly, not measurements of quality, which is why a person triages the
    output rather than a job consuming it. Explicit user feedback would be the single biggest improvement here, and it
    does not exist yet.

## `wait-for-stack.sh` — wait for healthy containers

Blocks until the `api` and `agent` containers report healthy. Used by the end-to-end CI job and handy locally after
`make compose-up`.

```bash
./scripts/wait-for-stack.sh
```
