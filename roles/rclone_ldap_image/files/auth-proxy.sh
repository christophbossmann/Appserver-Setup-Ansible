#!/bin/sh
# rclone --auth-proxy hook. rclone runs this per login and passes
#   {"user":"...","pass":"...","public_key":"..."}  on stdin.
# We verify the credentials with an LDAP BIND against the Authentik LDAP outpost.
# On success -> print the backend config (exit 0); on failure -> exit non-zero.
#
# Env:
#   LDAP_BASE_DN    (required)  e.g. dc=n8n,dc=ldap,dc=example,dc=com
#   LDAP_URL        (optional)  default ldap://authentik-ldap:3389
#   AUTH_PROXY_ROOT (optional)  local dir rclone serves for the user, default /data
#
# Authentik enforces WHO may bind (the provider's group) — a user not in the group
# gets "Insufficient access", i.e. the bind fails and we deny. So this stays dumb:
# just bind, no group logic.

input=$(cat)
user=$(printf '%s' "$input" | jq -r '.user // empty')
pass=$(printf '%s' "$input" | jq -r '.pass // empty')

if [ -z "${LDAP_BASE_DN:-}" ]; then
  echo "auth-proxy: LDAP_BASE_DN not set" >&2
  exit 1
fi
LDAP_URL="${LDAP_URL:-ldap://authentik-ldap:3389}"
ROOT="${AUTH_PROXY_ROOT:-/data}"

# Empty user/pass -> deny (avoids an anonymous bind succeeding).
[ -n "$user" ] && [ -n "$pass" ] || exit 1

# ldapsearch (NOT ldapwhoami — the outpost doesn't implement the whoami extended op).
# A base-scoped search doubles as the bind check: succeeds only if bind + access OK.
if ldapsearch -x -LLL -H "$LDAP_URL" \
     -D "cn=${user},ou=users,${LDAP_BASE_DN}" -w "$pass" \
     -b "$LDAP_BASE_DN" -s base dn >/dev/null 2>&1; then
  printf '{"type":"local","_root":"%s"}\n' "$ROOT"
else
  exit 1
fi
