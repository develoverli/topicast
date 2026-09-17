# Deployment

topicast is one container plus one SQLite file on a volume.

!!! danger "Run one instance"
    The delivery worker runs inside the API process and SQLite is a single-writer database.
    Do not scale the service to several replicas — they would fight over the queue.

## Docker Compose

```yaml
services:
  topicast:
    image: ghcr.io/develoverli/topicast:latest
    restart: unless-stopped
    env_file: [.env]
    ports:
      - "127.0.0.1:8080:8080"
    volumes:
      - topicast-data:/data
      - ./config.yaml:/config/config.yaml:ro

volumes:
  topicast-data:
```

Pin the tag (`:0.1.0`) in production and let Dependabot or Renovate bump it.

!!! warning "Published ports bypass the host firewall"
    Docker writes its own iptables rules, so `ports: "8080:8080"` is reachable from outside even
    when UFW denies that port. Bind to `127.0.0.1`, to a VPN address, or keep the port internal
    and let a reverse proxy reach it over the Docker network.

## Behind a reverse proxy

=== "Caddy"

    ```caddy
    topicast.example.com {
        reverse_proxy topicast:8080
    }
    ```

=== "Traefik (labels)"

    ```yaml
    labels:
      - traefik.enable=true
      - traefik.http.routers.topicast.rule=Host(`topicast.example.com`)
      - traefik.http.routers.topicast.entrypoints=websecure
      - traefik.http.routers.topicast.tls.certresolver=letsencrypt
      - traefik.http.services.topicast.loadbalancer.server.port=8080
    ```

=== "nginx"

    ```nginx
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        client_max_body_size 55m;   # uploads
    }
    ```

Set `TOPICAST_TRUSTED_PROXIES` to the proxy's address so client IPs are logged correctly.

Exposing it publicly? Read [Security](security.md) first — at minimum disable `/docs` and
`/metrics`, and consider restricting by IP.

## On a private network (Tailscale, WireGuard, LAN)

The simplest safe setup: bind the port to the VPN interface and skip TLS entirely.

```yaml
ports:
  - "100.x.y.z:8080:8080"     # the host's Tailscale address
```

Client containers on another host reach it by hostname when their host is on the same tailnet:

```yaml
services:
  my-app:
    extra_hosts:
      - "vpn-host:100.x.y.z"
    environment:
      TOPICAST_URL: http://vpn-host:8080
```

`extra_hosts` avoids depending on MagicDNS being ready when the container starts.

## Dokploy

1. **Create Application** → source: this repository (or the GHCR image).
2. **Environment**: paste your `.env` values.
3. **Volumes**: mount `topicast-data` at `/data`, and your `config.yaml` at
   `/config/config.yaml`.
4. **Ports**: publish the same port the container listens on
   (`TOPICAST_PORT=8080` → published `8080`, target `8080`).
5. **Domain**: add one if you want TLS through Traefik.

## Kubernetes

A single-replica `Deployment` with a `PersistentVolumeClaim` for `/data`, `strategy:
type: Recreate` (never two writers), and probes:

```yaml
livenessProbe:
  httpGet: { path: /healthz, port: 8080 }
readinessProbe:
  httpGet: { path: /readyz, port: 8080 }
  periodSeconds: 30
```

Mount `config.yaml` from a `ConfigMap` and the secrets from a `Secret`.

## Without containers

```bash
uv tool install git+https://github.com/develoverli/topicast
export TOPICAST_SECRET_KEY=... TELEGRAM_BOT_TOKEN=... TELEGRAM_CHAT_ID=...
topicast serve
```

systemd unit:

```ini
[Unit]
Description=topicast
After=network-online.target

[Service]
Type=simple
User=topicast
WorkingDirectory=/opt/topicast
EnvironmentFile=/opt/topicast/.env
ExecStart=/opt/topicast/.venv/bin/topicast serve
Restart=on-failure
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/topicast/data

[Install]
WantedBy=multi-user.target
```

## Upgrading

```bash
docker compose pull && docker compose up -d
```

Schema migrations run automatically at startup. Back up `/data/topicast.db` first if you care
about the history; the queue itself is usually a few rows.

## Backups

Copy the SQLite file safely while the service runs:

```bash
docker compose exec topicast \
  python -c "import sqlite3; sqlite3.connect('/data/topicast.db').backup(sqlite3.connect('/data/backup.db'))"
```

The database holds API key hashes and message history. Treat the backup as a secret.
