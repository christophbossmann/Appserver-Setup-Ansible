# Nextcloud import drop-folder

Drop the data files that the import manifest references here:

- **`.vcf`** — address books (contacts)
- **`.ics`** — calendars (events) and task lists (VTODO)

The manifest that maps files → accounts/collections is
`group_vars/all/nextcloud_imports.yml` (variable `nextcloud_imports`).

Notes:
- The `.vcf`/`.ics` files are **gitignored** (personal data); only this README is
  tracked, so a fresh clone starts empty.
- A file may hold **many** objects (a full export); it's split per object and each
  is uploaded over CardDAV/CalDAV, keyed by its **UID** — re-importing updates in
  place instead of duplicating.
- Import is **one-time**, guarded by a content hash: a normal deploy does nothing.
  Edit a file to trigger a re-import (updates/adds; never deletes).
- The target account is **auto-created** if missing (so imports work before a
  user's first login); toggle with `nextcloud_imports_create_users`.
- The same file can go to several accounts (repeat it per user) → each gets its
  own independent copy (not a live-shared collection).

Run a slice: `--tags nc_contacts`, `--tags nc_calendars`, `--tags nc_tasks`, or
all three with `--tags nc_imports`.
