# stacks/ — generic docker-compose services

Services deployed via the generic `compose_stack` role. Each subdirectory is a
self-contained service definition; postgres, komodo, pgadmin and
playwright_mcp already live here (their old per-service roles are gone).

## Adding a new tool (example: Immich)

**1. Create the stack definition:**

```
stacks/immich/
├── docker-compose.yml.j2   # REQUIRED — plain compose file, Jinja allowed
├── stack.yml               # optional — deployment settings (see below)
└── caddy_route.yml         # optional — reverse-proxy route
```

`stacks/immich/docker-compose.yml.j2` — any compose file works, including
fully custom images. All Ansible vars are available. Image convention:
templates reference ONLY `<name>_image`; the `<service>_version` var is the
bump knob the `_image` default is composed from (see step 2):

```yaml
services:
  immich:
    image: {{ immich_image }}
    ports:
      - "127.0.0.1:{{ immich_port }}:2283"
    volumes:
      - {{ immich_upload_dir }}:/usr/src/app/upload
```

`stacks/immich/stack.yml` — how to deploy it (all keys optional, see
[roles/compose_stack/defaults/main.yml](../roles/compose_stack/defaults/main.yml)):

```yaml
dirs:
  - uploads
health_url: "http://127.0.0.1:{{ immich_port }}/api/server/ping"
```

`stacks/immich/caddy_route.yml` — the route, picked up automatically by the
caddy role's resolver (same mechanism as role routes):

```yaml
caddy_route_immich:
  name: immich
  hostname_label: immich
  caddy_upstream: "127.0.0.1:2283"
```

**2. Tunables/secrets** (versions, ports, passwords) go into
`group_vars/all/immich.yml` — overridable per host in `host_vars/<host>.yml`:

```yaml
immich_version: "v1.135.3"
immich_image: "ghcr.io/immich-app/immich-server:{{ immich_version }}"
immich_port: 2283
immich_upload_dir: /opt/immich/uploads
```

**3. Wire it into the playbook** (one line, tag-gated):

```yaml
- { role: compose_stack, vars: { stack: immich }, tags: [immich] }
```

**4. Activate the route** in `host_vars/<host>.yml`:

```yaml
caddy_services:
  immich: internal    # or external
```

**5. Deploy:**

```bash
ansible-playbook playbooks/server-all.yml --limit myserver --tags immich,caddy
```

## Operations

- **Fresh install** (wipe containers + volumes + data dir of ONE stack):
  `--tags immich -e compose_stack_fresh=true`
- **Remove a service:** delete its `caddy_services` entry (route disappears on
  the next caddy run), remove the playbook line, then `docker compose down`
  on the host and delete `stacks/<name>/`.
- **Recreate semantics:** containers are force-recreated only when the
  rendered compose file or a config file actually changed; otherwise
  `docker compose up -d` is a no-op.

## Raw volume inspection over WebDAV (read-only, VPN + Authentik)

Both `immich` and `nextcloud` ship an optional **read-only** WebDAV sidecar that
mounts the raw data volume `:ro` for inspection / spot-recovery — browse the actual
files without going through the app. It is **not** the way to *write* files (that
would corrupt the app's own bookkeeping); the `:ro` bind and rclone `--read-only`
both enforce that.

| Stack | Sidecar | Mounts (`:ro`) | Internal URL | Loopback |
|---|---|---|---|---|
| immich | `immich-webdav` | `{{ immich_upload_dir }}` | `webdav-immich.int.<host>` | `127.0.0.1:8890` |
| nextcloud | `nextcloud-webdav` | `{{ nextcloud_data_dir }}` | `webdav-nextcloud.int.<host>` | `127.0.0.1:8891` |

Same pattern as `n8n-webdav` / `playground-webdav`:
- **Image** `rclone-ldap:local` (built by `roles/rclone_ldap_image`), runs
  `rclone serve webdav --read-only --auth-proxy` as root purely to *read* files of
  any owner — the read-only mount blocks all writes.
- **Reachable only over Tailscale** — the route is marked `internal` in
  `caddy_services` (host_vars), so it lands under the `*.int.<host>` wildcard bound
  to `tailscale0`. Not exposed publicly.
- **Auth = Authentik LDAP.** Login is an Authentik user in `webdav-<app>-users`
  (or any admin). The `auth-proxy.sh` binds `cn=<user>,ou=users,<base_dn>` against
  the LDAP outpost; the provider + group come from `authentik_ldap_apps` /
  `authentik_groups` in `group_vars/all/authentik.yml`.

**Toggle:** `immich_webdav_enabled` / `nextcloud_webdav_enabled` (group_vars).
Basic-auth instead of LDAP: set `*_webdav_ldap: false` (uses `*_webdav_user/password`).

**Mount (Windows example):**
```
net use Z: https://webdav-immich.int.example.com/ /user:<authentik-user> *
```
Windows caching gotcha (same as the other int routes): if a stale login sticks,
`cmdkey /delete:webdav-immich.int.<host>` then re-map.

### Nextcloud's native WebDAV (read-write, cache-safe)

For **normal** file access to Nextcloud use its own WebDAV, not the raw sidecar —
it keeps the DB file cache (`oc_filecache`) consistent:

```
https://nextcloud.<host>/remote.php/dav/files/<user>/
```
Log in with an **app password** (Settings → Security), not the SSO password.
