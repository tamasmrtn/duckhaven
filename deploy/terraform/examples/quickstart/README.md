# Quickstart

The cheapest DuckHaven deployment that actually works. Use it to try DuckHaven on Azure,
or as a disposable environment.

Four inputs: a region, a suffix that makes globally-unique names yours, an image tag, and
an environment name.

```sh
export ARM_SUBSCRIPTION_ID=<your subscription>

terraform init -backend-config=backend.hcl
terraform apply \
  -var location=swedencentral \
  -var name_suffix=abc123 \
  -var duckhaven_image_tag=v1.0.0
```

## What is reduced, and what is not

Reduced: SKUs and replica counts. Burstable PostgreSQL at the 32 GiB storage floor with
no standby, LRS storage, a Basic registry, one small replica of each app, and a 1 GB/day
cap on log ingestion.

**Not reduced: the network.** Private endpoints, VNet injection, the delegated agent
subnet and its security group are identical to `../production`. Agents move and query
real data over these paths, so the isolation is the deployment rather than a tier of it.
Private endpoints are consequently the largest line item here after PostgreSQL, at
roughly $7/month each.

The registry SKU costs nothing in security either way — every image pull is
authenticated by a managed identity holding `AcrPull`, on Basic exactly as on Premium.

## Elastic compute is off

So is the NAT gateway, and they belong together: Azure has retired default outbound
access, so a VNet-injected agent has no route to the API's public ingress without the
gateway. It would provision, fail to register, and be reaped at the provisioning
deadline. The module rejects that combination rather than letting it happen.

Both bill hourly whether or not traffic flows, which is why they are off by default.
Turn them on together for a session that exercises agents:

```sh
terraform apply ... -var nat_gateway_enabled=true -var elastic_compute_enabled=true
```

…after adding those two as variables here, or by editing `main.tf` — this root sets them
directly, since a quickstart that asks about NAT gateways is not a quickstart.

## Destroying is the real saving

Everything bills for existing, not for being used, and the whole environment is
reproducible from one `terraform apply`. `terraform destroy` between sessions saves more
than any SKU choice. `az postgres flexible-server stop` pauses compute for up to 7 days
if the data needs keeping.

See [`../../README.md`](../../README.md) for the first-apply sequence, which is staged
regardless of which example you use.
