---
type: how-to
---

# Update DuckHaven

Pull new images and restart the stack; database migrations run on their own when the API starts.

## Before you begin

- **You need:** shell access to the control-plane host, and to each agent host you run.
- **Back up first:** take a [backup](backup-restore.md) of the databases and the secret key.
- **Check the API version:** if the update changes `api_version`, upgrade API clients first; see
  [below](#upgrade-api-clients-first).

## Default — track `:latest`

```bash
docker compose pull
docker compose up -d
```

By default the compose file pulls `ghcr.io/tamasmrtn/duckhaven-api:latest`,
which is rebuilt on every push to `main`. Same for the agent image on each
agent host.

## Pin a release

For predictable upgrades, pin to a release tag. Add to `.env`:

```sh
DUCKHAVEN_IMAGE_TAG=1.2.3
```

Then:

```bash
docker compose pull
docker compose up -d
```

Tags published per release: `:X.Y.Z`, `:X.Y`, `:X` (without the Git tag's `v`). Agent images are
also published per [runtime](../compute/runtimes.md), as `:X.Y.Z-duckdb<runtime>`, and the
bundled agent runs the default runtime's.

## Upgrade API clients first

Before an update that changes the API's `api_version`, upgrade the tools that call it: scripts, dbt, dlt and BI
connectors. Moving to `api_version` 3 (v0.9.0 and later, and `:latest`) needs
`duckhaven-sql-connector` 0.8.0 or later, which `dbt-duckhaven` and `dlt-duckhaven` use, and `dh` 0.4.0 or later. What
each version changes is in [API versions](../reference/api-versions.md).

## One-time: updating past the Chainguard base image migration

The `api` and `agent` images moved from a Debian-based base to Chainguard's distroless base, which runs as a fixed
non-root user (UID/GID `65532`) instead of the dynamically-allocated `duckhaven` user the old images used. Existing
volumes (`api_data`, `agent_results`) still have files owned by the old UID; the new image can read them but can't
write new files into directories it doesn't own, so the agent in particular will fail to write result files after the
upgrade until ownership is fixed.

If you're updating an **existing** deployment (not a fresh install), run this once per volume before starting the new
containers, using the currently-running (pre-migration) image, which still has a shell:

```bash
docker compose stop api agent
docker run --rm --user root -v <project>_api_data:/data <old-api-image> chown -R 65532:65532 /data
docker run --rm --user root -v <project>_agent_results:/data <old-api-image> chown -R 65532:65532 /data
docker compose pull
docker compose up -d
```

(Any image still holding a shell works for the `chown` step — the old `api` image is the simplest choice since it's
already pulled.) Fresh installs are unaffected — the new images pre-create these directories owned by `65532` already.

## Roll back

If a release breaks you, set `DUCKHAVEN_IMAGE_TAG` in `.env` to the previous
known-good tag and `docker compose up -d`.

## Agents

Update each agent host independently (the protocol is forward-compatible):

```bash
docker pull ghcr.io/tamasmrtn/duckhaven-agent:latest-duckdb1.5
docker restart duckhaven-agent
```

Pull the tag for the runtime the host runs. Plain `:latest` keeps working as the default runtime's
image. **Upgrade the control plane first** whenever a release adds a runtime you want to use: an
agent reporting a runtime the control plane doesn't know is refused work until the control
plane is upgraded too.

Elastic compute follows the control plane on its own. A provisioned or restarted agent runs its
runtime's image at the control plane's version, so after an upgrade each agent picks up the new
build the next time it starts. Agents that are already running keep their old build until the idle
reaper stops them, or you do. On the Docker backend a `:latest-duckdb…` tag is only pulled when the
host doesn't have it, so if you ride `:latest`, `docker compose pull` those images yourself.

## Changing the default runtime

The default runtime (`DEFAULT_RUNTIME` in `.env`) is what the bundled agent and auto-provisioned
compute run. To move it, for example from a runtime that is being deprecated:

1. Set `DEFAULT_RUNTIME` to the new runtime and `docker compose up -d`. The bundled agent is
   recreated on the new image, and the API starts provisioning pool compute on it.
2. Existing elastic agents keep their runtime. Pool agents on the old one idle out; recreate compute
   you created by hand if it should move.
3. Check [DuckLake catalog formats](../compute/runtimes.md#mixing-runtimes-on-the-same-data) first:
   an agent on a runtime whose DuckLake format differs from a catalog's is refused on that catalog.

## Check that it worked

`curl -s http://localhost:8000/api/version` reports the new `version`, the API is healthy in `docker compose ps`, and
your agents show `healthy` under **Compute**.

## If it goes wrong

- **The API keeps restarting after the update.** Read `docker compose logs api`; a failed migration says so there.
  [Roll back](#roll-back) to the previous tag and restore the backup you took.
- **Postgres keeps restarting.** The image moved to a new Postgres major version; see
  [Upgrade Postgres to a new major version](backup-restore.md#upgrade-postgres-to-a-new-major-version).
- **Agents are refused work after the update.** They run a runtime the control plane does not know, or an image that
  is too old; see [Troubleshoot compute](../compute/troubleshooting.md).
- **The agent cannot write results after moving to the distroless images.** Its volumes still belong to the old
  user; see the one-time step [above](#one-time-updating-past-the-chainguard-base-image-migration).

## Related

- [API versions](../reference/api-versions.md) — what each `api_version` changed.
- [Backup and restore](backup-restore.md)
- [Release notes](../release-notes/index.md)
