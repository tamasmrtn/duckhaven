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

## Roll back

If a release breaks you, set `DUCKHAVEN_IMAGE_TAG` in `.env` to the previous
known-good tag and `docker compose up -d`.

## Agents

Update each agent host independently (the protocol is forward-compatible):

```bash
docker pull ghcr.io/tamasmrtn/duckhaven-agent:latest
docker restart duckhaven-agent
```
