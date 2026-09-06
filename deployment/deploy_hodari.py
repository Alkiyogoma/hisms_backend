#!/usr/bin/env python3
"""
HODARI Production Deployment Script
Uploads project to VPS 187.7.21.133 and configures for production.
Domain: connect.hodari.ac.tz | Port: 8070

Usage:
    python deploy_hodari.py
    python deploy_hodari.py --upload-only
    python deploy_hodari.py --setup-only
"""
import os
import sys
import tarfile
import time
import argparse
import getpass

try:
    import paramiko
except ImportError:
    print("Installing paramiko...")
    os.system(f"{sys.executable} -m pip install paramiko")
    import paramiko

# =============================================================================
# Configuration
# =============================================================================
VPS_HOST = "187.7.21.133"
VPS_PORT = 22
VPS_USER = "root"
REMOTE_DEPLOY_DIR = "/opt/hodari"
REMOTE_BACKEND_DIR = "/opt/hodari/hisms_backend"
LOCAL_PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # hisms_backend parent
LOCAL_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))  # hisms_backend
DOMAIN = "connect.hodari.ac.tz"
HODARI_USER = "hodari"

# Files/dirs to exclude from upload
EXCLUDE_DIRS = {
    "venv", ".venv", "__pycache__", ".git", "node_modules",
    "dist", "build", ".pytest_cache", ".mypy_cache", ".tools",
    ".hypothesis", "staticfiles", "media", "backups",
    "deployment",  # We upload deployment separately
    "scratch",
}

EXCLUDE_FILES = {
    "db.sqlite3", "db.sqlite3-wal", "db.sqlite3-shm",
    ".env", ".env.save", "server.log",
}

# Colors
class C:
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    CYAN = '\033[96m'
    BLUE = '\033[94m'
    BOLD = '\033[1m'
    END = '\033[0m'


def banner():
    print(f"""{C.BLUE}{'='*70}
  HODARI Production Deployment
  Target: {VPS_USER}@{VPS_HOST}:{REMOTE_DEPLOY_DIR}
  Domain: {DOMAIN} | Port: 8070
  Time: {time.strftime('%Y-%m-%d %H:%M:%S')}
{'='*70}{C.END}""")


def step(msg):
    print(f"\n{C.CYAN}[{time.strftime('%H:%M:%S')}]{C.END} {C.YELLOW} {msg}{C.END}")


def ok(msg):
    print(f"  {C.GREEN}{C.END} {msg}")


def warn(msg):
    print(f"  {C.YELLOW}{C.END} {msg}")


def fail(msg):
    print(f"  {C.RED}{C.END} {msg}")


def run_cmd(client, command, timeout=120, sudo=False):
    """Execute command on remote server."""
    if sudo:
        cmd = f"sudo -S bash -c '{command}'"
    else:
        cmd = command

    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout)
    exit_code = stdout.channel.recv_exit_status()
    out = stdout.read().decode("utf-8", errors="replace").strip()
    err = stderr.read().decode("utf-8", errors="replace").strip()
    return out, err, exit_code


def is_excluded(rel_path):
    """Check if path should be excluded."""
    parts = rel_path.replace("\\", "/").split("/")
    for part in parts:
        if part in EXCLUDE_DIRS or part.startswith("."):
            return True
    basename = os.path.basename(rel_path)
    if basename in EXCLUDE_FILES or basename.endswith((".pyc", ".pyo", ".swp")):
        return True
    return False


def create_tarball(project_dir, output_path):
    """Create tarball of project files for upload."""
    step("Creating deployment tarball...")

    count = 0
    with tarfile.open(output_path, "w:gz") as tar:
        for root, dirs, files in os.walk(project_dir):
            # Skip excluded directories
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith(".")]

            rel_root = os.path.relpath(root, project_dir)
            if rel_root == ".":
                rel_root = ""

            for fname in files:
                rel_file = os.path.join(rel_root, fname) if rel_root else fname
                if is_excluded(rel_file):
                    continue

                fpath = os.path.join(root, fname)
                arcname = os.path.join("hisms_backend", rel_file)
                tar.add(fpath, arcname=arcname)
                count += 1
                if count % 500 == 0:
                    print(f"    ... {count} files added")

        # Also add deployment directory
        deploy_dir = os.path.join(project_dir, "deployment")
        if os.path.isdir(deploy_dir):
            for root, dirs, files in os.walk(deploy_dir):
                for fname in files:
                    fpath = os.path.join(root, fname)
                    rel_file = os.path.relpath(fpath, project_dir)
                    tar.add(fpath, arcname=rel_file)
                    count += 1

    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    ok(f"Tarball created: {count} files, {size_mb:.1f} MB")
    return output_path


def connect_vps(password):
    """Connect to VPS via SSH."""
    step(f"Connecting to {VPS_HOST}...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(VPS_HOST, VPS_PORT, VPS_USER, password, timeout=30)
    ok(f"Connected to {VPS_HOST}")
    return client


def upload_tarball(client, tarball_path, password):
    """Upload tarball to VPS via SFTP."""
    step("Uploading tarball to VPS...")

    sftp = client.open_sftp()
    remote_path = f"{REMOTE_DEPLOY_DIR}/deploy.tar.gz"

    # Ensure remote dir exists
    try:
        sftp.stat(REMOTE_DEPLOY_DIR)
    except FileNotFoundError:
        run_cmd(client, f"mkdir -p {REMOTE_DEPLOY_DIR}", sudo=True)

    file_size = os.path.getsize(tarball_path)
    start = time.time()
    last_pct = [0]

    def progress(transferred, total):
        if total > 0:
            pct = int(transferred * 100 / total)
            if pct >= last_pct[0] + 10:
                elapsed = time.time() - start
                speed = transferred / (1024 * 1024) / elapsed if elapsed > 0 else 0
                print(f"    ... {pct}% ({transferred // (1024*1024)} MB @ {speed:.1f} MB/s)")
                last_pct[0] = pct

    sftp.put(tarball_path, remote_path, callback=progress)
    elapsed = time.time() - start
    speed = (file_size / (1024 * 1024)) / elapsed if elapsed > 0 else 0
    ok(f"Uploaded {file_size / (1024*1024):.1f} MB in {elapsed:.1f}s ({speed:.1f} MB/s)")
    sftp.close()
    return remote_path


def extract_and_setup(client, password):
    """Extract tarball and setup project on VPS."""
    step("Extracting files on VPS...")

    # Backup existing if any
    run_cmd(client, f"if [ -d {REMOTE_BACKEND_DIR} ]; then cp -r {REMOTE_BACKEND_DIR} {REMOTE_BACKEND_DIR}.backup.$(date +%Y%m%d_%H%M%S) 2>/dev/null || true; fi", sudo=True)

    # Extract
    out, err, code = run_cmd(client, f"cd {REMOTE_DEPLOY_DIR} && tar xzf deploy.tar.gz", timeout=60, sudo=True)
    if code == 0:
        ok("Files extracted")
    else:
        warn(f"Extraction had issues: {err[:200]}")

    # Cleanup tarball
    run_cmd(client, f"rm -f {REMOTE_DEPLOY_DIR}/deploy.tar.gz", sudo=True)

    # Set ownership
    run_cmd(client, f"chown -R {HODARI_USER}:{HODARI_USER} {REMOTE_DEPLOY_DIR}", sudo=True)
    run_cmd(client, f"chmod -R 755 {REMOTE_DEPLOY_DIR}", sudo=True)

    ok("Files extracted and permissions set")


def setup_env(client, password):
    """Write production .env file."""
    step("Writing production .env file...")

    env_content = """DJANGO_SECRET_KEY=hodari-pr0d-s3cr3t-k3y-2026-elimcore!
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
    with sftp.open(f"{REMOTE_BACKEND_DIR}/.env", "w") as f:
        f.write(env_content)
    sftp.close()

    run_cmd(client, f"chmod 600 {REMOTE_BACKEND_DIR}/.env", sudo=True)
    run_cmd(client, f"chown {HODARI_USER}:{HODARI_USER} {REMOTE_BACKEND_DIR}/.env", sudo=True)

    ok("Production .env written")


def setup_python_and_deps(client, password):
    """Setup Python venv and install dependencies."""
    VENV = f"{REMOTE_DEPLOY_DIR}/venv"

    step("Setting up Python environment...")

    # Create venv if not exists
    out, _, _ = run_cmd(client, f"ls {VENV}/bin/python 2>/dev/null && echo EXISTS || echo MISSING")
    if "EXISTS" not in out:
        run_cmd(client, f"python3 -m venv {VENV}", sudo=True, timeout=60)
        run_cmd(client, f"chown -R {HODARI_USER}:{HODARI_USER} {VENV}", sudo=True)
        ok("Virtual environment created")
    else:
        ok("Virtual environment exists")

    # Upgrade pip
    run_cmd(client, f"{VENV}/bin/pip install --upgrade pip setuptools wheel -q", timeout=60)

    # Install requirements
    step("Installing Python dependencies (this may take a few minutes)...")
    out, err, code = run_cmd(client, f"cd {REMOTE_BACKEND_DIR} && {VENV}/bin/pip install -r requirements.txt -q 2>&1", timeout=300)
    if code == 0:
        ok("Dependencies installed")
    else:
        warn(f"Some dependencies may have failed: {err[:300]}")

    # Install gunicorn and extras
    run_cmd(client, f"{VENV}/bin/pip install -q gunicorn psycopg2-binary django-celery-beat", timeout=120)
    ok("Additional packages installed")


def setup_django(client, password):
    """Run migrations and collect static files."""
    VENV = f"{REMOTE_DEPLOY_DIR}/venv"

    step("Running database migrations...")
    out, err, code = run_cmd(client, f"cd {REMOTE_BACKEND_DIR} && {VENV}/bin/python manage.py migrate --no-input 2>&1", timeout=180)
    for line in out.split("\n")[-5:]:
        if line.strip():
            print(f"    {line.strip()}")
    if code == 0:
        ok("Migrations applied")
    else:
        warn(f"Migration issues: {err[:200]}")

    step("Collecting static files...")
    out, err, code = run_cmd(client, f"cd {REMOTE_BACKEND_DIR} && {VENV}/bin/python manage.py collectstatic --no-input -q 2>&1", timeout=120)
    ok("Static files collected")

    step("Running Django system checks...")
    out, err, code = run_cmd(client, f"cd {REMOTE_BACKEND_DIR} && {VENV}/bin/python manage.py check --deploy 2>&1", timeout=30)
    if "ERROR" not in out.upper():
        ok("Django checks passed")
    else:
        warn(f"Some deployment checks flagged issues (check output)")


def setup_services(client, password):
    """Configure nginx, systemd, and start services."""
    step("Configuring Nginx...")

    # Copy nginx config
    run_cmd(client, f"cp {REMOTE_BACKEND_DIR}/deployment/nginx_hodari.conf /etc/nginx/sites-available/hodari", sudo=True)
    run_cmd(client, f"ln -sf /etc/nginx/sites-available/hodari /etc/nginx/sites-enabled/hodari", sudo=True)
    run_cmd(client, f"rm -f /etc/nginx/sites-enabled/default", sudo=True)

    # Test and reload
    out, err, code = run_cmd(client, "nginx -t 2>&1", sudo=True)
    if code == 0:
        run_cmd(client, "systemctl reload nginx", sudo=True)
        ok("Nginx configured")
    else:
        warn(f"Nginx config issue: {err[:200]}")

    step("Configuring systemd services...")
    run_cmd(client, f"cp {REMOTE_BACKEND_DIR}/deployment/hodari.service /etc/systemd/system/", sudo=True)
    run_cmd(client, f"cp {REMOTE_BACKEND_DIR}/deployment/hodari-celery.service /etc/systemd/system/", sudo=True)
    run_cmd(client, f"systemctl daemon-reload", sudo=True)
    run_cmd(client, "systemctl enable hodari", sudo=True)
    run_cmd(client, "systemctl enable hodari-celery", sudo=True)
    ok("Systemd services configured")

    step("Starting services...")
    run_cmd(client, "systemctl restart hodari", sudo=True)
    time.sleep(3)
    run_cmd(client, "systemctl restart hodari-celery", sudo=True)

    # Check status
    out, _, _ = run_cmd(client, "systemctl is-active hodari", sudo=True)
    if "active" in out:
        ok("HODARI service is RUNNING")
    else:
        warn(f"HODARI service status: {out}")
        out, _, _ = run_cmd(client, "journalctl -u hodari -n 20 --no-pager", sudo=True)
        print(f"    Logs:\n{out[-1000:]}")

    out, _, _ = run_cmd(client, "systemctl is-active hodari-celery", sudo=True)
    if "active" in out:
        ok("Celery worker is RUNNING")
    else:
        warn(f"Celery status: {out}")


def verify_deployment(client, password):
    """Verify the deployment is working."""
    step("Verifying deployment...")

    # Test HTTP
    out, _, code = run_cmd(client, "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8070/ 2>/dev/null || echo 000", timeout=10)
    if out and out != "000":
        ok(f"HTTP response on port 8070: {out}")
    else:
        warn("No HTTP response on port 8070")

    # Test through nginx
    out, _, code = run_cmd(client, f"curl -s -o /dev/null -w '%{http_code}' -H 'Host: {DOMAIN}' http://127.0.0.1:8070/ 2>/dev/null || echo 000", timeout=10)
    if out and out != "000":
        ok(f"Nginx proxy response: {out}")
    else:
        warn("Nginx proxy not responding")

    # Check all services
    step("Service Status:")
    for svc in ["hodari", "hodari-celery", "postgresql", "redis-server", "nginx"]:
        status, _, _ = run_cmd(client, f"systemctl is-active {svc}", sudo=True)
        if "active" in status:
            ok(f"{svc}: {C.GREEN}RUNNING{C.END}")
        else:
            warn(f"{svc}: {status}")

    # Disk and memory
    out, _, _ = run_cmd(client, "df -h / | tail -1 | awk '{print $5}'")
    ok(f"Disk usage: {out}")

    out, _, _ = run_cmd(client, "free -m | awk '/Mem:/{print $7}'")
    ok(f"Available memory: {out}MB")


def main():
    parser = argparse.ArgumentParser(description="Deploy HODARI to VPS")
    parser.add_argument("--upload-only", action="store_true", help="Only upload files")
    parser.add_argument("--setup-only", action="store_true", help="Only setup (skip upload)")
    parser.add_argument("--host", default=VPS_HOST, help="VPS host")
    parser.add_argument("--user", default=VPS_USER, help="SSH user")
    parser.add_argument("--password", default=None, help="SSH password (use --password 'pass' or VPS_PASSWORD env var)")
    args = parser.parse_args()

    banner()

    # Get password from args, env var, or interactive prompt
    password = args.password or os.environ.get("VPS_PASSWORD")
    if not password:
        password = getpass.getpass(f"Password for {args.user}@{args.host}: ")

    # Connect
    client = connect_vps(password)

    try:
        if not args.setup_only:
            # Create and upload tarball
            tarball = os.path.join(LOCAL_PROJECT_DIR, "hodari_deploy.tar.gz")
            create_tarball(LOCAL_PROJECT_DIR, tarball)
            upload_tarball(client, tarball, password)
            extract_and_setup(client, password)

            # Cleanup local tarball
            if os.path.exists(tarball):
                os.remove(tarball)

        if not args.upload_only:
            # Setup
            setup_env(client, password)
            setup_python_and_deps(client, password)
            setup_django(client, password)
            setup_services(client, password)
            verify_deployment(client, password)

        # Final summary
        print(f"""
{C.BLUE}{'='*70}
{C.GREEN}  DEPLOYMENT COMPLETE!{C.BLUE}
{'='*70}

  {C.CYAN}Access:{C.END} https://{DOMAIN}
  {C.CYAN}Port:{C.END}   8070 (HTTP) / 8443 (HTTPS after SSL)
  {C.CYAN}Path:{C.END}   {REMOTE_BACKEND_DIR}
  {C.CYAN}User:{C.END}   {HODARI_USER}

  {C.YELLOW}Next steps:{C.END}
  1. Configure DNS: Point {DOMAIN} → 187.7.21.133
  2. Setup SSL: certbot --nginx -d {DOMAIN}
  3. Create superuser: cd {REMOTE_BACKEND_DIR} && {REMOTE_DEPLOY_DIR}/venv/bin/python manage.py createsuperuser
  4. Seed demo data: cd {REMOTE_BACKEND_DIR} && {REMOTE_DEPLOY_DIR}/venv/bin/python manage.py seed_demo_data

  {C.YELLOW}Useful commands:{C.END}
  sudo systemctl status hodari
  sudo journalctl -u hodari -f
  sudo systemctl restart hodari
  sudo systemctl restart hodari-celery

{C.BLUE}{'='*70}{C.END}
""")

    except Exception as e:
        fail(f"Deployment failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        client.close()
        print(f"\n  {C.GREEN}Connection closed{C.END}\n")


if __name__ == "__main__":
    main()
