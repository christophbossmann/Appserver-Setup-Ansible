# App Server Setup with Ansible

Ansible playbooks, run from your own controller, that turn a fresh Ubuntu/Debian host
into a **hardened, production-ready application server** — or a **disposable test VM
that is still publicly reachable through a Cloudflare Tunnel** — using the *same* roles
and the *same* variables.

> These playbooks configure the **target** servers (your app hosts). They do not set up
> an Ansible control node — you run them from your existing workstation/controller.

The flagship stack is [n8n](https://n8n.io) (workflow automation) with PostgreSQL,
pgAdmin, Grafana/Prometheus monitoring, Komodo container management, a Playwright
browser agent, a sandboxed Claude AI playground, and a Caddy reverse proxy — but every
piece is a self-contained role or compose stack you can mix and match.

**New here?** → [QUICKSTART.md](QUICKSTART.md)  ·  **Adding a service?** → [stacks/README.md](stacks/README.md)

---

## Why this exists (the intention)

There are two situations this repo is built for, and it handles both without forking
your configuration:

### 1. Real production server (public VPS / root server)

You have a server with a public IP and a domain pointing at it. The playbooks:

- **Harden it** — UFW firewall, Fail2Ban, SSH lockdown (no root login, no password
  auth), automatic security updates, swap tuning, and a built-in *auto-rollback* so a
  bad firewall/SSH change can't lock you out.
- **Run your apps in Docker** behind Caddy, which obtains real TLS certificates
  automatically (Let's Encrypt via acme-dns / Cloudflare DNS-01, including wildcards).
- Expose internal admin UIs (Grafana, pgAdmin, Cockpit, Komodo) **only over
  Tailscale**, never to the public internet.

### 2. Private test VM that still needs public webhooks

You're developing on a VM at home with **no public IP and no open ports** — behind
NAT/CGNAT, where inbound webhooks (Telegram, Stripe, OAuth callbacks) normally can't
reach you. This repo solves that with a **Cloudflare Tunnel**: `cloudflared` dials
*outbound* to Cloudflare, Cloudflare terminates public HTTPS on a real hostname and
forwards requests down the tunnel to Caddy → n8n. Result: a stable public HTTPS URL
with a valid certificate, working webhooks and OAuth — no port forwarding, no
dynamic DNS, no exposed home IP.

Switching between the two is a per-host decision (the `route_int`/`route_ext`
profiles in host_vars). The application configuration does not change.

---

## Repository layout

```
.
├── ansible.cfg
├── ansible-bootstrap.sh            # one-time: create the "ansible" service account on a new host
├── inventory.ini                   # your hosts (git-ignored; copy inventory.ini.example)
├── playbooks/
│   ├── server-n8n.yml              # the full application host (apps + edge)
│   ├── server-playground.yml       # Claude AI playground (can share a host with the above)
│   ├── server-base.yml             # hardening + users + docker + tailscale + caddy
│   └── server-proxy.yml            # cloudflared + Caddy proxy host (no apps)
├── roles/                          # dedicated roles — services WITH lifecycle logic
├── stacks/<name>/                  # generic compose services, deployed by the
│                                   #   compose_stack role (postgres, komodo, pgadmin, …)
├── group_vars/all/
│   ├── <service>.yml               # per-service config (auto-loaded, committable)
│   ├── vault.yml                   # ALL secrets, vault_* convention (ansible-vault)
│   ├── vault.example.yml           # committable template for vault.yml
│   ├── users.yml                   # admin_user — the primary human account name
│   └── proxy_routes.yml            # base wildcard-cert routes + profile docs
└── host_vars/<host>.yml            # per-host: domain, ingress model, caddy_services
```

---

## The core model

**Playbook = what a host runs. `caddy_services` = what a host routes. Tags = what a
single run touches.** Three independent decisions:

1. **Playbooks** list the services a host should have. Every role invocation is
   tag-gated with its own name; the edge roles (`caddy`, `cloudflare_dns`,
   `cloudflared`, `caddy_post_check`) share the extra tag `caddy`:

   ```bash
   # everything:
   ansible-playbook -i inventory.ini playbooks/server-n8n.yml --limit myserver
   # just update n8n + re-render the edge (fast):
   ansible-playbook -i inventory.ini playbooks/server-n8n.yml --limit myserver --tags n8n,caddy
   ```

2. **Routes live next to their service** — `roles/<role>/defaults/caddy_route.yml` or
   `stacks/<stack>/caddy_route.yml`. The caddy role discovers ALL of them on every run
   and renders exactly what the host's `caddy_services` map declares, so even a
   one-tag run produces a complete Caddyfile:

   ```yaml
   # host_vars/<host>.yml
   caddy_services:
     komodo: internal      # tailscale-only
     n8n: external         # public (tunnel or direct, per route_ext)
   ```

   Removing an entry removes the route on the next run — that IS the cleanup.

3. **`route_int` / `route_ext`** in host_vars define what internal/external *mean* on
   this host (TLS mode, bind interface, tunnel on/off). The same service can be
   internal on one host and external on another — one word in `caddy_services`.

**Services** come in two shapes:

- **`stacks/<name>/`** — plain docker-compose services deployed by the generic
  `compose_stack` role (dirs, configs, compose, pull, up, health check, shared
  networks). Adding a tool like Immich is a folder + one playbook line —
  see [stacks/README.md](stacks/README.md).
- **Dedicated roles** — services with real lifecycle logic that a generic deployer
  shouldn't fake: n8n (brain seeding, fresh-install DB reset), monitoring (dashboard
  provisioning), cockpit (apt/systemd, not docker), claude_playground (sandboxed AI
  runtime behind a root broker).

---

## Caddy TLS modes

Set globally with `caddy_tls_mode:` (host_vars) and override per route:

| Mode | When to use |
|------|-------------|
| `auto` | Public VServer with direct DNS and open 80/443. Standard Let's Encrypt. |
| `acme_dns` | DNS-01 via an [acme-dns](https://github.com/joohoi/acme-dns) relay. One-time CNAME at your registrar, then fully automatic — including **wildcard** certs. Works with any DNS provider. |
| `cloudflare_dns` | DNS-01 via the Cloudflare API. Supports wildcards. Needs a Cloudflare API token. |
| `tailscale` | Certs via `tailscale cert` for `*.ts.net` names. |
| `internal` | Caddy's own internal CA, for purely local testing. |
| `off` | HTTP-only — something else terminates TLS (e.g. a Cloudflare Tunnel pointed at `HTTP → localhost:80`). |
| `inherit` | Reuse a wildcard cert already obtained by another route (the default for internal routes). |
| `manual_cert` | Pre-obtained cert/key files already on the host. |

For `cloudflare_dns` / `acme_dns`, the Caddy role builds a custom Caddy binary with the
required DNS plugin via `xcaddy`.

The acme-dns registration is backed up to the controller
(`~/.ansible/acmedns/<host>.json`) after every run and **auto-seeded onto fresh
servers** — a reinstalled host keeps its registration and the one-time
`_acme-challenge` CNAME stays valid forever. The role verifies the CNAME against the
registration before confirming it (typos fail fast), and a play only goes green once
the certificates actually exist in Caddy's store.

---

## Tailscale VPN & internal-only services

[Tailscale](https://tailscale.com) is a zero-config mesh VPN (WireGuard). Every
enrolled machine joins your private *tailnet* and gets a stable `100.64.0.0/10`
address. This repo uses it to administer the servers without exposing SSH/admin UIs,
and to make selected services reachable **only** over the VPN.

- The `tailscale` role installs and connects the node (`tailscale_auth_key`), and can
  back up `/var/lib/tailscale/tailscaled.state` to the controller so a reinstalled
  server rejoins as the *same* machine (`tailscale_state_backup_path`).
- Internal routes get `caddy_bind_interfaces: [tailscale0]` via the `route_int`
  profile — Caddy literally does not listen for those hostnames on any other
  interface. Optionally add `caddy_allowed_ranges: [100.64.0.0/10]` and the
  hardening role's `hardening_ufw_full_access_ranges` for defence in depth.
- Every service container binds `127.0.0.1` — the only ways in are the Caddy route
  (tailscale) or an SSH tunnel (`ssh -L <port>:127.0.0.1:<port> ansible@<host>`).

---

## Cloudflare tokens & tunnel

The Cloudflare features use two **distinct** credentials (stored in the vault):

| Vault var | What it is | Used for |
|-----------|------------|----------|
| `vault_cloudflare_api_token` | A Cloudflare **API token** (`Zone:DNS:Edit`, `Zone:Zone:Read`, `Account:Cloudflare Tunnel:Edit`) | DNS-01 challenges, tunnel public hostnames, CNAME records via API |
| `vault_cloudflared_tunnel_connector_token_<host>` | The Tunnel **connector token** (a JWT, not an API token) | Only for `cloudflared service install <token>` |

Tunnel-only setups (edge TLS at Cloudflare, Caddy on plain :80) need just the
connector token. The API token is required for `cloudflare_dns` certificates or when
Ansible manages tunnel hostnames / DNS records for you.

---

## n8n behind a reverse proxy (why webhooks work)

When `n8n_public_hostname` is set (group_vars/all/n8n.yml), the n8n role injects the
environment n8n needs to know its real public URL: `N8N_HOST`, `N8N_PROTOCOL`,
`WEBHOOK_URL`, `N8N_EDITOR_BASE_URL`, `N8N_PROXY_HOPS`, `N8N_SECURE_COOKIE`. That is
what makes Telegram bot webhooks, generic webhook triggers and OAuth redirects resolve
to the correct external `https://…` address instead of `localhost`.

Extra env vars can be layered on without touching the role via `n8n_extra_env`.
Each name in `n8n_brain_agents` gets a persistent "brain" directory under
`/opt/n8n/files/`, seeded once from `roles/n8n/files/brain/<name>/` (never
overwritten — the journal is agent state) and editable via the n8n WebDAV route.

---

## Roles & stacks

Dedicated roles:

| Role | What it does |
|------|--------------|
| `hardening` | UFW, Fail2Ban, SSH hardening, unattended-upgrades, swap, Docker/UFW compat, clock-sync gate, auto-rollback safety window |
| `users` | Human users (name from `admin_user`), SSH keys, sudo policy |
| `docker` | Docker CE + Compose plugin; `docker_users` group membership |
| `tailscale` | Tailscale VPN with identity backup/restore |
| `compose_stack` | Generic deployer for everything under `stacks/` |
| `n8n` | n8n workflow engine, reverse-proxy env, WebDAV brain access, agent dirs |
| `monitoring` | Prometheus + Grafana with provisioned datasource/dashboards |
| `cockpit` | Cockpit web console (apt/systemd) |
| `claude_playground` | Sandboxed Claude Code runtime: rootless Docker, root broker (`playgroundctl`), public demo route, WebDAV for workspace + memory files |
| `caddy` | Caddy reverse proxy: TLS modes, xcaddy DNS plugins, route resolver, acme-dns registration/seeding, certificate gates |
| `caddy_post_check` | End-to-end HTTP checks of every routed hostname after deployment |
| `cloudflare_dns` | Cloudflare DNS records via API (tunnel CNAMEs) |
| `cloudflared` | Tunnel connector install + API-managed public hostnames |

Compose stacks (`stacks/<name>/`, deployed by `compose_stack`):

| Stack | What it is |
|-------|------------|
| `postgres` | Shared PostgreSQL backend (loopback-only, shared `backend` network, never auto-pulled) |
| `pgadmin` | pgAdmin 4 with auto-registered Postgres server |
| `komodo` | [Komodo](https://komo.do) container/deployment management (Core + Periphery + FerretDB) |
| `playwright_mcp` | Playwright MCP server with noVNC browser view |

---

## Secrets

Every secret is a `vault_*` variable in `group_vars/all/vault.yml` — consumers
(host_vars, group_vars, route files) only ever reference `{{ vault_* }}`. Set it up
from the committed template:

```bash
cp group_vars/all/vault.example.yml group_vars/all/vault.yml
$EDITOR group_vars/all/vault.yml            # fill in real values
ansible-vault encrypt group_vars/all/vault.yml
echo 'vault_password_file = ~/.ansible/vault-pass' # → [defaults] in ansible.cfg
```

Notes:

- Grafana, pgAdmin and the Komodo init admin apply their password on **first start
  only** — change them in the running service afterwards.
- Set `vault_n8n_encryption_key` for production; without it n8n regenerates one and
  stored credentials are lost on a rebuild.
- Generate strong secrets with `openssl rand -hex 32`; basic-auth hashes with
  `caddy hash-password`.

---

## Verification

The repo is `--check --diff` safe end-to-end, including against fresh hosts:

```bash
ansible-playbook -i inventory.ini playbooks/server-n8n.yml --limit myserver --check --diff
```

Real runs fail hard when something is broken: health checks have no
`failed_when: false` escape, the caddy role blocks until acme_dns certificates
actually exist (and prints the ACME errors from the journal when they don't), and
`caddy_post_check` probes every routed hostname end-to-end at the end of a full run.
