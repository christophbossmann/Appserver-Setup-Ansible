# playbooks/tests/ — TEST & VERIFICATION playbooks (NOT deployed)

Everything in this directory is a **test / verification** playbook. These are the
opposite of a deploy:

- They are **never** included or run by the productive playbook (`playbooks/server-all.yml`).
- They are **non-destructive**: they only read state (and restore into throwaway
  scratch resources that they clean up again). Live databases and files are never
  touched.
- Run them **on demand** to gain confidence, or wire them into cron/CI — they exit
  non-zero on failure.

This is the marker that keeps them from being confused with the productive roles in
`roles/` (which ARE rolled out). Nothing here belongs in `roles/`, and no role in
`roles/` is a test.

| Playbook | What it proves | Destructive? |
|----------|----------------|--------------|
| `test-backup-restore.yml` | The local pull backups (`./backups/<host>/<app>/`) actually restore: each app's DB dump is restored into a throwaway `verify_restore_<app>` DB, checked, then dropped; the file/photo backup is checked for content. | No |
| `test-snapshot-rollback.yml` | The btrfs snapshot→rollback cycle works: on a throwaway `/var/tmp` scratch subvolume it snapshots, writes a file, rolls back (delete + re-create from the ro snapshot), and asserts the pre-snapshot file survived while the post-snapshot file is gone. No reboot, root `@`/snapper history untouched. Does NOT test the production root-rollback path (`btrfs-rollback_LEGACY.sh`, which needs a reboot). | No |

## vs. the operational playbooks (in `playbooks/`, NOT here)

Those DO act on live data and are run deliberately when you actually need them:

- `backup-pull.yml` — pull a real local backup to the controller.
- `nextcloud-restore-local.yml` / `nextcloud-restore.yml` — a real (destructive) restore.
- `backup-setup.yml` — one-time cloud (rclone) setup.

Run a test:

```bash
ansible-playbook playbooks/tests/test-backup-restore.yml --limit <host>
ansible-playbook playbooks/tests/test-backup-restore.yml --limit <host> -e verify_only=immich
```
