#!/usr/bin/env bash
# Provision a Hetzner CX22 and print SSH instructions.
# Requires: HCLOUD_TOKEN, hcloud CLI, SSH public key at ~/.ssh/id_ed25519.pub
set -euo pipefail

: "${HCLOUD_TOKEN:?export HCLOUD_TOKEN first}"
export PATH="${HOME}/.local/bin:${PATH}"

NAME="${SERVER_NAME:-merch-desk}"
LOCATION="${SERVER_LOCATION:-ash}"   # ash = Ashburn US; alternatives: nbg1, fsn1, hel1
TYPE="${SERVER_TYPE:-cx22}"
IMAGE="${SERVER_IMAGE:-ubuntu-24.04}"
SSH_PUB="${SSH_PUB:-${HOME}/.ssh/id_ed25519.pub}"

if [[ ! -f "${SSH_PUB}" ]]; then
  echo "Missing SSH public key: ${SSH_PUB}" >&2
  exit 1
fi

KEY_NAME="merch-desk-$(hostname -s)"
if ! hcloud ssh-key describe "${KEY_NAME}" >/dev/null 2>&1; then
  hcloud ssh-key create --name "${KEY_NAME}" --public-key-from-file "${SSH_PUB}"
fi

if hcloud server describe "${NAME}" >/dev/null 2>&1; then
  echo "Server ${NAME} already exists."
else
  hcloud server create \
    --name "${NAME}" \
    --type "${TYPE}" \
    --image "${IMAGE}" \
    --location "${LOCATION}" \
    --ssh-key "${KEY_NAME}"
fi

IP="$(hcloud server ip "${NAME}")"
echo "SERVER_IP=${IP}"
echo "ssh root@${IP}"
