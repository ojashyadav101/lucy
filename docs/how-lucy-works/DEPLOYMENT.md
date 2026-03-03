# Lucy Deployment Guide

## Overview

Lucy runs as a **persistent systemd service on a Contabo VPS**. It connects to Slack via Socket Mode (a WebSocket maintained by Slack — no inbound port needed). All code lives in this GitHub repo. Deployment is fully automated via a git push hook.

---

## Infrastructure at a Glance

| Component       | Location                              | Details                                         |
|----------------|---------------------------------------|-------------------------------------------------|
| **Lucy bot**    | Contabo VPS `167.86.82.46`            | systemd service `/etc/systemd/system/lucy.service` |
| **PostgreSQL**  | Docker on VPS                         | `lucy-postgres` container, port 5432            |
| **Qdrant**      | Docker on VPS                         | `lucy-qdrant` container, ports 6333/6334        |
| **OpenClaw**    | Native binary on VPS                  | Port 18789 (localhost only), started via crontab |
| **ClawRoute**   | systemd on VPS                        | Model router, `clawroute.service`               |
| **GitHub repo** | `github.com/ojashyadav101/lucy`       | Source of truth for all code                    |
| **Bare git repo** | `/opt/lucy/repo.git` on VPS         | Push target — triggers auto-deploy              |

---

## VPS Access

```
IP:       167.86.82.46
SSH user: root
SSH key:  ~/.ssh/id_lucy_vps (on local machine, already authorised on VPS)
Command:  ssh -i ~/.ssh/id_lucy_vps root@167.86.82.46
Alias:    ssh contabo-lucy   (configured in ~/.ssh/config)
```

---

## How Auto-Deploy Works

```
Your laptop
    │
    └── git push origin main
            │
            ├── → GitHub (https://github.com/ojashyadav101/lucy.git)
            │       source of truth / backup
            │
            └── → Contabo bare repo (ssh://contabo-lucy/opt/lucy/repo.git)
                        │
                        └── post-receive hook fires
                                ├── unset GIT_DIR
                                ├── cd /opt/lucy/app
                                ├── git fetch origin main (from GitHub)
                                ├── git reset --hard origin/main
                                ├── pip install -e .
                                └── systemctl restart lucy
                                        └── Lucy is live ~5 seconds after push
```

### The deploy hook log
```bash
tail -f /var/log/lucy-deploy.log
```

---

## Git Remotes (local laptop)

```
origin  https://github.com/ojashyadav101/lucy.git  (fetch)
origin  https://github.com/ojashyadav101/lucy.git  (push)  → GitHub
origin  ssh://contabo-lucy/opt/lucy/repo.git        (push)  → Contabo auto-deploy
```

`git push origin main` → pushes to **both** simultaneously.

---

## Directory Layout on VPS

```
/opt/lucy/
├── app/           ← Working copy of the repo (cloned from GitHub)
│   ├── .env       ← Production environment variables (NOT in git)
│   ├── .venv/     ← Python 3.12 virtual environment
│   ├── workspaces/ ← Live workspace data (skills, crons, logs per Slack team)
│   └── ...
└── repo.git/      ← Bare repo (push target)
    └── hooks/
        └── post-receive  ← Auto-deploy hook

/etc/systemd/system/lucy.service
/var/log/lucy-deploy.log     ← Hook deployment log
/var/log/openclaw-gateway.log
```

---

## Key Commands on VPS

```bash
# Lucy service management
systemctl status lucy
systemctl restart lucy
systemctl stop lucy
systemctl start lucy

# Live logs (the main thing you'll use)
journalctl -u lucy -f
journalctl -u lucy -n 100 --no-pager

# Deploy log (see what happened on last push)
tail -30 /var/log/lucy-deploy.log

# Docker services (Postgres + Qdrant)
docker ps
docker logs lucy-postgres
docker logs lucy-qdrant

# Database access
docker exec -it lucy-postgres psql -U lucy -d lucy

# OpenClaw gateway status
systemctl status clawroute
ps aux | grep openclaw
tail -f /var/log/openclaw-gateway.log
```

---

## Environment Variables (`.env` on VPS)

The `.env` file lives at `/opt/lucy/app/.env` on the VPS. It is **not committed to git**.

Key vars and their VPS values:

| Variable | VPS Value |
|---|---|
| `LUCY_DATABASE_URL` | `postgresql+asyncpg://lucy:lucy@localhost:5432/lucy` |
| `LUCY_OPENCLAW_BASE_URL` | `http://127.0.0.1:18789` |
| `LUCY_QDRANT_URL` | `http://localhost:6333` |
| `LUCY_ENV` | `production` |

To update `.env` on VPS:
```bash
ssh contabo-lucy "nano /opt/lucy/app/.env"
systemctl restart lucy
```

Or push from local (`.env` is gitignored, so do it manually):
```bash
scp -i ~/.ssh/id_lucy_vps .env root@167.86.82.46:/opt/lucy/app/.env
ssh contabo-lucy "systemctl restart lucy"
```

---

## How to Re-run Setup From Scratch

If the VPS is wiped or you need to set up a new server:

```bash
# 1. From your laptop — copy .env to VPS first
scp -i ~/.ssh/id_lucy_vps .env root@NEW_IP:/tmp/lucy.env

# 2. Run the setup script
ssh -i ~/.ssh/id_lucy_vps root@NEW_IP 'bash -s' < deploy/setup-vps.sh
```

The script does everything: clones the repo, creates the venv, installs Lucy, migrates the DB, installs the systemd service, and starts Lucy.

---

## Logs

All logs from Lucy go to **journald on the VPS**. Never check your laptop for Lucy logs — it's not running there.

```bash
# Connect and watch live
ssh contabo-lucy "journalctl -u lucy -f"

# Last 200 lines
ssh contabo-lucy "journalctl -u lucy -n 200 --no-pager"

# Errors only
ssh contabo-lucy "journalctl -u lucy -p err -n 50 --no-pager"

# Since a specific time
ssh contabo-lucy "journalctl -u lucy --since '2026-03-03 10:00:00' --no-pager"
```

---

## Startup / Autostart Behaviour

- **Lucy**: `systemctl enable lucy` → starts on reboot automatically
- **PostgreSQL**: Docker container with `restart: unless-stopped` in the Compose file
  - Started via `/root/lucy-db-docker-compose.yml`
  - If it's not running: `docker compose -f /root/lucy-db-docker-compose.yml up -d`
- **Qdrant**: Same Docker Compose file as Postgres
- **OpenClaw gateway**: Started via root crontab (`@reboot`) — check with `ps aux | grep openclaw`

---

## Workflow for Developers

1. Edit code on your laptop
2. `git add . && git commit -m "your message"`
3. `git push origin main`
4. Both GitHub and Contabo receive the push simultaneously
5. Lucy auto-restarts on Contabo within ~10 seconds
6. Watch it: `ssh contabo-lucy "journalctl -u lucy -f"`
