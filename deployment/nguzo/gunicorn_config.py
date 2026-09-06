"""
Gunicorn config for the hodari.nguzo.co.tz site (SECOND site on the VPS).
Isolated from connect.hodari.ac.tz: its own port, so the two never collide.
Referenced by hodari-nguzo.service as --config path.
"""
import multiprocessing

# Distinct port from connect.hodari.ac.tz (which uses 8007). Nginx proxies here.
bind = "127.0.0.1:8009"

workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "sync"
timeout = 120
graceful_timeout = 30
keepalive = 32
max_requests = 1000
max_requests_jitter = 50
proc_name = "hodari-nguzo"
preload_app = True
