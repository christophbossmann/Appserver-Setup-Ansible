# Quickstart

From a fresh Ubuntu/Debian host to the full running stack. Background and concepts
live in [README.md](README.md) — this is just the path.

## 0. Prerequisites

- Ansible on your controller (WSL/Linux/macOS), `ansible-vault` included.
- A target host running recent Ubuntu/Debian, SSH-reachable as a sudo user.
- A domain. For the tunnel model additionally a (free) Cloudflare account with the
  domain onboarded; for internal routes a [Tailscale](https://tailscale.com) account.
- An SSH keypair for the admin user. The private key stays in `~/.ssh/` on the
  controller; copy ONLY its public half into the private config dir as
  `../ansible-private/ssh_pubkeys/<admin_user>_key.pub` (same file name as in `~/.ssh/`; the name comes from `admin_user` in
  `group_vars/all/users.yml`). Playbooks read public keys exclusively from
  `ssh_pubkeys/` — never from `~/.ssh/`, where the private keys sit next to them.

## 1. Bootstrap the host (one-time)

Creates the passwordless, key-only `ansible` service account all playbook runs use:

```bash
./ansible-bootstrap.sh --gen-key <target_ip> <ssh_user_with_sudo>
```

## 2. Private configuration

This repo holds only code and sanitized `*.yml.example` files. Your real configuration
lives in a SEPARATE (private) directory/repo next to it. `ansible.cfg` points at
`../ansible-private/inventory.ini`, and Ansible loads `group_vars/` and `host_vars/`
relative to the inventory:

```
~/ansible            # this repo (public)
~/ansible-private    # your config (private git repo or a plain directory)
├── inventory.ini
├── group_vars/all/<name>.yml    # from group_vars/all/<name>.yml.example here
├── host_vars/<your_host>.yml
└── ssh_pubkeys/<admin_user>_key.pub
```

```bash
P=../ansible-private
mkdir -p $P/group_vars/all $P/host_vars $P/ssh_pubkeys
cp inventory.ini.example $P/inventory.ini          # your host name(s) + IP(s)
for f in group_vars/all/*.yml.example; do cp -n "$f" "$P/group_vars/all/$(basename "$f" .example)"; done
```

Keep `group_vars/all/vault*.yml` out of git while it is unencrypted. Ignore it in the
private repo's `.gitignore`.

## 3. Host configuration

```bash
cp host_vars/myserver.example.yml ../ansible-private/host_vars/<your_host>.yml
$EDITOR ../ansible-private/host_vars/<your_host>.yml
```

The file is heavily commented. The essentials to decide:

- `global_host` / `internal_host` — your public and internal domain bases.
- `caddy_tls_mode: acme_dns` — recommended; works with any DNS provider.
- The ingress model via `route_ext`:
  - **HOME/tunnel**: `caddy_tls_mode: "off"`, `tunnel_enabled: true` (+ the
    cloudflared/account/tunnel IDs and `cloudflared_service_install: true`)
  - **VPS/direct**: `caddy_tls_mode: inherit`, `tunnel_enabled: false`,
    `wildcard_cert_enabled: true` — DNS A records must point at the host.
- `caddy_services:` — which services run here and whether each is
  `internal` (tailscale-only) or `external` (public).

## 4. Secrets (vault)

```bash
cd ../ansible-private
cp ../ansible/group_vars/all/vault.yml.example group_vars/all/vault.yml
$EDITOR group_vars/all/vault.yml     # fill every CHANGEME (hints per section)
ansible-vault encrypt group_vars/all/vault.yml   # until then: keep it git-ignored
echo "your-vault-password" > ~/.ansible/vault-pass && chmod 600 ~/.ansible/vault-pass
# then add to ansible.cfg [defaults]:  vault_password_file = ~/.ansible/vault-pass
```

## 5. Deploy

```bash
ansible-playbook playbooks/server-all.yml --limit <your_host>
```

**SSH key with a passphrase?** Ansible can't ask for it, so run the playbook under a
one-shot ssh-agent — the passphrase is asked ONCE, the agent ends with the command:

```bash
ssh-agent bash -c 'ssh-add ~/.ssh/ansible_key && ansible-playbook playbooks/server-all.yml --limit <your_host>'
```

Every server playbook starts with a controller-side preflight
(`playbooks/preflight-ssh-agent.yml`) that fails fast — before any SSH connection — when
the key has a passphrase but isn't in an agent, and prints exactly this command for the
current run. Same for all commands below.

Two interactive moments on a first run:

- **Tailscale login** — if no `tailscale_auth_key` is set, the role prints a login
  URL.
- **acme-dns CNAME** — the play pauses once and shows you a
  `_acme-challenge.<domain> → <uuid>.auth.acme-dns.io` CNAME to create at your DNS
  provider (DNS only, not proxied). It is verified before the play continues, kept
  valid forever by the controller-side backup, and never asked again — even after a
  full server rebuild.

The run only goes green once certificates exist and every route in
`caddy_services` answers over HTTPS.

Optional, same host or another one:

```bash
ansible-playbook playbooks/server-playground.yml --limit <your_host>
```

(The playground pauses for a one-time `claude.ai` login via
`docker exec -it claude-ctl claude`.)

## 6. Day-2 operations

```bash
# preview any change first (works even against fresh hosts):
ansible-playbook playbooks/server-all.yml --limit <host> --check --diff

# update ONE service + re-render routes/tunnel/checks (fast):
ansible-playbook playbooks/server-all.yml --limit <host> --tags komodo,caddy

# bump a service version: edit <service>_version in ../ansible-private/group_vars/all/<service>.yml, then:
ansible-playbook playbooks/server-all.yml --limit <host> --tags <service>

# wipe + reinstall one compose stack (DESTRUCTIVE: containers, volumes, data dir):
ansible-playbook playbooks/server-all.yml --limit <host> \
  --tags <stack> -e compose_stack_fresh=true

# stop routing a service: remove it from caddy_services in host_vars, then --tags caddy
```

Add a new tool (own compose file, own image): [stacks/README.md](stacks/README.md) —
a `stacks/<name>/` folder, a `group_vars/all/<name>.yml`, one playbook line, one
`caddy_services` entry.

## 7. Maintenance tasks

The common operational runs, copy-paste ready. Full options per playbook:
[playbooks/README.md](playbooks/README.md) and each playbook's header.

```bash
# BACKUP — pull app backups to the controller (lands in backup_pull_dir, e.g. D:\backup).
# Incremental rsync; re-runs only transfer changes. Nextcloud is in maintenance for the dump.
ansible-playbook playbooks/backup-pull.yml --limit <host>      # all apps, incl. originals

# ...only immich + nextcloud, WITHOUT the immich originals (those sit on the Storage Box,
# covered by its snapshots; library/ + upload/ are kept as symlinks only):
ansible-playbook playbooks/backup-pull.yml --limit <host> \
  -e backup_only=immich,nextcloud -e backup_pull_originals=false

# RESTORE from that local backup (DESTRUCTIVE — overwrites live data + DB):
ansible-playbook playbooks/immich-restore-local.yml    --limit <host> -e immich_restore_confirm=YES
ansible-playbook playbooks/nextcloud-restore-local.yml --limit <host> -e nc_restore_confirm=YES

# ...immich DB ONLY — files untouched, originals stay as they are on the Storage Box:
ansible-playbook playbooks/immich-restore-local.yml    --limit <host> -e immich_restore_confirm=YES \
  -e immich_restore_files=false
#   (files only instead: -e immich_restore_db=false; nextcloud: nc_restore_files / nc_restore_db)
#   thumbs/ and encoded-video/ are in the backup by default (backup_immich_thumbs /
#   backup_immich_encoded_video) and restored when present — skip with
#   -e immich_restore_thumbs=false / -e immich_restore_encoded_video=false.
#   encoded-video/ holds the motion-photo clips (<id>-MP.mp4, LOCAL, not on the Storage Box).
#   Afterwards run Administration -> Job Queues -> Generate Thumbnails -> "Missing" (the play
#   tells you if "All" is needed); if motion photos don't move: Extract Metadata -> "Missing".

# UPGRADE immich / nextcloud (auto PITR rollback on failure; persists the new version):
ansible-playbook playbooks/immich-upgrade.yml --limit <host> \
  -e immich_upgrade_version=vX.Y.Z -e upgrade_confirm=YES
ansible-playbook playbooks/nextcloud-upgrade.yml --limit <host> \
  -e nextcloud_upgrade_version=NN-apache -e upgrade_confirm=YES

# POINT-IN-TIME RECOVERY of a whole postgres instance (DESTRUCTIVE, every DB on it):
ansible-playbook playbooks/postgres-pitr-restore.yml --limit <host> \
  -e pitr_instance=postgres-business -e pitr_target_time="YYYY-MM-DD HH:MM:SS+00" -e pitr_confirm=YES

# After a btrfs SNAPSHOT rollback of a DB data subvolume (snapper/`snapshot`), run ON THE HOST:
#   sudo pg-pitr-backup --after-rollback <instance>     # e.g. postgres-business, authentik
# (archive still holds the discarded future's WAL under the same names -> archiving stalls;
#  this rotates wal/ aside + takes a fresh base backup. Prefer PITR for intended DB time travel.)

# VERIFY backups restore cleanly (non-destructive, throwaway scratch targets):
ansible-playbook playbooks/tests/test-backup-restore.yml --limit <host>
```

A DB dump only matches the originals of roughly the same moment: when restoring immich,
pair it with a Storage Box snapshot from about the same time.

## Where things end up

| What | Where |
|------|-------|
| Service data | `/opt/<service>/` on the host |
| Rendered Caddyfile | `/etc/caddy/Caddyfile` |
| acme-dns registration backup | `~/.ansible/acmedns/<host>.json` (controller) |
| Tailscale identity backup | `~/.ansible/tailscale-state/<host>.state` (controller) |
| Internal UIs | `https://<service>.int.<domain>` (via Tailscale) |
| Public apps | `https://<service>.<domain>` |
| Debug access to a loopback port | `ssh -L <port>:127.0.0.1:<port> ansible@<host>` |
