#!/bin/bash
# Simple script to setup a second OpenClaw instance for Lucy
# This copies your existing setup pattern and just changes ports/tokens

set -e

echo "=== OpenClaw for Lucy - VPS Setup ==="
echo "This will create a completely isolated second instance"
echo ""

# Configuration
NEW_PORT=18790
WORK_DIR="/opt/openclaw-lucy"
SERVICE_NAME="openclaw-lucy"

# Generate unique tokens
LUCY_TOKEN="lucy-$(openssl rand -hex 16)"
LUCY_HOOKS_TOKEN="lucy-hooks-$(openssl rand -hex 16)"

echo "New Gateway Token: $LUCY_TOKEN"
echo "New Hooks Token: $LUCY_HOOKS_TOKEN"
echo ""

# Create directory structure
sudo mkdir -p "$WORK_DIR"/{plugins,data,logs}

echo ""
echo "=== NEXT STEPS ==="
echo ""
echo "1. Copy your existing OpenClaw binary to: $WORK_DIR/"
echo "   Example: sudo cp /path/to/your/current/openclaw $WORK_DIR/"
echo ""
echo "2. Create config file at: $WORK_DIR/config.yaml"
echo "   Use port: $NEW_PORT (instead of 18789)"
echo "   Use token: $LUCY_TOKEN"
echo ""
echo "3. Create systemd service: /etc/systemd/system/$SERVICE_NAME.service"
echo "   (Copy from your existing service, just change paths and port)"
echo ""
echo "4. Start the service:"
echo "   sudo systemctl daemon-reload"
echo "   sudo systemctl enable $SERVICE_NAME"
echo "   sudo systemctl start $SERVICE_NAME"
echo ""
echo "=== TOKENS TO SAVE ==="
echo ""
echo "Gateway URL: http://167.86.82.46:$NEW_PORT"
echo "Gateway Token: $LUCY_TOKEN"
echo "Hooks Token: $LUCY_HOOKS_TOKEN"
echo ""
echo "Copy these into your local .env file for Lucy"
