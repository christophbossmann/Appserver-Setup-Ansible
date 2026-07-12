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
ansible-playbook -i inventory.ini playbooks/server-n8n.yml --limit myserver --tags immich,caddy
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
