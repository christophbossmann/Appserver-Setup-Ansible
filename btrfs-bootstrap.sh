#!/bin/bash
# btrfs-bootstrap.sh - Run from the Ansible controller (like ansible-bootstrap.sh).
# One-time FOUNDATIONAL btrfs setup for a fresh Ubuntu server so snapshots + rollback
# work. This is deliberately OUTSIDE Ansible: it's a one-way, reboot-requiring migration
# you run once and watch — not an idempotent deploy step. (The Ansible btrfs_snapshots
# role still owns the deploy-time ans_* checkpoints + the probe/guard afterwards.)
#
# Because the @ move needs a reboot in the middle, this runs in TWO PHASES and is
# re-runnable — it detects where it is:
#   PHASE 1 (root still "flat"):  move / into the @ subvolume, fix fstab + GRUB.
#                                 -> you REBOOT (manually), then run this again.
#   PHASE 2 (root already @):     install snapper + grub-btrfs, drop old raw snapshots.
#
# This script does NOT create any user or key — it only logs in (with whatever
# credentials you give it) and does the btrfs setup. Creating the 'ansible' SSH user
# is ansible-bootstrap.sh's job; keep the two separate.
#
# Usage: ./btrfs-bootstrap.sh <target_ip> <ssh_user> [ssh_key_path] [root_device]
#   ssh_user    a sudo-capable user (root, the installer's initial admin, or the
#               'ansible' account from ansible-bootstrap.sh)
#   ssh_key     OPTIONAL — used ONLY to log in, if the file exists; otherwise you are
#               prompted for a PASSWORD. A fresh server may not have your key yet.
#               Pass '' or '-' to force password auth even if a key file exists.
#   root_device optional; auto-detected from / if omitted (e.g. /dev/vda3)
set -e

TARGET_HOST="$1"
SSH_USER="$2"
SSH_KEY="${3:-$HOME/.ssh/ansible_key}"
ROOT_DEVICE="${4:-}"

if [ -z "$TARGET_HOST" ] || [ -z "$SSH_USER" ]; then
    echo "Usage: $0 <target_ip> <ssh_user> [ssh_key_path] [root_device]"
    echo ""
    echo "Example: $0 203.0.113.10 ansible"
    echo "         $0 203.0.113.10 ansible ~/.ssh/ansible_key"
    echo "         $0 203.0.113.10 ansible ~/.ssh/ansible_key /dev/vda3"
    exit 1
fi

# Auth: use the key if it exists, otherwise fall back to an interactive PASSWORD.
# No BatchMode (so ssh can prompt for a password), -tt for a TTY (password + sudo).
SSH_OPTS="-o StrictHostKeyChecking=accept-new"
if [ -n "$SSH_KEY" ] && [ "$SSH_KEY" != "-" ] && [ -f "$SSH_KEY" ]; then
    SSH_OPTS="$SSH_OPTS -o IdentitiesOnly=yes -i $SSH_KEY"
    AUTH_INFO="SSH key ($SSH_KEY), password fallback if it is rejected"
else
    SSH_OPTS="$SSH_OPTS -o PubkeyAuthentication=no"
    AUTH_INFO="password (you will be prompted)"
fi

echo "btrfs-bootstrap on $TARGET_HOST (user $SSH_USER) — auth: $AUTH_INFO"

# The 'snapshot' helper command (shipped next to this script) is installed to
# /usr/local/bin on the server in PHASE 2 — base64 it here and hand it to the remote job.
SNAPSHOT_TOOL="$(dirname "$0")/btrfs_bootstrap_resources/snapshot"   # ONLY installer of the helper; roles/btrfs_snapshots just checks it exists
SNAPSHOT_TOOL_B64=""
if [ -f "$SNAPSHOT_TOOL" ]; then
    SNAPSHOT_TOOL_B64=$(base64 < "$SNAPSHOT_TOOL" | tr -d '\n')
else
    echo "  (note: '$SNAPSHOT_TOOL' not found — skipping the 'snapshot' helper install)"
fi

# We capture the remote job (quoted heredoc = nothing expands on the controller) into a
# variable, then run it base64-decoded via `sudo bash -c "<script>"` — NOT via stdin.
# That keeps stdin = the terminal, so an interactive sudo PASSWORD prompt works for a
# non-root user too (with stdin carrying the script, sudo would eat its first line).
REMOTE_SCRIPT=$(cat <<'REMOTE'
set -euo pipefail
DEV="${1:-}"
SNAPSHOT_TOOL_B64="${2:-}"

G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; B='\033[1;36m'; NC='\033[0m'
banner() { echo -e "$1"; }

# --- must be btrfs ---------------------------------------------------------
if [ "$(findmnt -no FSTYPE /)" != "btrfs" ]; then
  banner "${R}/ is not btrfs — nothing to do. Reinstall on btrfs first.${NC}"; exit 1
fi
# resolve the root device (strip any [subvol] suffix findmnt appends)
[ -z "$DEV" ] && DEV="$(findmnt -no SOURCE / | sed 's/\[.*//')"

# --- which phase? already on subvol=@ ? ------------------------------------
if findmnt -no OPTIONS / | tr ',' '\n' | grep -qx 'subvol=/@'; then
  PHASE=2
else
  PHASE=1
fi

if [ "$PHASE" = "1" ]; then
  # ===================== PHASE 1: move / into @ ==========================
  banner "${B}PHASE 1: converting / to the @ subvolume layout (device: $DEV)${NC}"

  # already an @ subvolume present? (a re-run before reboot) -> stop, tell to reboot
  mount -o subvolid=5 "$DEV" /mnt
  if [ -e /mnt/@/etc/fstab ]; then
    umount /mnt
    banner "${Y}An '@' subvolume already exists — looks like PHASE 1 already ran.${NC}"
    banner "${Y}REBOOT the server, then run this script again for PHASE 2.${NC}"
    exit 0
  fi

  echo "  - snapshotting current / into @ ..."
  btrfs subvolume snapshot / /mnt/@ >/dev/null
  # NOTE: do NOT pre-create @/.snapshots — snapper create-config (PHASE 2) makes it
  # a subvolume itself and fails if a plain directory is already sitting there.

  # CRITICAL: make @ the filesystem's DEFAULT subvolume. GRUB's config prefix is
  # `($root)/boot/grub` with NO subvol, so at boot GRUB reads grub.cfg from the
  # DEFAULT subvolume. If that stays the top-level (ID 5), GRUB keeps reading the OLD
  # top-level /boot/grub/grub.cfg forever — the system still boots (its cmdline has
  # rootflags=subvol=@) but every later `update-grub` (which writes @/boot/grub/...)
  # is invisible, so e.g. the grub-btrfs 'Ubuntu snapshots' submenu never appears.
  # The Ubuntu installer sets this automatically for a real @ layout; we must too.
  echo "  - setting @ as the DEFAULT subvolume (so GRUB reads @/boot/grub) ..."
  btrfs subvolume set-default /mnt/@

  echo "  - backing up + editing /etc/fstab (add subvol=@ to the btrfs / line) ..."
  cp /mnt/@/etc/fstab /mnt/@/etc/fstab.pre-btrfs-bootstrap
  awk '
    $2=="/" && $3=="btrfs" && $4 !~ /subvol=/ { $4=$4",subvol=@" }
    { print }
  ' /mnt/@/etc/fstab.pre-btrfs-bootstrap > /mnt/@/etc/fstab
  if ! awk '$2=="/" && $3=="btrfs" && $4 ~ /subvol=@/{f=1} END{exit f?0:1}' /mnt/@/etc/fstab; then
    banner "${R}fstab edit failed (no btrfs / line with subvol=@). Restoring + aborting.${NC}"
    cp /mnt/@/etc/fstab.pre-btrfs-bootstrap /mnt/@/etc/fstab; umount /mnt; exit 1
  fi

  echo "  - pointing GRUB at subvol=@ ..."
  cp /etc/default/grub /etc/default/grub.pre-btrfs-bootstrap
  grep -q 'rootflags=subvol=@' /etc/default/grub || \
    sed -i 's|GRUB_CMDLINE_LINUX_DEFAULT="|GRUB_CMDLINE_LINUX_DEFAULT="rootflags=subvol=@ |' /etc/default/grub
  grub-editenv /boot/grub/grubenv set btrfs_subvol=@ 2>/dev/null || true
  update-grub >/dev/null 2>&1
  cp /etc/default/grub /mnt/@/etc/default/grub
  umount /mnt

  echo ""
  banner "${G}────────────────────────────────────────────────────────────${NC}"
  banner "${G}  PHASE 1 done — / is prepared to boot from subvol=@${NC}"
  banner "${Y}  ==> REBOOT NOW (manually):   sudo reboot${NC}"
  banner "${B}  After reboot, verify:        btrfs subvolume show /   (-> @)${NC}"
  banner "${B}  Then run this script AGAIN for PHASE 2 (snapper/grub-btrfs)${NC}"
  banner "${G}────────────────────────────────────────────────────────────${NC}"
  exit 0
fi

# ============== PHASE 2: snapper + grub-btrfs + cleanup ==================
banner "${B}PHASE 2: / is on @ — setting up snapper + grub-btrfs${NC}"
export DEBIAN_FRONTEND=noninteractive

echo "  - installing packages ..."
apt-get update -qq
apt-get install -y snapper btrfs-progs inotify-tools grub2-common bzip2 git make >/dev/null

echo "  - snapper root config + timeline ..."
if ! snapper list-configs 2>/dev/null | awk 'NR>2{print $1}' | grep -qx root; then
  # snapper create-config creates /.snapshots as a subvolume itself; a leftover EMPTY
  # dir there (e.g. from an older phase-1 mkdir) makes it fail — remove it first. Only
  # if empty, so we never touch a .snapshots that already holds snapshots.
  if [ -d /.snapshots ] && [ -z "$(ls -A /.snapshots 2>/dev/null)" ]; then
    btrfs subvolume delete /.snapshots >/dev/null 2>&1 || rmdir /.snapshots 2>/dev/null || true
  fi
  snapper -c root create-config /
fi
# Timeline = ONE snapshot per day (no hourly tier): keep 7 daily, 4 weekly, 6 monthly.
snapper -c root set-config \
  TIMELINE_CREATE=yes TIMELINE_LIMIT_HOURLY=0 TIMELINE_LIMIT_DAILY=7 \
  TIMELINE_LIMIT_WEEKLY=4 TIMELINE_LIMIT_MONTHLY=6 TIMELINE_LIMIT_YEARLY=0
# snapper-timeline.timer fires HOURLY by default — make it daily (drop-in, idempotent).
mkdir -p /etc/systemd/system/snapper-timeline.timer.d
printf '[Timer]\nOnCalendar=\nOnCalendar=daily\n' > /etc/systemd/system/snapper-timeline.timer.d/daily.conf
systemctl daemon-reload 2>/dev/null || true

# --- FLAT layout: make .snapshots a SEPARATE top-level subvolume (@snapshots) --------
# snapper create-config made a NESTED @/.snapshots subvolume. We replace it with a
# top-level @snapshots subvolume mounted at /.snapshots via fstab. Why: a rollback swaps
# the @ subvolume — with a NESTED .snapshots the whole snapshot history lives inside @
# and gets wiped/broken on every rollback ("IO Error (.snapshots is not a btrfs
# subvolume)"). With @snapshots OUTSIDE @, a rollback never touches the history — snapper
# keeps working and older snapshots survive. (Same layout snapper-rollback/ArchWiki use.)
echo "  - flat layout: separate @snapshots subvolume (survives rollbacks) ..."
if ! grep -qE '[[:space:]]/\.snapshots[[:space:]]' /etc/fstab; then
  umount /.snapshots 2>/dev/null || true
  btrfs subvolume delete /.snapshots >/dev/null 2>&1 || rmdir /.snapshots 2>/dev/null || true
  TLM=$(mktemp -d)
  mount -o subvolid=5 "$DEV" "$TLM"
  [ -d "$TLM/@snapshots" ] || btrfs subvolume create "$TLM/@snapshots" >/dev/null
  umount "$TLM"; rmdir "$TLM"
  RUUID=$(findmnt -no UUID /)
  echo "UUID=$RUUID /.snapshots btrfs defaults,subvol=@snapshots 0 0" >> /etc/fstab
  mkdir -p /.snapshots
  systemctl daemon-reload 2>/dev/null || true   # pick up the new fstab entry (silence the hint)
  mount /.snapshots
  chmod 750 /.snapshots
  echo "      @snapshots created + mounted at /.snapshots (fstab entry added)"
else
  echo "      already flat (fstab has a /.snapshots entry) — skipping"
fi
systemctl enable --now snapper-timeline.timer snapper-cleanup.timer >/dev/null 2>&1

echo "  - grub-btrfs (boot-menu snapshot entries) ..."
if [ ! -f /usr/share/man/man8/grub-btrfs.8.bz2 ]; then
  rm -rf /usr/local/src/grub-btrfs
  git clone --depth 1 -b 4.13 https://github.com/Antynea/grub-btrfs.git /usr/local/src/grub-btrfs >/dev/null 2>&1
  make -C /usr/local/src/grub-btrfs install >/dev/null 2>&1 || true   # last step (update-grub) rerun below
fi
# enable + start grub-btrfsd (watches /.snapshots, refreshes the GRUB snapshot menu).
# Verify it actually came up instead of silently swallowing a failure.
systemctl enable --now grub-btrfsd >/dev/null 2>&1 || true
if systemctl is-active --quiet grub-btrfsd; then
  echo "      grub-btrfsd active"
else
  banner "${Y}      grub-btrfsd NOT active — check 'systemctl status grub-btrfsd' (inotify-tools installed?)${NC}"
fi
# show the GRUB menu (headless VPS: reachable via the host's VNC console at boot)
grep -q '^GRUB_TIMEOUT_STYLE=' /etc/default/grub && \
  sed -i 's/^GRUB_TIMEOUT_STYLE=.*/GRUB_TIMEOUT_STYLE=menu/' /etc/default/grub || \
  echo 'GRUB_TIMEOUT_STYLE=menu' >> /etc/default/grub
grep -q '^GRUB_TIMEOUT=' /etc/default/grub && \
  sed -i 's/^GRUB_TIMEOUT=.*/GRUB_TIMEOUT=5/' /etc/default/grub || \
  echo 'GRUB_TIMEOUT=5' >> /etc/default/grub

echo "  - removing old raw @snapshots/backup_* snapshots (they break grub-btrfs) ..."
mount -o subvolid=5 "$DEV" /mnt 2>/dev/null || true
btrfs subvolume list /mnt | awk '/@snapshots\/backup_/{print $NF}' | while read -r s; do
  btrfs subvolume delete --commit-after "/mnt/$s" >/dev/null 2>&1 && echo "      deleted /$s"
done
umount /mnt 2>/dev/null || true

echo "  - creating a verification snapshot ..."
# Only once — so re-running this script (e.g. just to refresh the 'snapshot' helper)
# doesn't pile up verify snapshots. Skip if one already exists.
if snapper -c root list 2>/dev/null | grep -q 'btrfs-bootstrap_verify_'; then
  echo "      a verification snapshot already exists — skipping"
else
  snapper -c root create -d "btrfs-bootstrap_verify_$(date +%Y-%m-%d_%H-%M)" --cleanup number \
    && echo "      snapshot created" || banner "${Y}      snapshot failed — check 'snapper -c root list'${NC}"
fi

# CRITICAL: re-install GRUB so its config prefix points INTO @. After the @ move,
# GRUB's baked prefix + the ESP stub still say ($root)/boot/grub, which resolves via
# the btrfs top-level (ID 5) — so GRUB keeps reading the STALE pre-@ grub.cfg forever
# and never shows the snapshot submenu, even though update-grub writes @/boot/grub.
# `btrfs subvolume set-default @` alone does NOT fix this (not all GRUB builds honor
# the btrfs default subvolume). Running grub-install now (booted on @) lets grub-probe
# detect the subvol and bake prefix=($root)/@/boot/grub, so GRUB reads the LIVE cfg.
echo "  - re-installing GRUB so its prefix points into @ ..."
if [ -d /sys/firmware/efi ]; then
  grub-install --target=x86_64-efi --efi-directory=/boot/efi --bootloader-id=ubuntu --recheck >/dev/null 2>&1 \
    && echo "      grub-install (UEFI) OK" || banner "${Y}      grub-install (UEFI) warned — check /boot/efi/EFI/ubuntu/grub.cfg${NC}"
else
  DISK="/dev/$(lsblk -no PKNAME "$DEV" 2>/dev/null | head -1)"
  grub-install "$DISK" >/dev/null 2>&1 \
    && echo "      grub-install (BIOS -> $DISK) OK" || banner "${Y}      grub-install (BIOS) warned${NC}"
fi

echo "  - regenerating GRUB config (so the snapshot appears in the boot menu) ..."
update-grub >/dev/null 2>&1 || banner "${Y}  (update-grub warned — check for a metadata-less snapshot)${NC}"

# grub.cfg only gets a `submenu 'Ubuntu snapshots' { configfile .../grub-btrfs.cfg }`
# guarded by `if [ ! -e grub-btrfs.cfg ]`. That grub-btrfs.cfg is written by the
# grub-btrfsd DAEMON only when /.snapshots CHANGES — NOT by update-grub. On a first
# setup it therefore often doesn't exist yet at the first reboot, so the guard hides
# the submenu and the GRUB menu looks empty. Generate it now (the same script the
# daemon runs) so the 'Ubuntu snapshots' entry shows on the very next boot.
if [ -x /etc/grub.d/41_snapshots-btrfs ]; then
  /etc/grub.d/41_snapshots-btrfs >/dev/null 2>&1 \
    && echo "      grub-btrfs.cfg generated (snapshot submenu ready for next boot)" \
    || banner "${Y}      grub-btrfs.cfg generation warned — check 'ls -l /boot/grub/grub-btrfs.cfg'${NC}"
fi

# install the 'snapshot' helper command so list/create/rollback are one word from anywhere
if [ -n "$SNAPSHOT_TOOL_B64" ]; then
  echo "  - installing the 'snapshot' helper -> /usr/local/bin/snapshot ..."
  printf %s "$SNAPSHOT_TOOL_B64" | base64 -d > /usr/local/bin/snapshot
  chmod +x /usr/local/bin/snapshot
  echo "      done -> 'snapshot list' / 'snapshot create <desc>' / 'snapshot rollback <N>'"
fi

echo ""
banner "${G}────────────────────────────────────────────────────────────${NC}"
banner "${G}  PHASE 2 done — btrfs FOUNDATION ready (@, snapper, grub-btrfs)${NC}"
banner "${B}  A verification snapshot was created; the 'snapshot' helper is installed.${NC}"
banner ""
banner "${Y}  NEXT STEPS:${NC}"
banner "${Y}   1) sudo reboot  — then verify the GRUB 'snapshots' submenu (VNC console)${NC}"
banner "${Y}   2) ./ansible-bootstrap.sh <host> <user>   (creates the 'ansible' user)${NC}"
banner "${Y}   3) ansible-playbook playbooks/server-all.yml${NC}"
banner "${B}      ^ THIS is where the data-domain subvolumes (@business/@dev/@authentik),${NC}"
banner "${B}        the ans_pre_setup/ans_subvols_ready anchors and the ans_*${NC}"
banner "${B}        checkpoints are created — NOT this bootstrap.${NC}"
banner ""
banner "${B}  Manage snapshots (anywhere on the server, auto-sudo — 'snapshot help'):${NC}"
banner "${B}    snapshot list | create <desc> | undo <N> | rollback <N>${NC}"
banner "${B}    snapshot -c <config> ...            (any data volume, e.g. business)${NC}"
banner "${B}    snapshot volumes | volume-add <name> <mountpoint>${NC}"
banner "${Y}  NOTE: 'snapper rollback' does NOT work here (subvol=@ hardcoded) —${NC}"
banner "${Y}        use 'snapshot rollback' / 'snapshot -c <vol> rollback'.${NC}"
banner "${G}────────────────────────────────────────────────────────────${NC}"
REMOTE
)

# Ship the script base64-encoded and run it as an argument to `sudo bash -c` (not via
# stdin). stdin stays the terminal -> ssh's password prompt AND an interactive sudo
# password prompt both work. $0=_ , $1=ROOT_DEVICE, $2=snapshot-tool-b64 remotely.
REMOTE_B64=$(printf '%s' "$REMOTE_SCRIPT" | base64 | tr -d '\n')
ssh -tt $SSH_OPTS "$SSH_USER@$TARGET_HOST" \
    "sudo bash -c \"\$(printf %s '$REMOTE_B64' | base64 -d)\" _ '$ROOT_DEVICE' '$SNAPSHOT_TOOL_B64'"

echo ""
echo "btrfs-bootstrap finished."
