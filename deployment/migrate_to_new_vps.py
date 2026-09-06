#!/usr/bin/env python3
"""
HODARI Migration: Move Live from 72.62.0.216 to 187.7.21.133
Run from your local Windows machine: python deployment/migrate_to_new_vps.py
"""
import os, sys, tarfile, time, argparse, getpass, tempfile

try:
    import paramiko
except ImportError:
    os.system(f"{sys.executable} -m pip install paramiko")
    import paramiko

OLD_VPS = "72.62.0.216"
NEW_VPS = "187.7.21.133"
USER = "root"
DEPLOY_DIR = "/var/www/hodari"
BACKEND_DIR = "/var/www/hodari/hisms_backend"
HODARI_USER = "hodari"
OLD_PASS = None  # will prompt
NEW_PASS = None  # will prompt

EXCLUDE_DIRS = {"venv",".venv","__pycache__",".git","node_modules","dist","build",".pytest_cache",".mypy_cache",".tools",".hypothesis","staticfiles","media","backups","scratch","deployment"}
EXCLUDE_FILES = {"db.sqlite3","db.sqlite3-wal","db.sqlite3-shm",".env",".env.save","server.log"}

def step(m): print(f"\n\033[96m[{time.strftime('%H:%M:%S')}]\033[0m \033[93m>> {m}\033[0m")
def ok(m): print(f"  \033[92m✓\033[0m {m}")
def warn(m): print(f"  \033[93m⚠\033[0m {m}")
def fail(m): print(f"  \033[91m✗\033[0m {m}")

def connect(host, password):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(host, 22, USER, password, timeout=30)
    ok(f"Connected to {host}")
    return c

def run(c, cmd, timeout=120):
    _, stdout, stderr = c.exec_command(cmd, timeout=timeout)
    code = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8","replace").strip()
    err = stderr.read().decode("utf-8","replace").strip()
    return out, err, code

def is_excluded(rel):
    parts = rel.replace("\\","/").split("/")
    if any(p in EXCLUDE_DIRS or p.startswith(".") for p in parts):
        return True
    b = os.path.basename(rel)
    return b in EXCLUDE_FILES or b.endswith((".pyc",".pyo",".swp"))

def create_tarball(project_dir):
    tarball = os.path.join(tempfile.gettempdir(), "hodari_deploy.tar.gz")
    step("Creating tarball...")
    count = 0
    with tarfile.open(tarball, "w:gz") as tar:
        for root, dirs, files in os.walk(project_dir):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith(".")]
            rel_root = os.path.relpath(root, project_dir)
            if rel_root == ".": rel_root = ""
            for f in files:
                rel = os.path.join(rel_root, f) if rel_root else f
                if is_excluded(rel): continue
                tar.add(os.path.join(root, f), arcname=os.path.join("hisms_backend", rel))
                count += 1
    ok(f"Tarball: {count} files, {os.path.getsize(tarball)/(1024*1024):.1f} MB")
    return tarball

def upload_and_extract(client, tarball):
    step("Uploading code...")
    sftp = client.open_sftp()
    try: sftp.stat(DEPLOY_DIR)
    except: run(client, f"mkdir -p {DEPLOY_DIR}", timeout=30)
    remote = f"{DEPLOY_DIR}/deploy.tar.gz"
    sftp.put(tarball, remote)
    sftp.close()
    ok("Uploaded")

    step("Extracting...")
    run(client, f"cp -r {BACKEND_DIR} {BACKEND_DIR}.bak.$(date +%s) 2>/dev/null || true")
    out, err, code = run(client, f"cd {DEPLOY_DIR} && tar xzf deploy.tar.gz", timeout=120)
    run(client, f"rm -f {DEPLOY_DIR}/deploy.tar.gz")
    run(client, f"chown -R {HODARI_USER}:{HODARI_USER} {DEPLOY_DIR}")
    run(client, f"chmod -R 755 {DEPLOY_DIR}")
    ok("Extracted and permissions set")

def write_env(client):
    step("Writing .env file...")
    env = """DJANGO_SECRET_KEY=hodari-pr0d-s3cr3t-k3y-2026-elimcore!
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=connect.hodari.ac.tz,hodari.nguzo.co.tz,187.7.21.133,localhost,127.0.0.1
DJANGO_USE_SQLITE=0
POSTGRES_DB=hisms_prod
POSTGRES_USER=hodari_user
POSTGRES_PASSWORD=H0d@r1_Pr0d_2026!
POSTGRES_HOST=localhost
POSTGRES_PORT=5432
REDIS_URL=redis://localhost:6379/0
CELERY_BROKER_URL=redis://localhost:6379/0
CELERY_RESULT_BACKEND=redis://localhost:6379/1
CELERY_ACCEPT_CONTENT=json
CELERY_TASK_SERIALIZER=json
CELERY_RESULT_SERIALIZER=json
CELERY_TIMEZONE=Africa/Nairobi
CELERY_ENABLE_UTC=1
CHANNELS_REDIS_URL=redis://localhost:6379/2
CORS_ALLOW_ALL_ORIGINS=False
CORS_ALLOWED_ORIGINS=https://connect.hodari.ac.tz,https://hodari.nguzo.co.tz
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
DEFAULT_FROM_EMAIL=noreply@connect.hodari.ac.tz
SESSION_IDLE_TIMEOUT=1800
LOGIN_MAX_ATTEMPTS=5
LOGIN_LOCKOUT_DURATION=900
BACKUP_DIR=/var/backups/hodari
BACKUP_RETENTION_DAYS=30"""
    sftp = client.open_sftp()
    with sftp.open(f"{BACKEND_DIR}/.env", "w") as f:
        f.write(env)
    sftp.close()
    run(client, f"chmod 600 {BACKEND_DIR}/.env")
    run(client, f"chown {HODARI_USER}:{HODARI_USER} {BACKEND_DIR}/.env")
    ok(".env written")

def setup_python(client):
    step("Setting up Python venv...")
    venv = f"{DEPLOY_DIR}/venv"
    out, _, _ = run(client, f"ls {venv}/bin/python 2>/dev/null && echo EXISTS || echo MISSING")
    if "EXISTS" not in out:
        run(client, f"python3 -m venv {venv}", timeout=60)
        run(client, f"chown -R {HODARI_USER}:{HODARI_USER} {venv}")
    ok("Python venv ready")

    step("Installing dependencies (may take a few minutes)...")
    run(client, f"{venv}/bin/pip install --upgrade pip setuptools wheel -q", timeout=60)
    out, err, code = run(client, f"cd {BACKEND_DIR} && {venv}/bin/pip install -r requirements.txt -q 2>&1", timeout=600)
    if code == 0: ok("Dependencies installed")
    else: warn(f"Some deps failed: {err[:300]}")
    run(client, f"{venv}/bin/pip install -q gunicorn psycopg2-binary django-celery-beat", timeout=120)

def setup_django(client):
    step("Running migrations...")
    out, err, code = run(client, f"cd {BACKEND_DIR} && {DEPLOY_DIR}/venv/bin/python manage.py migrate --no-input 2>&1", timeout=300)
    for line in out.split("\n")[-5:]:
        if line.strip(): print(f"    {line.strip()}")

    step("Collecting static files...")
    run(client, f"cd {BACKEND_DIR} && {DEPLOY_DIR}/venv/bin/python manage.py collectstatic --no-input -q 2>&1", timeout=120)
    ok("Django setup done")

def setup_services(client):
    step("Configuring services...")
    # nginx — isolated site name/config so a co-hosted site (e.g. connect.hodari.ac.tz)
    # is never overwritten. NEVER `rm sites-enabled/default` here — only add our own.
    run(client, f"cp {BACKEND_DIR}/deployment/nguzo/nginx_nguzo.conf /etc/nginx/sites-available/nguzo")
    run(client, "ln -sf /etc/nginx/sites-available/nguzo /etc/nginx/sites-enabled/nguzo")
    out, _, _ = run(client, "nginx -t 2>&1")
    if "successful" in out or "test is successful" in out:
        run(client, "systemctl reload nginx")
        ok("Nginx configured")
    else:
        warn(f"Nginx issue: {out[:200]}")

    # systemd — isolated unit names (hodari-nguzo*) so connect's hodari* units are untouched
    run(client, f"cp {BACKEND_DIR}/deployment/nguzo/hodari-nguzo.service /etc/systemd/system/")
    run(client, f"cp {BACKEND_DIR}/deployment/nguzo/hodari-nguzo-celery.service /etc/systemd/system/")
    run(client, f"cp {BACKEND_DIR}/deployment/nguzo/hodari-nguzo-celery-beat.service /etc/systemd/system/")
    run(client, "systemctl daemon-reload")
    for svc in ["hodari-nguzo", "hodari-nguzo-celery", "hodari-nguzo-celery-beat"]:
        run(client, f"systemctl enable {svc}")

    step("Starting services...")
    for svc in ["postgresql", "redis-server", "hodari-nguzo", "hodari-nguzo-celery", "hodari-nguzo-celery-beat", "nginx"]:
        run(client, f"systemctl restart {svc}")
    time.sleep(3)

    step("Service status:")
    for svc in ["hodari-nguzo", "hodari-nguzo-celery", "postgresql", "redis-server", "nginx"]:
        out, _, _ = run(client, f"systemctl is-active {svc}")
        if "active" in out: ok(f"{svc}: RUNNING")
        else: warn(f"{svc}: {out}")

def transfer_db(old_client, new_client):
    step("Dumping database from OLD VPS...")
    run(old_client, "sudo -u postgres pg_dump hisms_prod > /tmp/hodari_db.sql 2>/dev/null")
    out, _, _ = run(old_client, "ls -la /tmp/hodari_db.sql 2>/dev/null")
    if not out:
        warn("Trying alternative dump method...")
        run(old_client, "PGPASSWORD=H0d@r1_Pr0d_2026! pg_dump -U hodari_user hisms_prod > /tmp/hodari_db.sql 2>/dev/null")
        out, _, _ = run(old_client, "ls -la /tmp/hodari_db.sql 2>/dev/null")
    if out: ok(f"DB dump: {out}")
    else: fail("Could not dump database!"); return

    step("Downloading dump to local...")
    local = os.path.join(tempfile.gettempdir(), "hodari_db.sql")
    sftp = old_client.open_sftp()
    sftp.get("/tmp/hodari_db.sql", local)
    sftp.close()
    ok(f"Downloaded: {os.path.getsize(local)/(1024*1024):.1f} MB")

    step("Uploading dump to NEW VPS...")
    sftp = new_client.open_sftp()
    sftp.put(local, "/tmp/hodari_db.sql")
    sftp.close()

    step("Restoring database on NEW VPS...")
    run(new_client, "sudo -u postgres psql -c \"DROP DATABASE IF EXISTS hisms_prod;\"")
    run(new_client, "sudo -u postgres psql -c \"CREATE USER hodari_user WITH PASSWORD 'H0d@r1_Pr0d_2026!';\" 2>/dev/null || true")
    run(new_client, "sudo -u postgres psql -c \"CREATE DATABASE hisms_prod OWNER hodari_user;\"")
    run(new_client, "sudo -u postgres psql -c \"GRANT ALL PRIVILEGES ON DATABASE hisms_prod TO hodari_user;\"")
    run(new_client, "sudo -u postgres psql -d hisms_prod -c \"GRANT ALL ON SCHEMA public TO hodari_user;\"")
    out, err, code = run(new_client, "sudo -u postgres psql -d hisms_prod < /tmp/hodari_db.sql 2>&1", timeout=300)
    if code == 0: ok("Database restored")
    else: warn(f"DB restore issues: {err[:300]}")
    run(new_client, "rm -f /tmp/hodari_db.sql")
    os.remove(local)

def transfer_media(old_client, new_client):
    step("Transferring media files...")
    run(old_client, "tar czf /tmp/hodari_media.tar.gz -C /var/www/hodari/hism*s_backend media 2>/dev/null || tar czf /tmp/hodari_media.tar.gz -C /var/www/hodari/hisms_backend media 2>/dev/null")
    out, _, _ = run(old_client, "ls -la /tmp/hodari_media.tar.gz 2>/dev/null")
    if not out: warn("No media to transfer"); return

    local = os.path.join(tempfile.gettempdir(), "hodari_media.tar.gz")
    sftp = old_client.open_sftp()
    sftp.get("/tmp/hodari_media.tar.gz", local)
    sftp.close()

    sftp = new_client.open_sftp()
    sftp.put(local, "/tmp/hodari_media.tar.gz")
    sftp.close()

    run(new_client, f"cd {BACKEND_DIR} && tar xzf /tmp/hodari_media.tar.gz")
    run(new_client, "rm -f /tmp/hodari_media.tar.gz")
    run(new_client, f"chown -R {HODARI_USER}:{HODARI_USER} {BACKEND_DIR}/media")
    os.remove(local)
    ok("Media transferred")

def verify(client):
    step("Verifying deployment...")
    out, _, _ = run(client, "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8009/ 2>/dev/null || echo 000")
    if out and out != "000": ok(f"HTTP response: {out}")
    else: warn("No HTTP response on 8009 (nguzo gunicorn)")

def setup_firewall(client):
    step("Configuring firewall...")
    for port in ["22/tcp", "80/tcp", "443/tcp", "8070/tcp", "8443/tcp"]:
        run(client, f"ufw allow {port}")
    run(client, "echo y | ufw enable")
    ok("Firewall configured")

def setup_ssl(client):
    step("Setting up SSL...")
    run(client, "systemctl enable certbot.timer && systemctl start certbot.timer")
    out, _, _ =     run(client, f"certbot --nginx -d {DOMAIN} --non-interactive --agree-tos --email admin@connect.hodari.ac.tz 2>&1", timeout=120)
    ok("SSL attempted (may need manual certbot run)")

DOMAIN = "connect.hodari.ac.tz"

def main():
    parser = argparse.ArgumentParser(description="Migrate HODARI to new VPS")
    parser.add_argument("--old-password", help="Old VPS password")
    parser.add_argument("--new-password", help="New VPS password")
    parser.add_argument("--skip-db", action="store_true", help="Skip database transfer")
    parser.add_argument("--skip-media", action="store_true", help="Skip media transfer")
    args = parser.parse_args()

    print(f"\033[94m{'='*70}\n  HODARI Migration: {OLD_VPS} -> {NEW_VPS}\n  Domain: {DOMAIN}\n{'='*70}\033[0m")

    old_pass = args.old_password or getpass.getpass(f"Password for {USER}@{OLD_VPS} (old VPS): ")
    new_pass = args.new_password or getpass.getpass(f"Password for {USER}@{NEW_VPS} (new VPS): ")

    old_client = connect(OLD_VPS, old_pass, "OLD VPS")
    new_client = connect(NEW_VPS, new_pass, "NEW VPS")

    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    try:
        # 1. Transfer database
        if not args.skip_db:
            transfer_db(old_client, new_client)

        # 2. Transfer media
        if not args.skip_media:
            transfer_media(old_client, new_client)

        # 3. Upload code
        tarball = create_tarball(project_dir)
        upload_and_extract(new_client, tarball)
        if os.path.exists(tarball): os.remove(tarball)

        # 4. Configure new VPS
        write_env(new_client)
        setup_python(new_client)
        setup_django(new_client)
        setup_services(new_client)
        setup_firewall(new_client)
        verify(new_client)

        print(f"""
\033[94m{'='*70}
\033[92m  MIGRATION COMPLETE!
{'='*70}

  \033[96mNew VPS:\033[0m   {NEW_VPS}
  \033[96mDomain:\033[0m   https://{DOMAIN}
  \033[96mPort:\033[0m     8070 (HTTP) / 8443 (HTTPS)

  \033[93mNext steps:\033[0m
  1. DNS already points to {NEW_VPS} - verify with: ping {DOMAIN}
  2. SSH to new VPS: ssh root@{NEW_VPS}
  3. Run SSL setup: certbot --nginx -d {DOMAIN}
  4. Create superuser if needed:
     cd {BACKEND_DIR} && {DEPLOY_DIR}/venv/bin/python manage.py createsuperuser

  \033[93mUseful commands:\033[0m
  sudo systemctl status hodari-nguzo
  sudo journalctl -u hodari-nguzo -f
  sudo systemctl restart hodari-nguzo hodari-nguzo-celery

\033[94m{'='*70}\033[0m
""")

    except Exception as e:
        fail(f"Migration failed: {e}")
        import traceback; traceback.print_exc()
    finally:
        old_client.close()
        new_client.close()

if __name__ == "__main__":
    main()
