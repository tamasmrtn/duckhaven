# Elastic compute on a single Docker host

This page enables [elastic compute](../concepts/elastic-compute.md) with the **Docker** backend,
provisioning agents as containers on the machine already running your stack. It is the homelab
counterpart to [Elastic compute on Azure](azure-elastic-setup.md): same auto-terminating model, one
box, no cloud account.

Everything is in one override file, `deploy/docker-compose.elastic.yml`:

```bash
docker compose -f deploy/docker-compose.yml \
               -f deploy/docker-compose.elastic.yml up -d
```

That points the API at the Docker backend, adds a socket proxy between the two, and removes the
always-on `agent` service — because scaling to zero is the point. Run a query against the elastic
pool and an agent appears; leave it idle and it goes away.

## Read this before you enable it

Provisioning containers means giving the control plane a path to the Docker daemon, and **creating a
container is close to being root on the host**. A container can ask for a bind mount of `/` and for
`Privileged: true`, and the daemon will grant it.

The override does not hand over the socket directly. It runs
[`docker-socket-proxy`](https://github.com/Tecnativa/docker-socket-proxy), which sits between the
API and the daemon and allows only the calls the backend actually makes. **Be clear about what that
does and does not buy you:**

| | |
|---|---|
| **Does** | Denies `/exec`, so the control plane cannot run commands inside other containers |
| | Denies `/secrets`, `/configs`, `/swarm`, `/nodes` |
| | Denies `/volumes`, so it cannot enumerate storage the deployment does not own |
| | Keeps the socket off the API container's filesystem, so a file-read bug in the API no longer hands over the daemon |
| **Does not** | Inspect request bodies. It filters paths and methods only |
| | Prevent a `POST /containers/create` carrying `Binds: ["/:/host"]` and `Privileged: true` — that request is indistinguishable from a legitimate agent create |

It is also worth being clear about what the proxy does **not** hide. With `CONTAINERS`, `IMAGES`,
`NETWORKS` and `INFO` granted, the control plane can list every container, image and network on
this host and read its CPU, memory and kernel details. Those are reads it needs to do its job, but
they cover the whole host rather than only what DuckHaven created.

So the proxy narrows *what the control plane can reach*. It does not make container creation
unprivileged, and it is not a sandbox.

!!! warning "The real bound is the daemon, not the proxy"
    If you want container creation to stop short of host root, configure the daemon that way. Both
    options below bound the blast radius regardless of what a compromised control plane asks for,
    and neither is enabled by this override — they are host configuration you apply yourself.

- **[Rootless mode](https://docs.docker.com/engine/security/rootless/)** — the daemon and every
  container run as an unprivileged user, so a container that maps "root" maps to your user.
- **[userns-remap](https://docs.docker.com/engine/security/userns-remap/)** — keeps the daemon as
  root but remaps container UIDs into an unprivileged range.

This is a genuine step down from the Azure deployment, where the control plane holds a custom role
scoped to a single resource group and Azure Resource Manager enforces that scope. Docker has no
equivalent notion of scope.

## What the proxy is allowed to do

Every section not listed here stays denied — that is the image's default, and it covers `AUTH`,
`SECRETS`, `CONFIGS`, `EXEC`, `SWARM`, `NODES`, `TASKS`, `SERVICES`, `PLUGINS`, `VOLUMES`, `SYSTEM`,
`BUILD` and `COMMIT`.

| Grant | Why the backend needs it |
|---|---|
| `POST` | The master write switch. Without it every allowed section is read-only |
| `CONTAINERS` | Create, inspect and remove agents, and list them for the leak sweep |
| `ALLOW_START` | Start a created container |
| `ALLOW_STOP`, `ALLOW_RESTARTS` | Terminate an agent when it goes idle |
| `IMAGES` | Pull the agent image the first time, on a host that has never run a static agent |
| `NETWORKS` | Resolve the agent network when attaching a new container to it |
| `INFO` | Read this host's CPU and memory, so the create-agent dialog can offer sizes it will actually run |

`tests/deploy/test_compose_elastic.py` asserts both halves of this — the grants **and** the denials —
so widening the proxy cannot land unnoticed.

## Agent networking

Provisioned agents join `duckhaven_internal`, the same isolated network the static agent already
sits on alone. It is declared `internal: true`, so an agent has no route off the host: it reaches
the API, Polaris, MinIO and the OTLP collector, and nothing else. No ports are published, so an
agent's result server is reachable from the control plane and from nowhere else.

That is the single-host equivalent of the Azure design's delegated subnet and its network security
group, and it needs no configuration — it is the network the stack already has.

If a catalog uses external storage (`s3`, `adls_gen2`) an agent needs egress and this restriction
gets in the way; see `deploy/docker-compose.egress-opt-out.yml` and understand the trade before
using it.

## Agent hardening

An agent you are given is contained exactly as tightly as one you start by hand. The backend
reproduces the static agent's sandbox in every provisioned container:

- read-only root filesystem, with a `tmpfs` on `/tmp` and a private volume for results
- `no-new-privileges`
- all Linux capabilities dropped
- a pids cap, and a concrete memory limit for the agent's cgroup-aware sizing to read

This matters because the API's [statement policy](../concepts/query-execution.md), not a hard SQL
allowlist, governs what reaches DuckDB — so the agent is contained at the OS layer instead. Anything
less on the elastic path would be a downgrade disguised as a feature.

Agent-side tracing is forwarded automatically: whatever `OTEL_EXPORTER_OTLP_ENDPOINT` the control
plane is configured with is passed to each agent, so spans keep flowing after the static agent is
taken out. Anything else you tuned on a static agent goes in `ELASTIC_AGENT_ENV`.

## Sizing

The create-agent dialog's sliders are bounded by **this host**, read from `docker info` — not by a
fixed constant. The maximum offered is the machine's capacity minus a reserve for everything else
on it:

```text
max vCPU  = host vCPU   - ELASTIC_DOCKER_RESERVE_CPU
max GiB   = host memory - ELASTIC_DOCKER_RESERVE_MEMORY_GB
```

The reserve exists because a single-host deployment runs the API, Postgres, Polaris and MinIO on
the same machine every agent lands on. Offering the whole host as an agent size would let one
query starve the stack serving it. Both floors are 1, so a small machine still offers a usable
size.

If the daemon cannot be reached — or `INFO` is not granted on the proxy — the range falls back to
a conservative 1–4 vCPU / 1–16 GiB rather than guessing high, since a size the platform then
refuses surfaces as a provisioning failure minutes later instead of a narrower slider now.

## Cost

The admin UI shows an hourly rate per agent size. On hardware you already own the marginal hourly
cost is zero, so the override zeroes it rather than leaving the Azure list prices it otherwise
defaults to — which would show cloud pricing for your own box.

If you want a real figure, set both variables to an electricity-derived rate:

```bash
ELASTIC_AZURE_PRICE_VCPU_HOUR=0.004
ELASTIC_AZURE_PRICE_MEMORY_GB_HOUR=0.0005
```

They read `AZURE` for historical reasons but apply to whichever provider is configured.

## Settings

| Variable | Default | Purpose |
|---|---|---|
| `ELASTIC_PROVIDER` | `null` | Set to `docker` for this backend |
| `ELASTIC_DOCKER_HOST` | `tcp://docker-socket-proxy:2375` | Where the daemon is reached. Point it at the proxy, not at a socket |
| `ELASTIC_DOCKER_NETWORK` | `duckhaven_internal` | The network agents are attached to |
| `ELASTIC_DOCKER_RESERVE_CPU` | `1` | vCPU held back from agent sizing for the rest of the stack |
| `ELASTIC_DOCKER_RESERVE_MEMORY_GB` | `2` | Memory held back, same |
| `ELASTIC_DEFAULT_CPU` | `2` | vCPU per agent when nothing names a size (provider-independent) |
| `ELASTIC_DEFAULT_MEMORY_GB` | `4` | Memory per agent, same |
| `ELASTIC_AGENT_ENV` | `{}` | JSON object of extra environment for every provisioned agent, for anything you tuned on a static one |

The lifecycle knobs — `ELASTIC_IDLE_TIMEOUT_S`, `ELASTIC_MAX_LIFETIME_S`,
`ELASTIC_MAX_AGENTS_PER_POOL` and the rest — are provider-independent and documented in the
[configuration reference](../reference/configuration.md).

## Verifying it

Check the proxy is reachable from the API and from nowhere else:

```bash
# Works: the API can list containers through the proxy.
docker compose exec api curl -sf docker-socket-proxy:2375/v1.44/containers/json

# Refused: exec and secrets are denied even to the API.
docker compose exec api curl -s -o /dev/null -w '%{http_code}\n' \
  docker-socket-proxy:2375/v1.44/secrets
```

Then run a query against the elastic pool and watch an agent appear, serve it, and go away:

```bash
docker ps --filter label=duckhaven-managed=true
```

Leave it idle past `ELASTIC_IDLE_TIMEOUT_S` and the reaper terminates it. A container carrying that
label with no live agent row is treated as a leak and removed on the next reaper cycle — which is
why nothing else should ever wear it.

## Related

- [Elastic compute](../concepts/elastic-compute.md) — what the lifecycle does and why.
- [Elastic compute on Azure](azure-elastic-setup.md) — the same feature against Container Instances.
- [Add an agent](add-agent.md) — running a static agent by hand, which still works alongside.
