# local_upgrade — upgrades started on the server itself

Lets an admin upgrade services from a phone or any browser: a small page on the server shows
what can be upgraded, asks for confirmation, runs the upgrade and shows the log. The upgrade
is done by the **same Ansible playbooks** the controller uses (`playbooks/*-upgrade.yml`),
with their verification and automatic rollback — they simply run on the host, against the
host.

Why on the host: nothing outside the server has to hold an SSH key or the vault (no CI
runner, no external automation service). The controller stays the place where things are
*changed*; this is only for *applying known updates* when the controller is not at hand.

Host-gated: nothing happens unless `local_upgrade_enabled: true` is set in host_vars.

## What it puts on the host (all root-only)

| Path | What |
|---|---|
| `/opt/ansible-local/repo` | snapshot of this repository, refreshed by every controller run |
| `/opt/ansible-local/config` | this host's config: an inventory with itself (`ansible_connection=local`), group_vars, its host_vars and a **filtered** copy of the vault |
| `/etc/app-upgrade/catalog.json` | what may be upgraded here and how (from `playbooks/vars/upgrade_catalog.yml` + `local_upgrade_services`) |
| `/usr/local/bin/app-upgrade` | the only entry point: validates, pre-checks, runs, renders the pages |
| `app-upgrade@<service>:<version>.service` | one systemd unit per run — it keeps running when the SSO or the monitoring stack restarts as part of the upgrade |
| `/etc/needrestart/conf.d/50-app-upgrade.conf` | keeps needrestart from restarting a running upgrade (it did: a server upgrade was killed after its package step and started over) |
| `/etc/webhook-upgrade.conf` | hooks for the distro's `webhook` service (loopback only) |
| `/var/lib/app-upgrade/` | last result per service, version records for the reconciliation |

The vault copy leaves out every key whose name matches `local_upgrade_vault_exclude`
(regexes, host_vars) — secrets of other hosts have no business here. Everything else the
host already holds in its services' config files.

## The page

`https://upgrade.<internal domain>/` — reachable only through the VPN, behind Authentik
forward-auth, admins only (so the SSO's MFA applies).

- **Overview**: per service the running version, the possible upgrades, the last result with
  a link to its log; first row: the server itself (pending packages, reboot required?).
- **Confirm page** per upgrade: findings now → in the new version (as scanned), what
  happens on failure, and the result of the pre-checks. After the click it follows the run
  and its Ansible log live.
- The version cells of the "Update overview per image" table in Grafana link here.

It is not a web application: `webhook` runs `app-upgrade` once per request and returns its
output. Nothing listens besides `webhook` on `127.0.0.1`.

## Late rollback

For 72 hours after an upgrade the overview shows "roll back to <previous version> (until …)"
next to the service. The confirm page says what goes back and what is lost — for Immich and
Nextcloud that both apps' data since the upgrade goes back, because they share one database
instance — and asks you to type `<service> rollback`. It runs
`playbooks/upgrade-rollback.yml` (see playbooks/README.md); `app-upgrade expire`
(hourly, `app-upgrade-expire.timer`) cleans up when the window is over.

## Blocked IPs

`/hooks/blocklist` lists the own firewall blocklist (roles/hardening, `blocklist`) and adds or
removes entries — all ports, also the Docker-published ones. Entries from
`hardening_blocked_sources` are managed by Ansible (shown, not removable here); entries added on
the page stay until removed on the page. The `blocklist` script refuses private / tailnet
ranges, this server, the fail2ban exceptions, the public addresses of the tailnet's devices, SSH
sources of the last 30 days and networks broader than /16 (IPv6 /32). Changes go through
`POST /hooks/blocklist-change` with the same Origin rule as upgrades. The Security dashboard
shows the list ("Blocked IPs") and the sources Spamhaus DROP and the blocklist drop.

## The snapshot page

`https://upgrade.<internal domain>/hooks/snapshots` (link in the page header) lists the btrfs
snapshots of every snapper config — the OS root and each data volume — with what deleting
each one would free (measured nightly by `snapshot-metrics`, `roles/monitoring`; Grafana has
the same data as the "Snapshots" dashboard). Per snapshot: **delete**, **keep** (exempt from
the automatic cleanup), **release** (hand a kept one back to the cleanup — not for the
permanent `ans_*` deploy checkpoints), **roll back**; per volume: take a snapshot now. Delete and roll back
ask you to type the volume and number.

A rollback is done by the host's `snapshot` helper (`btrfs-bootstrap.sh`), wrapped with what
the volume needs:

- **Data volume**: the containers that bind it (found from their mounts, plus
  `local_upgrade_snapshot_stop`) are stopped, the volume is swapped, they are started again;
  no reboot. For a volume holding a postgres instance with a WAL archive,
  `pg-pitr-backup --after-rollback` runs afterwards (otherwise archiving stalls). Follow-up
  commands per volume: `local_upgrade_snapshot_post`.
- **OS root**: swaps `@` and **reboots**. That takes the Docker images, container
  definitions, `/etc` and `/opt` back — but not the data volumes: an application upgraded
  since then would start in its old version on its newer database. Use a snapshot taken
  after the last upgrade, or take the data volumes back to the same time.

In both cases snapshots newer than the target are dropped, and the volume as it was is kept
as `<subvolume>_rollback_backup_<date>` until you delete it (listed on the page).
For "back to a few hours ago" of a database, PITR (`postgres-pitr-restore.yml`) is the
better tool: it is exact to the second and keeps the archive intact.

It is listed in the catalog as the pseudo service `snapshots` (`kind: snapshots`) and uses
the same start hook, run unit, lock and log as an upgrade.

## What it will and will not do

It runs the catalog's playbook for a service listed in `local_upgrade_services`, to a version
the nightly registry comparison found for what is running — nothing else. A caller cannot
choose playbooks, variables or arbitrary image tags.

Refused before anything starts: unknown service or version · another upgrade or a backup
running · less than 10 % free disk · for database-backed services no recent base backup or
a stalled WAL archive (the rollback would not be safe) · Docker Hub's anonymous pull limit
used up (the new image could not be downloaded).

Not offered on the host: services whose roles need the controller's caches (headscale,
headplane) and update kinds a catalog entry excludes via `targets` (e.g. a Postgres major
change).

## Security model

- Network: internal route only (VPN). Login: Authentik forward-auth, admin group, MFA.
- The start hook accepts only POSTs carrying the page's own `Origin` — another site cannot
  make a logged-in browser start something. Who started a run is recorded with it.
- `webhook` itself has no login and listens on loopback: a process on the host can start an
  upgrade to a known version (and nothing else). Do **not** expose its hooks through a
  Caddy `handle` in `caddy_extra` — `handle` is ordered before the forward-auth `route` and
  would bypass the login.
- While Authentik itself is being upgraded the page is unreachable (forward-auth asks it on
  every request). The run continues and rolls back on its own; reload the page afterwards.

## Enabling it on a host

1. host_vars: `local_upgrade_enabled: true`, and `local_upgrade_vault_exclude` for the vault
   keys this host must not get.
2. host_vars `caddy_services`: `upgrade: internal`.
3. `authentik_forward_auth_apps`: an entry `{ name: upgrade, display_name: "Updates",
   hostname: "{{ svc_edge_host.get('upgrade', '') }}" }` (admins only by default).
4. Deploy: `--tags local_upgrade,monitoring,caddy,authentik,headscale` (headscale: the
   internal DNS record of the new host name).

The repository snapshot and the config copy are refreshed by **every** `server-all.yml` run
(`--tags local_upgrade` is enough after changing a playbook).

## Versions: host and config repo

An upgrade started here cannot write to the private config repo. It records what it did in
`/var/lib/app-upgrade/versions/`; the next run from the controller takes it over into the
private config before anything is deployed (`playbooks/tasks/adopt_host_versions.yml`) and
says so — commit it then. Several upgrades in a row are handled as one step from the
version the repo still has.

## On the host

```bash
app-upgrade status                              # last results + what is running (JSON)
journalctl -t app-upgrade --since today         # the runs' Ansible output
systemctl list-units 'app-upgrade@*' --all      # the run units
app-upgrade request <service> <version>         # what the page's button does
```
