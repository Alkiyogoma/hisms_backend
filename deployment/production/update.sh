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

# Uploaded files (lesson plans, photos …): MEDIA_ROOT from .env, else the
# media/ folder inside the code. Keep them outside the code folder  see
# PRODUCTION_DEPLOY.md "Uploaded files".
MEDIA="$(grep -E '^MEDIA_ROOT=' "$APP/.env" | tail -1 | cut -d= -f2- | tr -d '\r" ')"
MEDIA="${MEDIA:-$APP/media}"
count_media() { find "$MEDIA" -type f ! -path '*/previews/*' 2>/dev/null | wc -l; }

echo "==> 0/7 Backing up uploaded files ($MEDIA)"
case "$MEDIA" in "$APP"|"$APP"/*)
  echo "    WARNING: uploads are inside the code folder. Move them out (PRODUCTION_DEPLOY.md, 'Uploaded files')." ;;
esac
mkdir -p "$BACKUP_DIR/media"
# Adds and updates, never deletes: a file lost from the server stays here.
rsync -a "$MEDIA/" "$BACKUP_DIR/media/"
MEDIA_BEFORE=$(count_media)
echo "    $MEDIA_BEFORE files, copied to $BACKUP_DIR/media/"

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

MEDIA_AFTER=$(count_media)
if [ "$MEDIA_AFTER" -lt "$MEDIA_BEFORE" ]; then
  echo "!!  Uploaded files went from $MEDIA_BEFORE to $MEDIA_AFTER during the update. NOT restarting."
  echo "    Restore them with:  rsync -a $BACKUP_DIR/media/ $MEDIA/ && chown -R $APPUSER: $MEDIA"
  exit 1
fi
chown -R "$APPUSER:$APPUSER" "$MEDIA"

echo "==> 7/7 Restarting services (loads new templates + code)"
systemctl restart hodari hodari-celery hodari-celery-beat

sleep 2
echo "--- status ---"
systemctl is-active hodari hodari-celery hodari-celery-beat || true
curl -sI --connect-timeout 8 https://connect.hodari.ac.tz/ | head -1 || true
echo "Done. Database backup: $DUMP"
