# Alternative deployment: single Ubuntu VPS

> Back to the [main README](../README.md). Most people should use the
> [Render deployment](../README.md#deploy-to-render) instead; this guide is for
> running everything on a Linux server you manage yourself.

**Status: tutorial only; this deployment has not been executed end to end.**
Run these commands on your VPS, not on your development computer. The example uses Ubuntu 24.04 LTS, systemd, Nginx, and one domain.
Shell examples were syntax-checked, and the backup script was tested against a
temporary live WAL database. Nginx, systemd, and certificate issuance have not
been exercised on a VPS by this project yet.

## 1. Choose a server and prepare DNS

- Start with one Linux VPS with persistent local SSD storage. As an initial
  sizing estimate, 2 vCPUs and 4 GB RAM leave room to build Next.js on the server;
  this is not a measured production capacity guarantee. Monitor actual usage.
- Create an Ubuntu 24.04 LTS server and log in over SSH using a non-root account
  with `sudo`. Keep your SSH key available and follow your provider's SSH setup.
- Create a DNS **A** record for a hostname such as `llms.example.com`, pointing
  to the VPS's public IPv4 address. Only add an **AAAA** record if the server's
  IPv6 routing/firewall is configured too.
- Replace `YOUR_REPOSITORY_URL`, `YOUR_TESTED_COMMIT`, and `llms.example.com`
  throughout this tutorial. Push the completed project to the repository first;
  uncommitted local files are not included in a server-side Git clone. Use a
  read-only deploy key for a private repository, without embedding credentials
  in URLs or config files.

The deployed request path is:

```text
Internet → HTTPS Nginx :443 → Next.js 127.0.0.1:3000
                                  → FastAPI 127.0.0.1:8000 → SQLite
                                                            ↕
                                                  worker + scheduler
```

All browser traffic, **including `/api/*`**, goes to Next.js. Its existing proxy
forwards requests to FastAPI. Keep ports 3000 and 8000 private. Next.js recommends
placing a reverse proxy in front of a self-hosted Node server.
([Next.js self-hosting guide](https://nextjs.org/docs/app/guides/self-hosting))

## 2. Install system dependencies and create an application user

On the VPS:

```sh
sudo apt update
sudo apt install -y git curl ca-certificates xz-utils python3 python3-venv python3-pip nginx sqlite3 ufw snapd cron
sudo useradd --system --create-home --home-dir /opt/llms-txt --shell /usr/sbin/nologin llms
sudo install -d -o llms -g llms -m 0755 /opt/llms-txt
sudo install -d -o llms -g llms -m 0700 /var/lib/llms-txt /var/backups/llms-txt
sudo install -d -m 0750 /etc/llms-txt
```

Install **Node.js 24 LTS and npm system-wide** from the official Linux binary
distribution, matching your VPS architecture and checking its published checksum:

```sh
(
set -eu
node_install_dir="$(mktemp -d /tmp/llms-node.XXXXXX)"
cd "$node_install_dir"
case "$(uname -m)" in
  x86_64) node_arch=x64 ;;
  aarch64|arm64) node_arch=arm64 ;;
  *) printf 'Use the official Node installer for this CPU architecture.\n'; exit 1 ;;
esac
curl -fsSLO https://nodejs.org/dist/latest-v24.x/SHASUMS256.txt
node_archive="$(awk -v suffix="-linux-${node_arch}.tar.xz" 'substr($2, length($2)-length(suffix)+1) == suffix { print $2 }' SHASUMS256.txt)"
test -n "$node_archive"
curl -fsSLO "https://nodejs.org/dist/latest-v24.x/$node_archive"
awk -v name="$node_archive" '$2 == name' SHASUMS256.txt | sha256sum -c -
sudo tar -xJf "$node_archive" -C /usr/local --strip-components=1 --no-same-owner
)
hash -r
```

Run the lines in order and stop if downloading or checksum verification fails.
This installs the current Node 24 patch under `/usr/local`; for production
reproducibility, record the downloaded version and checksum with your release.
See the [official Node.js downloads](https://nodejs.org/en/download). Do not use
Ubuntu 24.04's older default Node package or a Node installation available only
inside your personal nvm shell. The service below assumes `/usr/local/bin/node`;
if using another system-wide installer, update that service path accordingly.

```sh
node --version
npm --version
python3 --version
command -v node
sudo -u llms /usr/local/bin/node --version
```

The last command must work with the **same Node path used in the service**.
`npm` must also be on the PATH used by the build commands below. Node 24 and
Python 3.12 from Ubuntu 24.04 satisfy this project's runtime requirements.

## 3. Clone a tested revision, install dependencies, and build

```sh
sudo -u llms -H git clone YOUR_REPOSITORY_URL /opt/llms-txt/app
sudo -u llms git -C /opt/llms-txt/app checkout --detach YOUR_TESTED_COMMIT
sudo -u llms python3 -m venv /opt/llms-txt/app/backend/.venv
sudo -u llms /opt/llms-txt/app/backend/.venv/bin/python -m pip install -r /opt/llms-txt/app/backend/requirements.txt

sudo -u llms -H sh -c 'cd /opt/llms-txt/app/backend && .venv/bin/python -m pytest -q'
sudo -u llms -H sh -c 'cd /opt/llms-txt/app/frontend && npm ci --include=dev && npm run lint && npm run build'
```

Stop if any check fails. Keep `frontend/node_modules` and `.next` on the server;
this repository uses a normal Next.js Node server, not a static export or a
standalone bundle. Do not copy a macOS virtual environment or `node_modules` to
Linux. Install dependencies on the target OS. No Chrome installation is needed
to run the application; Chrome is only needed for the optional browser tests.

Python dependencies currently use version ranges rather than a committed lock.
Record the installed versions alongside the commit for rollback:

```sh
sudo -u llms -H sh -c 'cd /opt/llms-txt/app && backend/.venv/bin/python -m pip freeze > /var/backups/llms-txt/python-$(git rev-parse --short HEAD).txt'
```

## 4. Configure persistent production environment files

Use `sudoedit /etc/llms-txt/backend.env` to create:

```dotenv
LLMS_DB_PATH=/var/lib/llms-txt/llms.sqlite3
LLMS_CHECK_INTERVAL=86400
LLMS_POLL_INTERVAL=2
LLMS_JOB_TIMEOUT=300
LLMS_MAX_PAGES=40
LLMS_MAX_DEPTH=3
LLMS_REQUEST_TIMEOUT=15
LLMS_REQUEST_DELAY=1
PYTHONUNBUFFERED=1
```

Create `/etc/llms-txt/frontend.env` with `sudoedit`:

```dotenv
NODE_ENV=production
BACKEND_URL=http://127.0.0.1:8000
```

```sh
sudo chown root:root /etc/llms-txt/backend.env /etc/llms-txt/frontend.env
sudo chmod 0600 /etc/llms-txt/backend.env /etc/llms-txt/frontend.env
```

systemd reads these files before starting each service; use plain `KEY=value`
lines, without `export`. Both Python services must load the **same backend env**.
The absolute database path keeps data outside the Git checkout so application
updates do not replace it. The `llms` user needs write access to its directory
for SQLite WAL/SHM files and the worker lock. Keep this on a local filesystem,
not NFS or a shared network volume. There is no need for a production
`frontend/.env.local`; `BACKEND_URL` is read from the service environment at
runtime. No CORS configuration or public backend hostname is needed.

## 5. Supervise all three processes with systemd

Create the following files with `sudoedit`. Do not use `--reload`, `next dev`,
multiple worker services, or a separate scheduler in production.

`/etc/systemd/system/llms-api.service`:

```ini
[Unit]
Description=llms.txt FastAPI API
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=llms
Group=llms
WorkingDirectory=/opt/llms-txt/app/backend
EnvironmentFile=/etc/llms-txt/backend.env
ExecStart=/opt/llms-txt/app/backend/.venv/bin/python -m uvicorn app.api.main:app --host 127.0.0.1 --port 8000 --workers 1
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
UMask=0077
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/llms-worker.service`:

```ini
[Unit]
Description=llms.txt crawler worker and recrawl scheduler
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=llms
Group=llms
WorkingDirectory=/opt/llms-txt/app/backend
EnvironmentFile=/etc/llms-txt/backend.env
ExecStart=/opt/llms-txt/app/backend/.venv/bin/python -m app.worker
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
UMask=0077
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

`/etc/systemd/system/llms-frontend.service`:

```ini
[Unit]
Description=llms.txt Next.js frontend and API proxy
Wants=network-online.target
After=network-online.target llms-api.service

[Service]
Type=simple
User=llms
Group=llms
WorkingDirectory=/opt/llms-txt/app/frontend
EnvironmentFile=/etc/llms-txt/frontend.env
ExecStart=/usr/local/bin/node /opt/llms-txt/app/frontend/node_modules/next/dist/bin/next start --hostname 127.0.0.1 --port 3000
Restart=on-failure
RestartSec=5
TimeoutStopSec=30
UMask=0077
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

This starts the same Next.js production server as `npm start`, invoking Node
directly so systemd supervises the actual server process. Adjust the Node path
if required by step 2. Start and check the services:

```sh
sudo systemd-analyze verify /etc/systemd/system/llms-api.service /etc/systemd/system/llms-worker.service /etc/systemd/system/llms-frontend.service
sudo systemctl daemon-reload
sudo systemctl enable --now llms-api llms-worker llms-frontend
sudo systemctl status llms-api llms-worker llms-frontend --no-pager
curl --fail http://127.0.0.1:8000/api/health
curl --fail http://127.0.0.1:3000/api/health
```

Both health requests should return `{"status":"ok"}`. API and worker initialize
the database automatically; retry after a few seconds if startup is still in
progress. An interrupted running job is recovered when the
sole worker restarts. Health checks cover API/database availability, **not worker
liveness**; generation in step 8 verifies the complete flow.

## 6. Put Nginx in front of Next.js

Create `/etc/nginx/conf.d/llms-limits.conf`:

```nginx
limit_req_zone $binary_remote_addr zone=llms_submissions:10m rate=6r/m;
```

On Ubuntu this file is included inside Nginx's `http` block. Create
`/etc/nginx/snippets/llms-proxy.conf`:

```nginx
proxy_pass http://127.0.0.1:3000;
proxy_http_version 1.1;
proxy_set_header Host $host;
proxy_set_header X-Real-IP $remote_addr;
proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header Connection "";
proxy_connect_timeout 5s;
proxy_read_timeout 30s;
proxy_buffering off;
proxy_cache off;
```

Create `/etc/nginx/sites-available/llms-txt`, replacing the hostname:

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name llms.example.com;
    client_max_body_size 16k;
    limit_req_status 429;

    location ~ ^/api/(generate|websites/[^/]+/refresh)/?$ {
        limit_req zone=llms_submissions burst=2 nodelay;
        include /etc/nginx/snippets/llms-proxy.conf;
    }

    location / {
        include /etc/nginx/snippets/llms-proxy.conf;
    }
}
```

```sh
sudo ln -s /etc/nginx/sites-available/llms-txt /etc/nginx/sites-enabled/llms-txt
sudo nginx -t
sudo systemctl enable --now nginx
sudo systemctl reload nginx
```

Create the symlink only once. On an existing server, preserve other sites and
check for a conflicting `server_name` before enabling this block. Do not add a
`location /api` that forwards directly to port 8000: that would bypass the
frontend proxy. The sample limits generation/refresh requests per client IP;
job polling every two seconds is left unrestricted. These are starting limits,
not comprehensive abuse protection. Nginx documents its per-key request limits
in the [request limiting module](https://nginx.org/en/docs/http/ngx_http_limit_req_module.html).

Crawls run in the worker, so an HTTP connection does not need to stay open for
the entire crawl. The Nginx 30-second timeout exceeds the existing Next.js proxy's
20-second per-request deadline; job status is fetched separately. If using a
CDN, do not cache `/api/*`, and configure trusted real-IP handling before relying
on per-client rate limits. This example assumes Nginx receives client traffic
directly, without a CDN.

## 7. Configure the firewall and HTTPS

Allow SSH **before** enabling the firewall. If your SSH port is not 22, allow
that actual port instead of relying on the `OpenSSH` profile. Keep a second SSH
session or your provider's console available while changing firewall rules.

```sh
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
sudo ufw status
```

Also allow SSH and ports 80/443 in the VPS provider's network firewall. Do not
allow inbound 3000/8000. The worker needs outbound public DNS and HTTP/HTTPS for
crawling; do not block that egress. See Ubuntu's
[firewall guide](https://ubuntu.com/server/docs/how-to/security/firewalls/).

Once DNS resolves to the VPS and HTTP port 80 is reachable, obtain a certificate:

```sh
sudo snap install --classic certbot
sudo /snap/bin/certbot --nginx --redirect -d llms.example.com
sudo nginx -t
sudo systemctl status snap.certbot.renew.timer --no-pager
sudo /snap/bin/certbot renew --dry-run
```

Use one Certbot installation method; do not install competing apt and snap
versions. Certbot updates the matching Nginx site for TLS and HTTP-to-HTTPS
redirects. Keep port 80 open for HTTP-01 certificate renewal. See the official
[Ubuntu HTTPS instructions](https://ubuntu.com/server/docs/how-to/security/obtain-tls-certificates/).

## 8. Verify the deployed application

```sh
curl --fail https://llms.example.com/api/health
curl --head https://llms.example.com
sudo ss -ltnp
sudo journalctl -u llms-api -u llms-worker -u llms-frontend -n 100 --no-pager
```

Confirm 3000/8000 listen only on `127.0.0.1`, then open
`https://llms.example.com` in a browser. Generate `https://example.com`, observe
completion, inspect the preview and counts, copy and download the file, and
regenerate it. Test from a mobile viewport too. Check the worker log for the
completed job; a healthy API with permanently queued jobs is not a successful
deployment. Verify services remain enabled after a planned reboot.

The app does not upload a generated file to its source website. Download
`llms.txt` and place it at that website's root yourself. This application has no
authentication, job-ownership checks, or global queue quotas. IDs are not an
access-control boundary. Keep a take-home demo restricted to intended reviewers
using your hosting access controls if needed; per-IP rate limits alone do not
make an unrestricted public crawler suitable for arbitrary traffic.

## 9. Back up SQLite and test recovery

Use SQLite's online backup operation, not a plain copy of a running WAL-mode
database. Committed data can still reside in `-wal`. The backup API creates a
consistent snapshot while API/worker connections remain active.
([SQLite backup documentation](https://www.sqlite.org/backup.html))

Create `/usr/local/bin/llms-txt-backup` with `sudoedit`:

```sh
#!/bin/sh
set -eu
umask 077
snapshot="/var/backups/llms-txt/llms-$(date -u +%Y%m%dT%H%M%SZ).sqlite3"
sqlite3 -readonly -cmd '.timeout 10000' /var/lib/llms-txt/llms.sqlite3 ".backup '$snapshot'"
test "$(sqlite3 "$snapshot" 'PRAGMA journal_mode=DELETE;')" = delete
test "$(sqlite3 -readonly "$snapshot" 'PRAGMA integrity_check;')" = ok
printf '%s\n' "$snapshot"
```

```sh
sudo chmod 0755 /usr/local/bin/llms-txt-backup
sudo -u llms /usr/local/bin/llms-txt-backup
sudo systemctl enable --now cron
sudo crontab -u llms -e
```

Add a nightly schedule to that user's crontab (03:00 in the server's timezone):

```cron
0 3 * * * /usr/local/bin/llms-txt-backup >> /var/backups/llms-txt/backup.log 2>&1
```

The script makes each snapshot self-contained in rollback-journal mode before
checking it; the live source database remains in WAL mode. Copy snapshots to
private off-VPS storage; a backup on the same disk does not
protect against losing the VPS. Retain a defined set of daily/weekly copies and
monitor backup failures/disk usage. Also retain the deployed Git commit, the
Python package snapshot, `/etc/llms-txt/`, Nginx config, and systemd unit files in
private backup storage. Do not commit environment files or backups to Git.

For a restore, first validate the chosen snapshot and **stop all three services**.
Replace `CHOSEN_SNAPSHOT.sqlite3` with the actual backup filename:

```sh
sudo sqlite3 -readonly /var/backups/llms-txt/CHOSEN_SNAPSHOT.sqlite3 'PRAGMA integrity_check;'
sudo systemctl stop llms-frontend llms-worker llms-api
recovery_dir="/var/lib/llms-txt/pre-restore-$(date -u +%Y%m%dT%H%M%SZ)"
sudo install -d -o llms -g llms -m 0700 "$recovery_dir"
for suffix in '' '-wal' '-shm'; do
  if sudo test -e "/var/lib/llms-txt/llms.sqlite3$suffix"; then
    sudo mv "/var/lib/llms-txt/llms.sqlite3$suffix" "$recovery_dir/"
  fi
done
sudo install -o llms -g llms -m 0600 /var/backups/llms-txt/CHOSEN_SNAPSHOT.sqlite3 /var/lib/llms-txt/llms.sqlite3
sudo systemctl start llms-api llms-worker llms-frontend
```

Proceed only if integrity check returned `ok` and all services actually stopped.
Do not mix the restored database with old WAL/SHM files or delete the worker
lock to bypass a running worker. Restoring reverts data to the snapshot time;
retain the moved files for recovery. Recheck health and a stored download after
restoration, and rehearse the process on a separate test database before relying
on backups.

## 10. Updates, rollback, and troubleshooting

This simple update procedure has **planned downtime**; do not run `next build`
over a `.next` directory used by a live server. Record the current Git commit
and installed Python versions, take a verified backup, and then:

```sh
sudo systemctl stop llms-frontend llms-worker llms-api
sudo -u llms git -C /opt/llms-txt/app fetch origin
sudo -u llms git -C /opt/llms-txt/app checkout --detach YOUR_NEW_TESTED_COMMIT
sudo -u llms /opt/llms-txt/app/backend/.venv/bin/python -m pip install -r /opt/llms-txt/app/backend/requirements.txt
sudo -u llms -H sh -c 'cd /opt/llms-txt/app/backend && .venv/bin/python -m pytest -q'
sudo -u llms -H sh -c 'cd /opt/llms-txt/app/frontend && npm ci --include=dev && npm run lint && npm run build'
sudo systemctl start llms-api llms-worker llms-frontend
```

Stop on any failed command; do not start an incomplete build. If unit files
changed, run `sudo systemctl daemon-reload` before starting services. Environment
file edits require restarting the affected services. Repeat step 8 after each
update. No database schema migration framework is implemented; review any future
schema change and its recovery plan before applying it.

For an application rollback, stop the services, check out the previously
recorded commit, reinstall frontend dependencies with `npm ci`, restore the
matching Python dependency snapshot (prefer a fresh virtual environment), rerun
tests/build, and restart. Keep the persistent database if the older code is
compatible; restore a database backup only when necessary, understanding that
newer jobs/generations would be lost. This tutorial does not claim zero-downtime
or automatic rollback.

| Symptom | Check or action |
| --- | --- |
| Nginx returns 502 | Check `systemctl status llms-frontend` and its journal; verify port 3000 and successful `.next` build. |
| UI cannot reach generation service | Check `/etc/llms-txt/frontend.env`, restart the frontend, and curl both loopback health endpoints. |
| Jobs remain pending | Check the worker service/logs and matching `LLMS_DB_PATH`; API health alone cannot detect this. |
| Worker reports another worker | Stop the stray process using the same database; do not remove its lock file to force concurrency. |
| SQLite permission/read-only errors | Check ownership of `/var/lib/llms-txt` and its files, free space, and that API/worker run as `llms`. |
| Build is killed or fails from memory pressure | Check RAM/swap and build logs; build on a larger machine or compatible Linux CI if necessary. |
| Node executable missing in systemd | Use the actual system-wide Node path; service processes do not initialize your nvm shell. |
| Nginx returns 429 | Submission/refresh rate limit was exceeded; wait, inspect access logs, and tune limits for the intended audience. |
| HTTPS issuance/renewal fails | Check A/AAAA records, public port 80, matching `server_name`, Certbot logs, and renewal timer. |
| Crawl fails for one website | Inspect safe job errors and worker logs; robots rules, DNS, timeouts, or JavaScript-only HTML can prevent useful output. |

For ongoing operation, monitor the three services, public health, successful job
completion/queue age, disk/database growth, backup success, certificate renewal,
and memory. Systemd captures application logs in the journal; Nginx logs are
under `/var/log/nginx/`. No frontend analytics, authentication, new database,
or deployment automation has been added by this tutorial.
