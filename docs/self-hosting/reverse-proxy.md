# Reverse proxy + TLS

The bundled `api` service publishes plain HTTP on port 8000. For any deploy
reachable beyond a private network, front it with a reverse proxy that
terminates TLS. Caddy is the smallest path; nginx / Traefik / Cloudflare
Tunnel all work equally well.

## Caddy (recommended)

`Caddyfile`:

```caddy
duckhaven.example.com {
    encode gzip
    reverse_proxy api:8000
}
```

Add Caddy alongside the existing stack as a compose overlay
(`docker-compose.tls.yml`):

```yaml
services:
  caddy:
    image: caddy:2-alpine
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile:ro
      - caddy_data:/data
      - caddy_config:/config
    depends_on:
      api:
        condition: service_healthy

  # Drop the direct port 8000 publish; only Caddy is exposed.
  api:
    ports: []

volumes:
  caddy_data:
  caddy_config:
```

Launch with the overlay:

```bash
docker compose -f docker-compose.yml -f docker-compose.tls.yml up -d
```

Set `COOKIE_SECURE=true` in `.env` so the api issues `Secure`-flagged session
cookies; otherwise the browser drops them and login appears to silently fail.

Caddy auto-provisions a Let's Encrypt cert for the hostname on first request.

## What the agent sees

`POST /admin/agents/bootstrap` reads `X-Forwarded-Proto` / `X-Forwarded-Host`
(set automatically by Caddy and most other proxies). The compose snippet
generated in the admin UI uses `wss://duckhaven.example.com/agents/connect`
when fronted by TLS, `ws://...` otherwise.

## Private-network deploys

For Tailscale / WireGuard / VPN-only deploys, you can skip TLS termination
entirely — the tunnel encrypts the wire. Leave `COOKIE_SECURE=false` (the
default) and address the api by its private hostname.
