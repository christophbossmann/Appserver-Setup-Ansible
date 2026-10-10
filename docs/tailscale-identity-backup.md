# Tailscale identity (tailscaled.state) backup — removed, and how to bring it back

**Status:** removed from the `tailscale` role on 2026-09-27. This file preserves the
mechanism so it can be reintroduced verbatim when it is actually needed.

## What it did

A Tailscale node's identity (its private node key) lives in
`/var/lib/tailscale/tailscaled.state`. The `tailscale` role used to **round-trip** that
file through the Ansible controller:

- **Export** (default ON, after every run): `fetch` the live `tailscaled.state` to the
  controller at `~/.ansible/tailscale-state/<host>.state`.
- **Import** (auto, on the next run): if the node is **not** currently logged in and a
  backup exists at that path, copy it back into place and restart `tailscaled` — so the
  node keeps its **exact identity/IP without re-registering** (no new auth key, no new
  device entry). A **live login always wins** (never re-imported over a working node),
  and `tailscale_reauth_force=true` overrides import.

So on a host rebuild the node came back as the *same* Tailscale identity automatically.

## Why it was removed

This deployment's tailnet is a **self-hosted Headscale that is co-located on the node
itself**. In that setup the backup is at best redundant and at worst a footgun:

- Re-enrolment is cheap: reusable **1-year pre-auth keys** are cached on the controller
  (`~/.ansible/headscale-keys/<host>/<user>.preauthkey`), so a rebuilt node just runs
  `tailscale up --login-server … --authkey …`.
- A changed IP **self-heals**: the `.int` DNS records (`headscale_int_dns`) and Caddy's
  `tailscale0` bind are **derived from the live `tailscale ip -4`**, so
  `--tags headscale,caddy` re-derives everything if the node gets a new address.
- A **stale** backup would be auto-imported on rebuild and could restore an *outdated*
  identity (e.g. a pre-cutover Tailscale-cloud state) instead of enrolling cleanly.

## When to bring it back

Reintroduce it for any tailnet whose **control server is NOT co-located / not yours**,
where re-enrolling is costly or you must preserve identity:

- The **Tailscale cloud** (device limits, manual approval, you don't own the coordinator).
- A **remote Headscale** on a different machine.
- A future **dev↔prod cross-join**, where dev's node joins prod's tailnet.

**Same role or a separate one?** For a *single* client pointed at a foreign controller it
is the **same role** — the only differences (login-server URL, authkey source, whether to
preserve identity) are all **vars**, not different logic. A genuinely *separate* building
block is only needed for the **simultaneous cross-join** (two tailnets live on one host at
once): that requires a **second `tailscaled` instance** (dual-homing, network namespaces,
CGNAT `/10` route conflicts) — materially different plumbing.

## How to reintroduce (verbatim)

### 1. `roles/tailscale/defaults/main.yml` — re-add the vars

```yaml
# --- State import (auto) ----------------------------------------------------
tailscale_state_file: "~/.ansible/tailscale-state/{{ inventory_hostname }}.state"

# --- State export (default ON) ----------------------------------------------
tailscale_export_state: true
tailscale_state_export_path: "~/.ansible/tailscale-state/{{ inventory_hostname }}.state"

# Backward-compat override for the export destination.
tailscale_state_backup_path: ""
```

### 2. `roles/tailscale/tasks/main.yml` — re-add the backup check + import + export

Replace the simplified `Decide the identity action` task with the backup-aware version,
add the import block after the "keep existing login" debug, and add the export task at the
very end of the `Configure, connect and export Tailscale` block.

```yaml
    # --- check for a controller-side backup at tailscale_state_file ---------
    - name: Check for a state backup on the controller
      ansible.builtin.stat:
        path: "{{ tailscale_state_file }}"
      delegate_to: localhost
      become: false
      register: tailscale_state_backup
      when: tailscale_state_file | length > 0

    - name: Decide the identity action
      ansible.builtin.set_fact:
        # import only when: not already logged in, not forcing reauth, and a backup exists
        tailscale_do_import: "{{ (not tailscale_logged_in) and (not (tailscale_reauth_force | bool)) and (tailscale_state_file | length > 0) and (tailscale_state_backup.stat.exists | default(false)) }}"
        # interactive auth when: forcing reauth, OR (not logged in AND no backup to import)
        tailscale_needs_auth: "{{ (tailscale_reauth_force | bool) or ((not tailscale_logged_in) and not ((tailscale_state_file | length > 0) and (tailscale_state_backup.stat.exists | default(false)))) }}"

    # (after the "Keep the existing Tailscale login" debug task)

    # --- import the controller backup onto the server ----------------------
    - name: Import Tailscale state from the controller backup
      when: tailscale_do_import | bool
      block:
        - name: Stop tailscaled before restoring state
          ansible.builtin.systemd:
            name: tailscaled
            state: stopped
          failed_when: false

        - name: Ensure tailscale state directory exists
          ansible.builtin.file:
            path: /var/lib/tailscale
            state: directory
            owner: root
            group: root
            mode: "0700"

        - name: Copy the backup state file into place
          ansible.builtin.copy:
            src: "{{ tailscale_state_file }}"
            dest: /var/lib/tailscale/tailscaled.state
            owner: root
            group: root
            mode: "0600"

        - name: Start tailscaled with the restored state
          ansible.builtin.systemd:
            name: tailscaled
            state: started

        - name: Confirm Tailscale identity after import
          ansible.builtin.debug:
            msg: "Imported Tailscale state from {{ tailscale_state_file }} — node identity restored without a new auth key."

    # (at the very end of the block, after the final status tasks)

    # --- export state back to the controller (default ON) ------------------
    - name: Export tailscaled state back to the controller
      ansible.builtin.fetch:
        src: /var/lib/tailscale/tailscaled.state
        dest: "{{ tailscale_state_backup_path if (tailscale_state_backup_path | length > 0) else tailscale_state_export_path }}"
        flat: true
      when: tailscale_export_state | bool
```

The identity decision order it restores: (a) already logged in → keep; (b) reauth_force →
interactive auth; (c) backup exists → import (no prompt); (d) otherwise → interactive auth.
