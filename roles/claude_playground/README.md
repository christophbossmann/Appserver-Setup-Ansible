# claude_playground

Provisions a sandboxed AI playground on the host and the **`playgroundctl`**
runtime broker in front of it. Two responsibilities, run in this order:

1. **Provisioning (Ansible only).** A locked-down `playground` Linux user with
   its **own rootless Docker daemon**, plus the `playgroundctl` broker. The
   Python broker does **no** provisioning — it only verifies the environment at
   startup and serves allowed requests.
2. **AI runtime.** A single `claude-ctl` container running Claude Code (Remote
   Control). It has **no** host Docker access — no `helper`, no
   `/var/run/docker.sock`. It manages playground workloads only through
   `playgroundctl` -> the rootless playground daemon.

> Requires the `docker` role earlier in the play (it installs Docker and adds
> the Docker CE apt repo that provides `docker-ce-rootless-extras`).

## Architecture

```
Host
├── Host Docker daemon  /var/run/docker.sock   (read-only via `playgroundctl host …`)
├── User playground
│   ├── rootless Docker daemon
│   ├── /run/user/<uid>/docker.sock
│   └── /home/playground/workspace            (shared with claude-ctl, setgid)
├── playgroundctl broker (root, systemd)
│   ├── /run/playground-ctl.sock              (root:playgroundctl 0660)
│   └── write actions -> rootless playground daemon only
└── claude-ctl (AI runtime, uid 10001)
    └── gets ONLY /run/playground-ctl.sock, never docker.sock
```

## What the provisioning does

Mirrors the original `setup-playground-rootless-docker.sh`, split into task files:

| File | Does |
|---|---|
| `tasks/provision_packages.yml` | installs `uidmap`, `dbus-user-session`, `docker-ce-rootless-extras` (+ `acl`); verifies the rootless tooling |
| `tasks/provision_user.yml` | creates user `playground` (home, **password locked**, **not** in `docker`) and group `playgroundctl`; adds subuid/subgid (`231072:65536`); enables linger; creates the workspace (owner `playground`, **not** a symlink, setgid) |
| `tasks/provision_rootless.yml` | installs rootless Docker for `playground` (idempotent via `creates:`), enables/starts the `--user` docker service, verifies reachability |
| `tasks/broker.yml` | deploys the broker to `/opt/playground-ctl`, symlinks `/usr/local/bin/playgroundctl`, installs `playground-ctl.service`, asserts the socket is `root:playgroundctl 0660` |
| `tasks/stack.yml` | builds/starts the `claude-ctl` container (broker socket + shared workspace via `group_add`); handles Claude login + Remote Control |

Toggle provisioning with `playground_ctl_provision`; toggle the AI container with
`playground_stack_enabled`.

## The broker (`files/playground-ctl/`)

Thin Python runtime. At startup `preflight.py` hard-fails (and the service
crash-loops under `Restart=always`) if any precondition is missing — which never
happens on a correctly applied host, because Ansible provisions everything first.
Preconditions: `playground` user exists, `playgroundctl` group exists, workspace
exists and is not a symlink, rootless socket exists, rootless daemon reachable.

Policy (read-only host, full rootless playground, `playground/`-only image rm) is
fixed in `playground_ctl/policy.py` per task.md. The four identity values are
mirrored in `defaults/main.yml` as `(policy)` and must stay in sync.

## Agent guide

`templates/CLAUDE.md.j2` is rendered to `/home/playground/workspace/CLAUDE.md`
and mounted into the container as `/workspace/CLAUDE.md`, so Claude Code reads it
automatically. It tells the AI it has no direct Docker, lists the allowed
`playgroundctl` commands, and describes the build/run loop. Rendered with
`force: false` so the AI's own edits are preserved (delete it on the host to pick
up template changes).

## Smoke tests (from task.md)

```bash
# rootless daemon reachable
PG=$(id -u playground)
sudo -u playground env XDG_RUNTIME_DIR=/run/user/$PG \
  DOCKER_HOST=unix:///run/user/$PG/docker.sock docker ps

playgroundctl host ps           # read-only
playgroundctl host compose up   # blocked, no usage:
#   playgroundctl: permission denied: command 'compose up' is not allowed on daemon 'host'

docker ps                       # host containers
playgroundctl playground ps     # separate rootless daemon
```

## Environments (multiple deploy targets)

The playground provides **N independent environments** (default `pg1`, `pg2`,
`pg3`, set via `playground_environments`). **One** claude-ctl AI runtime + **one**
broker manage all of them; the session picks the target per action with
`--env <name>`. Each environment gets:

- its own workspace subdir `/home/playground/workspace/<env>`
- its own loopback port `playground_env_base_port + <index>` (pg1→8100, pg2→8101, …)
- its own Caddy route `<env>.<host>` (route `caddy_route_<env>`, external + basic_auth)

The broker resolves `--env` against a **root-owned allowlist**
(`environments.json`, rendered by Ansible from `playground_environments`). The
sandbox only ever sends the env *key* — never a path or a port — so the lock-down
model is preserved (no path traversal, broker provisions nothing).

```bash
playgroundctl playground envs                      # list envs + ports
playgroundctl playground compose up   --env pg2    # deploy to pg2
playgroundctl playground http get /   --env pg2    # verify pg2's app
```

On a fresh host the role seeds each env's subdir with a tiny **"Welcome to
Playground"** default app on that env's port and brings it up via the broker, so
every `<env>.<host>` route works out of the box. Replace
`/home/playground/workspace/<env>/docker-compose.yml` with any stack — keep one
service on that env's `127.0.0.1:<port>` if the public page should keep working.

### Adding an environment (e.g. `pg4`)

1. append `pg4` to `playground_environments`
2. copy the `caddy_route_pg3` block to `caddy_route_pg4` in
   `defaults/caddy_route.yml` and bump the port offset (+3)
3. add `pg4: external` to the host's `caddy_services`

then re-run the playbook. Toggle the default apps with
`playground_default_app_enabled`.
