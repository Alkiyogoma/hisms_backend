#!/usr/bin/env bash
# =============================================================================
# Update PRODUCTION (connect.hodari.ac.tz) to the latest `production` branch.
#
#   sudo bash deployment/production/update.sh        # asks before changing anything
#   sudo bash deployment/production/update.sh -y     # no prompt
#
# Safety:
#   * refuses to run if the server has hand edits to tracked files
#   * takes a database backup BEFORE touching code or migrations
#   * prints the exact rollback commands if anything fails
#
# Testing (testing.hodari.ac.tz) is updated separately from `main` with
# deployment/nguzo/update.sh.
# =============================================================================
set -euo pipefail

# --- Settings (override with environment variables if the server differs) ---
APP="${APP:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
VENV="${VENV:-/opt/hodari/venv}"
APPUSER="${APPUSER:-$(stat -c %U "$APP")}"
VENVUSER="${VENVUSER:-$(stat -c %U "$VENV")}"
BRANCH="${BRANCH:-production}"
SERVICES="${SERVICES:-hodari hodari-celery hodari-celery-beat}"
SITE_URL="${SITE_URL:-https://connect.hodari.ac.tz/}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/hodari}"

ASSUME_YES=0
[[ "${1:-}" == "-y" ]] && ASSUME_YES=1

as_app() { sudo -H -u "$APPUSER" "$@"; }
git_app() { as_app git -C "$APP" "$@"; }

fail() { echo "ERROR: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || fail "run with sudo"
[[ -d "$APP/.git" ]] || fail "$APP is not a git checkout (see PRODUCTION_DEPLOY.md, section 3)"
[[ -x "$VENV/bin/python" ]] || fail "virtualenv not found at $VENV (set VENV=...)"
[[ -f "$APP/.env" ]] || fail "$APP/.env is missing"

echo "==> App: $APP   venv: $VENV   user: $APPUSER   branch: $BRANCH"

# --- 1. Preflight -------------------------------------------------------------
current_branch="$(git_app rev-parse --abbrev-ref HEAD)"
[[ "$current_branch" == "$BRANCH" ]] || fail "checkout is on '$current_branch', expected '$BRANCH'"

if [[ -n "$(git_app status --porcelain --untracked-files=no)" ]]; then
  git_app status --short --untracked-files=no
  fail "tracked files were edited on the server. Commit them to the repo or discard them first."
fi

echo "==> 1/7 Fetching origin/$BRANCH"
git_app fetch --quiet origin "$BRANCH"
PREV="$(git_app rev-parse HEAD)"
NEXT="$(git_app rev-parse "origin/$BRANCH")"

if [[ "$PREV" == "$NEXT" ]]; then
  echo "Already up to date ($(git_app log -1 --format='%h %s'))."
  exit 0
fi

echo
echo "Commits to deploy:"
git_app log --oneline "$PREV..$NEXT"
NEW_MIGRATIONS="$(git_app diff --name-only --diff-filter=A "$PREV" "$NEXT" -- '*/migrations/0*.py')"
echo
echo "New migrations: ${NEW_MIGRATIONS:-none}"
if git_app diff --quiet "$PREV" "$NEXT" -- requirements.txt; then
  echo "requirements.txt: unchanged"
else
  echo "requirements.txt: CHANGED"
fi
echo

if [[ $ASSUME_YES -eq 0 ]]; then
  read -r -p "Deploy to PRODUCTION? [y/N] " answer
  [[ "$answer" =~ ^[Yy]$ ]] || { echo "Aborted. Nothing changed."; exit 1; }
fi

# --- 2. Database backup -------------------------------------------------------
DB_NAME="$(grep -E '^POSTGRES_DB=' "$APP/.env" | tail -1 | cut -d= -f2- | tr -d '\r" ')"
[[ -n "$DB_NAME" ]] || fail "POSTGRES_DB not found in $APP/.env"
mkdir -p "$BACKUP_DIR"
DUMP="$BACKUP_DIR/${DB_NAME}_predeploy_$(date +%F_%H%M)_${PREV:0:7}.dump"
echo "==> 2/7 Backing up database '$DB_NAME' -> $DUMP"
sudo -u postgres pg_dump -Fc "$DB_NAME" > "$DUMP"
[[ -s "$DUMP" ]] || fail "backup file is empty; not deploying"
echo "$PREV" > "$BACKUP_DIR/last_deploy_previous_commit"

rollback_help() {
  cat >&2 <<EOF

!!! Deploy failed. The site may be running partially updated code.
Roll back the code:
  sudo -H -u $APPUSER git -C $APP reset --hard $PREV
  sudo systemctl restart $SERVICES
If migrations ran and the old code errors on the new schema, restore the database:
  sudo systemctl stop $SERVICES
  sudo -u postgres pg_restore --clean --if-exists -d $DB_NAME $DUMP
  sudo systemctl start $SERVICES
EOF
}
trap rollback_help ERR

# --- 3. Code ------------------------------------------------------------------
echo "==> 3/7 Updating code to ${NEXT:0:7}"
git_app reset --hard --quiet "$NEXT"

# --- 4. Dependencies ----------------------------------------------------------
echo "==> 4/7 Python dependencies"
sudo -H -u "$VENVUSER" "$VENV/bin/pip" install --quiet -r "$APP/requirements.txt"

# --- 5. Checks + migrations ---------------------------------------------------
echo "==> 5/7 Django checks and migrations"
(cd "$APP" && as_app "$VENV/bin/python" manage.py check)
(cd "$APP" && as_app "$VENV/bin/python" manage.py migrate --noinput)

# --- 6. Static files ----------------------------------------------------------
echo "==> 6/7 Collecting static files"
(cd "$APP" && as_app "$VENV/bin/python" manage.py collectstatic --noinput --verbosity 0)

# --- 7. Restart + health check ------------------------------------------------
echo "==> 7/7 Restarting: $SERVICES"
systemctl restart $SERVICES
sleep 3
for s in $SERVICES; do
  systemctl is-active --quiet "$s" || { systemctl status "$s" --no-pager | tail -20; false; }
done
code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$SITE_URL" || true)"
[[ "$code" =~ ^(200|301|302)$ ]] || { echo "Health check $SITE_URL returned HTTP $code"; false; }

trap - ERR
echo
echo "Production is now on: $(git_app log -1 --format='%h %s')"
echo "Backup kept at: $DUMP"
