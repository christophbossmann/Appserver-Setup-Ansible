# Playbooks

Entry points for this repo. Everything targets a host from the inventory (`../ansible-private/inventory.ini`, set in `ansible.cfg`) — always
pass `--limit <host>` (e.g. `--limit myserver`) so you act on exactly one
machine. Most day-to-day work is `server-all.yml` with `--tags`.

> **Note:** `server-all.yml` was previously named `server-n8n.yml`. It installs the
> whole stack, not just n8n, hence the rename.

> **SSH key with a passphrase:** Ansible can't ask for it — run every playbook under a
> one-shot ssh-agent (passphrase asked ONCE, the agent ends with the command):
>
> ```bash
> ssh-agent bash -c 'ssh-add ~/.ssh/ansible_key && ansible-playbook playbooks/server-all.yml --limit <host>'
> ```
>
> Or once per terminal session (valid until the terminal is closed):
> `eval "$(ssh-agent -s)" && ssh-add ~/.ssh/ansible_key`
>
> Each server playbook starts with [`preflight-ssh-agent.yml`](preflight-ssh-agent.yml),
> which fails fast — before any SSH connection — if the key isn't in an agent and prints
> this exact command for the current run. Details: [QUICKSTART § 5](../QUICKSTART.md#5-deploy).

## Site / deploy

| Playbook | What it does |
|---|---|
| **`server-all.yml`** | The main site playbook — installs/converges **everything** (all `stacks/` + roles: postgres, postgres-business, authentik, immich, nextcloud, n8n, komodo, pgadmin, monitoring, caddy, PITR, backups, …). Idempotent; scope with `--tags`. |
| **`server-base.yml`** | Base machine setup only: hardening, users, Docker, Tailscale. Run once per new host before `server-all.yml`. |
| **`server-proxy.yml`** | Installs Caddy + the `cloudflared` package. Attaches a Cloudflare tunnel only with `-e cloudflared_service_install=true -e cloudflared_tunnel_connector_token=…`. |
| **`server-immich.yml`** | Test/subset: postgres + immich + immich_uploader only. |
| **`server-playground.yml`** | Test/subset: the Claude Playground (Supabase + playground-ctl) with its Caddy route. |
| **`preload-images.yml`** | Pre-pulls the Docker images so a later `server-all.yml` starts containers in seconds instead of pulling. |

```bash
# Full converge of the dev host
ansible-playbook playbooks/server-all.yml --limit myserver

# Just one component
ansible-playbook playbooks/server-all.yml --limit myserver --tags nextcloud
```

## Backups

| Playbook | What it does |
|---|---|
| **`backup-setup.yml`** | Interactive one-time setup of the restic-over-rclone **cloud** backup (OneDrive/pCloud). See `roles/backup_restic/README.md`. |
| **`backup-pull.yml`** | On-demand **pull** of backups to the controller into `./backups/<host>/<app>/` (gitignored). Generic over `backup_jobs`; select with `-e backup_only=immich` (comma-separated) or `full`. Takes its own consistent dump. |
| `pull_backup_job.yml` | Helper task file included per job by `backup-pull.yml` — **not run directly**. |

The scheduled cloud backups themselves live in `roles/backup_restic` (systemd timer);
the job list (what gets dumped + which paths) is `group_vars/all/backup.yml`.

Hosts with a Storage Box can additionally keep copies **on the box** without the controller
(`roles/storagebox_backup`, host-gated by `storagebox_backup_enabled`): the immich
thumbnails are mirrored nightly and a `pg_dump` of each listed database is written every
4 days (last 4 sets kept). Operate it on the host with `storagebox-backup list |
restore-thumbs | fetch-dumps | dumps --force`.

## Restore

| Playbook | What it does |
|---|---|
| **`nextcloud-restore.yml`** | Restore Nextcloud from the **restic** repo (cloud). Guard: `-e nc_restore_confirm=YES`. Destructive; maintenance-mode → restic restore → rsync data+config → `pg_restore --clean` → ownership safety-net → `files:scan`. |
| **`nextcloud-restore-local.yml`** | Same, but restores from the **local** `./backups/<host>/nextcloud/` pulled by `backup-pull.yml` (no restic needed). |
| **`immich-restore-local.yml`** | Restore Immich from the **local** `./backups/<host>/immich/`. Guard: `-e immich_restore_confirm=YES`. Stops immich → rsync the upload dir back (`--delete`) → `pg_restore --clean` the `immich` DB on `postgres-business` (ownership safety-net) → restart + wait healthy. Scope with `-e immich_restore_files=false` / `-e immich_restore_db=false`. Only touches the immich DB, so Nextcloud (same instance) is unaffected. |
| **`postgres-pitr-restore.yml`** | **DESTRUCTIVE** Point-in-Time Recovery of one postgres instance (whole cluster) to a target time from base backup + WAL. `-e pitr_instance=<name> -e pitr_target_time='YYYY-MM-DD HH:MM:SS+00' -e pitr_confirm=YES`. Also the manual rollback lever for a botched upgrade. |

> Immich's cloud restore (from restic) isn't a dedicated playbook yet — the local one
> above + PITR on `postgres-business` + the restic `immich` job cover recovery. After any
> immich DB restore/rollback, `isAdmin` reverts to the backup era; it self-heals on the
> next Authentik login (immich_role claim), or use `immich-admin grant-admin` as break-glass.

## Upgrades (with automatic rollback)

Every upgrade playbook follows the same model: take a way back, switch to the new version,
**verify that the service really works**, and on any failure put the old state back and end
with `failed=1, rescued=1` — by design, so a rolled-back upgrade cannot be mistaken for a
successful one. The new version is written to the private config only after every check
passed.

| Playbook | Upgrades | Way back |
|---|---|---|
| **`immich-upgrade.yml`** | Immich. `-e immich_upgrade_version=<tag>` | PITR of `postgres-business` + old image |
| **`nextcloud-upgrade.yml`** | Nextcloud, **one major at a time**. `-e nextcloud_upgrade_version=<tag>` | PITR of `postgres-business` + the pre-upgrade program dir (`html`: the official image keeps the code there, and the old image refuses to start on newer code) + old image |
| **`authentik-upgrade.yml`** | Authentik server, worker and LDAP outpost together. `-e authentik_upgrade_version=<tag>` | PITR of the `authentik` instance + old images |
| **`postgres-business-upgrade.yml`** | The shared database **image** of immich + nextcloud within the same Postgres major: Postgres minor, OS packages, pgvector/VectorChord. Runs `ALTER EXTENSION … UPDATE` and rebuilds the VectorChord indexes. `-e postgres_business_upgrade_version=<tag>` | old image first, then PITR to the moment before |
| **`stack-upgrade.yml`** | Everything without a database of its own (grafana, prometheus, exporters, cadvisor, dozzle, headplane) and services with file-based state (headscale, acme-dns: the state dir is copied before and put back). `-e upgrade_service=<name> -e upgrade_version=<tag>` | previous image (+ state copy) |
| **`host-upgrade.yml`** | The server itself: all apt sources (Docker, Tailscale, Ubuntu), a newer **Caddy** build with the same DNS plugins (validated against the live Caddyfile before it is swapped in), optionally a reboot. `-e host_upgrade_mode=packages \| packages-reboot \| reboot` | snapper pre/post snapshot pair of `/`; the previous Caddy binary is put back automatically if Caddy does not come up |

All of them need `-e upgrade_confirm=YES`. Because Nextcloud **and** Immich share the
`postgres-business` instance, both are write-frozen (maintenance mode) whenever that
instance could be rolled back — in the immich, nextcloud and postgres-business upgrades.

Shared options:
- **`-e force_rollback=true`** — DRILL: roll back at the end **even on success**. Run this
  once before trusting a playbook with a real upgrade; it exercises the whole way back.
  (Not for `host-upgrade.yml`.)
- **`-e upgrade_health_retries=N`** — health/status poll attempts (×5 s); lower for drills.
- **`-e upgrade_keep_old=false`** — drop the pre-rollback data dir (default keeps it for
  inspection: `<data dir>-pre-pitr-<stamp>`; delete it once you no longer need it).

```bash
# Real upgrade
ansible-playbook playbooks/nextcloud-upgrade.yml \
  --limit myserver -e nextcloud_upgrade_version=32.0.5-apache -e upgrade_confirm=YES

# Non-committal drill (upgrades, verifies, then rolls back)
ansible-playbook playbooks/immich-upgrade.yml \
  --limit myserver -e immich_upgrade_version=v3.0.3 -e upgrade_confirm=YES \
  -e force_rollback=true

# A service from the catalog
ansible-playbook playbooks/stack-upgrade.yml \
  --limit myserver -e upgrade_service=grafana -e upgrade_version=13.2.3 -e upgrade_confirm=YES
```

### Late rollback (up to 72 h after an upgrade)

For when something turns out not to work only later. Every kept upgrade leaves a record on
the host (`/var/lib/app-upgrade/rollback/<service>.json`) with what it takes to go back;
`upgrade-rollback.yml` uses it:

```bash
ansible-playbook playbooks/upgrade-rollback.yml --limit myserver -e rollback_service=nextcloud -e upgrade_confirm=YES
```

(or the "roll back to …" link on the on-host upgrade page). What goes back:

| Service | How |
|---|---|
| immich, nextcloud, postgres_business | They share the `postgres-business` instance, so it goes back **as a whole** (PITR to the upgrade's anchor) — both apps lose their database changes since then. Their data volumes go back to btrfs snapshots taken at the anchor (catalog `snapshots:`), so files and database match. Immich originals on the Storage Box uploaded since are imported again. Any other service on the instance upgraded after the anchor goes back with it. |
| authentik | PITR of its own instance + the previous images. |
| everything else | The previous image, plus the state copy taken before the upgrade (catalog `state_paths:`). |

The previous images are pulled first — if that fails, nothing is changed. The window is
`rollback_window_hours` (default 72); `app-upgrade expire` (hourly timer) then removes the
record, the snapshots and the state copies. A drill (`force_rollback`) or a failed upgrade
leaves no record.

### The upgrade catalog

[`vars/upgrade_catalog.yml`](vars/upgrade_catalog.yml) is the single place that says **how**
each service is upgraded: which playbook, which variable in which private config file holds
the version, which containers must run the new image afterwards, optional service-specific
checks (`verify`), state dirs to copy, and which kinds of update may be started on the host
(`targets`). Adding a service to `stack-upgrade.yml` is one entry there.

### Upgrades started on the server (no controller)

With `local_upgrade_enabled` (host_vars) the host can run these same playbooks itself,
triggered from a small web page behind the VPN and the SSO login — see
[`roles/local_upgrade/README.md`](../roles/local_upgrade/README.md).

Such an upgrade cannot write to the private config repo, so every upgrade leaves a record on
the host (`/var/lib/app-upgrade/versions/<var>.json`), and **every run from the controller
first reconciles**: a version upgraded on the host is taken over into the private config
(commit it afterwards) instead of being silently rolled back to what the repo still says.
If repo and host contradict each other the run stops; `-e version_conflict_ok=true` lets
the repo win on purpose.

## `tasks/` — shared includes (not run directly)

| File | Used by |
|---|---|
| `pitr_restore.yml` | `postgres-pitr-restore.yml` and the upgrade rollbacks — the actual PITR steps (move data dir aside → extract base + WAL → recover to target → fresh base on the new timeline). |
| `nextcloud_rollback.yml`, `immich_rollback.yml`, `authentik_rollback.yml`, `postgres_business_rollback.yml` | the respective upgrade's rescue **and** its `force_rollback` path. |
| `stack_upgrade_deploy.yml`, `stack_upgrade_rollback.yml` | `stack-upgrade.yml`: (re)deploy + verify, used for the upgrade and for the rollback alike. |
| `rollback_prepare.yml`, `rollback_record.yml`, `volume_snapshot_rollback.yml` | the late rollback: snapshots at the anchor, the record, swapping a data volume back. |
| `record_version.yml`, `adopt_host_versions.yml` | version records on the host and their reconciliation with the private config (see above). |

## `tests/` — non-destructive verification (on-demand only)

`test-backup-restore.yml` (+ `-target.yml`, + `README.md`) restores each backup into a
throwaway scratch DB and asserts it's valid — **never touches live data**, never run by
`server-all.yml`. `-e verify_only=immich` to scope. See `playbooks/tests/README.md`.
