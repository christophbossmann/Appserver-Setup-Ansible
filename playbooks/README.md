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

## Upgrades (with automatic PITR rollback)

Both share the same safety model: because Nextcloud **and** Immich sit on the shared
`postgres-business` instance, **both apps are quiesced** for the whole window (Nextcloud
`maintenance:mode --on`; Immich native `immich-admin enable-maintenance-mode`). A PITR
**anchor** is captured, the image is bumped + verified, and on any failure the business
instance is rolled back to the anchor and the old image redeployed. Rollback logic is
shared via `tasks/{nextcloud,immich}_rollback.yml`.

| Playbook | What it does |
|---|---|
| **`nextcloud-upgrade.yml`** | `-e nextcloud_upgrade_version=<tag> -e upgrade_confirm=YES`. One major hop at a time. |
| **`immich-upgrade.yml`** | `-e immich_upgrade_version=<tag> -e upgrade_confirm=YES`. |

Shared options:
- **`-e force_rollback=true`** — DRILL mode: roll back at the end **even on success**
  (non-committal test of the full upgrade+rollback cycle).
- **`-e upgrade_health_retries=N`** — health/status poll attempts (×5 s); lower for drills.
- **`-e upgrade_keep_old=false`** — drop the pre-rollback data dir (default keeps it).

```bash
# Real upgrade
ansible-playbook playbooks/nextcloud-upgrade.yml \
  --limit myserver -e nextcloud_upgrade_version=32-apache -e upgrade_confirm=YES

# Non-committal drill (upgrades, verifies, then rolls back)
ansible-playbook playbooks/immich-upgrade.yml \
  --limit myserver -e immich_upgrade_version=v3.0.3 -e upgrade_confirm=YES \
  -e force_rollback=true
```

A failed upgrade ends with `failed=1, rescued=1` **by design** — the final task fails
loudly to signal "upgrade failed and was rolled back".

## `tasks/` — shared includes (not run directly)

| File | Used by |
|---|---|
| `pitr_restore.yml` | `postgres-pitr-restore.yml` and both upgrade rollbacks — the actual PITR steps (move data dir aside → extract base + WAL → recover to target → fresh base on the new timeline). |
| `nextcloud_rollback.yml` | `nextcloud-upgrade.yml` rescue **and** its `force_rollback` path. |
| `immich_rollback.yml` | `immich-upgrade.yml` rescue **and** its `force_rollback` path. |

## `tests/` — non-destructive verification (on-demand only)

`test-backup-restore.yml` (+ `-target.yml`, + `README.md`) restores each backup into a
throwaway scratch DB and asserts it's valid — **never touches live data**, never run by
`server-all.yml`. `-e verify_only=immich` to scope. See `playbooks/tests/README.md`.
