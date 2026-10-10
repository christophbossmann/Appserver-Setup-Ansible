# Avatars do NOT live here

Profile photos are personal data. They belong in the PRIVATE config next to the inventory:

    <private config>/avatars/<file>        # e.g. ../ansible-private/avatars/alice.jpg

Reference them per user in `authentik_users` (`avatar: alice.jpg`). The directory is
`authentik_avatar_dir` (default: `<inventory dir>/avatars`). Square, ~128 px, png or jpg.
This folder is git-ignored for images as a safety net.
