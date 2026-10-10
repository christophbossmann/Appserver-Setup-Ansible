# Supabase (self-hosted)

Curated from the upstream `supabase/docker` compose, wired into the repo's
`compose_stack` pattern. Deploys the full app stack **minus** analytics/vector
(upstream already dropped those) and **with** Edge Functions.

Services: `db` (Postgres), `supavisor` (pooler), `kong` (API gateway + Studio
front), `studio`, `auth` (GoTrue), `rest` (PostgREST), `realtime`, `storage`,
`imgproxy`, `meta`, `functions` (Edge/Deno).

## Exposure model

- **Web UI + API** run behind **Kong** → Caddy route `supabase.<global_host>`
  (external, **no** Caddy basic_auth). Kong binds `127.0.0.1:8000` only.
  - Studio is protected by `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD`.
  - The API (`/rest`, `/auth`, `/realtime`, `/storage`) is protected by the
    anon/service keys + Postgres RLS — that's why there is no blanket basic_auth
    (it would break API access for apps).
- **Postgres is NOT internet-reachable.** The pooler binds `127.0.0.1:5432`
  (session) / `127.0.0.1:6543` (transaction); `db` itself publishes nothing.
  Reach it from the host or an SSH tunnel: `ssh -L 5432:127.0.0.1:5432 <host>`.

## Playground access

Playground apps use the **public URL**, like any Supabase client:

```js
const supabase = createClient(
  "https://supabase.<global_host>",
  "<ANON_KEY>"   // vault_supabase_anon_key — safe for client/frontend use
)
```

Frontends connect from the browser, so the public URL is the natural (and only
sensible) path — no cross-daemon networking needed. A server-side playground
container that must avoid the public hop can add
`extra_hosts: ["host.docker.internal:host-gateway"]` and use
`http://host.docker.internal:8000` — add that only if/when needed.

## Secrets

All secrets live in `group_vars/all/vault.yml` (`vault_supabase_*`). Generate a
full set:

```bash
bash stacks/supabase/generate-keys.sh   # prints ready-to-paste vault lines
```

`ANON_KEY` / `SERVICE_ROLE_KEY` are HS256 JWTs signed with `JWT_SECRET` —
regenerate both whenever you change `vault_supabase_jwt_secret`.

## Deploy

```bash
ansible-playbook playbooks/server-all.yml --limit <host> --tags supabase,caddy
```

First start does DB init + all service migrations (a few minutes). The stack's
health probe waits for Kong to report healthy.

## Notes

- The `configs/` tree (kong.yml, kong-entrypoint.sh, db/*.sql, functions,
  pooler.exs) is vendored verbatim from upstream so DB init, routing and pooling
  behave exactly as upstream. When bumping images, re-check these against the
  matching upstream tag.
- SMTP is fake by default (no outbound mail); point `supabase_smtp_*` at a real
  relay to enable email confirmations / password resets.
- Images are pinned via `group_vars/all/supabase.yml` (`supabase_*_image`) and
  are auto-collected by `playbooks/preload-images.yml`.
