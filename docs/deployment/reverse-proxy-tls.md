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

## Nginx Proxy Manager

If you already run [Nginx Proxy Manager](https://nginxproxymanager.com/) for
other services, point it at DuckHaven instead of running Caddy. NPM gives
you a GUI for proxy hosts and Let's Encrypt; the trade-off vs Caddy is one
extra container and a few clicks per host.

### Put DuckHaven and NPM on a shared docker network

NPM can only reach the api container by name if both are on the same docker
network. Easiest pattern: create an external network and attach both stacks
to it.

```bash
docker network create proxy
```

In NPM's compose file, attach the `app` service to `proxy`:

```yaml
services:
  app:
    image: jc21/nginx-proxy-manager:latest
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
      - "81:81"   # admin UI
    volumes:
      - npm_data:/data
      - npm_letsencrypt:/etc/letsencrypt
    networks:
      - proxy

networks:
  proxy:
    external: true

volumes:
  npm_data:
  npm_letsencrypt:
```

In DuckHaven's compose file, add the same external network to the api
service and drop the direct port 8000 publish:

```yaml
services:
  api:
    # ports: []   # remove the "8000:8000" publish — NPM is the only ingress
    networks:
      - default
      - proxy

networks:
  proxy:
    external: true
```

### Create the proxy host in the NPM UI

1. Open NPM's admin UI on port 81 and sign in (default `admin@example.com`
   / `changeme` — change on first login).
2. **Hosts → Proxy Hosts → Add Proxy Host**.
   - **Domain Names:** `duckhaven.example.com`
   - **Scheme:** `http`
   - **Forward Hostname / IP:** `api` (the compose service name)
   - **Forward Port:** `8000`
   - **Cache Assets:** off
   - **Block Common Exploits:** on
   - **Websockets Support:** **on** *(required — the agent
     `/agents/connect` endpoint is a WebSocket)*
3. **SSL** tab:
   - **SSL Certificate:** *Request a new SSL Certificate*
   - **Force SSL:** on
   - **HTTP/2 Support:** on
   - **Use a DNS Challenge:** only if your host isn't publicly reachable on
     port 80 (Let's Encrypt's HTTP-01 challenge needs that).
4. Save. NPM provisions the cert and reloads in a few seconds.

### Bump the WebSocket timeout

NPM inherits nginx's 60-second `proxy_read_timeout`, which closes idle
WebSocket connections — agents will reconnect every minute, spamming the
audit log. Open the proxy host's **Advanced** tab and paste:

```nginx
proxy_read_timeout 86400s;
proxy_send_timeout 86400s;
```

24 hours is conventional for long-lived WebSockets; lower it if you prefer
shorter sessions.

### Set `COOKIE_SECURE=true`

Same as the Caddy path: NPM terminates TLS, so the api must issue
`Secure`-flagged cookies or the browser will drop them. Add to DuckHaven's
`.env`:

```sh
COOKIE_SECURE=true
```

NPM forwards `X-Forwarded-Proto: https` and `X-Forwarded-Host: <your
hostname>` by default — both are what DuckHaven reads to derive the agent
`control_plane_url`, so the add-agent dialog will render `wss://...`
snippets correctly with no extra config.

## What the agent sees

`POST /admin/agents/bootstrap` reads `X-Forwarded-Proto` / `X-Forwarded-Host`
(set automatically by Caddy and most other proxies). The compose snippet
generated in the admin UI uses `wss://duckhaven.example.com/agents/connect`
when fronted by TLS, `ws://...` otherwise.

## Private-network deploys

For Tailscale / WireGuard / VPN-only deploys, you can skip TLS termination
entirely — the tunnel encrypts the wire. Leave `COOKIE_SECURE=false` (the
default) and address the api by its private hostname.
