# HODARI Production Deployment Guide

## Overview
- **VPS:** 187.7.21.133 (root)
- **Domain:** connect.hodari.ac.tz
- **Port:** 8070 (HTTP) / 8443 (HTTPS)
- **Database:** PostgreSQL (`hisms_prod`)
- **App Server:** Gunicorn (port 8008 internal → Nginx 8070/8443)
- **Cache/Queue:** Redis + Celery

---

## Quick Start (3 steps)

### Step 1: First-time VPS Setup
SSH into the VPS and run the setup script:
```bash
ssh root@187.7.21.133
# Enter password: Hodari@2026@

# Download and run setup
cd /opt 2>/dev/null || cd /root
# Upload setup_vps.sh first, then:
chmod +x /var/www/hodari/hisms_backend/deployment/setup_vps.sh
/var/www/hodari/hisms_backend/deployment/setup_vps.sh
```

### Step 2: Deploy from Windows
On your Windows machine (from the project directory):
```bash
cd C:\Users\Elizabeth Lusiani\Desktop\HODARI\hisms_backend
python deployment/deploy_hodari.py
# Enter VPS password when prompted
```

### Step 3: Configure SSL & DNS
```bash
ssh root@187.7.21.133

# Point domain DNS first, then:
certbot --nginx -d connect.hodari.ac.tz --non-interactive --agree-tos --email admin@connect.hodari.ac.tz

# Update nginx for HTTPS (after cert is issued)
# The setup script handles this automatically
```

---

## Detailed Deployment Steps

### 1. DNS Configuration
Point your domain to the VPS IP:
```
Type: A Record
Name: connect.hodari.ac.tz
Value: 187.7.21.133
TTL: 300
```

### 2. VPS Initial Setup
The setup script installs:
- Python 3.12+ with venv
- PostgreSQL 15+
- Redis 7+
- Nginx
- Let's Encrypt (certbot)
- Firewall (UFW)

### 3. Database Configuration
Database is created automatically:
- **Database:** hisms_prod
- **User:** hodari_user
- **Password:** H0d@r1_Pr0d_2026! (change in production!)

### 4. Environment Variables
Production `.env` is created at `/var/www/hodari/hisms_backend/.env`
Key settings:
- `DJANGO_DEBUG=0` (production mode)
- `DJANGO_ALLOWED_HOSTS=connect.hodari.ac.tz,...`
- `DJANGO_USE_SQLITE=0` (using PostgreSQL)

### 5. Services
Three systemd services run:
- **hodari** - Gunicorn (Django app on port 8008)
- **hodari-celery** - Celery worker (background tasks)
- **hodari-celery-beat** - Celery beat (scheduled tasks)

### 6. SSL/HTTPS
After DNS is configured:
```bash
certbot --nginx -d connect.hodari.ac.tz
```
Auto-renewal is configured via systemd timer.

---

## Port Configuration (No Interference)

The HODARI deployment uses:
- **Port 8070** - HTTP (Nginx) → Gunicorn on 127.0.0.1:8008
- **Port 8443** - HTTPS (Nginx) → Gunicorn on 127.0.0.1:8008

Other Django projects on the server remain unaffected because:
1. Each project binds to a different port
2. Nginx uses separate server blocks
3. Gunicorn binds to different localhost ports

---

## Performance Optimizations Applied

### Nginx
- Gzip compression enabled
- Static file caching (30 days)
- Rate limiting on login/admin
- WebSocket support for real-time features
- Security headers (HSTS, CSP, X-Frame-Options)

### Gunicorn
- Worker count = (CPU cores × 2) + 1 (max 9)
- Thread-based workers for better performance
- Keep-alive connections
- Request limiting for security

### PostgreSQL
- Shared buffers: 256MB
- Effective cache: 1GB
- Work memory: 16MB
- Max connections: 200

### Redis
- Max memory: 256MB
- LRU eviction policy
- Used for: Celery broker, Channels layer, session cache

### Django
- Static files served by Nginx (not Django)
- Session timeout: 30 minutes
- CSRF protection enabled
- Security headers enabled

---

## Monitoring & Maintenance

### View Logs
```bash
# Application logs
sudo journalctl -u hodari -f

# Celery worker logs
sudo journalctl -u hodari-celery -f

# Nginx access logs
tail -f /var/log/nginx/access.log

# Nginx error logs
tail -f /var/log/nginx/error.log
```

### Restart Services
```bash
sudo systemctl restart hodari          # Django app
sudo systemctl restart hodari-celery   # Celery worker
sudo systemctl restart nginx           # Web server
```

### Database Backup
```bash
# Manual backup
cd /var/www/hodari/hisms_backend
/var/www/hodari/venv/bin/python manage.py dumpdata --natural-foreign --natural-primary > /var/backups/hodari/backup_$(date +%Y%m%d).json

# Restore
/var/www/hodari/venv/bin/python manage.py loaddata /var/backups/hodari/backup_20260611.json
```

### Check Service Status
```bash
sudo systemctl status hodari
sudo systemctl status hodari-celery
sudo systemctl status nginx
sudo systemctl status postgresql
sudo systemctl status redis-server
```

---

## Troubleshooting

### App not responding on port 8070
```bash
# Check if gunicorn is running
ps aux | grep gunicorn

# Check nginx config
sudo nginx -t

# Check firewall
sudo ufw status

# Check if port is in use
sudo ss -tlnp | grep 8070
```

### Database connection errors
```bash
# Check PostgreSQL
sudo systemctl status postgresql

# Test connection
psql -h localhost -U hodari_user -d hisms_prod

# Check .env settings
cat /var/www/hodari/hisms_backend/.env
```

### SSL certificate issues
```bash
# Check certificate status
sudo certbot certificates

# Force renewal
sudo certbot renew --force-renewal

# Check nginx SSL config
grep -A 20 "ssl" /etc/nginx/sites-available/hodari
```

### Memory issues
```bash
# Check memory usage
free -h

# Check Redis memory
redis-cli info memory

# Restart services if needed
sudo systemctl restart hodari hodari-celery
```

---

## Security Checklist

- [x] Django DEBUG=0 (production mode)
- [x] HTTPS with Let's Encrypt
- [x] HSTS enabled (1 year)
- [x] Security headers (CSP, X-Frame-Options, etc.)
- [x] Rate limiting on login endpoints
- [x] Session timeout (30 min)
- [x] CSRF protection
- [x] Database user with limited privileges
- [x] Redis bound to localhost only
- [x] Firewall (UFW) configured
- [x] Gunicorn not exposed directly (Nginx proxy)
- [x] Backup automation configured

---

## File Structure on VPS

```
/var/www/hodari/
├── hisms_backend/          # Django project
│   ├── config/             # Settings, URLs, WSGI/ASGI
│   ├── academics/          # Academic module
│   ├── admissions/         # Admissions module
│   ├── attendance/         # Attendance module
│   ├── finance/            # Finance module
│   ├── hr/                 # HR module
│   ├── students/           # Students module
│   ├── timetable/          # Timetable module
│   ├── communications/     # Communications module
│   ├── welfare/            # Welfare module
│   ├── events/             # Events module
│   ├── tasks/              # Tasks module
│   ├── audit/              # Audit module
│   ├── core/               # Core module
│   ├── reports/            # Reports module
│   ├── users/              # Users module
│   ├── discipline/         # Discipline module
│   ├── templates/          # HTML templates
│   ├── static/             # Static files (CSS, JS, images)
│   ├── media/              # Uploaded files (student photos, etc.)
│   ├── staticfiles/        # Collected static files
│   ├── deployment/         # Deployment configs
│   ├── .env                # Environment variables
│   ├── manage.py           # Django management
│   └── requirements.txt    # Python dependencies
├── venv/                   # Python virtual environment
└── deploy.tar.gz           # Temporary upload file (auto-cleaned)
```

---

## Support

For issues, check:
1. Service logs: `sudo journalctl -u hodari -n 50`
2. Nginx logs: `/var/log/nginx/error.log`
3. Django checks: `cd /var/www/hodari/hisms_backend && /var/www/hodari/venv/bin/python manage.py check`
4. Database: `cd /var/www/hodari/hisms_backend && /var/www/hodari/venv/bin/python manage.py dbshell`
