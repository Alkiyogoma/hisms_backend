"""
Gunicorn configuration for HODARI production server.
Referenced by hodari.service as --config path.
"""
import multiprocessing
import os

# Bind to localhost — Nginx proxies to this from port 8070
bind = "127.0.0.1:8008"

# Workers: 2-4 per CPU core is typical for a Django app
workers = multiprocessing.cpu_count() * 2 + 1

# Worker class — sync works well behind Nginx
worker_class = "sync"

# Timeouts
timeout = 120
graceful_timeout = 30
keepalive = 32

# Max requests before recycling a worker (reduces memory leaks)
max_requests = 1000
max_requests_jitter = 50

# Logging
accesslog = "/var/log/hodari/gunicorn_access.log"
errorlog = "/var/log/hodari/gunicorn_error.log"
loglevel = "warning"
access_log_format = '%({X-Forwarded-For}i)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s"'

# Process name
proc_name = "hodari"

# Daemon off — systemd manages the process
daemon = False

# Preload app for faster worker spawns
preload_app = True

# User/group
user = "hodari"
group = "hodari"

# Server hooks
def on_starting(server):
    """Ensure log directories exist."""
    for log_path in [accesslog, errorlog]:
        log_dir = os.path.dirname(log_path)
        try:
            os.makedirs(log_dir, exist_ok=True)
        except PermissionError:
            pass

def post_fork(server, worker):
    """Post-fork hook — logs worker start."""
    server.log.info(f"Worker spawned (pid: {worker.pid})")
