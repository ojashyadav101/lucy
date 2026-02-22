#!/bin/bash
# OpenClaw Gateway Fix for Lucy
# Run this on your VPS as root or lucy-oclaw user

set -e

echo "🤝 Lucy OpenClaw Gateway Fix"
echo "=============================="

# 1. Check current OpenClaw config
echo "Step 1: Checking current configuration..."
if [ -f /home/lucy-oclaw/.openclaw/openclaw.json ]; then
    echo "Found config at: /home/lucy-oclaw/.openclaw/openclaw.json"
    echo "Current config:"
    cat /home/lucy-oclaw/.openclaw/openclaw.json
else
    echo "❌ Config not found at expected location"
    exit 1
fi

# 2. Backup current config
echo ""
echo "Step 2: Backing up current config..."
cp /home/lucy-oclaw/.openclaw/openclaw.json /home/lucy-oclaw/.openclaw/openclaw.json.backup

# 3. Update config to enable API
echo ""
echo "Step 3: Updating config to enable HTTP API..."
cat > /home/lucy-oclaw/.openclaw/openclaw.json << 'CONFIG'
{
  "gateway": {
    "port": 18791,
    "host": "0.0.0.0",
    "api_enabled": true,
    "api_base_path": "/",
    "auth": {
      "type": "bearer",
      "token": "lucy-openclaw-token-20260221"
    }
  },
  "models": {
    "default": "openrouter/moonshotai/kimi-k2.5",
    "providers": {
      "openrouter": {
        "api_key": "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",
        "base_url": "https://openrouter.ai/api/v1"
      }
    }
  },
  "features": {
    "sessions": true,
    "streaming": true,
    "memory": true,
    "tools": true
  }
}
CONFIG

echo "Config updated."

# 4. Restart OpenClaw service
echo ""
echo "Step 4: Restarting OpenClaw service..."
if systemctl is-active --quiet openclaw-lucy; then
    systemctl restart openclaw-lucy
    echo "Service restarted."
else
    echo "⚠️  Service not running. Starting it..."
    systemctl start openclaw-lucy
fi

# 5. Wait for startup
echo ""
echo "Step 5: Waiting for gateway to start..."
sleep 5

# 6. Test the fix
echo ""
echo "Step 6: Testing API..."
if curl -s http://localhost:18791/health | grep -q "status"; then
    echo "✅ SUCCESS! API is now accessible."
    curl -s http://localhost:18791/health
else
    echo "❌ API still not accessible. Check logs:"
    journalctl -u openclaw-lucy -n 50
fi

echo ""
echo "=============================="
echo "Fix complete!"
