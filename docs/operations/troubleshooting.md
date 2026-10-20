---
type: troubleshooting
---

# Troubleshoot the deployment

Find what you see on the control-plane host, behind your proxy or on the network, then follow what to do.

Problems with one subject have their own pages: [sign-in and access](../access/troubleshooting.md),
[queries](../querying/troubleshooting.md) and [compute](../compute/troubleshooting.md). Start with the logs:
`docker compose logs api`, `docker compose logs agent`, or any other service of the stack.

## You need a shell inside the `api` or `agent` container

**What it means.** Both images are distroless: no shell, package manager or coreutils, so `docker compose exec api sh`
fails. Logs, `docker inspect`, `docker stats`, `docker top` and the health checks all work from outside, as they use
the Docker API.

**What to do.**

- To read a file, copy it out: `docker compose cp api:/var/duckhaven/setup_token ./setup_token`. This does not run
  anything in the container.
- For a shell, attach a debug sidecar with `docker debug ⟨container⟩`, which works on distroless images. It ships with
  Docker Desktop; check it exists on your host's Docker before you rely on it.
- As a last resort, run the service temporarily on a `-dev` build of the base image, which has a shell, knowing it is
  not the exact production image.

## Sign-in succeeds, then returns to the login page

**What it means.** The browser rejected the session cookie, usually because `COOKIE_SECURE` does not match how users
reach DuckHaven.

**What to do.** See [Troubleshoot sign-in and
access](../access/troubleshooting.md#sign-in-seems-to-succeed-then-returns-to-the-login-page) and [Reverse proxy &
TLS](../deployment/reverse-proxy-tls.md).

## Nothing is reachable, and the network is Tailscale

**What it means.** Where Tailscale is the only path to the control plane and its agents, a Tailscale outage makes
DuckHaven unreachable. Agents reconnect every 5 seconds and come back on their own once the tailnet recovers.

**What to do.** Keep a record of the control plane's and agents' Tailscale addresses, so you can check each is
reachable, with `tailscale ping`, when the tailnet returns.

## Bursts of "IO Error: Could not connect to server" naming the object store

**What it means.** Many concurrent queries fail at once with errors naming the object store, the store answers fine
from elsewhere (`curl http://⟨store⟩:9000/health/ready` returns 200), its logs show nothing, and the errors clear
within about a minute. The agent ran out of outbound ports: each connection to the store waits a minute in `TIME_WAIT`
after it closes.

**What to do.** Confirm it by counting the agent's sockets in `TIME_WAIT`:

```bash
docker exec ⟨agent container⟩ python3 -c "
n=0
for f in ('/proc/net/tcp','/proc/net/tcp6'):
    for line in open(f).read().splitlines()[1:]:
        if line.split()[3]=='06': n+=1
print('TIME_WAIT:', n)
print('port range:', open('/proc/sys/net/ipv4/ip_local_port_range').read().strip())"
```

A count near the width of the port range (28,232 for the default 32768–60999) is the cause. Agents reuse connections
with `httpfs_connection_caching`, which keeps the count in the hundreds; an agent that hits this runs an image that
does not. Run the current agent image; widening the port range only delays the failure. See
[Connection reuse](../data/storage-backends.md#connection-reuse).

## Postgres restarts in a loop after an update

**What it means.** The Postgres image moved to a new major version, which cannot start on the old data directory.

**What to do.** Go back to the previous image tag, then follow
[Upgrade Postgres to a new major version](../deployment/backup-restore.md#upgrade-postgres-to-a-new-major-version).

## A setting in `.env` has no effect

**What it means.** The container was not recreated after the change, or the setting is not passed to it.

**What to do.** Run `docker compose up -d`, which recreates containers whose configuration changed; a plain restart
keeps the old environment. To see what the container received, run
`docker inspect ⟨container⟩ --format '{{json .Config.Env}}'`; `exec … env` does not work on the distroless image.

## Related

- [Monitoring](monitoring.md) — metrics and alerts.
- [Backup and restore](../deployment/backup-restore.md)
- [Update DuckHaven](../deployment/updating.md)
