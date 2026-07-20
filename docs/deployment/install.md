# Install DuckHaven

DuckHaven runs as one `docker compose` stack — Postgres, Apache Polaris, and the
DuckHaven API (which serves both the REST API and the web UI on port 8000). No
`git clone` is required.

## Prerequisites

- Linux host with Docker Engine 24+ and Docker Compose v2
- 8 GB RAM minimum
- (Recommended) Tailscale or another private network for ingress

## Install

```bash
curl -O https://raw.githubusercontent.com/tamasmrtn/duckhaven/main/deploy/docker-compose.yml
docker compose up -d
```

On first boot the stack auto-generates `POSTGRES_PASSWORD`, `SECRET_KEY`, and
a one-shot first-admin setup token, and applies Alembic migrations
automatically. No `.env` editing required.

## Create the first admin

Read the setup token on the host:

```bash
docker compose exec api cat /var/duckhaven/setup_token
```

Open `http://<host>:8000` in a browser. The SPA detects an empty database and
routes you to the setup screen — paste the token, pick admin credentials,
submit. The token is consumed (deleted) after the admin is created and is not
regenerated on subsequent boots.

To start over, wipe the stack:

```bash
docker compose down -v
```

(This wipes Postgres, secrets, and the setup token.)

## Add agents

Agents are deployed to separate hosts. See [add-agent.md](./add-agent.md).

## Agent network egress

The bundled agent is attached to an **isolated Docker network with no route off the host**. It can reach the API,
Polaris, the object store, and the trace collector — and nothing else. Because agents run a broad SQL surface governed
by a statement policy rather than a hard allowlist, this is the layer that contains a statement trying to read from or
write to an arbitrary address. See [Sandboxing](../concepts/sql-sessions.md#sandboxing).

### When you must opt out

An agent needs real outbound access when any of these are true:

- a catalog's storage backend is external (`s3`, `adls_gen2`)
- Polaris runs off-host
- the OTLP collector runs off-host

Apply the shipped override:

```sh
docker compose -f deploy/docker-compose.yml \
               -f deploy/docker-compose.egress-opt-out.yml up -d
```

!!! warning "Opting out removes a security layer"
    With the agent back on the default network, the API statement policy is the only thing left between a session
    statement and arbitrary egress. Prefer restricting egress to the hosts you actually need — with a host firewall, or
    the `NetworkPolicy` below — over removing the restriction entirely.

### On Kubernetes

DuckHaven does not ship Kubernetes manifests yet, so this is a recipe rather than something the product applies for
you. A default-deny egress policy for the agent, allowing only DNS plus the hosts it needs:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: duckhaven-agent-egress
spec:
  podSelector:
    matchLabels:
      app: duckhaven-agent
  policyTypes:
    - Egress
  egress:
    # DNS resolution.
    - to:
        - namespaceSelector: {}
          podSelector:
            matchLabels:
              k8s-app: kube-dns
      ports:
        - protocol: UDP
          port: 53
    # The control plane, Polaris, and the object store.
    - to:
        - podSelector:
            matchLabels:
              app: duckhaven-api
        - podSelector:
            matchLabels:
              app: polaris
      ports:
        - protocol: TCP
          port: 8000
        - protocol: TCP
          port: 8181
    # External object storage, if your catalogs use it.
    - to:
        - ipBlock:
            cidr: 0.0.0.0/0
            except:
              - 169.254.169.254/32 # block the cloud metadata endpoint
      ports:
        - protocol: TCP
          port: 443
```

!!! warning "NetworkPolicy needs a CNI that implements it"
    A `NetworkPolicy` is inert unless your cluster runs a network plugin that enforces it (Calico, Cilium, Antrea,
    and others do). Applying this manifest on a cluster without one gives you **no protection at all** and no error.
    Confirm enforcement before relying on it.

## Next steps

- [Update](./updating.md) — pull a new release.
- [Reverse proxy + TLS](./reverse-proxy-tls.md) — front the stack with Caddy.
- [Backup and restore](./backup-restore.md) — protecting Postgres.
