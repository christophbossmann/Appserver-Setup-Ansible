#!/usr/bin/env bash
# Generate all Authentik secrets for group_vars/all/vault.yml.
#
#   bash stacks/authentik/generate-keys.sh
#
# Prints ready-to-paste vault_authentik_* lines. Copy them over the CHANGEME
# placeholders. The secret key signs sessions/tokens — NEVER change it on a live
# install (it invalidates all sessions). Needs: openssl.
set -euo pipefail
command -v openssl >/dev/null || { echo "openssl required" >&2; exit 1; }

# hex where the value ends up in a postgres:// URL (URL-safe).
PG_PASS=$(openssl rand -hex 24)
SECRET_KEY=$(openssl rand -base64 60 | tr -d '\n')
BOOTSTRAP_PASSWORD=$(openssl rand -hex 16)
BOOTSTRAP_TOKEN=$(openssl rand -hex 32)

# One OIDC client secret per native-OIDC app. Add app names here as you add them
# to authentik_oauth_apps (grafana, komodo, immich, pgadmin, …).
OAUTH_APPS="grafana immich komodo pgadmin"

cat <<EOF

# ---- paste into group_vars/all/vault.yml (replace the CHANGEME lines) ----
vault_authentik_postgres_password: "${PG_PASS}"
vault_authentik_secret_key: "${SECRET_KEY}"
vault_authentik_bootstrap_password: "${BOOTSTRAP_PASSWORD}"
vault_authentik_bootstrap_token: "${BOOTSTRAP_TOKEN}"
vault_authentik_smtp_password: ""
EOF
for app in $OAUTH_APPS; do
  echo "vault_authentik_oauth_${app}_secret: \"$(openssl rand -hex 32)\""
done
cat <<EOF
# --------------------------------------------------------------------------
# First login after deploy:  akadmin / ${BOOTSTRAP_PASSWORD}
EOF
