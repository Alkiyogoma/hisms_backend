# Live update runbook — switch production to the `hodari` database

Run these on the **VPS** (as a sudo user). This keeps `DB=hodari` but uses a
**dedicated non-superuser role** (`hodari_user`) with a strong password —
never the `postgres` superuser on a public server.

> A fresh strong password and secret key were generated for you below. If you
> prefer your own, swap them everywhere they appear.

---

## 1. Create the role and database in PostgreSQL

```bash
sudo -u postgres psql <<'SQL'
-- Create the app role (idempotent-ish: will error if it already exists, that's fine)
CREATE USER hodari_user WITH PASSWORD 'wPDUA3bTLJvLMrLgWfWuL5KT36XU';
ALTER ROLE hodari_user SET client_encoding TO 'utf8';
ALTER ROLE hodari_user SET default_transaction_isolation TO 'read committed';
ALTER ROLE hodari_user SET timezone TO 'UTC';

CREATE DATABASE hodari OWNER hodari_user;
GRANT ALL PRIVILEGES ON DATABASE hodari TO hodari_user;
SQL

# PostgreSQL 15+ : grant schema rights (connect to the new DB first)
sudo -u postgres psql -d hodari <<'SQL'
GRANT ALL ON SCHEMA public TO hodari_user;
ALTER DATABASE hodari OWNER TO hodari_user;
SQL
```

Verify the credentials work over TCP:

```bash
PGPASSWORD='wPDUA3bTLJvLMrLgWfWuL5KT36XU' psql -h 127.0.0.1 -U hodari_user -d hodari -c '\conninfo'
```

---

## 2. Point the app's `.env` at the new database

Edit `/var/www/hodari/hisms_backend/.env` and set exactly these (keep everything
else — allowed hosts, Redis, email — as it already is):

```ini
DJANGO_DEBUG=0
DJANGO_USE_SQLITE=0
POSTGRES_DB=hodari
POSTGRES_USER=hodari_user
POSTGRES_PASSWORD=wPDUA3bTLJvLMrLgWfWuL5KT36XU
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
```

> `DJANGO_DEBUG` **must stay `0`** on live (enables HSTS, secure cookies, and the
> HTTPS redirect the Nginx `:8443` config relies on). `DEBUG=1` is only for local.

```bash
chmod 600 /var/www/hodari/hisms_backend/.env
```

---

## 3. Migrate and restart

```bash
sudo -u hodari -i
cd /var/www/hodari/hisms_backend
source /var/www/hodari/venv/bin/activate

git pull                                   # if you're shipping the cbor2 pin / doc changes
pip install -r requirements.txt            # only if deps changed
python manage.py check --deploy
python manage.py migrate --noinput
python manage.py collectstatic --noinput
python manage.py createsuperuser           # only if this is a brand-new DB
exit

sudo systemctl restart hodari hodari-celery hodari-celery-beat
systemctl is-active hodari hodari-celery hodari-celery-beat
```

---

## 4. Verify

```bash
curl -I https://connect.hodari.ac.tz:8443/
sudo journalctl -u hodari -n 30 --no-pager
```

Then log in at `https://connect.hodari.ac.tz:8443/admin/`.

---

## Notes / cautions

- **This is a NEW, empty `hodari` database.** If production currently runs on the
  old `hisms_prod` DB and you need that data, follow the full data-migration
  procedure below **instead of** step 1's `CREATE DATABASE hodari` and instead of
  running `createsuperuser` in step 3.

---

## Appendix A — Migrate existing data from `hisms_prod` to `hodari`

Run on the VPS. Replaces step 1 (DB creation) when you need to keep prod data.

```bash
# 1. Stop the app so nothing writes during the dump
sudo systemctl stop hodari hodari-celery hodari-celery-beat

# 2. Dump the current production database
sudo -u postgres pg_dump -Fc hisms_prod > /var/backups/hodari/hisms_prod_$(date +%F).dump
ls -lh /var/backups/hodari/hisms_prod_*.dump   # sanity check

# 3. Create the new empty hodari DB owned by the app role
sudo -u postgres psql <<'SQL'
DROP DATABASE IF EXISTS hodari;
CREATE DATABASE hodari OWNER hodari_user;
GRANT ALL PRIVILEGES ON DATABASE hodari TO hodari_user;
SQL
sudo -u postgres psql -d hodari -c "GRANT ALL ON SCHEMA public TO hodari_user;"

# 4. Restore into hodari, then hand ownership to hodari_user
sudo -u postgres pg_restore --no-owner --role=hodari_user --no-privileges \
     -d hodari /var/backups/hodari/hisms_prod_$(date +%F).dump

sudo -u postgres psql -d hodari -c "ALTER DATABASE hodari OWNER TO hodari_user;"
sudo -u postgres psql -d hodari <<'SQL'
DO $$DECLARE r record;
BEGIN
  FOR r IN SELECT tablename FROM pg_tables WHERE schemaname='public' LOOP
    EXECUTE format('ALTER TABLE public.%I OWNER TO hodari_user', r.tablename);
  END LOOP;
  FOR r IN SELECT sequence_name FROM information_schema.sequences WHERE sequence_schema='public' LOOP
    EXECUTE format('ALTER SEQUENCE public.%I OWNER TO hodari_user', r.sequence_name);
  END LOOP;
END$$;
SQL
```

Then continue with **step 2** of the main runbook (point `.env` at `hodari`),
run `migrate` (should report "No migrations to apply"), **skip** `createsuperuser`,
and restart. Verify before trusting it:

```bash
sudo -u postgres psql -d hisms_prod -tAc "SELECT count(*) FROM users_user;"
sudo -u postgres psql -d hodari     -tAc "SELECT count(*) FROM users_user;"
```

> `pg_restore` may print non-fatal notices ("already exists", plpgsql comments) —
> those are safe. Keep the `.dump` and the old `hisms_prod` DB for a few days as a
> rollback path (point `.env` back to `hisms_prod` and restart to revert).
- Rotate the generated password if it has ever been shared in plaintext.
- Optional but recommended: also rotate `DJANGO_SECRET_KEY` on live to a fresh
  value (a new one was generated for you): `9eqjqkple^i20*^9k43-yh1vibvd$*wuup0-jtvfw3w!#!+aoz`
  (note: rotating the secret key invalidates existing sessions — users re-login).
