# btrfs_snapshots

Takes **deploy-time snapshots** — `ans_pre_run` at the start of every `server-all.yml` run
(the newest `btrfs_pre_run_keep`, default 5, are kept) plus the one-time anchors
`ans_pre_setup` / `ans_subvols_ready` / `ans_after_ansible` (after the first complete run) via **snapper** on a host that is **already prepared** for btrfs
snapshots. It does **not** set anything up — no filesystem conversion, no snapper install,
no grub-btrfs. That one-time foundation (the `@`-layout + snapper + grub-btrfs, which needs
a reboot and is a one-way migration) is done **outside Ansible** by
[`../../btrfs-bootstrap.sh`](../../btrfs-bootstrap.sh) — see [`../../btrfs-setup.md`](../../btrfs-setup.md).

The play calls the role once per checkpoint with a `btrfs_snapshot: <label>` var; each call
creates that one snapshot via the server's `snapshot` helper command (`snapshot create
--cleanup number <label>_<timestamp>`) — the same tool you use by hand — so the mechanism is
consistent everywhere. grub-btrfsd adds the snapshot to the GRUB menu on its own.

## The switch — `btrfs_snapshots_enable`
| value | behaviour |
|---|---|
| `true` | this host **is** a btrfs-snapshots host — **fail** if it isn't set up (run `btrfs-bootstrap.sh`) |
| `auto` | **default** — create the checkpoint when the host is set up, else **skip with a warning** |
| `false` | **always skip** (no checkpoints) |

Set `btrfs_snapshots_enable: true` in the host's `host_vars` for real btrfs hosts (so a
missing/broken setup is caught loudly); leave it at `auto` for non-btrfs hosts, where every
checkpoint is a safe no-op.

It also keeps the **retention of the root snapshots** at `btrfs_root_retention` (default:
daily snapshots for 14 days, the last 10 package-run/upgrade snapshots (5 pre/post pairs); permanent ones stay).

## Key variables (see `defaults/main.yml`)
- `btrfs_check_path` (default `/`) — the mount whose FS type decides btrfs-or-not.
- `btrfs_checkpoint_config` (default `root`) — the snapper config the checkpoints use
  (must already exist, created by the bootstrap script).
- `btrfs_checkpoint_skip_if_exists` (default `true`) — at most one `ans_<label>_*` per host;
  set `false` to create a fresh timestamped snapshot every run.

## Manage snapshots
```bash
snapper -c root list                 # list snapshots (incl. the ans_* checkpoints)
snapper -c root create -d "manual"   # manual snapshot
snapper -c root undochange N..0      # revert FILE changes since snapshot N (no reboot)
snapper -c root modify -c "" N       # make snapshot N permanent (drop cleanup algorithm)
```
**Full rollback:** `snapper rollback` does **not** work on this Ubuntu `@`-layout (fstab +
GRUB hardcode `subvol=@`, so it can't switch the default subvolume — "Cannot detect ambit").
Use the installed `snapshot rollback <N>` tool instead (on the server, or
`ssh <host> sudo snapshot rollback <N>` from the controller) — it swaps the `@`
subvolume for a fresh copy of the snapshot (one command, from the running system, then
reboots). On a
headless VPS you can also boot **read-only** into any snapshot from the **GRUB menu** via the
provider's **VNC console at boot** (grub-btrfs), just to inspect it. See `../../btrfs-setup.md` §5.

**Overview + space:** the "Snapshots" dashboard in Grafana (`roles/monitoring`,
`snapshot-metrics`) shows every snapshot of every config with the space deleting it would
free. On hosts with `roles/local_upgrade` the same list is on the portal's snapshot page,
with delete / keep / roll back per snapshot.

## Notes
- On non-btrfs hosts with `auto` the role prints a warning and does nothing — safe to keep
  wired into the site playbook.
- Snapshots are same-disk, point-in-time — NOT an off-site backup. Pair with `btrfs
  send/receive` (or restic) for off-site.
