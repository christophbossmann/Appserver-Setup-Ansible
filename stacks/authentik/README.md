# Authentik (self-hosted IdP / SSO)

Self-contained `compose_stack`: **own Postgres** (not the shared prod-postgres —
the auth foundation must not depend on the DB it protects), the Authentik
**server** + **worker**, named volumes. No Redis (2026.x doesn't need it), no
Docker socket (we use the server's **embedded outpost**, not external ones).

Externally reachable at `https://authentik.<global_host>` (Caddy fronts the
loopback-bound server on `127.0.0.1:{{ authentik_http_port }}`).

## Deploy

```bash
bash stacks/authentik/generate-keys.sh   # -> paste into group_vars/all/vault.yml
ansible-playbook playbooks/server-all.yml --limit <host> --tags authentik,caddy
```

First start runs all migrations (a few minutes). Then log in at
`https://authentik.<host>` as **akadmin** / `vault_authentik_bootstrap_password`
and change the password.

## Three SSO integration methods

Everything below is **declarative** — Authentik objects (providers, applications,
groups, users, outposts) are created by **blueprints** under
`stacks/authentik/blueprints/*.yaml.j2`, applied automatically on stack start. No
UI clicking. The blueprint inputs live in `group_vars/all/authentik.yml`.

1. **Native OIDC** — the app knows Authentik as an OpenID provider (best UX:
   roles, single-logout, works with mobile/API). In use for **Immich, Grafana,
   Komodo, pgAdmin**. Defined in `authentik_oauth_apps` → blueprint
   `blueprints/oauth-apps.yaml.j2`; the client secret is shared with the app's own
   OIDC config (`vault_authentik_oauth_<name>_secret`).
2. **Forward-auth** — Caddy asks Authentik's embedded outpost before proxying.
   Protects **any** browser app. In use for **playwright, Supabase Studio, pg1–pg5**.
   Defined in `authentik_forward_auth_apps` → blueprint `blueprints/forward-auth.yaml.j2`
   + `caddy_forward_auth: true` on the route. **Browser only** — never on API/webhook
   paths (immich mobile, supabase `/rest`+`/auth`, n8n `/webhook`); those are bypassed
   with `caddy_forward_auth_except_paths` or served on a separate key-auth host.
3. **LDAP** — for clients that speak LDAP, not HTTP (our **WebDAV** services).
   A dedicated LDAP outpost lets rclone verify each login against Authentik. See
   *WebDAV over LDAP* below.

## Adding a forward-auth service (declarative)

No UI clicking — the blueprint creates the Proxy Provider + Application + outpost
binding. To protect a new hostname:

1. Add an entry to `authentik_forward_auth_apps` in `group_vars/all/authentik.yml`:
   ```yaml
   - { name: myapp, display_name: "My App", hostname: "myapp.{{ internal_host }}" }
   # optional: cookie_domain (SSO across subdomains), access_groups: [some-group]
   ```
2. Set `caddy_forward_auth: true` on that service's route (its `caddy_route.yml`).
   For API/webhook prefixes that must skip auth, add `caddy_forward_auth_except_paths`.
3. Re-run: `ansible-playbook … --tags authentik,caddy`.

Unauthenticated requests then bounce to the Authentik login and back. Access
defaults to admins; widen it with `access_groups` (see groups below).

## What's configured (inventory)

| Method | Apps | Source |
|--------|------|--------|
| OIDC | grafana, immich, komodo, pgadmin | `authentik_oauth_apps` → `oauth-apps.yaml.j2` |
| Forward-auth | playwright, supabase (Studio), pg1–pg5 | `authentik_forward_auth_apps` → `forward-auth.yaml.j2` |
| LDAP | webdav-n8n, webdav-playground, all-users | `authentik_ldap_apps` → `ldap.yaml.j2` |

App-specific bootstrap that lives in its own role: `roles/immich_bootstrap` (local
Immich admin matching the akadmin email so the first OIDC login owns the instance),
`roles/pgadmin_servers` (registers the 3 shared Postgres instances), Grafana single-
logout (`GF_AUTH_SIGNOUT_REDIRECT_URL` in roles/monitoring), Komodo `init_admin: false`
(first OIDC login becomes admin natively).

## Declarative users & groups

Managed via the blueprints from `group_vars/all/authentik.yml`:

- **`authentik_groups`** — created (immich-users, pg-users, webdav-n8n-users,
  webdav-playground-users). Bind a group to an app via that app's `access_groups:`.
- **`authentik_groups_absent`** — deleted with their bindings (blueprints don't
  prune, so renamed/removed groups are listed here to clean them up).
- **`authentik_service_accounts`** — non-interactive logins (e.g. `playwright-bot`).
  Created by the blueprint; passwords set via the API by `roles/authentik_bootstrap`
  (convergent — always reconciled to the vault value).
- **`authentik_users`** — real people. Created by the blueprint (never pruned);
  password is **initial-only** (set once when the account has no usable password, so
  a user's own later password change is never overwritten). Omit `password` to have
  them set it via an Authentik recovery link.
  > Note: users created by hand in the Authentik **UI** (not listed in
  > `authentik_users`) survive re-rollout but are **not** managed here — add them to
  > `authentik_users` if you want their group membership reproducible.

## WebDAV over LDAP

The n8n and playground WebDAV shares authenticate against Authentik via a
dedicated **LDAP outpost** (container `authentik-ldap`), so any Authentik user in
the right group can log in with their normal Authentik password — no per-service
credentials. Fully IaC:

- **Outpost + providers**: `stacks/authentik/blueprints/ldap.yaml.j2` — ONE outpost
  serves one provider per service, each on its own `base_dn` (see
  `authentik_ldap_apps` in `group_vars/all/authentik.yml`). The outpost token is
  fetched by `roles/authentik_bootstrap`.
- **Per-service auth**: the WebDAV container runs the `rclone-ldap:local` image
  (`roles/rclone_ldap_image`) and `rclone serve webdav --auth-proxy`, which does an
  LDAP bind `cn=<user>,ou=users,<base_dn>` per login (`n8n_webdav_ldap` /
  `playground_webdav_ldap`). No Caddy `basic_auth` in this mode — Caddy passes the
  `Authorization` header straight through to the container.
- **Who may log in**: members of `webdav-n8n-users` / `webdav-playground-users`
  (the provider's `search_group`). **Admins** (`authentik Admins`) always have
  access too — a second policy binding per app grants it without adding them to
  each webdav group (`authentik_ldap_admin_access: true`). Group hierarchy can't
  express this (a group has only one parent).

**Login** = Authentik username + password. Verify from a shell (LDAP exposed on the
tailscale IP, see `authentik_ldap_bind_addresses`):

```bash
ldapsearch -x -H ldap://<tailscale-ip>:3389 \
  -D "cn=<user>,ou=users,dc=n8n,dc=ldap,<...>" -W \
  -b "dc=n8n,dc=ldap,<...>" -s base
# result: 0 Success = ok · 49 = wrong password · 50 = not in the group
# (NOTE: an EMPTY password does an anonymous bind that always "succeeds" reading the
#  public root DSE — that is NOT a credential check. Always type a real password.)
```

### ⚠️ Windows WebDAV caching gotcha

The Windows WebDAV client (the **WebClient** service) caches credentials in RAM and
replays **stale** ones even after you re-enter a password. Symptom: it works in a
browser (incognito) immediately, but Explorer / a mapped drive keeps failing with
**"System error 5 / Access denied"** — and the outpost log shows
`flow error password: Invalid password` in a loop while the **bind DN / username is
correct**. Fix (PowerShell **as admin** — the service restart is the key step):

```powershell
net use * /delete /y
cmdkey /delete:webdav-playground.int.<host>   # + every hit from `cmdkey /list`
net stop webclient ; net start webclient
net use Z: https://webdav-playground.int.<host>/ /user:<authentik-user> *  # * = fresh prompt
```

After the first successful login it's fast (rclone caches the auth result; each LDAP
bind otherwise runs the full auth flow, ~2–3 s — visible as `took-ms` in the log).

## Notes

- `vault_authentik_secret_key` signs sessions/tokens — **never change it** on a
  live install (invalidates all sessions).
- Images pinned in `group_vars/all/authentik.yml` (`authentik_*_image`),
  auto-collected by `playbooks/preload-images.yml`.
- SMTP is disabled by default (no password resets by mail); point
  `authentik_smtp_*` at a real relay to enable.
- After a blueprint deploy the **LDAP outpost re-syncs its providers** — expect
  ~40–60 s where LDAP binds transiently fail (`Insufficient access` / `No provider
  found`) before recovering. Not a bug; wait and re-test.
- LDAP ports (3389/6636) are published on the **tailscale IP** only, never `0.0.0.0`
  (Docker bypasses ufw). Set the extra bind address in `host_vars` via
  `authentik_ldap_bind_addresses`.
