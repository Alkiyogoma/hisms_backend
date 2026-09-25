# Running HODARI / HISMS Locally

Step-by-step guide to run the school management system on your own computer
(macOS or Linux) for development and testing.

> For server deployment see `deployment/DEPLOY_UBUNTU.md` and
> `deployment/LIVE_UPDATE_RUNBOOK.md`. This guide is for **local** use only.

---

## 1. Prerequisites

| Tool | Version | Needed for | Install (macOS) |
|---|---|---|---|
| Python | 3.12 | Everything | `brew install python@3.12` |
| Git | any | Getting the code | `brew install git` |
| PostgreSQL | 14+ | *Optional* — same DB engine as live | `brew install postgresql@16` |
| Redis | 6+ | *Optional* — Celery tasks, live attendance updates | `brew install redis` |
| Pango / Cairo | — | PDF generation (report cards, ID cards) | `brew install pango` |

On Ubuntu/Debian use `apt` instead:

```bash
sudo apt install -y python3.12 python3.12-venv postgresql redis-server libpango-1.0-0 libpangocairo-1.0-0 libcairo2
```

**Quick start vs full setup:** you can run the app with only Python using
SQLite (Option A below). Use PostgreSQL + Redis (Option B) when you need to
test with a copy of live data or test background tasks.

---

## 2. Get the code

```bash
git clone git@github.com:Alkiyogoma/hisms_backend.git
```

```bash
cd hisms_backend
```

---

## 3. Create a virtual environment and install packages

```bash
python3.12 -m venv venv
```

```bash
source venv/bin/activate
```

Confirm the venv is active — this must print a path inside this project's
`venv/` folder and Python 3.12:

```bash
which python && python --version
```

```bash
python -m pip install --upgrade pip && python -m pip install -r requirements.txt
```

> `cbor2` is pinned to 5.6.5 in `requirements.txt` because newer versions need
> a Rust compiler on macOS. Don't upgrade it.

---

## 4. Configure the environment (`.env`)

Copy the template:

```bash
cp .env.example .env
```

Generate a secret key and paste it into `DJANGO_SECRET_KEY` in `.env`:

```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Then edit `.env` using **one** of the options below.

### Option A — SQLite (quickest, no database server)

```ini
DJANGO_SECRET_KEY=<paste generated key>
DJANGO_DEBUG=1
DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost
CSRF_TRUSTED_ORIGINS=http://127.0.0.1:8000,http://localhost:8000
DJANGO_USE_SQLITE=1
```

Remove or comment out the `REDIS_URL` line if Redis is not running (the app
then uses an in-memory cache).

The database file is created at `db.sqlite3` in the project folder.

### Option B — PostgreSQL (same as live)

Start PostgreSQL and create a database and user:

```bash
brew services start postgresql@16
```

```bash
createuser hodari_user --pwprompt
```

```bash
createdb hodari_local --owner=hodari_user
```

Then in `.env`:

```ini
DJANGO_SECRET_KEY=<paste generated key>
DJANGO_DEBUG=1
DJANGO_ALLOWED_HOSTS=127.0.0.1,localhost
CSRF_TRUSTED_ORIGINS=http://127.0.0.1:8000,http://localhost:8000
DJANGO_USE_SQLITE=0
POSTGRES_DB=hodari_local
POSTGRES_USER=hodari_user
POSTGRES_PASSWORD=<the password you chose>
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_SSLMODE=disable
```

> ⚠️ If `POSTGRES_PASSWORD` is empty and the host is local, the app silently
> falls back to SQLite. Set a password.

### Settings that matter locally

| Variable | Local value | Why |
|---|---|---|
| `DJANGO_DEBUG` | `1` | Serves static/media files, disables template caching, and writes emails to local JSON files instead of sending them |
| `WEBHOOK_API_TOKEN`, `ATTENDANCE_WEBHOOK_SECRET` | any value | Attendance hardware/Laravel webhooks reject all requests when empty |
| `REDIS_URL` | only if Redis is running | Enables the Redis cache |

**Never copy the live server's `.env` to your computer's project** — it
points at production and contains production secrets.

---

## 5. Create the database tables

```bash
python manage.py migrate
```

Check that the models and migrations are in sync (should print
`No changes detected`):

```bash
python manage.py makemigrations --check --dry-run
```

---

## 6. Create roles and users

Create the roles and permissions (required — menus and pages depend on them):

```bash
python manage.py seed_roles
```

Then **either** create test users for every role (local/demo only — they use
known passwords):

```bash
python manage.py seed_users
```

This prints a table of usernames and passwords, e.g. `superadmin` /
`Hodari@SA1`. See `core/management/commands/seed_users.py` for the full list.

**Or** create just your own admin account:

```bash
python manage.py createsuperuser
```

> ⚠️ Never run `seed_users` or other `seed_*` commands on the live server.

### Optional: demo data

Fills the system with sample students, applicants, incidents and timetable
data so pages aren't empty:

```bash
python manage.py seed_demo_data
```

---

## 7. Run the app

```bash
python manage.py runserver
```

Open <http://127.0.0.1:8000/login/> and sign in.

That's enough for most development work. Sections 8–9 are only needed for
background jobs, live updates and testing with real data.

---

## 8. Background tasks (optional — needs Redis)

Attendance notifications, reminders, scheduled reports and live dashboard
updates run through Celery. Start Redis:

```bash
brew services start redis
```

Make sure `.env` contains the Redis settings from `.env.example`
(`REDIS_URL`, `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`,
`CHANNELS_REDIS_URL`).

In a **second terminal** (with `source venv/bin/activate`), start the worker:

```bash
celery -A config worker --loglevel=info
```

In a **third terminal**, start the scheduler for periodic jobs:

```bash
celery -A config beat --loglevel=info --scheduler django_celery_beat.schedulers:DatabaseScheduler
```

### Live (WebSocket) attendance updates

`runserver` does not serve WebSockets. To test real-time attendance screens,
run the ASGI server instead of `runserver`:

```bash
daphne -b 127.0.0.1 -p 8000 config.asgi:application
```

---

## 9. Test with a copy of the live database (optional)

Useful before deploying migrations. Requires Option B (PostgreSQL).

1. Take a backup on the server and copy it to your computer — see the backup
   section of `deployment/LIVE_UPDATE_RUNBOOK.md`. Keep backups **outside** the
   project folder (e.g. `~/hodari/backups/`) so they're never committed; they
   contain real student and parent data.

2. Restore it into a separate local database:

   ```bash
   createdb hodari_livecopy --owner=hodari_user
   ```

   ```bash
   pg_restore --no-owner --no-privileges --role=hodari_user -d hodari_livecopy ~/hodari/backups/hisms_prod_YYYY-MM-DD.dump
   ```

3. Set `POSTGRES_DB=hodari_livecopy` in `.env`, then see what would be applied
   and apply it:

   ```bash
   python manage.py showmigrations | grep '\[ \]'
   ```

   ```bash
   python manage.py migrate
   ```

4. For photos and documents, extract the server's media backup into `media/`.

Delete the copy when finished:

```bash
dropdb hodari_livecopy
```

---

## 10. Running tests

```bash
python manage.py test students attendance welfare academics admissions hr
```

Some test modules need extra packages that aren't in `requirements.txt`:

```bash
python -m pip install pytest hypothesis
```

---

## 11. Useful commands

| Task | Command |
|---|---|
| Django shell | `python manage.py shell` |
| Check configuration | `python manage.py check` |
| List migrations | `python manage.py showmigrations` |
| Collect static files (only needed with `DJANGO_DEBUG=0`) | `python manage.py collectstatic` |
| Import students from Google Sheets export | `python manage.py import_from_sheets /path/to/all_students.json` |
| UI component style guide | open `/styleguide/` while logged in |

---

## 12. Troubleshooting

| Problem | Fix |
|---|---|
| `No module named 'pip'` or `No module named 'django'` although the prompt shows `(venv)` | The wrong venv is active (e.g. one copied from the server, whose `activate` points to `/opt/hodari/venv`). Run `deactivate`, then `source venv/bin/activate` inside this project and check `which python` |
| `OSError: cannot load library 'libpango…'` / PDF errors | Install Pango: `brew install pango` (macOS) or the `apt` packages in section 1 |
| `pip install` fails building `cbor2` | Keep the `cbor2==5.6.5` pin from `requirements.txt` |
| `CSRF verification failed` on login | Add your URL (e.g. `http://127.0.0.1:8000`) to `CSRF_TRUSTED_ORIGINS` in `.env` |
| `DisallowedHost` error | Add the host to `DJANGO_ALLOWED_HOSTS` |
| Pages have no styling with `DJANGO_DEBUG=0` | Run `collectstatic`, or use `DJANGO_DEBUG=1` locally |
| `Error 61 connecting to localhost:6379` | Redis isn't running — start it, or remove the Redis lines from `.env` |
| Data seems to vanish / wrong DB used | `POSTGRES_PASSWORD` empty → app fell back to SQLite. Set the password or set `DJANGO_USE_SQLITE=1` explicitly |
| Menus empty or "403 Forbidden" after login | Run `python manage.py seed_roles`, and make sure your user has a role (superusers created with `createsuperuser` may need a role set in `/admin/`) |
| `relation … does not exist` | Run `python manage.py migrate` |
