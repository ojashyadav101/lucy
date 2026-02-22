#!/bin/bash
# Deploy a second OpenClaw instance for Lucy on the existing Contabo VPS
# Run this on your VPS: 167.86.82.46

set -e

echo "=== Setting up OpenClaw for Lucy ==="

# Configuration
OPENCLAW_PORT=18790
WORK_DIR="/opt/openclaw-lucy"
SERVICE_NAME="openclaw-lucy"
LUCY_TOKEN="lucy-$(openssl rand -hex 16)"
LUCY_HOOKS_TOKEN="lucy-hooks-$(openssl rand -hex 16)"

# 1. Create working directory
echo "Creating working directory: $WORK_DIR"
sudo mkdir -p "$WORK_DIR"
sudo mkdir -p "$WORK_DIR/plugins"
sudo mkdir -p "$WORK_DIR/data"
sudo mkdir -p "$WORK_DIR/logs"

# 2. Clone OpenClaw (or copy from existing if you have it locally)
echo "Setting up OpenClaw..."
cd "$WORK_DIR"

# Option A: If you have OpenClaw as a tarball/binary
# sudo tar -xzf /path/to/openclaw.tar.gz -C "$WORK_DIR"

# Option B: Clone from GitHub (most common)
if [ ! -d "$WORK_DIR/openclaw" ]; then
    sudo git clone https://github.com/open-claw/openclaw.git "$WORK_DIR/openclaw" 2>/dev/null || echo "Note: Using existing OpenClaw setup method"
fi

# 3. Create config file
echo "Creating OpenClaw configuration..."
sudo tee "$WORK_DIR/config.yaml" > /dev/null <<EOF
# OpenClaw Gateway Configuration for Lucy
server:
  port: $OPENCLAW_PORT
  host: 0.0.0.0

auth:
  gateway_token: "$LUCY_TOKEN"
  hooks_token: "$LUCY_HOOKS_TOKEN"

plugins:
  directory: "$WORK_DIR/plugins"
  auto_load: true

storage:
  data_dir: "$WORK_DIR/data"
  
logging:
  level: info
  file: "$WORK_DIR/logs/openclaw.log"
  
features:
  sessions: true
  engram: true
  cron: true
  sub_agents: true
EOF

# 4. Create systemd service
echo "Creating systemd service..."
sudo tee "/etc/systemd/system/$SERVICE_NAME.service" > /dev/null <<EOF
[Unit]
Description=OpenClaw Gateway for Lucy (AI Agent)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$WORK_DIR
ExecStart=$WORK_DIR/openclaw/openclaw-gateway --config $WORK_DIR/config.yaml
Restart=always
RestartSec=10
StandardOutput=append:$WORK_DIR/logs/service.log
StandardError=append:$WORK_DIR/logs/error.log

[Install]
WantedBy=multi-user.target
EOF

# 5. Set permissions
sudo chmod 600 "$WORK_DIR/config.yaml"
sudo chown -R root:root "$WORK_DIR"

# 6. Start the service
echo "Starting OpenClaw service..."
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl start "$SERVICE_NAME"

# 7. Wait for startup and verify
sleep 3
if sudo systemctl is-active --quiet "$SERVICE_NAME"; then
    echo ""
    echo "=== OpenClaw for Lucy is RUNNING ==="
    echo "Gateway URL: http://167.86.82.46:$OPENCLAW_PORT"
    echo "Gateway Token: $LUCY_TOKEN"
    echo "Hooks Token: $LUCY_HOOKS_TOKEN"
    echo ""
    echo "Service status: sudo systemctl status $SERVICE_NAME"
    echo "Logs: sudo journalctl -u $SERVICE_NAME -f"
    echo ""
    echo "Add these to your .env file:"
    echo "LUCY_OPENCLAW_BASE_URL=http://167.86.82.46:$OPENCLAW_PORT"
    echo "LUCY_OPENCLAW_API_KEY=$LUCY_TOKEN"
else
    echo "ERROR: Service failed to start. Check logs:"
    echo "sudo journalctl -u $SERVICE_NAME -n 50"
    exit 1
fi
