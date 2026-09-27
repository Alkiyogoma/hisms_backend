#!/usr/bin/env bash
# =============================================================================
# Live-update connect.hodari.ac.tz (PRODUCTION) to the latest code.
# Backs up the database, pulls code, applies EVERY kind of change and restarts.
# Run:  sudo bash /opt/hodari/hisms_backend/deployment/production/update.sh
# =============================================================================
set -euo pipefail

APP=/opt/hodari/hisms_backend
VENV=/opt/hodari/venv
APPUSER=hodari
BACKUP_DIR=/var/backups/hodari

echo "==> 1/7 Backing up the database"
DB_NAME="$(grep -E '^POSTGRES_DB=' "$APP/.env" | tail -1 | cut -d= -f2- | tr -d '\r" ')"
mkdir -p "$BACKUP_DIR"
DUMP="$BACKUP_DIR/${DB_NAME}_$(date +%F_%H%M).dump"
sudo -u postgres pg_dump -Fc "$DB_NAME" > "$DUMP"
echo "    $DUMP (code was at $(git -C "$APP" log -1 --format=%h))"

echo "==> 2/7 Pulling latest code"
git -C "$APP" fetch origin
# Safe: .env and media/ are gitignored, so they are NOT touched.
git -C "$APP" pull

echo "==> 3/7 Ensuring ownership"
chown -R "$APPUSER:$APPUSER" "$APP"

echo "==> 4/7 Python deps (no-op if requirements unchanged)"
sudo -u "$APPUSER" "$VENV/bin/pip" install -q -r "$APP/requirements.txt"

echo "==> 5/7 Database migrations (no-op if none pending)"
sudo -u "$APPUSER" "$VENV/bin/python" "$APP/manage.py" migrate --noinput

echo "==> 6/7 Collecting static files (CSS/JS/images -> staticfiles/)"
sudo -u "$APPUSER" "$VENV/bin/python" "$APP/manage.py" collectstatic --noinput

echo "==> 7/7 Restarting services (loads new templates + code)"
systemctl restart hodari hodari-celery hodari-celery-beat

sleep 2
echo "--- status ---"
systemctl is-active hodari hodari-celery hodari-celery-beat || true
curl -sI --connect-timeout 8 https://connect.hodari.ac.tz/ | head -1 || true
echo "Done. Database backup: $DUMP"
