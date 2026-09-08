# Update DuckHaven

Updates pull a new image and restart the stack. Migrations apply automatically
when the api container starts.

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
DUCKHAVEN_IMAGE_TAG=v1.2.3
```

Then:

```bash
docker compose pull
docker compose up -d
```

Tags published per release: `:vX.Y.Z`, `:vX.Y`, `:vX`.

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
docker pull ghcr.io/tamasmrtn/duckhaven-agent:latest
docker restart duckhaven-agent
```
