# Security

topicast is built for a private network. It can be exposed publicly behind TLS, but the
defaults assume you control who can reach the port.

## Keys

A key looks like `tc_1a2b3c4d_<48 hex chars>`:

- `tc_` makes it recognisable to secret scanners.
- The middle part is the public id, stored in clear and used for lookup.
- Only `HMAC-SHA256(TOPICAST_SECRET_KEY, key)` is stored. The key itself is shown once.
- Comparison is constant-time. A failed attempt logs the path and client, never the key.

`TOPICAST_SECRET_KEY` is the pepper. Keep it out of the image and out of version control;
changing it invalidates every key at once — which is also your break-glass move.

## Scopes and aliases

Give each service its own key with the least it needs:

```bash
topicast keys create grafana   --scope hooks --alias alerts
topicast keys create deploy-ci --scope send  --alias deploys
topicast keys create ops-tool  --scope send --scope edit --alias '*'
```

| Scope | Allows |
|---|---|
| `send` | `POST /v1/messages`, `GET /v1/messages/{id}` |
| `edit` | `PATCH` and `DELETE` on messages |
| `hooks` | `POST /v1/hooks/...` only |

A key restricted to `alerts` gets `403 alias_not_allowed` for any other alias, and a `hooks`
key cannot post to `/v1/messages` at all — which is what makes it safe to put in a URL.

Revoke immediately when a service is retired or a key leaks:

```bash
topicast keys revoke grafana
```

## Webhook tokens in URLs

Most webhook senders cannot set headers, so the key travels as `?token=`. That means it can
appear in proxy logs and browser history. Mitigations, in order of importance:

1. Only ever use `--scope hooks` keys there, scoped to one alias.
2. topicast never logs query strings.
3. Verify signatures where the sender supports them — set `hooks.github.secret`.
4. Rotate the key if the URL was shared.

## Exposure

- **Published Docker ports bypass UFW and firewalld.** `ports: "8080:8080"` is reachable from
  the internet even when your firewall denies it. Bind to `127.0.0.1` or a VPN address.
- Disable the public surfaces if the port is shared:
  `TOPICAST_DOCS_ENABLED=false`, `TOPICAST_METRICS_ENABLED=false`.
- Terminate TLS in a reverse proxy; topicast speaks plain HTTP.
- Set `TOPICAST_TRUSTED_PROXIES` to your proxy so forwarded client IPs are trustworthy.

## Input handling

- **Markup**: text is passed to Telegram as sent. If you interpolate user input into `HTML` or
  `MarkdownV2`, escape it on your side — topicast escapes only the level prefix it adds.
- **Templates**: generic webhook templates run in a Jinja2 sandbox
  (`ImmutableSandboxedEnvironment`), which blocks attribute traversal and mutation, but a
  template still decides what is sent. Only add templates you trust.
- **Uploads**: files are streamed to `/data/spool` with generated names — the client-supplied
  filename is only forwarded to Telegram, never used as a path. Size is capped by
  `TOPICAST_MAX_UPLOAD_MB`.
- **Media by URL**: Telegram fetches the URL, not topicast, so a URL cannot be used to probe
  your internal network through this service.
- **Bodies**: webhook payloads are capped at 1 MB.

## What topicast does not do

- No user accounts, sessions or cookies — keys only.
- No inbound Telegram updates: it never polls `getUpdates` and never exposes a Telegram webhook.
- No message content in metrics; logs hold a message id and alias, not the text.

## Reporting a vulnerability

Privately, through
[GitHub Security Advisories](https://github.com/develoverli/topicast/security/advisories/new).
See [SECURITY.md](https://github.com/develoverli/topicast/blob/main/SECURITY.md).
