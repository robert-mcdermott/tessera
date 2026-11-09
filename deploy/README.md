# Running Tessera as a service

Reference files for a server. Copy them, change the names and paths, and read
[Running it as a service](../docs/operations.md#running-it-as-a-service) first.

| Path | What it is |
|---|---|
| `container/Dockerfile` | A container image built with uv from the lock file, running as an unprivileged user, with the configuration and the data home as volumes |
| `container/compose.yaml` | Tessera with its ledger in PostgreSQL 16, its first lines the steps to start it |
| `systemd/tessera.service` | The web app, sandboxed |
| `systemd/tessera-sync.service`, `.timer` | A sync every night |
| `systemd/tessera-backup.service`, `.timer` | A backup every night, before the sync |
| `proxy/Caddyfile` | Caddy in front of the app and the public site, with certificates it obtains itself |
| `proxy/nginx.conf` | The same for nginx |

Each was checked: the image builds and serves the demo organization, the compose file runs it on
PostgreSQL, the units pass `systemd-analyze verify`, and both proxy configurations pass their
own checks (`caddy validate`, `nginx -t`).
