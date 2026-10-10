#!/bin/bash
# btrfs-rollback.sh - Run from the Ansible controller (like btrfs-bootstrap.sh).
# Roll the system root back to snapper snapshot #N, in ONE command, from the normal
# running system — no need to boot into a snapshot or a rescue first.
#
# How it works (the same trick Timeshift uses): a btrfs mount is tied to the subvolume
# *ID*, not its name. So the live @ can be RENAMED while it's the running root without
# disturbing it — the kernel keeps using it by id. We rename the current @ to a
# timestamped backup, create a fresh writable @ from snapshot #N, make it the default,
# and reboot. On reboot fstab/GRUB resolve `subvol=@` to the new @ -> you're rolled back.
# (`snapper rollback` can't do this on the Ubuntu @-layout — fstab+GRUB hardcode
# subvol=@, so it errors with "Cannot detect ambit". See btrfs-setup.md §5.)
#
# The old system is kept as @_rollback_backup_<date> (a safety net) — nothing is deleted.
#
# Usage: ./btrfs-rollback.sh <target_ip> <ssh_user> <snapshot_N> [ssh_key_path] [root_device]
#   snapshot_N  the # column from `snapper -c root list` (the snapshot to roll back to)
#   ssh_key     OPTIONAL — only for login; omit/pass '-' to force a password prompt
#   root_device optional; auto-detected from / if omitted (e.g. /dev/vda3)
set -e

TARGET_HOST="$1"
SSH_USER="$2"
SNAP_N="$3"
SSH_KEY="${4:-$HOME/.ssh/ansible_key}"
ROOT_DEVICE="${5:-}"

if [ -z "$TARGET_HOST" ] || [ -z "$SSH_USER" ] || [ -z "$SNAP_N" ]; then
    echo "Usage: $0 <target_ip> <ssh_user> <snapshot_N> [ssh_key_path] [root_device]"
    echo ""
    echo "Find the snapshot number with:  snapper -c root list"
    echo "Then e.g.:  $0 203.0.113.10 ansible 4"
    exit 1
fi

case "$SNAP_N" in
    ''|*[!0-9]*) echo "snapshot_N must be a number (the # from 'snapper -c root list'), got '$SNAP_N'."; exit 1 ;;
esac

# Auth: use the key if it exists, otherwise fall back to an interactive PASSWORD.
SSH_OPTS="-o StrictHostKeyChecking=accept-new"
if [ -n "$SSH_KEY" ] && [ "$SSH_KEY" != "-" ] && [ -f "$SSH_KEY" ]; then
    SSH_OPTS="$SSH_OPTS -o IdentitiesOnly=yes -i $SSH_KEY"
    AUTH_INFO="SSH key ($SSH_KEY), password fallback if it is rejected"
else
    SSH_OPTS="$SSH_OPTS -o PubkeyAuthentication=no"
    AUTH_INFO="password (you will be prompted)"
fi

echo "btrfs-rollback on $TARGET_HOST (user $SSH_USER) -> snapshot #$SNAP_N — auth: $AUTH_INFO"

REMOTE_SCRIPT=$(cat <<'REMOTE'
set -euo pipefail
N="${1:-}"
DEV="${2:-}"

G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; B='\033[1;36m'; NC='\033[0m'
say() { echo -e "$1"; }

if [ "$(findmnt -no FSTYPE /)" != "btrfs" ]; then
  say "${R}/ is not btrfs — nothing to roll back.${NC}"; exit 1
fi
[ -z "$DEV" ] && DEV="$(findmnt -no SOURCE / | sed 's/\[.*//')"

say "${B}Rolling back to snapshot #$N by swapping the @ subvolume (device: $DEV)${NC}"

mount -o subvolid=5 "$DEV" /mnt
if [ ! -d /mnt/@ ]; then
  umount /mnt; say "${R}No @ subvolume found on $DEV — is this the @-layout? Aborting.${NC}"; exit 1
fi
# layout: flat (@snapshots = separate top-level subvolume) vs nested (@/.snapshots)
if [ -d /mnt/@snapshots ]; then FLAT=1; SNAPBASE=/mnt/@snapshots; else FLAT=0; SNAPBASE=/mnt/@/.snapshots; fi
if [ ! -e "$SNAPBASE/$N/snapshot/etc/fstab" ]; then
  umount /mnt
  say "${R}Snapshot #$N not found (looked for $SNAPBASE/$N/snapshot). Run 'snapper -c root list'.${NC}"; exit 1
fi

OLD="/mnt/@_rollback_backup_$(date +%F_%H-%M)"
echo "  - renaming the current @ -> ${OLD#/mnt} (kept as a safety net) ..."
mv /mnt/@ "$OLD"                       # safe on the live root: mount tracks the subvol by id

echo "  - creating a fresh writable @ from snapshot #$N ..."
# flat: @snapshots is separate (path unchanged); nested: it moved under $OLD with @
if [ "$FLAT" = 1 ]; then SRC="/mnt/@snapshots/$N/snapshot"; else SRC="$OLD/.snapshots/$N/snapshot"; fi
btrfs subvolume snapshot "$SRC" /mnt/@ >/dev/null
if [ "$FLAT" = 1 ]; then
  # drop snapshots NEWER than #N (the abandoned timeline); history <= #N is kept
  for d in /mnt/@snapshots/*/; do
    m=$(basename "$d"); case "$m" in ''|*[!0-9]*) continue;; esac
    if [ "$m" -gt "$N" ]; then
      btrfs subvolume delete "$d/snapshot" >/dev/null 2>&1 || true
      rm -rf "$d" && echo "      dropped snapshot #$m (newer than #$N)"
    fi
  done
else
  # nested layout: new @ has only an empty .snapshots DIR — recreate as a subvolume so
  # snapper works again (this layout does NOT preserve older history on rollback).
  rm -rf /mnt/@/.snapshots 2>/dev/null || true
  btrfs subvolume create /mnt/@/.snapshots >/dev/null
fi
btrfs subvolume set-default /mnt/@
umount /mnt

say ""
say "${G}────────────────────────────────────────────────────────────${NC}"
say "${G}  Rollback staged — @ now holds the contents of snapshot #$N${NC}"
say "${B}  Old system kept as ${OLD#/mnt} (btrfs subvolume delete it later).${NC}"
say "${B}  Note: the restored @ starts with an empty snapper history.${NC}"
say "${Y}  ==> Rebooting now into the restored system ...${NC}"
say "${G}────────────────────────────────────────────────────────────${NC}"
sleep 2
reboot
REMOTE
)

# Ship base64-encoded and run as an argument to `sudo bash -c` (stdin stays the
# terminal -> password + interactive sudo work). $1=SNAP_N, $2=ROOT_DEVICE remotely.
REMOTE_B64=$(printf '%s' "$REMOTE_SCRIPT" | base64 | tr -d '\n')
ssh -tt $SSH_OPTS "$SSH_USER@$TARGET_HOST" \
    "sudo bash -c \"\$(printf %s '$REMOTE_B64' | base64 -d)\" _ '$SNAP_N' '$ROOT_DEVICE'" || true

echo ""
echo "btrfs-rollback finished (the target is rebooting into snapshot #$SNAP_N)."
