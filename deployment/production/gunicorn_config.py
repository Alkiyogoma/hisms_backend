"""
Gunicorn configuration for PRODUCTION (connect.hodari.ac.tz).

Mirrors the config the live server has been running since it was deployed by
hand, so switching production to git changes nothing about how it is served:
nginx proxies to 127.0.0.1:8007, and user/logging are handled by systemd.

The hodari service is pointed here by the systemd override
/etc/systemd/system/hodari.service.d/production-gunicorn.conf (see
PRODUCTION_DEPLOY.md, step 3.7). The top-level deployment/gunicorn_config.py is
NOT used by production.
"""

bind = "127.0.0.1:8007"
workers = 3
worker_class = "sync"
timeout = 120
max_requests = 1000
max_requests_jitter = 50
proc_name = "hodari"
preload_app = True
