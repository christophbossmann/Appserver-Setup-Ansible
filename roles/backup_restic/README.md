# backup_restic — per-app cloud backup + selective restore

restic repository on a public cloud provider (OneDrive/pCloud — provider-agnostic
via rclone), client-side encrypted, deduplicated, nightly systemd timer.

## Model: one backup, per-app restore

Each `backup_jobs` entry becomes its **own restic snapshot** (tagged with the app
name). A nightly run backs up everything enabled; **restore is always selective** —
you choose which app comes back, and files land in a **staging dir** first
(`/opt/backups/restore/<app>`), never straight over live data.

## Enabling the nightly cloud upload (one-time)

1. On any machine with a browser: `rclone authorize "onedrive"` (or `"pcloud"`).
2. Paste the printed remote block as `[cloudbackup]` into `vault_rclone_backup_conf`.
3. `backup_cloud_enabled: true`, then `ansible-playbook … --tags backup`.

`vault_restic_password` is the repo key — **keep a copy off the host** (losing it =
losing every backup). `backup_include_authentik: false` drops the Authentik DB from
the backup (fine when you rely on declarative users).

## Manual run

```bash
systemctl start backup-restic.service     # run now
journalctl -u backup-restic -f            # watch
```

## Local pull to the controller (no cloud, no restic)

`playbooks/backup-pull.yml` fetches a fresh, consistent copy straight onto the
**controller** — independent of restic/cloud. It reuses the same `backup_jobs`
(per-app pre-dumps + paths), so you get the DB dumps AND the data (immich photos,
Nextcloud files) locally.

```bash
ansible-playbook playbooks/backup-pull.yml --limit <host>
#   subset:  -e backup_only=immich            (comma-separated for several)
#   flaky link:  ANSIBLE_SSH_RETRIES=5 ansible-playbook ...
```

Lands under `backup_pull_dir` — here `/mnt/d/backup/<host>/` = **`D:\backup\<host>\`**
(a Windows drive via its WSL mount, keeps the WSL/C: disk free). Each `<app>` gets
`<app>-db/db.dump` + its data dirs (`immich/immich/library`, `nextcloud/data` + `config`,
plus the Nextcloud DAV `.ics`/`.vcf` export). This copy lives **off the VM**, so it
survives a full server loss OR a **netcup snapshot restore** (which reverts the whole
VM disk incl. photos) — always pull one BEFORE any risky reset.

Restore this local pull with `playbooks/immich-restore-local.yml` /
`playbooks/nextcloud-restore-local.yml` (`-e <app>_restore_confirm=YES`).

### Why re-pulls are cheap — rsync is incremental (photos aren't downloaded twice)

The pull runs `rsync -az --delete --modify-window=1` per path (see
`playbooks/pull_backup_job.yml`). rsync compares **each file by path** and, by default,
**skips** any file already at the destination with the **same size and mtime** — only
NEW or CHANGED files cross the wire. So the immich library (append-mostly) transfers
only the photos added since the last pull; the hundreds of MB already on `D:` are
skipped, never re-fetched.

- `-a` (archive) preserves mtimes → enables the size+mtime quick-check.
- `-z` compresses data in transit.
- `--delete` mirrors the source (removes local files no longer on the server).
- `--modify-window=1` tolerates the coarse timestamps of the Windows/DrvFs mount, so a
  re-pull doesn't needlessly re-transfer unchanged files over a 1-second rounding.
- `-e backup_pull_checksum=true` switches to content-HASH comparison (`--checksum`):
  never re-transfers identical content even if the mtime differs, but reads every file
  on both ends each run (slower). Rarely needed.

Because rsync is per-path, an already-backed-up photo is a no-op on the next pull —
nothing is ever stored or downloaded twice.

## Restore (on the server, as root)

```bash
backup-restore list                 # every snapshot (tag = app)
backup-restore list nextcloud       # just nextcloud snapshots

backup-restore files nextcloud            # latest -> /opt/backups/restore/nextcloud
backup-restore files nextcloud <snapID>   # a specific snapshot

backup-restore db nextcloud               # restore only the DB dump + PRINT the
                                          # exact (destructive) pg_restore command
```

`files` restores into staging; you then copy what you actually want into place
(e.g. `rsync -a /opt/backups/restore/nextcloud/opt/nextcloud/data/  /opt/nextcloud/data/`).
`db` never runs the restore itself — it prints the `pg_restore … --clean` line for
you to run when you are sure.

### Nextcloud full restore (order)

```bash
backup-restore files nextcloud                 # get data/ + config/ + db.dump
docker exec -u www-data nextcloud php occ maintenance:mode --on
rsync -a /opt/backups/restore/nextcloud/<...>/data/   /opt/nextcloud/data/
rsync -a /opt/backups/restore/nextcloud/<...>/config/ /opt/nextcloud/html/config/
backup-restore db nextcloud                    # then run the printed pg_restore line
docker exec -u www-data nextcloud php occ maintenance:mode --off
docker exec -u www-data nextcloud php occ files:scan --all
# Re-converge apps/config to the DECLARED state (adds apps you defined in Ansible
# that predate this backup; restores OIDC/avatar wiring):
ansible-playbook playbooks/server-all.yml --tags nextcloud --limit <host>
```

**Data vs. configuration.** The DB dump is authoritative for app-enabled STATE,
users, shares and file metadata — `pg_restore --clean` fully replaces the live DB,
so after a restore the app set reverts to the backup era. The final Ansible run
above re-asserts the DECLARED apps (`nextcloud_apps_enabled`), so anything you
added in Ansible after that backup is (re)installed; nothing is removed unless it
is in `nextcloud_apps_disabled`. Fresh-install-then-restore and
restore-onto-existing end up identical, because the DB restore replaces state
either way — the fresh install's default apps do not "survive", they are part of
the replaced DB.

### Identity note (SSO)

Nextcloud accounts key on the **username** (uid mapping = `preferred_username`),
not the Authentik `sub`. So a restored/declaratively-recreated Authentik with the
same usernames re-links on login **without** restoring the Authentik DB. Restore
Authentik's DB only if you need non-declarative UI users, MFA enrolments or tokens
back — and then restore it alongside the same-era Nextcloud data.
