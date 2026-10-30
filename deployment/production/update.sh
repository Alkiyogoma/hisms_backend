#!/usr/bin/env bash
# Update connect.hodari.ac.tz, preserving database and uploaded-file backups.
# Run: sudo bash /opt/hodari/hisms_backend/deployment/production/update.sh
set -Eeuo pipefail
umask 077

APP=/opt/hodari/hisms_backend
VENV=/opt/hodari/venv
APPUSER=hodari
BACKUP_DIR=/var/backups/hodari
SITE_URL=https://connect.hodari.ac.tz/
SERVICES=(hodari hodari-celery hodari-celery-beat)
# Celery Beat writes its pidfile here and lists it in ReadWritePaths; /run is
# wiped at boot, and systemd refuses to start Beat (226/NAMESPACE) without it.
RUN_DIR=/run/hodari
# gunicorn needs a few seconds after a restart; until then nginx answers with
# an error page. Check the site this many times, this many seconds apart.
SITE_ATTEMPTS=20
SITE_RETRY_DELAY=3
STEP="Preparing deployment"
LOG=""
DUMP=""
DB_BACKUP_READY=0

fail() {
  printf '\nERROR: %s\n' "$*" >&2
  return 1
}

on_error() {
  local status="$1" line="$2"
  trap - ERR
  printf '\nDEPLOYMENT FAILED during: %s (line %s, exit %s).\n' "$STEP" "$line" "$status" >&2
  [[ -z "$LOG" ]] || printf 'Deployment log: %s\n' "$LOG" >&2
  if [[ "$DB_BACKUP_READY" == 1 ]]; then
    printf 'Database backup: %s\n' "$DUMP" >&2
  fi
  printf 'Deployment has not been confirmed successful. Review the error above before retrying.\n' >&2
  exit "$status"
}
trap 'on_error "$?" "$LINENO"' ERR

step() {
  STEP="$1"
  printf '\n==> %s [%s]\n' "$STEP" "$(date '+%H:%M:%S')"
}

# Read simple dotenv values without executing the file or failing on an absent
# optional key. Quoted paths, spaces, CRLF and "export KEY=value" are supported.
read_env() {
  awk -v key="$1" '
    {
      line = $0
      sub(/\r$/, "", line)
      sub(/^[[:space:]]*/, "", line)
      sub(/^export[[:space:]]+/, "", line)
      if (line !~ "^" key "[[:space:]]*=") next
      sub(/^[^=]*=[[:space:]]*/, "", line)
      quote = substr(line, 1, 1)
      if (quote == "\"" || quote == sprintf("%c", 39)) {
        line = substr(line, 2)
        end = index(line, quote)
        if (end) line = substr(line, 1, end - 1)
      } else {
        sub(/[[:space:]]+#.*$/, "", line)
        sub(/[[:space:]]*$/, "", line)
      }
      value = line
    }
    END { print value }
  ' "$APP/.env"
}

count_media() {
  find "$MEDIA" -type f ! -path '*/previews/*' | wc -l | tr -d '[:space:]'
}

printf '\nHODARI PRODUCTION DEPLOYMENT\nStarted: %s\nApplication: %s\n' "$(date '+%Y-%m-%d %H:%M:%S %Z')" "$APP"
[[ "$(id -u)" == 0 ]] || fail "Run this script with sudo bash $APP/deployment/production/update.sh"
[[ -d "$APP/.git" ]] || fail "Git checkout not found at $APP."
[[ -f "$APP/.env" ]] || fail "Configuration file not found: $APP/.env"
[[ -x "$VENV/bin/python" && -x "$VENV/bin/pip" ]] || fail "Python environment not found at $VENV."
for command in awk git rsync sudo chown pg_dump systemctl curl tee; do
  command -v "$command" >/dev/null || fail "Required command is missing: $command"
done

mkdir -p "$BACKUP_DIR"
LOG="$BACKUP_DIR/deploy_$(date '+%Y-%m-%d_%H%M%S')_$$.log"
exec > >(tee -a "$LOG") 2>&1
printf 'Deployment log: %s\n' "$LOG"

DB_NAME="$(read_env POSTGRES_DB)"
[[ -n "$DB_NAME" ]] || fail "POSTGRES_DB is missing or empty in $APP/.env. No deployment changes were made."
MEDIA="$(read_env MEDIA_ROOT)"
if [[ -z "$MEDIA" ]]; then
  MEDIA="$APP/media"
  mkdir -p "$MEDIA"
fi
[[ -d "$MEDIA" ]] || fail "Upload directory does not exist: $MEDIA. Check MEDIA_ROOT before deploying."
# Every link and logo in production emails is built from SITE_URL; a missing
# or copied testing value sends parents to the testing site.
ENV_SITE_URL="$(read_env SITE_URL)"
[[ "${ENV_SITE_URL%/}" == "${SITE_URL%/}" ]] || fail "SITE_URL in $APP/.env is '${ENV_SITE_URL:-<missing>}' but must be ${SITE_URL%/} (emails link to it). No deployment changes were made."

BEFORE="$(git -C "$APP" rev-parse --short HEAD)"
BRANCH="$(git -C "$APP" branch --show-current)"
[[ -n "$BRANCH" ]] || fail "The checkout is not on a branch. Check out the production branch before deploying."
printf 'Branch: %s\nCurrent commit: %s\nDatabase: %s\nUploads: %s\n' "$BRANCH" "$BEFORE" "$DB_NAME" "$MEDIA"

step "1/8 Backing up uploaded files"
case "$MEDIA" in "$APP"|"$APP"/*)
  printf 'NOTE: uploads are inside the code folder. See PRODUCTION_DEPLOY.md for moving them outside it.\n' ;;
esac
mkdir -p "$BACKUP_DIR/media"
MEDIA_BEFORE="$(count_media)"
# Adds and updates, never deletes: missing source files remain in the backup.
rsync -a --info=progress2 "$MEDIA/" "$BACKUP_DIR/media/"
printf 'Upload backup complete: %s files copied to %s/media/\n' "$MEDIA_BEFORE" "$BACKUP_DIR"

step "2/8 Backing up the database"
DUMP="$BACKUP_DIR/${DB_NAME}_$(date '+%Y-%m-%d_%H%M%S')_$$.dump"
sudo -u postgres pg_dump -Fc "$DB_NAME" > "$DUMP"
[[ -s "$DUMP" ]] || fail "Database backup is empty. Deployment stopped before pulling code."
DB_BACKUP_READY=1
printf 'Database backup complete: %s\n' "$DUMP"

step "3/8 Pulling latest code on $BRANCH"
GIT_TERMINAL_PROMPT=0 git -C "$APP" fetch --progress origin
# Fast-forward only: a production update must not create a merge commit.
GIT_TERMINAL_PROMPT=0 git -C "$APP" pull --ff-only --progress
AFTER="$(git -C "$APP" rev-parse --short HEAD)"
if [[ "$BEFORE" == "$AFTER" ]]; then
  printf 'No new commits (%s). Continuing with dependencies, migrations, static files and restart.\n' "$AFTER"
else
  printf 'Updated code: %s -> %s\n' "$BEFORE" "$AFTER"
fi

step "4/8 Ensuring application ownership"
chown -R "$APPUSER:$APPUSER" "$APP"
printf 'Application ownership updated.\n'

step "5/8 Installing Python dependencies"
sudo -u "$APPUSER" "$VENV/bin/pip" install --disable-pip-version-check -r "$APP/requirements.txt"
printf 'Dependencies ready.\n'

step "6/8 Applying database migrations"
sudo -u "$APPUSER" "$VENV/bin/python" "$APP/manage.py" migrate --noinput
printf 'Migrations complete.\n'

step "7/8 Collecting static files and checking uploads"
sudo -u "$APPUSER" "$VENV/bin/python" "$APP/manage.py" collectstatic --noinput
MEDIA_AFTER="$(count_media)"
if (( MEDIA_AFTER < MEDIA_BEFORE )); then
  printf 'Upload backup is available at %s/media/.\n' "$BACKUP_DIR" >&2
  fail "Uploaded files decreased from $MEDIA_BEFORE to $MEDIA_AFTER. Services have not been restarted."
fi
chown -R "$APPUSER:$APPUSER" "$MEDIA"
printf 'Static files ready. Upload count: %s before, %s after.\n' "$MEDIA_BEFORE" "$MEDIA_AFTER"

step "8/8 Restarting services and checking the site"
mkdir -p "$RUN_DIR"
chown "$APPUSER:$APPUSER" "$RUN_DIR"
systemctl restart "${SERVICES[@]}"
for service in "${SERVICES[@]}"; do
  if ! systemctl is-active --quiet "$service"; then
    systemctl status "$service" --no-pager --lines=15 || true
    fail "Service $service is not active after restart."
  fi
  printf '  %s: active\n' "$service"
done
printf 'Checking %s ...\n' "$SITE_URL"
HTTP_CODE=000
for (( attempt = 1; attempt <= SITE_ATTEMPTS; attempt++ )); do
  HTTP_CODE="$(curl --silent --location --output /dev/null --write-out '%{http_code}' \
    --connect-timeout 8 --max-time 20 "$SITE_URL")" || HTTP_CODE=000
  case "$HTTP_CODE" in
    2??|3??) break ;;
  esac
  if (( attempt < SITE_ATTEMPTS )); then
    printf '  Site answered HTTP %s; waiting for it to finish starting (check %s of %s).\n' \
      "$HTTP_CODE" "$attempt" "$SITE_ATTEMPTS"
    sleep "$SITE_RETRY_DELAY"
  fi
done
case "$HTTP_CODE" in
  2??|3??) printf 'Site responded with HTTP %s.\n' "$HTTP_CODE" ;;
  000) fail "Site could not be reached after $SITE_ATTEMPTS checks." ;;
  *) fail "Site still responded with HTTP $HTTP_CODE after $SITE_ATTEMPTS checks." ;;
esac

printf '\nDEPLOYMENT SUCCESSFUL in %ss\nBranch: %s\nCommit: %s -> %s\nDatabase backup: %s\nDeployment log: %s\n' \
  "$SECONDS" "$BRANCH" "$BEFORE" "$AFTER" "$DUMP" "$LOG"
