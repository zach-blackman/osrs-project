#!/usr/bin/env bash
# Run once on a fresh Ubuntu 24.04 VPS as root (or with sudo).
# Installs Docker, UFW (SSH-only), and prepares /opt/merch-desk.
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run as root: sudo bash deploy/bootstrap-vps.sh" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ca-certificates curl git ufw

if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi
systemctl enable --now docker

ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw --force enable

mkdir -p /opt/merch-desk
echo "Bootstrap done. Clone/copy the repo into /opt/merch-desk, add .env and"
echo "deploy/cloudflared/credentials.json, then:"
echo "  cd /opt/merch-desk && docker compose up -d db app"
echo "  docker compose exec app python backfill_history.py"
echo "  # after backfill + first scan:"
echo "  docker compose --profile tunnel up -d tunnel"
