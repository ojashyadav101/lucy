#!/bin/bash
# deploy/setup-vps.sh
# ─────────────────────────────────────────────────────────────────────────────
# One-shot setup script for Lucy on the Contabo VPS.
# Run this ONCE on the VPS to set up everything from scratch.
#
# Usage (from your laptop):
#   ssh -i ~/.ssh/id_lucy_vps root@167.86.82.46 'bash -s' < deploy/setup-vps.sh
#
# What it does:
#   1. Creates the bare git repo at /opt/lucy/repo.git (push target)
#   2. Clones working copy to /opt/lucy/app
#   3. Creates Python venv and installs Lucy
#   4. Installs the systemd service
#   5. Installs the post-receive deploy hook
#   6. Enables and starts Lucy
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

GITHUB_REPO="https://github.com/ojashyadav101/lucy.git"
DEPLOY_BASE="/opt/lucy"
BARE_REPO="$DEPLOY_BASE/repo.git"
APP_DIR="$DEPLOY_BASE/app"
VENV="$APP_DIR/.venv"
SERVICE_FILE="/etc/systemd/system/lucy.service"

log() { echo -e "\033[1;32m[setup]\033[0m $*"; }
warn() { echo -e "\033[1;33m[warn]\033[0m $*"; }
err() { echo -e "\033[1;31m[error]\033[0m $*" >&2; }

# ── 1. System dependencies ────────────────────────────────────────────────
log "Checking system dependencies..."
apt-get update -qq
apt-get install -y -qq python3.12 python3.12-venv python3-pip git curl

# ── 2. Create directory structure ────────────────────────────────────────
log "Creating $DEPLOY_BASE structure..."
mkdir -p "$DEPLOY_BASE"
mkdir -p /var/log/lucy

# ── 3. Set up bare git repo (push target) ────────────────────────────────
if [ ! -d "$BARE_REPO" ]; then
    log "Initialising bare repo at $BARE_REPO..."
    git init --bare "$BARE_REPO"
else
    log "Bare repo already exists at $BARE_REPO"
fi

# ── 4. Clone working copy from GitHub ────────────────────────────────────
if [ ! -d "$APP_DIR/.git" ]; then
    log "Cloning Lucy from GitHub to $APP_DIR..."
    git clone "$GITHUB_REPO" "$APP_DIR"
else
    log "Working copy already exists at $APP_DIR — pulling latest..."
    cd "$APP_DIR" && git pull origin main
fi

# Wire the bare repo to the working copy so post-receive can push to it
cd "$APP_DIR"
git remote set-url origin "$GITHUB_REPO" 2>/dev/null || git remote add origin "$GITHUB_REPO"

# ── 5. Python venv ───────────────────────────────────────────────────────
if [ ! -d "$VENV" ]; then
    log "Creating Python venv at $VENV..."
    python3.12 -m venv "$VENV"
fi

log "Installing Lucy Python dependencies..."
"$VENV/bin/pip" install --upgrade pip -q
"$VENV/bin/pip" install -e "$APP_DIR" -q

# ── 6. .env file ─────────────────────────────────────────────────────────
if [ ! -f "$APP_DIR/.env" ]; then
    warn ".env NOT found at $APP_DIR/.env"
    warn "You MUST copy your .env before starting Lucy:"
    warn "  scp -i ~/.ssh/id_lucy_vps .env root@167.86.82.46:$APP_DIR/.env"
else
    log ".env found — patching DATABASE_URL for VPS..."
    # On VPS, postgres runs in Docker on localhost:5432
    sed -i 's|LUCY_DATABASE_URL=.*|LUCY_DATABASE_URL=postgresql+asyncpg://lucy:lucy@localhost:5432/lucy|' "$APP_DIR/.env"
    # OpenClaw is local on this same VPS
    sed -i 's|LUCY_OPENCLAW_BASE_URL=.*|LUCY_OPENCLAW_BASE_URL=http://127.0.0.1:18789|' "$APP_DIR/.env"
    # Set ENV to production
    sed -i 's|LUCY_ENV=.*|LUCY_ENV=production|' "$APP_DIR/.env"
fi

# ── 7. Database migrations ───────────────────────────────────────────────
if [ -f "$APP_DIR/.env" ]; then
    log "Running database migrations..."
    cd "$APP_DIR"
    "$VENV/bin/python" scripts/init_db.py --migrate 2>&1 || warn "Migrations had issues — check manually"
fi

# ── 8. Install post-receive hook ────────────────────────────────────────
log "Installing post-receive deploy hook..."
cp "$APP_DIR/deploy/post-receive" "$BARE_REPO/hooks/post-receive"
chmod +x "$BARE_REPO/hooks/post-receive"

# ── 9. Install systemd service ──────────────────────────────────────────
log "Installing lucy.service..."
cp "$APP_DIR/deploy/lucy.service" "$SERVICE_FILE"
systemctl daemon-reload
systemctl enable lucy

# ── 10. Start Lucy ───────────────────────────────────────────────────────
if [ -f "$APP_DIR/.env" ]; then
    log "Starting Lucy..."
    systemctl restart lucy
    sleep 3
    if systemctl is-active --quiet lucy; then
        log "✓ Lucy is running!"
        systemctl status lucy --no-pager | head -20
    else
        err "Lucy failed to start. Check logs:"
        journalctl -u lucy -n 40 --no-pager
    fi
else
    warn "Skipping start — .env not present yet."
    warn "After copying .env: systemctl start lucy"
fi

log ""
log "════════════════════════════════════════════════"
log "Setup complete! Key info:"
log "  App dir:      $APP_DIR"
log "  Bare repo:    $BARE_REPO"
log "  Service:      systemctl status lucy"
log "  Live logs:    journalctl -u lucy -f"
log "  Deploy hook:  $BARE_REPO/hooks/post-receive"
log ""
log "From your laptop, add Contabo as a git remote:"
log "  git remote add contabo ssh://root@167.86.82.46$BARE_REPO"
log "  git push contabo main"
log "════════════════════════════════════════════════"
