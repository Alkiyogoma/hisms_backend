# Running `hodari.nguzo.co.tz` as a SECOND site (without breaking `connect.hodari.ac.tz`)

`connect.hodari.ac.tz` already runs on this VPS from **`/opt/hodari`**, as
**`hodari.service`** (gunicorn on **`127.0.0.1:8007`**), behind nginx on
standard **80/443**. This runbook stands up `hodari.nguzo.co.tz` as a fully
**isolated** second stack so the two never collide.

**Isolation contract — the second site gets its own everything:**

| Thing            | connect.hodari.ac.tz | hodari.nguzo.co.tz (this) |
|------------------|----------------------|---------------------------|
| Code dir         | `/opt/hodari`        | `/var/www/hodari`         |
| Virtualenv       | `/opt/hodari/venv`   | `/var/www/hodari/venv`    |
| Gunicorn port    | `127.0.0.1:8007`     | **`127.0.0.1:8009`**      |
| systemd units    | `hodari*`            | **`hodari-nguzo*`**       |
| nginx site       | `/etc/nginx/sites-available/hodari` | **`.../nguzo`** |
| TLS host (443)   | `connect.hodari.ac.tz` | `hodari.nguzo.co.tz` (same 443, routed by `server_name`) |
| Logs / run       | `/var/log/hodari`    | `/var/log/hodari-nguzo`   |

> Prereq: a DNS **A record** `hodari.nguzo.co.tz → 187.7.21.133`, and the
> firewall allows **80 + 443** (`sudo ufw allow 80/tcp && sudo ufw allow 443/tcp`).
> Both sites share standard 443 — nginx picks the right one by `server_name`.
> The old 8070/8443 scheme is **not** needed and should not be used here.

---

## 0. First: stop/clean any half-deployed nguzo (safe — never touches connect)

connect runs from `/opt/hodari`; everything below targets `/var/www/hodari` only.

```bash
# Kill any stray gunicorn/celery/daphne running from the nguzo venv/dir
sudo pkill -f '/var/www/hodari' ; sleep 1
# Stop + disable any systemd unit that points at /var/www/hodari
for u in $(grep -rl '/var/www/hodari' /etc/systemd/system/ 2>/dev/null | xargs -r -n1 basename); do
  echo "disabling $u"; sudo systemctl disable --now "$u"
done
sudo systemctl daemon-reload
# Verify: nothing left from /var/www/hodari, and connect is still healthy
ps -eo pid,args | grep '/var/www/hodari' | grep -v grep || echo 'no nguzo processes'
sudo systemctl is-active hodari.service; curl -sI https://connect.hodari.ac.tz/ | head -1
```

## 1. Directories, user, code, venv

```bash
sudo mkdir -p /var/www/hodari /var/log/hodari-nguzo /var/run/hodari-nguzo
id hodari >/dev/null 2>&1 || sudo useradd --system --home-dir /var/www/hodari --shell /bin/bash hodari
sudo chown -R hodari:hodari /var/www/hodari /var/log/hodari-nguzo /var/run/hodari-nguzo

# Put the code at /var/www/hodari/hisms_backend (git clone or rsync), then:
python3 -m venv /var/www/hodari/venv
/var/www/hodari/venv/bin/pip install -r /var/www/hodari/hisms_backend/requirements.txt
```

## 2. Its own `.env`

Create `/var/www/hodari/hisms_backend/.env` with **its own** secret key, hosts,
and — unless the two sites intentionally share data — **its own database** and
**its own Redis DB numbers** so tasks/cache don't cross over:

```
DJANGO_DEBUG=0
DJANGO_SECRET_KEY=<fresh: python -c "import secrets; print(secrets.token_urlsafe(64))">
DJANGO_ALLOWED_HOSTS=hodari.nguzo.co.tz,187.7.21.133,localhost,127.0.0.1
CSRF_TRUSTED_ORIGINS=https://hodari.nguzo.co.tz
POSTGRES_DB=hodari_nguzo            # separate DB from connect
POSTGRES_USER=hodari_nguzo
POSTGRES_PASSWORD=<strong>
POSTGRES_HOST=localhost
REDIS_URL=redis://localhost:6379/6  # distinct Redis DBs from connect (which uses 0/1/3…)
CELERY_BROKER_URL=redis://localhost:6379/7
CELERY_RESULT_BACKEND=redis://localhost:6379/8
SITE_URL=https://hodari.nguzo.co.tz
WEBHOOK_API_TOKEN=<generate>
ATTENDANCE_WEBHOOK_SECRET=<generate>
```

Then:
```bash
sudo chown hodari:hodari /var/www/hodari/hisms_backend/.env && sudo chmod 600 /var/www/hodari/hisms_backend/.env
sudo -u hodari /var/www/hodari/venv/bin/python /var/www/hodari/hisms_backend/manage.py migrate
sudo -u hodari /var/www/hodari/venv/bin/python /var/www/hodari/hisms_backend/manage.py collectstatic --noinput
```

## 3. Install the isolated systemd units (distinct names → no collision)

```bash
sudo cp /var/www/hodari/hisms_backend/deployment/nguzo/hodari-nguzo.service            /etc/systemd/system/
sudo cp /var/www/hodari/hisms_backend/deployment/nguzo/hodari-nguzo-celery.service     /etc/systemd/system/
sudo cp /var/www/hodari/hisms_backend/deployment/nguzo/hodari-nguzo-celery-beat.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now hodari-nguzo.service hodari-nguzo-celery.service hodari-nguzo-celery-beat.service
sudo systemctl status hodari-nguzo.service --no-pager | head -6
sudo ss -tlnp | grep 8009     # gunicorn should be listening here
```

## 4. Nginx site (own `server_name` on 443) + certificate

```bash
sudo cp /var/www/hodari/hisms_backend/deployment/nguzo/nginx_nguzo.conf /etc/nginx/sites-available/nguzo
sudo ln -sf /etc/nginx/sites-available/nguzo /etc/nginx/sites-enabled/nguzo
# Do NOT remove other sites-enabled entries — connect's `hodari` symlink must stay.
sudo mkdir -p /var/www/certbot
sudo nginx -t && sudo systemctl reload nginx

# Issue this site's own cert (webroot; keeps port 80 for ACME):
sudo certbot certonly --webroot -w /var/www/certbot -d hodari.nguzo.co.tz
sudo nginx -t && sudo systemctl reload nginx
```

## 5. Verify BOTH sites

```bash
curl -sI https://hodari.nguzo.co.tz/  | head -1   # expect 200/302
curl -sI https://connect.hodari.ac.tz/ | head -1   # still 200/302 — untouched
```

---

### Never do these on a multi-site box
- ❌ `sudo cp .../hodari.service /etc/systemd/system/` (overwrites connect's unit) — use the `hodari-nguzo*` units above.
- ❌ `sudo rm -f /etc/nginx/sites-enabled/default` / removing other symlinks — only add your own.
- ❌ `sudo ufw enable` without `443/tcp` — drops the co-hosted site's HTTPS.
- ❌ pointing the nguzo deploy at `/opt/hodari` — that is connect's tree.
