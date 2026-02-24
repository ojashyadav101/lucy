#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
# Enable OpenClaw Coding Tools for Lucy
# ─────────────────────────────────────────────────────────
#
# Run on VPS: ssh root@167.86.82.46
#   then: bash /path/to/enable_openclaw_coding.sh
#
# OR run remotely:
#   ssh root@167.86.82.46 'bash -s' < scripts/enable_openclaw_coding.sh
#
# This enables exec, read, write, edit, process, web_fetch
# on Lucy's OpenClaw Gateway so she can autonomously write,
# test, and fix code without human intervention.
# ─────────────────────────────────────────────────────────

set -euo pipefail

LUCY_CONFIG="/home/lucy-oclaw/.openclaw/openclaw.json"
BACKUP="${LUCY_CONFIG}.bak.$(date +%s)"
SERVICE="openclaw-lucy.service"

echo "==> Checking config exists..."
if [ ! -f "$LUCY_CONFIG" ]; then
    echo "ERROR: Config not found at $LUCY_CONFIG"
    exit 1
fi

echo "==> Backing up config to $BACKUP"
cp "$LUCY_CONFIG" "$BACKUP"

echo "==> Patching config to enable coding tools..."
# Use python3 (available on the VPS) to safely merge the config
python3 - "$LUCY_CONFIG" <<'PYEOF'
import json, sys

config_path = sys.argv[1]
with open(config_path) as f:
    config = json.load(f)

# Enable coding tools profile
if "tools" not in config:
    config["tools"] = {}

config["tools"]["profile"] = "coding"
config["tools"]["allow"] = [
    "group:fs",
    "group:runtime",
    "group:web",
    "group:sessions",
    "group:memory",
]

# Allow sessions_spawn and gateway over HTTP API (needed for coding sub-agent)
if "gateway" not in config:
    config["gateway"] = {}
if "tools" not in config["gateway"]:
    config["gateway"]["tools"] = {}
config["gateway"]["tools"]["allow"] = ["sessions_spawn", "gateway"]

# Enable chat completions API
if "http" not in config["gateway"]:
    config["gateway"]["http"] = {}
if "endpoints" not in config["gateway"]["http"]:
    config["gateway"]["http"]["endpoints"] = {}
config["gateway"]["http"]["endpoints"]["chatCompletions"] = {"enabled": True}

# Enable web search (needs BRAVE_API_KEY separately)
if "web" not in config.get("tools", {}):
    config["tools"]["web"] = {}
config["tools"]["web"]["fetch"] = {"enabled": True}

with open(config_path, "w") as f:
    json.dump(config, f, indent=2)

print(f"Config updated. Tools profile: {config['tools']['profile']}")
print(f"  Allow list: {config['tools']['allow']}")
print(f"  Gateway HTTP allows: {config['gateway']['tools']['allow']}")
print(f"  Chat completions: enabled")
PYEOF

echo ""
echo "==> Restarting OpenClaw service..."
systemctl restart "$SERVICE"
sleep 3

echo "==> Checking service status..."
systemctl is-active "$SERVICE" && echo "Service is running" || echo "ERROR: Service failed to start"

echo ""
echo "==> Verifying tools are accessible..."
curl -sS http://127.0.0.1:18791/tools/invoke \
  -H 'Authorization: Bearer lucy-openclaw-token-20260221' \
  -H 'Content-Type: application/json' \
  -d '{"tool": "exec", "args": {"command": "echo CODING_TOOLS_ENABLED"}}' | python3 -c "
import json, sys
d = json.load(sys.stdin)
if d.get('ok'):
    print('SUCCESS: exec tool is now available')
else:
    print(f'WARNING: exec tool still not available: {d}')
"

echo ""
echo "==> Done! Lucy can now use OpenClaw coding tools."
echo "    - exec: run shell commands"
echo "    - write: create files"
echo "    - read: read files"
echo "    - edit: modify files"
echo "    - process: manage background tasks"
echo "    - web_fetch: read web pages"
echo "    - sessions_spawn: create coding sub-agents"
