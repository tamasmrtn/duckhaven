# Troubleshooting

Common issues and where to look. For agent-specific depth, see the
[Agent reference](../reference/agent-reference.md#troubleshooting).

## An agent shows "unavailable"

- Check the agent's logs (`docker logs duckhaven-agent`).
- Verify `CONTROL_PLANE_URL` is reachable from the agent host.
- Confirm the bootstrap token has not expired (24-hour TTL) — mint a fresh one and re-register.
- Make sure the control plane's `/agents/connect` WebSocket is not blocked by a firewall or proxy timeout.

## Queries fail with extension errors

The chosen agent is missing the DuckDB extension the workspace's [storage backend](../concepts/storage-backends.md)
requires. Pick a compatible agent (incompatible ones are shown disabled) or rebuild the agent image with the extension.

## Result rows fail to load

- Confirm the agent is still running and reachable.
- A result that aged past the retention window is swept on the agent; re-run the query from its saved SQL.

## Login appears to fail silently behind a proxy

If the API is served over HTTPS, set `COOKIE_SECURE=true` so the browser keeps the session cookie. See
[Reverse proxy & TLS](../deployment/reverse-proxy-tls.md).

## Agent disconnects frequently

Check network stability between the agent and control plane. Behind NAT, ensure long-lived WebSocket connections are
not dropped; raise the proxy read timeout if one fronts the API.

## Related

- [Operator runbook](runbook.md) — startup, agents, backups, and concurrency.
- [Monitoring](monitoring.md) — live utilization and the audit log.
