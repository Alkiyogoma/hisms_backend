#!/usr/bin/env bash
# =============================================================================
# Live-update hodari.nguzo.co.tz to the latest main.
# Pulls code, then applies EVERY kind of change safely and restarts.
# Run:  sudo bash /var/www/hodari/hisms_backend/deployment/nguzo/update.sh
# =============================================================================
set -euo pipefail

APP=/var/www/hodari/hisms_backend
VENV=/var/www/hodari/venv
APPUSER=hodari

echo "==> 1/6 Pulling latest main"
git -C "$APP" fetch origin
# Hard reset to remote main. Safe: .env is gitignored, so it is NOT touched.
# (If you keep hand edits on the server, use 'git -C $APP pull' instead.)
git -C "$APP" reset --hard origin/main

echo "==> 2/6 Ensuring ownership"
chown -R "$APPUSER:$APPUSER" "$APP"

echo "==> 3/6 Python deps (no-op if requirements unchanged)"
sudo -u "$APPUSER" "$VENV/bin/pip" install -q -r "$APP/requirements.txt"

echo "==> 4/6 Database migrations (no-op if none pending)"
sudo -u "$APPUSER" "$VENV/bin/python" "$APP/manage.py" migrate --noinput

echo "==> 5/6 Collecting static files (CSS/JS/images -> staticfiles/)"
sudo -u "$APPUSER" "$VENV/bin/python" "$APP/manage.py" collectstatic --noinput

echo "==> 6/6 Restarting services (loads new templates + code)"
systemctl restart hodari-nguzo hodari-nguzo-celery hodari-nguzo-celery-beat

sleep 2
echo "--- status ---"
systemctl is-active hodari-nguzo || true
curl -sI --connect-timeout 8 https://hodari.nguzo.co.tz/ | head -1 || true
echo "Done."
