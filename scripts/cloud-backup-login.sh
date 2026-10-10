#!/usr/bin/env bash
# One-command cloud-backup setup with the "show URL -> authenticate -> continue"
# flow (like the Claude login). Ansible can't stream an interactive OAuth (it
# buffers task output), so rclone drives the browser login here, and Ansible
# takes over automatically once you're done.
#
#   scripts/cloud-backup-login.sh <host>          # e.g. myserver
#
# rclone shows the auth URL live, opens your browser (or prints the URL to open),
# you approve OneDrive/pCloud access, rclone continues — then this script pushes
# the result to <host>, verifies the remote and inits the restic repo.
set -euo pipefail
cd "$(dirname "$0")/.."

HOST="${1:?usage: cloud-backup-login.sh <host>}"

command -v rclone >/dev/null || { echo "Install rclone first:  sudo apt-get install -y rclone"; exit 1; }

CONF="$(mktemp)"; trap 'rm -f "$CONF"' EXIT

echo "=============================================================="
echo " rclone login — create a remote named EXACTLY: cloudbackup"
echo "   storage: onedrive   (or pcloud/…)"
echo "   client_id/secret: leave blank    advanced config: n"
echo "   'Use auto config?' -> y if this machine has a browser,"
echo "                         n to get a URL to open elsewhere"
echo " rclone will show the URL, you approve access, it continues."
echo "=============================================================="
echo
rclone config                                    # native URL-auth-continue UX

if ! rclone config show cloudbackup >"$CONF" 2>/dev/null || ! grep -q '^\[cloudbackup\]' "$CONF"; then
  echo "No remote named 'cloudbackup' was created — aborting."; exit 1
fi

echo
echo ">> Handing the config to Ansible ($HOST) ..."
RCLONE_CONF_PATH="$CONF" ansible-playbook playbooks/backup-setup.yml --limit "$HOST"

echo
echo "Done. To make it reproducible + enable the nightly timer, paste the SAME"
echo "[cloudbackup] section into vault_rclone_backup_conf and set"
echo "backup_cloud_enabled: true, then: --tags backup."
