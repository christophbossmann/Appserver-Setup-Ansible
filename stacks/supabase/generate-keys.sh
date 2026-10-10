#!/usr/bin/env bash
# Generate all Supabase secrets for group_vars/all/vault.yml.
#
#   bash stacks/supabase/generate-keys.sh
#
# Prints ready-to-paste `vault_supabase_*:` lines. Copy them over the CHANGEME
# placeholders in group_vars/all/vault.yml. ANON_KEY / SERVICE_ROLE_KEY are
# HS256 JWTs signed with the generated JWT_SECRET — if you ever change the JWT
# secret, re-run this and replace BOTH keys too. Needs: openssl + python3.
set -euo pipefail

command -v openssl >/dev/null || { echo "openssl required" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 required" >&2; exit 1; }

# URL-safe (hex) where the value ends up inside a postgres:// URL.
POSTGRES_PASSWORD=$(openssl rand -hex 24)
JWT_SECRET=$(openssl rand -hex 32)                 # 64 chars (>=32)
DASHBOARD_PASSWORD=$(openssl rand -hex 16)
SECRET_KEY_BASE=$(openssl rand -hex 40)            # 80 chars (>=64)
VAULT_ENC_KEY=$(openssl rand -hex 16)              # 32 chars (exactly)
PG_META_CRYPTO_KEY=$(openssl rand -hex 24)         # 48 chars (>=32)
REALTIME_DB_ENC_KEY=$(openssl rand -hex 8)         # 16 chars (exactly)
S3_ACCESS_KEY_ID=$(openssl rand -hex 16)
S3_ACCESS_KEY_SECRET=$(openssl rand -hex 32)

# HS256 JWTs for the anon and service_role API keys, signed with JWT_SECRET.
# Pure-stdlib (hmac/base64) — no pyjwt needed. 10-year expiry.
gen_jwt() {
  local role="$1"
  JWT_SECRET="$JWT_SECRET" ROLE="$role" python3 - <<'PY'
import base64, hmac, hashlib, json, os, time
def b64(b): return base64.urlsafe_b64encode(b).rstrip(b'=')
secret = os.environ['JWT_SECRET'].encode()
now = 1700000000  # fixed base (avoids Date.now(); exp is far in the future)
header = {"alg": "HS256", "typ": "JWT"}
payload = {"role": os.environ['ROLE'], "iss": "supabase", "iat": now, "exp": now + 315360000}
signing_input = b64(json.dumps(header, separators=(',',':')).encode()) + b'.' + \
                b64(json.dumps(payload, separators=(',',':')).encode())
sig = hmac.new(secret, signing_input, hashlib.sha256).digest()
print((signing_input + b'.' + b64(sig)).decode())
PY
}

ANON_KEY=$(gen_jwt anon)
SERVICE_ROLE_KEY=$(gen_jwt service_role)

cat <<EOF

# ---- paste into group_vars/all/vault.yml (replace the CHANGEME lines) ----
vault_supabase_postgres_password: "${POSTGRES_PASSWORD}"
vault_supabase_jwt_secret: "${JWT_SECRET}"
vault_supabase_anon_key: "${ANON_KEY}"
vault_supabase_service_role_key: "${SERVICE_ROLE_KEY}"
vault_supabase_dashboard_password: "${DASHBOARD_PASSWORD}"
vault_supabase_secret_key_base: "${SECRET_KEY_BASE}"
vault_supabase_vault_enc_key: "${VAULT_ENC_KEY}"
vault_supabase_pg_meta_crypto_key: "${PG_META_CRYPTO_KEY}"
vault_supabase_realtime_enc_key: "${REALTIME_DB_ENC_KEY}"
vault_supabase_s3_access_key_id: "${S3_ACCESS_KEY_ID}"
vault_supabase_s3_access_key_secret: "${S3_ACCESS_KEY_SECRET}"
# --------------------------------------------------------------------------
EOF
