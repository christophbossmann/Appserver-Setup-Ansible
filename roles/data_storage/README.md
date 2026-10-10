# data_storage

Provisions the persistent **data-domain roots** (`business` / `dev` / `authentik`, defined
in `group_vars/all/storage.yml`) that the service roles write into.

Two layers, so it is **filesystem-agnostic**:

- **Always:** creates the domain directories under `data_root` (default `/srv`). Services
  place their data there regardless of filesystem.
- **On btrfs with snapshots enabled** (`btrfs_snapshots_enable` = `true` or `auto`+btrfs):
  each domain root becomes its own **subvolume** `@<name>` with a **flat**
  `@<name>_snapshots` (mounted at `<path>/.snapshots`) + a **snapper config** (daily
  timeline, kept `data_snapshot_timeline_daily` days) + **nodatacow** (`chattr +C`) on the
  DB subdirs. A system rollback then leaves this data untouched, and each domain can be
  snapshotted / rolled back on its own (`snapshot -c business rollback <N>` — no reboot).
- **`btrfs_snapshots_enable=true`** requires btrfs (asserts); **`auto`/`false`** run fine on
  ext4 (plain directories, no subvolumes, no snapshots).

Runs **before** the service roles (so the subvolumes + nodatacow exist before any DB
initialises), and between the two anchor snapshots in `server-all.yml`:
`ans_pre_setup` (permanent — full reset incl. data) → **data_storage** →
`ans_subvols_ready` (permanent — reinstall keeping data).

Idempotent: every step is guarded, safe to re-run. See `../../btrfs-setup.md`.
