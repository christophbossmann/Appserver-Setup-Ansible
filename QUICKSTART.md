# Quickstart

From a fresh Ubuntu/Debian host to the full running stack. Background and concepts
live in [README.md](README.md) — this is just the path.

## 0. Prerequisites

- Ansible on your controller (WSL/Linux/macOS), `ansible-vault` included.
- A target host running recent Ubuntu/Debian, SSH-reachable as a sudo user.
- A domain. For the tunnel model additionally a (free) Cloudflare account with the
  domain onboarded; for internal routes a [Tailscale](https://tailscale.com) account.
- An SSH keypair for the admin user: `~/.ssh/<admin_user>_key(.pub)` on the
  controller (the name comes from `admin_user` in `group_vars/all/users.yml`).

## 1. Bootstrap the host (one-time)

Creates the passwordless, key-only `ansible` service account all playbook runs use:

```bash
./ansible-bootstrap.sh --gen-key <target_ip> <ssh_user_with_sudo>
```

## 2. Inventory

```bash
cp inventory.ini.example inventory.ini
$EDITOR inventory.ini          # your host name(s) + IP(s)
```

## 3. Host configuration

```bash
cp host_vars/myserver.example.yml host_vars/<your_host>.yml
$EDITOR host_vars/<your_host>.yml
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
cp group_vars/all/vault.example.yml group_vars/all/vault.yml
$EDITOR group_vars/all/vault.yml     # fill every CHANGEME (hints per section)
ansible-vault encrypt group_vars/all/vault.yml
echo "your-vault-password" > ~/.ansible/vault-pass && chmod 600 ~/.ansible/vault-pass
# then add to ansible.cfg [defaults]:  vault_password_file = ~/.ansible/vault-pass
```

## 5. Deploy

```bash
ansible-playbook -i inventory.ini playbooks/server-n8n.yml --limit <your_host>
```

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
ansible-playbook -i inventory.ini playbooks/server-playground.yml --limit <your_host>
```

(The playground pauses for a one-time `claude.ai` login via
`docker exec -it claude-ctl claude`.)

## 6. Day-2 operations

```bash
# preview any change first (works even against fresh hosts):
ansible-playbook -i inventory.ini playbooks/server-n8n.yml --limit <host> --check --diff

# update ONE service + re-render routes/tunnel/checks (fast):
ansible-playbook -i inventory.ini playbooks/server-n8n.yml --limit <host> --tags komodo,caddy

# bump a service version: edit <service>_version in group_vars/all/<service>.yml, then:
ansible-playbook -i inventory.ini playbooks/server-n8n.yml --limit <host> --tags <service>

# wipe + reinstall one compose stack (DESTRUCTIVE: containers, volumes, data dir):
ansible-playbook -i inventory.ini playbooks/server-n8n.yml --limit <host> \
  --tags <stack> -e compose_stack_fresh=true

# stop routing a service: remove it from caddy_services in host_vars, then --tags caddy
```

Add a new tool (own compose file, own image): [stacks/README.md](stacks/README.md) —
a `stacks/<name>/` folder, a `group_vars/all/<name>.yml`, one playbook line, one
`caddy_services` entry.

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
