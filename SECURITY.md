# Security Policy

## Supported versions

Only the latest release receives security fixes.

| Version | Supported |
|---------|-----------|
| latest | :white_check_mark: |
| older | :x: |

## Reporting a vulnerability

**Please do not report security vulnerabilities through public GitHub issues, pull requests,
or discussions.**

Report privately through GitHub's private vulnerability reporting:

1. Go to the [**Security** tab](https://github.com/develoverli/topicast/security) of this repository.
2. Click **Report a vulnerability**
   ([direct link](https://github.com/develoverli/topicast/security/advisories/new)).
3. Include as much of the following as you can:
   - Type of issue (e.g. authentication bypass, SSRF, template injection, information disclosure).
   - Affected version and file(s).
   - Step-by-step instructions to reproduce.
   - Proof of concept, if available.
   - Impact — what an attacker could achieve.

**Never include real bot tokens, API keys or `TOPICAST_SECRET_KEY` values in a report.**

## What to expect

- Acknowledgement within **7 days**.
- A status update (confirmed / not reproducible / needs more info) within **14 days**.
- Once fixed, a new release is published and the advisory is disclosed with credit to the
  reporter (unless you prefer to stay anonymous).

## Deployment notes

topicast is designed for a private network. Keep these in mind:

- **Do not expose it directly to the internet** without a reverse proxy with TLS. Published
  Docker ports bypass host firewalls such as UFW — bind to `127.0.0.1` or a VPN interface.
- API keys are stored as HMAC-SHA256 hashes, salted with `TOPICAST_SECRET_KEY`. Keep that
  value secret; changing it invalidates every key.
- Webhook keys travel in the URL. Create them with `--scope hooks` only, and scope them to a
  single alias. URLs may be logged by proxies — topicast itself never logs query strings.
- GitHub webhooks are only signature-verified when `hooks.github.secret` is set. Set it.
- Generic webhook templates are rendered in a Jinja2 sandbox, but a template is code:
  only accept templates from people you trust with the deployment.
- `/metrics` and `/docs` are unauthenticated. Disable them with `TOPICAST_METRICS_ENABLED=false`
  and `TOPICAST_DOCS_ENABLED=false` if the port is reachable by others.
- Uploaded files are spooled to `/data/spool` until delivery, then deleted.

## Scope

In scope: authentication and scope enforcement, key handling, webhook signature verification,
template sandbox escapes, path traversal in uploads, and injection into Telegram messages.

Out of scope: vulnerabilities in Telegram itself, and any deployment that intentionally
exposes topicast without authentication in front of it.
