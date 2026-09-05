#!/bin/bash
# =============================================================================
# HODARI Production VPS Setup Script
# Run this ONCE on the VPS (187.7.21.133) as root
# Domain: connect.hodari.ac.tz | Port: 8070
# =============================================================================
set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

DEPLOY_DIR="/opt/hodari"
BACKEND_DIR="${DEPLOY_DIR}/hisms_backend"
VENV_DIR="${DEPLOY_DIR}/venv"
DOMAIN="connect.hodari.ac.tz"
HODARI_USER="hodari"
DB_NAME="hisms_prod"
DB_USER="hodari_user"
DB_PASS="H0d@r1_Pr0d_2026!"

print_banner() {
    echo -e "${BLUE}"
    echo "========================================================================"
    echo "  HODARI Production VPS Setup"
    echo "  Domain: ${DOMAIN} | Port: 8070"
    echo "========================================================================"
    echo -e "${NC}"
}

step() {
    echo -e "\n${CYAN}[$(date +%H:%M:%S)]${NC} ${YELLOW}▶ $1${NC}"
}

ok() {
    echo -e "  ${GREEN}✓${NC} $1"
}

warn() {
    echo -e "  ${YELLOW}⚠${NC} $1"
}

fail() {
    echo -e "  ${RED}✗${NC} $1"
}

# =============================================================================
# STEP 1: System Updates & Dependencies
# =============================================================================
setup_system() {
    step "Updating system packages..."
    apt-get update -qq
    apt-get upgrade -y -qq

    step "Installing system dependencies..."
    apt-get install -y -qq \
        curl wget git unzip \
        python3 python3-pip python3-venv python3-dev \
        libpq-dev gcc \
        nginx certbot python3-certbot-nginx \
        supervisor \
        logrotate \
        ufw \
        htop

    ok "System packages installed"
}

# =============================================================================
# STEP 2: Install PostgreSQL
# =============================================================================
setup_postgresql() {
    step "Setting up PostgreSQL..."
    apt-get install -y -qq postgresql postgresql-contrib

    systemctl enable postgresql
    systemctl start postgresql

    # Create database and user
    sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='${DB_USER}'" | grep -q 1 || \
        sudo -u postgres psql -c "CREATE USER ${DB_USER} WITH PASSWORD '${DB_PASS}';"

    sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1 || \
        sudo -u postgres psql -c "CREATE DATABASE ${DB_NAME} OWNER ${DB_USER};"

    sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};"
    sudo -u postgres psql -d ${DB_NAME} -c "GRANT ALL ON SCHEMA public TO ${DB_USER};"

    # Tune PostgreSQL for production
    PG_CONF="/etc/postgresql/$(ls /etc/postgresql/ | sort -V | tail -1)/main/postgresql.conf"
    if [ -f "$PG_CONF" ]; then
        sed -i "s/#shared_buffers = 128MB/shared_buffers = 256MB/" "$PG_CONF"
        sed -i "s/#effective_cache_size = 4GB/effective_cache_size = 1GB/" "$PG_CONF"
        sed -i "s/#work_mem = 4MB/work_mem = 16MB/" "$PG_CONF"
        sed -i "s/#maintenance_work_mem = 64MB/maintenance_work_mem = 128MB/" "$PG_CONF"
        sed -i "s/#max_connections = 100/max_connections = 200/" "$PG_CONF"
        systemctl restart postgresql
    fi

    ok "PostgreSQL configured"
}

# =============================================================================
# STEP 3: Install & Configure Redis
# =============================================================================
setup_redis() {
    step "Setting up Redis..."
    apt-get install -y -qq redis-server

    systemctl enable redis-server
    systemctl start redis-server

    # Bind to localhost only
    sed -i 's/^bind .*/bind 127.0.0.1/' /etc/redis/redis.conf
    sed -i 's/^# maxmemory .*/maxmemory 256mb/' /etc/redis/redis.conf
    sed -i 's/^# maxmemory-policy .*/maxmemory-policy allkeys-lru/' /etc/redis/redis.conf

    systemctl restart redis-server

    # Verify
    if redis-cli ping | grep -q PONG; then
        ok "Redis is running"
    else
        warn "Redis may not be running correctly"
    fi
}

# =============================================================================
# STEP 4: Create HODARI User & Directory Structure
# =============================================================================
setup_user_and_dirs() {
    step "Creating hodari user and directories..."

    # Create user if not exists
    if ! id "$HODARI_USER" &>/dev/null; then
        useradd -m -s /bin/bash "$HODARI_USER"
        ok "Created user: ${HODARI_USER}"
    else
        ok "User ${HODARI_USER} already exists"
    fi

    # Create directories
    mkdir -p "$DEPLOY_DIR"
    mkdir -p "$BACKEND_DIR"
    mkdir -p /var/log/hodari
    mkdir -p /var/backups/hodari
    mkdir -p /var/run/hodari
    mkdir -p /var/www/certbot

    chown -R "$HODARI_USER:$HODARI_USER" "$DEPLOY_DIR"
    chown -R "$HODARI_USER:$HODARI_USER" /var/log/hodari
    chown -R "$HODARI_USER:$HODARI_USER" /var/backups/hodari
    chown -R "$HODARI_USER:$HODARI_USER" /var/run/hodari

    ok "Directories created"
}

# =============================================================================
# STEP 5: Setup Python Virtual Environment
# =============================================================================
setup_python() {
    step "Setting up Python virtual environment..."

    if [ ! -d "$VENV_DIR" ]; then
        sudo -u "$HODARI_USER" python3 -m venv "$VENV_DIR"
        ok "Virtual environment created"
    else
        ok "Virtual environment already exists"
    fi

    # Upgrade pip
    sudo -u "$HODARI_USER" "$VENV_DIR/bin/pip" install --upgrade pip setuptools wheel -q
    ok "pip upgraded"
}

# =============================================================================
# STEP 6: Install Python Dependencies
# =============================================================================
install_deps() {
    step "Installing Python dependencies..."
    sudo -u "$HODARI_USER" "$VENV_DIR/bin/pip" install -q -r "${BACKEND_DIR}/requirements.txt"
    sudo -u "$HODARI_USER" "$VENV_DIR/bin/pip" install -q gunicorn
    sudo -u "$HODARI_USER" "$VENV_DIR/bin/pip" install -q psycopg2-binary
    sudo -u "$HODARI_USER" "$VENV_DIR/bin/pip" install -q django-celery-beat
    sudo -u "$HODARI_USER" "$VENV_DIR/bin/pip" install -q weasyprint || warn "WeasyPrint install failed (optional)"

    ok "Dependencies installed"
}

# =============================================================================
# STEP 7: Configure Environment
# =============================================================================
setup_env() {
    step "Configuring environment..."

    # Create .env if not exists
    ENV_FILE="${BACKEND_DIR}/.env"
    if [ ! -f "$ENV_FILE" ]; then
        cat > "$ENV_FILE" << 'ENVEOF'
DJANGO_SECRET_KEY=hodari-pr0d-s3cr3t-k3y-2026-elimcore!
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=connect.hodari.ac.tz,hodari.elimcoregroup.com,187.7.21.133,localhost,127.0.0.1
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
CORS_ALLOWED_ORIGINS=https://connect.hodari.ac.tz,https://hodari.elimcoregroup.com
EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend
DEFAULT_FROM_EMAIL=noreply@connect.hodari.ac.tz
SESSION_IDLE_TIMEOUT=1800
LOGIN_MAX_ATTEMPTS=5
LOGIN_LOCKOUT_DURATION=900
BACKUP_DIR=/var/backups/hodari
BACKUP_RETENTION_DAYS=30
ENVEOF
    fi

    chmod 600 "$ENV_FILE"
    chown "$HODARI_USER:$HODARI_USER" "$ENV_FILE"

    ok "Environment configured"
}

# =============================================================================
# STEP 8: Run Django Migrations & Collect Static
# =============================================================================
setup_django() {
    step "Running Django migrations..."
    cd "$BACKEND_DIR"
    sudo -u "$HODARI_USER" "$VENV_DIR/bin/python" manage.py migrate --no-input 2>&1 || warn "Some migrations may have failed"

    step "Collecting static files..."
    sudo -u "$HODARI_USER" "$VENV_DIR/bin/python" manage.py collectstatic --no-input -q 2>&1 || warn "Static collection had issues"

    step "Creating log directory for Django..."
    mkdir -p /var/log/hodari
    touch /var/log/hodari/django.log
    chown "$HODARI_USER:$HODARI_USER" /var/log/hodari/django.log

    ok "Django setup complete"
}

# =============================================================================
# STEP 9: Configure Nginx
# =============================================================================
setup_nginx() {
    step "Configuring Nginx..."

    # Copy nginx config
    cp "${BACKEND_DIR}/deployment/nginx_hodari.conf" /etc/nginx/sites-available/hodari

    # Enable site
    ln -sf /etc/nginx/sites-available/hodari /etc/nginx/sites-enabled/hodari

    # Remove default site if it conflicts on port 8070
    rm -f /etc/nginx/sites-enabled/default

    # Test nginx config
    if nginx -t 2>&1; then
        systemctl reload nginx
        ok "Nginx configured and reloaded"
    else
        warn "Nginx config test failed - check /etc/nginx/sites-available/hodari"
    fi
}

# =============================================================================
# STEP 10: Configure Systemd Services
# =============================================================================
setup_services() {
    step "Configuring systemd services..."

    # Copy service files
    cp "${BACKEND_DIR}/deployment/hodari.service" /etc/systemd/system/
    cp "${BACKEND_DIR}/deployment/hodari-celery.service" /etc/systemd/system/
    cp "${BACKEND_DIR}/deployment/hodari-celery-beat.service" /etc/systemd/system/

    # Reload systemd
    systemctl daemon-reload

    # Enable services
    systemctl enable hodari
    systemctl enable hodari-celery
    systemctl enable hodari-celery-beat

    ok "Systemd services configured"
}

# =============================================================================
# STEP 11: Configure Firewall
# =============================================================================
setup_firewall() {
    step "Configuring firewall..."

    # Allow SSH
    ufw allow 22/tcp
    # Allow HTTP and HTTPS
    ufw allow 80/tcp
    ufw allow 443/tcp
    # Allow port 8070 (our app)
    ufw allow 8070/tcp
    # Allow port 8443 (SSL version if needed)
    ufw allow 8443/tcp

    # Enable UFW
    echo "y" | ufw enable

    ok "Firewall configured"
}

# =============================================================================
# STEP 12: Setup SSL with Let's Encrypt
# =============================================================================
setup_ssl() {
    step "Setting up SSL certificate..."

    # Only run if domain resolves to this server
    if curl -s --max-time 5 "http://${DOMAIN}/.well-known/acme-challenge/test" > /dev/null 2>&1 || \
       [ "$(dig +short ${DOMAIN} 2>/dev/null)" = "$(curl -s ifconfig.me 2>/dev/null)" ]; then
        certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --email "admin@connect.hodari.ac.tz" || warn "SSL setup failed - run certbot manually"
    else
        warn "Domain ${DOMAIN} may not resolve to this server yet."
        warn "Run after DNS is configured: certbot --nginx -d ${DOMAIN}"
    fi

    # Setup auto-renewal
    systemctl enable certbot.timer
    systemctl start certbot.timer

    ok "SSL setup attempted"
}

# =============================================================================
# STEP 13: Setup Log Rotation
# =============================================================================
setup_logging() {
    step "Setting up log rotation..."

    cat > /etc/logrotate.d/hodari << 'EOF'
/var/log/hodari/*.log {
    daily
    missingok
    rotate 30
    compress
    delaycompress
    notifempty
    create 0644 hodari hodari
    sharedscripts
    postrotate
        systemctl reload hodari > /dev/null 2>&1 || true
    endscript
}
EOF

    ok "Log rotation configured"
}

# =============================================================================
# STEP 14: Setup Backup Cron
# =============================================================================
setup_backups() {
    step "Setting up automated backups..."

    cat > /etc/cron.d/hodari-backup << 'EOF'
# Backup PostgreSQL database daily at 2 AM
0 2 * * * hodari /opt/hodari/venv/bin/python /opt/hodari/hisms_backend/manage.py dumpdata --natural-foreign --natural-primary -o /var/backups/hodari/db_backup_$(date +\%Y\%m\%d).json 2>/dev/null

# Cleanup old backups
0 3 * * * find /var/backups/hodari -name "*.json" -mtime +30 -delete 2>/dev/null
EOF

    ok "Backups configured"
}

# =============================================================================
# STEP 15: Start Services
# =============================================================================
start_services() {
    step "Starting all services..."

    systemctl start hodari
    sleep 2
    systemctl start hodari-celery
    systemctl start hodari-celery-beat

    # Verify
    echo ""
    step "Service Status:"
    for svc in hodari hodari-celery hodari-celery-beat postgresql redis-server nginx; do
        STATUS=$(systemctl is-active $svc 2>/dev/null || echo "unknown")
        if [ "$STATUS" = "active" ]; then
            ok "$svc: ${GREEN}RUNNING${NC}"
        else
            warn "$svc: ${YELLOW}${STATUS}${NC}"
        fi
    done
}

# =============================================================================
# STEP 16: Verify Deployment
# =============================================================================
verify() {
    step "Verifying deployment..."

    # Test HTTP
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8070/ 2>/dev/null || echo "000")
    if [ "$HTTP_CODE" != "000" ]; then
        ok "HTTP response: ${HTTP_CODE}"
    else
        warn "No HTTP response on port 8070"
    fi

    # Test HTTPS (if SSL is set up)
    HTTPS_CODE=$(curl -s -o /dev/null -w "%{http_code}" https://${DOMAIN}/ 2>/dev/null || echo "000")
    if [ "$HTTPS_CODE" != "000" ]; then
        ok "HTTPS response: ${HTTPS_CODE}"
    else
        warn "No HTTPS response (SSL may not be configured yet)"
    fi

    # Check disk space
    DISK_USAGE=$(df -h / | awk 'NR==2{print $5}' | tr -d '%')
    if [ "$DISK_USAGE" -lt 80 ]; then
        ok "Disk usage: ${DISK_USAGE}%"
    else
        warn "Disk usage is high: ${DISK_USAGE}%"
    fi

    # Check memory
    MEM_FREE=$(free -m | awk '/Mem:/{print $7}')
    ok "Available memory: ${MEM_FREE}MB"
}

# =============================================================================
# MAIN
# =============================================================================
main() {
    print_banner

    # Check if running as root
    if [ "$EUID" -ne 0 ]; then
        fail "Please run as root (sudo ./setup_vps.sh)"
        exit 1
    fi

    setup_system
    setup_postgresql
    setup_redis
    setup_user_and_dirs
    setup_python

    # Note: install_deps requires the backend code to be uploaded first
    if [ -f "${BACKEND_DIR}/requirements.txt" ]; then
        install_deps
    else
        warn "Backend code not found at ${BACKEND_DIR}. Run the upload script first."
        warn "Then re-run this script to install dependencies."
    fi

    setup_env

    if [ -f "${BACKEND_DIR}/manage.py" ]; then
        setup_django
    fi

    setup_nginx
    setup_services
    setup_firewall
    setup_logging
    setup_backups

    if [ -f "${BACKEND_DIR}/manage.py" ]; then
        start_services
        verify
    fi

    echo ""
    echo -e "${BLUE}========================================================================${NC}"
    echo -e "${GREEN}  SETUP COMPLETE!${NC}"
    echo -e "${BLUE}========================================================================${NC}"
    echo ""
    echo -e "  ${CYAN}Domain:${NC} https://${DOMAIN}"
    echo -e "  ${CYAN}Port:${NC}   8070 (HTTP) / 8443 (HTTPS)"
    echo -e "  ${CYAN}Path:${NC}   ${BACKEND_DIR}"
    echo -e "  ${CYAN}User:${NC}   ${HODARI_USER}"
    echo ""
    echo -e "  ${YELLOW}Next steps:${NC}"
    echo "  1. Upload project files: python deploy_hodari.py"
    echo "  2. Configure DNS: Point connect.hodari.ac.tz → 187.7.21.133"
    echo "  3. Setup SSL: certbot --nginx -d connect.hodari.ac.tz"
    echo "  4. Create admin: cd ${BACKEND_DIR} && ${VENV_DIR}/bin/python manage.py createsuperuser"
    echo ""
    echo -e "  ${YELLOW}Useful commands:${NC}"
    echo "  sudo systemctl status hodari"
    echo "  sudo journalctl -u hodari -f"
    echo "  sudo systemctl restart hodari"
    echo ""
}

main "$@"
