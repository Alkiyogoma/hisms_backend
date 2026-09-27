# Production deployment from git

One repository, two environments on the same VPS (`187.7.21.133`):

| | **Testing** | **Production** |
|---|---|---|
| URL | `https://testing.hodari.ac.tz` | `https://connect.hodari.ac.tz` |
| Git branch | `main` | `production` |
| Code directory | `/var/www/hodari/hisms_backend` | `/opt/hodari/hisms_backend` |
| Virtualenv | `/var/www/hodari/venv` | `/opt/hodari/venv` |
| Gunicorn | `127.0.0.1:8009` | `127.0.0.1:8007` |
| systemd units | `hodari-nguzo*` | `hodari`, `hodari-celery`, `hodari-celery-beat` |
| Config files | `deployment/nguzo/` | `deployment/production/` |
| Update command | `sudo bash deployment/nguzo/update.sh` | `sudo bash deployment/production/update.sh` |
| Database | its own (`.env` on that site) | `hisms_prod` (`.env` on that site) |

Each site keeps its own `.env` on the server. `.env` is gitignored, so git
never overwrites it.

> The files directly in `deployment/` (`gunicorn_config.py`, `hodari.service`,
> `nginx_hodari.conf` …) are **not** used by production. Production uses only
> `deployment/production/`.

---

## 1. Release workflow

```
feature branch ──PR──▶ main ──(auto / update.sh)──▶ TESTING
                          │
                          └──PR "Release YYYY-MM-DD"──▶ production ──update.sh──▶ PRODUCTION
```

1. Merge work into `main` through pull requests.
2. Deploy `main` to testing and check it there.
3. When testing is good, open a pull request **from `main` into `production`** on
   GitHub and merge it. Only code that has been on testing reaches production.
4. On the server, run the production update script (section 4).

Hotfix: branch from `production`, PR into `production`, deploy, then merge
`production` back into `main` so testing gets the fix too.

Recommended on GitHub → Settings → Branches: protect `production` (require a
pull request, no force pushes).

---

## 2. Create the `production` branch (once)

From your computer, on an up-to-date `main`:

```bash
git fetch origin && git branch production origin/main && git push -u origin production
```

---

## 3. One-time switch: manual production → git checkout

Production currently runs from files copied by hand. This section replaces
them with a git checkout **without changing** the services, nginx, port,
database, `.env` or uploaded media. The old folder is kept, so rollback is a
folder rename.

Expected downtime: about a minute (step 3.7).

### 3.1 Look before changing anything (read-only)

SSH in:

```bash
ssh root@187.7.21.133
```

Find the real paths, user and gunicorn config the services use:

```bash
systemctl cat hodari hodari-celery hodari-celery-beat | grep -E 'User=|Group=|WorkingDirectory=|ExecStart=|--config|EnvironmentFile'
```

```bash
ls -la /opt/hodari
```

```bash
grep -n "proxy_pass\|upstream\|server 127" /etc/nginx/sites-enabled/* | grep -v nguzo
```

Write down:

- that `WorkingDirectory=` is `/opt/hodari/hisms_backend`
- `APPUSER` — the `User=` (may be `root` if not set)
- whether `ExecStart=` uses `--config <path>/gunicorn_config.py`, and which path
- that nginx proxies to `127.0.0.1:8007`
- the owner of the venv: `stat -c %U /opt/hodari/venv`

> If `WorkingDirectory=` is anything other than `/opt/hodari/hisms_backend`,
> stop here and adjust `LIVE` below before continuing.

The commands below use shell variables — set them once per SSH session with
your app user (from `User=` above):

```bash
LIVE=/opt/hodari/hisms_backend; APPUSER=hodari; VENVUSER=$(stat -c %U /opt/hodari/venv); NEW=/opt/hodari/release_git; OLD=${LIVE}.manual-backup; APPHOME=$(getent passwd $APPUSER | cut -d: -f6)
```

### 3.2 Back up everything

```bash
mkdir -p /var/backups/hodari && sudo -u postgres pg_dump -Fc hisms_prod > /var/backups/hodari/hisms_prod_before_git_$(date +%F_%H%M).dump
```

```bash
tar czf /var/backups/hodari/live_code_before_git_$(date +%F).tar.gz --exclude=venv --exclude=staticfiles --exclude=__pycache__ -C "$(dirname $LIVE)" "$(basename $LIVE)"
```

Copy both files to your computer too (see `LOCAL_SETUP.md` §9).

### 3.3 Check the live code hasn't drifted

`new_school_app` was copied from the server on a certain date. If anyone edited
files on the server after that, those edits must be put into the repo first or
they will be lost. From **your computer**, list files that differ between the
server and your local copy (read-only dry run):

```bash
rsync -rcn --out-format='%n' --exclude=venv --exclude=__pycache__ --exclude='*.pyc' --exclude=staticfiles --exclude=media --exclude=.env root@187.7.21.133:/opt/hodari/hisms_backend/ ~/hodari/new_school_app/
```

Empty output = nothing changed since the copy. Any file listed must be
reviewed and, if it matters, merged into `main` → testing → `production`
before continuing.

### 3.4 Production `.env` additions

Production's `.env` stays as it is, plus these lines. The new code's default
`CSRF_TRUSTED_ORIGINS` no longer includes `hodari.elimcoregroup.com`, so list
every address people use to log in (include `:port` if URLs use one):

```ini
CSRF_TRUSTED_ORIGINS=https://connect.hodari.ac.tz,https://hodari.elimcoregroup.com
POSTGRES_SSLMODE=disable
```

Optional (the attendance webhooks already reject requests on production
today because these are unset; set them only when the webhook is in use):

```ini
WEBHOOK_API_TOKEN=<python3 -c "import secrets; print(secrets.token_urlsafe(48))">
ATTENDANCE_WEBHOOK_SECRET=<same command, different value>
```

### 3.5 Give the server read-only access to the repo

The repo is private. Create a deploy key for the app user:

```bash
sudo -H -u $APPUSER mkdir -p $APPHOME/.ssh && sudo -H -u $APPUSER ssh-keygen -t ed25519 -N '' -C "hodari-production" -f $APPHOME/.ssh/id_ed25519
```

```bash
cat $APPHOME/.ssh/id_ed25519.pub
```

Add that public key on GitHub → repo **Settings → Deploy keys → Add deploy
key** (leave "Allow write access" **unticked**). Then test:

```bash
sudo -H -u $APPUSER ssh -T git@github.com
```

It should say "successfully authenticated … does not provide shell access".

### 3.6 Prepare the new checkout next to the live one (live keeps running)

```bash
mkdir $NEW && chown $APPUSER: $NEW && sudo -H -u $APPUSER git clone --branch production git@github.com:Alkiyogoma/hisms_backend.git $NEW
```

```bash
cp -p $LIVE/.env $NEW/.env && chmod 600 $NEW/.env && chown $APPUSER: $NEW/.env
```

```bash
rsync -a $LIVE/media/ $NEW/media/ && chown -R $APPUSER: $NEW/media
```

Install the new package (`django-cotton`) into the shared venv. This only
adds a package; existing versions (including `cbor2` 6.x) are kept, so the
running site is not affected:

```bash
sudo -H -u $VENVUSER /opt/hodari/venv/bin/pip install -r $NEW/requirements.txt
```

Check the new code against the **live** database without changing it:

```bash
cd $NEW && sudo -H -u $APPUSER /opt/hodari/venv/bin/python manage.py check --deploy
```

```bash
cd $NEW && sudo -H -u $APPUSER /opt/hodari/venv/bin/python manage.py migrate --plan
```

Expected plan: only `admissions.0028_alter_applicant_parent_relationship` and
`hr.0019_remove_leaveallocation_idx_la_staff_and_more`. Both run no SQL
(`sqlmigrate` shows `-- (no-op)`), so the database schema does not change.
If the plan lists anything else, stop and review it first.

### 3.7 Switch (short downtime)

```bash
systemctl stop hodari hodari-celery hodari-celery-beat
```

Copy any files uploaded since step 3.6:

```bash
rsync -a $LIVE/media/ $NEW/media/ && chown -R $APPUSER: $NEW/media
```

Swap folders — the live path now holds the git checkout, so systemd and nginx
paths stay valid:

```bash
mv $LIVE $OLD && mv $NEW $LIVE
```

**Required:** the `hodari` service currently loads
`deployment/gunicorn_config.py`. In the repo that file is set up for a
different site (port `8008`, different log paths), so production must be pointed
at `deployment/production/gunicorn_config.py` (port `8007`, same settings the
live site runs today). This adds a systemd override; the original unit file is
not edited:

```bash
mkdir -p /etc/systemd/system/hodari.service.d && printf '[Service]\nExecStart=\nExecStart=/opt/hodari/venv/bin/gunicorn config.wsgi:application --config /opt/hodari/hisms_backend/deployment/production/gunicorn_config.py\n' > /etc/systemd/system/hodari.service.d/production-gunicorn.conf
```

Check it took effect — the last `ExecStart` must show `deployment/production/`:

```bash
systemctl daemon-reload && systemctl cat hodari | grep -A1 ExecStart
```

Apply migrations and static files, then start:

```bash
cd $LIVE && sudo -H -u $APPUSER /opt/hodari/venv/bin/python manage.py migrate --noinput && sudo -H -u $APPUSER /opt/hodari/venv/bin/python manage.py collectstatic --noinput
```

```bash
systemctl daemon-reload && systemctl start hodari hodari-celery hodari-celery-beat
```

### 3.8 Verify

```bash
systemctl is-active hodari hodari-celery hodari-celery-beat
```

```bash
curl -sI https://connect.hodari.ac.tz/ | head -1
```

```bash
journalctl -u hodari -n 50 --no-pager
```

Then in the browser: log in, open Students (search, a student profile, class
filter → **Print IDs**), Attendance today, Welfare, Finance, and check that
student photos/documents still show.

### 3.9 Rollback (if something is wrong)

The database schema did not change, so rolling back is only the folder swap:

```bash
systemctl stop hodari hodari-celery hodari-celery-beat
```

```bash
mv $LIVE ${LIVE}.git-failed && mv $OLD $LIVE
```

Remove the override from 3.7 so the old files use their old gunicorn config:

```bash
rm /etc/systemd/system/hodari.service.d/production-gunicorn.conf && systemctl daemon-reload
```

```bash
systemctl start hodari hodari-celery hodari-celery-beat
```

If you're in a new SSH session, set the variables from 3.1 again first.

Keep `/opt/hodari/hisms_backend.manual-backup` for a few weeks after a successful switch, then
delete it.

---

## 4. Every production deploy after the switch

After merging `main` → `production` on GitHub:

```bash
ssh root@187.7.21.133
```

```bash
sudo bash /opt/hodari/hisms_backend/deployment/production/update.sh
```

The script (same steps as the testing script `deployment/nguzo/update.sh`):

1. backs up the database to `/var/backups/hodari/<db>_<date>.dump` and prints
   the commit the server was on;
2. pulls the latest code on the checked-out branch (`git pull`);
3. fixes ownership, installs requirements, runs `migrate` and `collectstatic`;
4. restarts `hodari`, `hodari-celery`, `hodari-celery-beat` and shows their
   status and the site's HTTP response.

If something goes wrong, roll back to the commit printed in step 1 and, only if
new migrations ran and the old code errors, restore the backup:

```bash
cd /opt/hodari/hisms_backend && git reset --hard COMMIT && systemctl restart hodari hodari-celery hodari-celery-beat
```

```bash
systemctl stop hodari hodari-celery hodari-celery-beat && sudo -u postgres pg_restore --clean --if-exists -d hisms_prod /var/backups/hodari/BACKUP.dump && systemctl start hodari hodari-celery hodari-celery-beat
```

---

## 5. Rules

- **Never edit code on the production server.** Change it in the repo → `main`
  → testing → `production`. The update script refuses to run over hand edits.
- **Never copy one site's `.env` to the other.** Each has its own database,
  Redis DB numbers and secret key.
- **Never run `seed_*` commands on production.**
- Test migrations on a copy of the production database first when they touch
  existing tables (`LOCAL_SETUP.md` §9).
