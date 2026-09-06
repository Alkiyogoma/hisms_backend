# Deploying HODARI (HISMS) on an Ubuntu VPS with Nginx

A step-by-step guide to deploy this Django project to production on a fresh
Ubuntu server (22.04 / 24.04 LTS) using **PostgreSQL + Redis + Celery +
Gunicorn behind Nginx**.

This document is self-contained. It documents the **database configuration and
migration** in detail and then walks through the full server setup.

---

## 1. Architecture overview

```
                    ┌─────────────────────────────────────────┐
   Internet ──443──▶│  Nginx  (TLS termination, static/media)   │
                    └───────────────┬───────────────────────────┘
                                    │ proxy_pass 127.0.0.1:8007
                          ┌─────────▼──────────┐
                          │ Gunicorn (WSGI)     │  systemd: hodari.service
                          │ config.wsgi         │
                          └─────────┬──────────┘
                                    │
             ┌──────────────────────┼───────────────────────┐
             ▼                      ▼                        ▼
      ┌────────────┐        ┌──────────────┐        ┌────────────────┐
      │ PostgreSQL │        │    Redis     │        │  Celery worker │
      │  (hodari)  │        │ broker/cache │        │  + celery beat │
      └────────────┘        └──────────────┘        └────────────────┘
```

| Component        | Role                                    | systemd unit                |
|------------------|-----------------------------------------|-----------------------------|
| Gunicorn         | WSGI app server, binds `127.0.0.1:8007` | `hodari.service`            |
| Nginx            | Reverse proxy, TLS, static/media        | `nginx`                     |
| PostgreSQL       | Primary database                        | `postgresql`                |
| Redis            | Celery broker/result, cache, Channels   | `redis-server`              |
| Celery worker    | Background tasks                        | `hodari-celery.service`     |
| Celery beat      | Scheduled tasks (DatabaseScheduler)     | `hodari-celery-beat.service`|

Reference units and configs already live in `deployment/`:
`gunicorn_config.py`, `hodari.service`, `hodari-celery.service`,
`hodari-celery-beat.service`, `nginx_hodari.conf`.

**Conventions used below** (change to taste):

| Setting        | Value                              |
|----------------|------------------------------------|
| App user       | `hodari`                           |
| Project path   | `/opt/hodari/hisms_backend`        |
| Virtualenv     | `/opt/hodari/venv`                 |
| Domain         | `connect.example.com`              |
| DB name / user | `hodari` / `hodari_user`           |

> **Updating an existing live server to the `hodari` DB?** Follow the focused
> [LIVE_UPDATE_RUNBOOK.md](LIVE_UPDATE_RUNBOOK.md) instead of this full guide.

---

## 2. How the database is configured (read this first)

Database selection lives in [`config/settings.py`](../config/settings.py) and is
driven entirely by environment variables loaded from a `.env` file in the
project root (via `python-dotenv`).

### Selection logic

```python
# Uses SQLite only if EITHER:
#   DJANGO_USE_SQLITE = 1/true/yes
#   OR  POSTGRES_HOST is empty/localhost/127.0.0.1  AND  POSTGRES_PASSWORD is empty
# Otherwise → PostgreSQL.
```

So for production you **must** set `POSTGRES_PASSWORD` (and keep
`DJANGO_USE_SQLITE` unset/`0`) or the app silently falls back to SQLite.

### PostgreSQL settings block

```python
DATABASES = {
    "default": {
        "ENGINE":   "django.db.backends.postgresql",
        "NAME":     os.getenv("POSTGRES_DB", "hisms"),
        "USER":     os.getenv("POSTGRES_USER", "postgres"),
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", "postgres"),
        "HOST":     os.getenv("POSTGRES_HOST", "localhost"),
        "PORT":     os.getenv("POSTGRES_PORT", "5432"),
        "CONN_MAX_AGE": int(os.getenv("DB_CONN_MAX_AGE", "300")),  # persistent connections
    }
}
```

The Postgres driver is **psycopg 3** (`psycopg[binary]` in
[`requirements.txt`](../requirements.txt)).

### Legacy Laravel/MySQL source (migration only)

`settings.py` also defines `LARAVEL_DATABASE` (MySQL, `mysql-connector-python`)
used only for one-time data migration from the old check-in system. It is **not**
Django's `default` database and is optional. Leave `LARAVEL_DB_*` unset in
production unless you are running that import.

### Redis / Celery / Channels database allocation

All point at Redis on `localhost:6379` by default, using separate logical DBs:

| Purpose               | Env var              | Default DB |
|-----------------------|----------------------|------------|
| Celery broker         | `CELERY_BROKER_URL`  | `/0`       |
| Celery result backend | `CELERY_RESULT_BACKEND` | `/1`    |
| Channels (WebSocket)  | `CHANNELS_REDIS_URL` | `/2`       |
| Django cache          | `REDIS_URL` → `/3`   | `/3`       |

> Note: `deployment/production_settings.py` is a **reference overlay** and is
> **not** imported by `config.settings`. The systemd units run
> `DJANGO_SETTINGS_MODULE=config.settings`, so production behavior is controlled
> by `.env` (set `DJANGO_DEBUG=0`). Do not rely on `production_settings.py`
> being active unless you explicitly wire it in.

---

## 3. Provision the server

SSH in as root (or a sudo user) and install system packages.

```bash
sudo apt update && sudo apt upgrade -y

# Core runtime + build deps
sudo apt install -y python3 python3-venv python3-dev build-essential \
    libpq-dev git curl

# PostgreSQL, Redis, Nginx
sudo apt install -y postgresql postgresql-contrib redis-server nginx

# WeasyPrint (PDF rendering) system libraries — required, see requirements.txt
sudo apt install -y libpango-1.0-0 libpangocairo-1.0-0 libcairo2 \
    libgdk-pixbuf-2.0-0 libffi-dev shared-mime-info fonts-liberation

sudo systemctl enable --now postgresql redis-server nginx
```

Create the application user and directories:

```bash
sudo useradd --system --create-home --home-dir /opt/hodari --shell /bin/bash hodari
sudo mkdir -p /opt/hodari /var/log/hodari /var/backups/hodari /var/run/hodari
sudo chown -R hodari:hodari /opt/hodari /var/log/hodari /var/backups/hodari /var/run/hodari
```

---

## 4. Create the PostgreSQL database

Run as the `postgres` OS user. **Replace the password** with a strong secret.

```bash
sudo -u postgres psql <<'SQL'
CREATE DATABASE hodari;
CREATE USER hodari_user WITH PASSWORD 'CHANGE_ME_STRONG_PASSWORD';

-- Django-recommended session defaults
ALTER ROLE hodari_user SET client_encoding TO 'utf8';
ALTER ROLE hodari_user SET default_transaction_isolation TO 'read committed';
ALTER ROLE hodari_user SET timezone TO 'UTC';

GRANT ALL PRIVILEGES ON DATABASE hodari TO hodari_user;
SQL
```

On **PostgreSQL 15+** the `public` schema is locked down by default, so also
grant schema rights (connect to the new DB first):

```bash
sudo -u postgres psql -d hodari <<'SQL'
GRANT ALL ON SCHEMA public TO hodari_user;
ALTER DATABASE hodari OWNER TO hodari_user;
SQL
```

Local connections use PostgreSQL's `peer`/`md5` auth on `localhost`; no
`pg_hba.conf` change is needed for a same-host app connecting over TCP to
`127.0.0.1` with a password. Verify:

```bash
PGPASSWORD='CHANGE_ME_STRONG_PASSWORD' psql -h 127.0.0.1 -U hodari_user -d hodari -c '\conninfo'
```

> **Local development shortcut:** on your own machine it's fine to use the
> built-in `postgres` superuser (`POSTGRES_USER=postgres`), but never on a
> public server — live must use a dedicated role like `hodari_user` above.

---

## 5. Get the code and install dependencies

```bash
sudo -u hodari -i          # become the app user
cd /opt/hodari

# Clone (or rsync/scp your project) so the app lives at:
#   /opt/hodari/hisms_backend
git clone <YOUR_REPO_URL> hisms_backend
# — or — copy an existing checkout up with: rsync -av ./ hodari@server:/opt/hodari/hisms_backend/

python3 -m venv /opt/hodari/venv
source /opt/hodari/venv/bin/activate
pip install --upgrade pip
pip install -r /opt/hodari/hisms_backend/requirements.txt
```

---

## 6. Create the production `.env`

Create `/opt/hodari/hisms_backend/.env`. Generate a fresh secret key:

```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

```ini
# --- Core ---
DJANGO_SECRET_KEY=<paste-generated-key>
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=connect.example.com,127.0.0.1,localhost
# App is served on :8443 (shipped Nginx config) — include the port on the origin
CSRF_TRUSTED_ORIGINS=https://connect.example.com:8443

# --- Database (PostgreSQL) ---
DJANGO_USE_SQLITE=0
POSTGRES_DB=hodari
POSTGRES_USER=hodari_user
POSTGRES_PASSWORD=CHANGE_ME_STRONG_PASSWORD
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
DB_CONN_MAX_AGE=300

# --- Redis / Celery / Channels ---
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/1
CHANNELS_REDIS_URL=redis://localhost:6379/2

# --- CORS (mobile app / SPA) ---
CORS_ALLOW_ALL_ORIGINS=False
CORS_ALLOWED_ORIGINS=https://connect.example.com

# --- Email (optional; DatabaseEmailBackend reads SchoolSettings, falls back here) ---
DEFAULT_FROM_EMAIL=noreply@example.com
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=

# --- Backups ---
BACKUP_DIR=/var/backups/hodari
BACKUP_RETENTION_DAYS=30
```

Lock down the file (it holds secrets):

```bash
chmod 600 /opt/hodari/hisms_backend/.env
```

> With `DJANGO_DEBUG=0`, `settings.py` enforces `SECURE_SSL_REDIRECT`, HSTS,
> secure cookies, and `SECURE_PROXY_SSL_HEADER`. TLS must be terminated at Nginx
> (Section 9) or the app will redirect-loop.

---

## 7. Run migrations and collect static files

This is the database-migration step. The project has migrations across ~16
apps (academics, admissions, attendance, finance, hr, users, etc.).

```bash
cd /opt/hodari/hisms_backend
source /opt/hodari/venv/bin/activate

# 1. Sanity-check config against the DB
python manage.py check --deploy

# 2. Show the migration plan (optional, read-only)
python manage.py showmigrations

# 3. Apply all migrations — creates every table in PostgreSQL
python manage.py migrate --noinput

# 4. Create the first admin user
python manage.py createsuperuser

# 5. Collect static assets into staticfiles/ (served by Nginx)
python manage.py collectstatic --noinput
```

Notes:

- Celery Beat uses `django_celery_beat` (the `DatabaseScheduler`); its schedule
  tables are created by the same `migrate` run.
- `AUDIT_LOG_DB_CONSTRAINT` is automatically enabled on PostgreSQL (it is only
  relaxed under SQLite), so the audit foreign keys are enforced in production.
- Re-run `migrate` and `collectstatic` on every deploy that ships model or
  static changes.

**Quick smoke test** before wiring up systemd:

```bash
gunicorn config.wsgi:application --bind 127.0.0.1:8007
# Ctrl-C after confirming it boots without errors
```

---

## 8. Configure the systemd services

Copy the reference units from `deployment/` into systemd. They already assume
the `hodari` user, `/opt/hodari` paths, and `config.wsgi` / Celery app `config`.

```bash
sudo cp /opt/hodari/hisms_backend/deployment/hodari.service            /etc/systemd/system/
sudo cp /opt/hodari/hisms_backend/deployment/hodari-celery.service     /etc/systemd/system/
sudo cp /opt/hodari/hisms_backend/deployment/hodari-celery-beat.service /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now hodari hodari-celery hodari-celery-beat

# Verify
systemctl status hodari --no-pager
sudo journalctl -u hodari -n 50 --no-pager
```

Gunicorn (see [`gunicorn_config.py`](gunicorn_config.py)) binds
`127.0.0.1:8007`, runs `cpu_count()*2+1` sync workers, logs to
`/var/log/hodari/`, and drops to the `hodari` user. Make sure
`/var/log/hodari` is writable by `hodari` (done in Section 3).

---

## 9. Configure Nginx + TLS (shipped 8070/8443 config)

Use the shipped [`nginx_hodari.conf`](nginx_hodari.conf) as-is. It is built for
a **shared multi-site host** and listens on custom ports so it never collides
with other projects on the same server:

| Port | Purpose |
|------|---------|
| `80`   | Let's Encrypt ACME challenge + `301` redirect to the app on `8070` |
| `8070` | HTTP app (always works, even before a cert exists) |
| `8443` | HTTPS app (enabled once certbot has issued the cert) |

It proxies to `127.0.0.1:8007`, serves `/static/` and `/media/`, applies rate
limiting (`hodari_general` 30r/s, `hodari_login` 5r/m), security headers, and a
WebSocket (`/ws/`) location for Django Channels.

### 9.1 Edit the domain

The file ships with `server_name connect.hodari.ac.tz`. Change every occurrence
to your domain if different:

```bash
sudo cp /opt/hodari/hisms_backend/deployment/nginx_hodari.conf \
        /etc/nginx/sites-available/hodari

# Replace the domain if yours differs from connect.hodari.ac.tz
sudo sed -i 's/connect\.hodari\.ac\.tz/connect.example.com/g' \
        /etc/nginx/sites-available/hodari
```

Make sure `DJANGO_ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` in `.env`
(Section 6) match this domain — including the `:8443` port on the CSRF origin,
e.g. `CSRF_TRUSTED_ORIGINS=https://connect.example.com:8443`.

### 9.2 Enable HTTP first (ports 80 + 8070)

The `8443` `server {}` block references the Let's Encrypt cert, which does not
exist yet, so Nginx won't start with it enabled. Comment out the `8443` block
for the first bring-up, or issue the cert before reloading.

```bash
sudo ln -s /etc/nginx/sites-available/hodari /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo mkdir -p /var/www/certbot

sudo nginx -t && sudo systemctl reload nginx
```

The app is now reachable at `http://connect.example.com:8070/`.

### 9.3 Issue the TLS certificate (webroot)

The shipped config already serves the ACME challenge from `/var/www/certbot` on
port 80, so use the `--webroot` method (not `--nginx`):

```bash
sudo apt install -y certbot
sudo certbot certonly --webroot -w /var/www/certbot -d connect.example.com
```

Once the cert exists at
`/etc/letsencrypt/live/connect.example.com/`, re-enable the `8443` block (if you
commented it out) and reload:

```bash
sudo nginx -t && sudo systemctl reload nginx
```

HTTPS is now live at `https://connect.example.com:8443/`.

### 9.4 Open the firewall

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 8070/tcp
sudo ufw allow 8443/tcp
sudo ufw enable
```

> Certbot's timer auto-renews the cert. Because the shipped config uses the
> webroot challenge on port 80, no renewal hook changes are needed — just keep
> port 80 open.

---

## 10. Verify the deployment

```bash
# Services up?
systemctl is-active hodari hodari-celery hodari-celery-beat nginx postgresql redis-server

# App responds through Nginx (HTTP on 8070, HTTPS on 8443)
curl -I http://connect.example.com:8070/
curl -I https://connect.example.com:8443/

# DB connectivity from Django
cd /opt/hodari/hisms_backend && source /opt/hodari/venv/bin/activate
python manage.py dbshell -c '\dt' | head

# Celery worker sees the broker
celery -A config inspect ping
```

Then log in to `https://connect.example.com:8443/admin/` with the superuser.

---

## 11. Redeploy / update workflow

```bash
sudo -u hodari -i
cd /opt/hodari/hisms_backend
source /opt/hodari/venv/bin/activate

git pull                                   # or rsync new code
pip install -r requirements.txt            # if deps changed
python manage.py migrate --noinput         # apply new migrations
python manage.py collectstatic --noinput   # refresh static
exit

sudo systemctl restart hodari hodari-celery hodari-celery-beat
```

---

## 12. Database backup & restore

`pg_dump` is the source of truth for backups.

```bash
# Backup (compressed custom format)
pg_dump -h 127.0.0.1 -U hodari_user -Fc hodari > /var/backups/hodari/hodari_$(date +%F).dump

# Restore into a fresh DB
pg_restore -h 127.0.0.1 -U hodari_user -d hodari --clean --if-exists /var/backups/hodari/hodari_YYYY-MM-DD.dump
```

Automate a nightly dump with cron (as the `hodari` user):

```cron
0 2 * * * pg_dump -h 127.0.0.1 -U hodari_user -Fc hodari > /var/backups/hodari/hodari_$(date +\%F).dump && find /var/backups/hodari -name '*.dump' -mtime +30 -delete
```

(Put the DB password in `~/.pgpass` — `chmod 600` — so cron runs
non-interactively.)

---

## 13. Troubleshooting

| Symptom | Likely cause / fix |
|---------|--------------------|
| App uses SQLite, not Postgres | `POSTGRES_PASSWORD` empty or `POSTGRES_HOST` is `localhost` with no password → set both; keep `DJANGO_USE_SQLITE=0`. |
| Infinite HTTPS redirect loop | Nginx not sending `X-Forwarded-Proto https`; add it to the proxy block. |
| `502 Bad Gateway` | Gunicorn down or wrong bind — `journalctl -u hodari`; confirm it listens on `127.0.0.1:8007`. |
| CSRF verification failed | Add the domain to `CSRF_TRUSTED_ORIGINS` (with `https://`) in `.env`. |
| Static files 404 | Run `collectstatic`; check Nginx `alias` path matches `STATIC_ROOT` (`/opt/hodari/hisms_backend/staticfiles/`). |
| WeasyPrint / PDF errors | Install the Pango/Cairo libs from Section 3. |
| Celery tasks never run | Worker/beat not started, or Redis broker URL wrong — `celery -A config inspect ping`. |
| `permission denied for schema public` | Run the PG15+ `GRANT ALL ON SCHEMA public` from Section 4. |

---

### File reference (in `deployment/`)

- `gunicorn_config.py` — Gunicorn worker/logging config
- `hodari.service`, `hodari-celery.service`, `hodari-celery-beat.service` — systemd units
- `nginx_hodari.conf` — the Nginx config used in Section 9 (ports 80 / 8070 / 8443)
- `production_settings.py` — **reference only**, not auto-loaded
