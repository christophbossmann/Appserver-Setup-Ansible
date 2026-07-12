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

## Public route + default app (fixed port)

The host serves `playground.<host>` → `127.0.0.1:{{ playground_public_port }}`
(route `caddy_route_playground` in `roles/claude_playground/defaults/caddy_route.yml`, targeting
`localhost:8090`). On a fresh workspace the role seeds a tiny default
`docker-compose.yml` — an `http-echo` container printing **"Welcome to
Playground"** on that port — and brings it up via the broker, so the route works
out of the box.

Swap it freely: the AI (or you) can replace
`/home/playground/workspace/docker-compose.yml` with any stack. The only contract
is to keep one service published on `127.0.0.1:{{ playground_public_port }}` if
the public page should keep working. Toggle with `playground_default_app_enabled`;
change the port with `playground_public_port` (keep the Caddy route in sync).
