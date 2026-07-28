#!/usr/bin/env bash
# Deploy Merch Desk to an already-bootstrapped VPS over SSH.
# Usage: ./deploy/remote-deploy.sh root@SERVER_IP
set -euo pipefail

TARGET="${1:?usage: $0 root@SERVER_IP}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [[ ! -f "${ROOT}/.env" ]]; then
  echo "Missing ${ROOT}/.env — copy from .env.example first." >&2
  exit 1
fi
if [[ ! -f "${ROOT}/deploy/cloudflared/credentials.json" ]]; then
  echo "Missing tunnel credentials.json" >&2
  exit 1
fi

REMOTE_DIR=/opt/merch-desk

echo "==> Syncing repo to ${TARGET}:${REMOTE_DIR}"
ssh -o StrictHostKeyChecking=accept-new "${TARGET}" "mkdir -p ${REMOTE_DIR}"
rsync -az --delete \
  --exclude '.venv' \
  --exclude '__pycache__' \
  --exclude '.git' \
  --exclude '*.db' \
  --exclude '*.db-*' \
  --exclude '.lavish' \
  --exclude 'osrs_scanner.db*' \
  "${ROOT}/" "${TARGET}:${REMOTE_DIR}/"

echo "==> Bootstrap (idempotent)"
ssh "${TARGET}" "bash ${REMOTE_DIR}/deploy/bootstrap-vps.sh"

echo "==> Start db + app"
ssh "${TARGET}" "cd ${REMOTE_DIR} && docker compose up -d --build db app"

echo "==> Done. Next: backfill, then tunnel cutover."
echo "    ssh ${TARGET} 'cd ${REMOTE_DIR} && docker compose exec app python backfill_history.py'"
