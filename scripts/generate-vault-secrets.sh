#!/usr/bin/env bash
# =============================================================================
# generate-vault-secrets.sh — (re)generate group_vars/all/vault_secrets.yml FRESH
#
# vault_secrets.yml holds the MACHINE-GENERATED half of the vault: values no human
# ever needs to know or type (DB passwords, OIDC client secrets, signing/encryption
# keys, API tokens, salts). The USER/EXTERNAL half stays in group_vars/all/vault.yml
# (login passwords, external API keys, the Storage Box SSH key, EAB creds, …).
#
# WHAT THIS DOES: writes a COMPLETE, freshly-generated vault_secrets.yml — ALL values
# are new random secrets. Use it ONLY when you WANT new values: bootstrapping a brand
# new environment, or a full secret rotation. It is NOT part of the normal deploy.
#
# ⚠️  OVERWRITE = ROTATION: running this against an existing setup REPLACES every
#     secret (new DB passwords, new keys …) — the running services would then break
#     until redeployed/migrated. Therefore it REFUSES to overwrite an existing file
#     unless you pass --force. There is no "merge/preserve" mode here on purpose.
#
# ┌─────────────────────────────────────────────────────────────────────────────┐
# │  ‼️  KEEP THIS SCRIPT IN SYNC  ‼️                                             │
# │  EVERY new machine secret you add to the codebase (a new service's DB       │
# │  password, OIDC secret, signing key, …) MUST get a line in the SECRETS      │
# │  table below. If you forget, a freshly generated vault_secrets.yml will be  │
# │  MISSING that secret and a fresh install will break. Treat "add a vault_*   │
# │  machine secret" and "add it to this script" as ONE step.                   │
# └─────────────────────────────────────────────────────────────────────────────┘
#
# After running: review, then `ansible-vault encrypt group_vars/all/vault_secrets.yml`
# before committing.
# Usage:  ./scripts/generate-vault-secrets.sh [--force]
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/group_vars/all/vault_secrets.yml"
FORCE=0
[[ "${1:-}" == "--force" || "${1:-}" == "-f" ]] && FORCE=1

command -v openssl >/dev/null || { echo "openssl required"; exit 1; }
command -v python3 >/dev/null || { echo "python3 required"; exit 1; }

if [[ -f "$OUT" && $FORCE -ne 1 ]]; then
  echo "REFUSING: $OUT already exists."
  echo "This script REGENERATES ALL secrets (a full rotation) — it would overwrite the"
  echo "live values and break running services until redeployed. Re-run with --force"
  echo "only if you really want brand-new secrets."
  exit 1
fi

# --- generators ---------------------------------------------------------------
gen_hex()  { openssl rand -hex "$1"; }                                    # $1 bytes -> 2*$1 hex chars
gen_pw()   { LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c "$1"; }   # alphanumeric password
gen_sb_jwt() {                                                             # HS256 JWT signed with jwt_secret
  python3 - "$1" "$2" <<'PY'
import sys, hmac, hashlib, base64, json, time
role, secret = sys.argv[1], sys.argv[2]
b = lambda x: base64.urlsafe_b64encode(x).rstrip(b'=').decode()
now = int(time.time()); exp = now + 10*365*24*3600
h = b(json.dumps({"alg":"HS256","typ":"JWT"}, separators=(',',':')).encode())
p = b(json.dumps({"role":role,"iss":"supabase","iat":now,"exp":exp}, separators=(',',':')).encode())
s = b(hmac.new(secret.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest())
print(f"{h}.{p}.{s}")
PY
}

declare -a ORDER
declare -A V
put() { ORDER+=("$1"); V["$1"]="$2"; }

# =============================================================================
# SECRETS TABLE — one put() per machine secret. ADD NEW SECRETS HERE. (see banner)
# =============================================================================
# --- App admin / first-login passwords (BREAK-GLASS; day-to-day login is SSO) ---
put vault_authentik_bootstrap_password  "$(gen_pw 30)"   # akadmin (SSO root break-glass)
put vault_grafana_admin_password        "$(gen_pw 20)"
put vault_nextcloud_admin_password      "$(gen_pw 24)"
put vault_immich_admin_password         "$(gen_pw 20)"
put vault_komodo_init_admin_password    "$(gen_pw 30)"
put vault_supabase_dashboard_password   "$(gen_pw 20)"
put vault_pgadmin_password              "$(gen_pw 15)"

# --- Database passwords (random, url-safe hex) ---
put vault_postgres_password             "$(gen_hex 24)"
put vault_postgres_business_password    "$(gen_hex 24)"
put vault_authentik_postgres_password   "$(gen_hex 24)"
put vault_immich_db_password            "$(gen_hex 24)"
put vault_nextcloud_db_password         "$(gen_hex 24)"
put vault_komodo_database_password      "$(gen_hex 24)"
put vault_supabase_postgres_password    "$(gen_hex 24)"

# --- Authentik OIDC client secrets (both sides configured by Ansible) ---
put vault_authentik_oauth_grafana_secret   "$(gen_hex 32)"
put vault_authentik_oauth_headplane_secret "$(gen_hex 32)"
put vault_authentik_oauth_immich_secret    "$(gen_hex 32)"
put vault_authentik_oauth_komodo_secret    "$(gen_hex 32)"
put vault_authentik_oauth_nextcloud_secret "$(gen_hex 32)"
put vault_authentik_oauth_pgadmin_secret   "$(gen_hex 32)"

# --- Signing / encryption keys + API tokens (machine) ---
put vault_authentik_secret_key          "$(gen_hex 32)"
put vault_authentik_bootstrap_token     "api-token-$(gen_hex 24)"
put vault_authentik_playwright_password "$(gen_hex 24)"
put vault_immich_uploader_jwt_secret    "$(gen_hex 32)"
put vault_komodo_passkey                "$(gen_hex 32)"
# LOSING vault_restic_password = LOSING ALL BACKUPS — keep a copy OUTSIDE this repo.
put vault_restic_password               "$(gen_hex 24)"

# --- acme-dns API keys (Caddy X-Api-Key; 40 hex like the accounts expect) ---
put vault_acme_dns_dev_password         "$(gen_hex 20)"
put vault_acme_dns_int_password         "$(gen_hex 20)"

# --- admin_user OS password salt: FIXED CONSTANT (not random). A salt need not be secret;
#     it just has to be STABLE so the sha512_crypt hash is deterministic across runs (else
#     Ansible re-hashes every deploy). MUST be <=16 chars, alphabet ./0-9A-Za-z. ---
put vault_admin_user_salt               "s9Kd2Lp7Qm4Wx1Zr"

# --- Supabase (self-host). Enc-key lengths follow common self-host defaults; if a
#     fresh Supabase rejects one, adjust the length here. jwt_secret is generated
#     first so the anon/service_role JWTs can be derived from it. ---
SB_JWT="$(gen_hex 32)"
put vault_supabase_jwt_secret           "$SB_JWT"
put vault_supabase_secret_key_base      "$(gen_hex 32)"
put vault_supabase_realtime_enc_key     "$(gen_hex 16)"
put vault_supabase_pg_meta_crypto_key   "$(gen_hex 16)"
put vault_supabase_vault_enc_key        "$(gen_hex 16)"
put vault_supabase_s3_access_key_id     "$(gen_hex 10)"
put vault_supabase_s3_access_key_secret "$(gen_hex 20)"
put vault_supabase_anon_key             "$(gen_sb_jwt anon "$SB_JWT")"
put vault_supabase_service_role_key     "$(gen_sb_jwt service_role "$SB_JWT")"

# =============================================================================
# write out
# =============================================================================
{
  echo "---"
  echo "# =============================================================================="
  echo "# vault_secrets.yml — MACHINE-GENERATED secrets (Part B of the vault)."
  echo "#"
  echo "# Generated by scripts/generate-vault-secrets.sh (a FULL regeneration). The"
  echo "# USER/EXTERNAL secrets live in vault.yml. Encrypt this file before committing:"
  echo "#   ansible-vault encrypt group_vars/all/vault_secrets.yml"
  echo "#"
  echo "# ‼️  When you add a new machine secret, ALSO add it to the SECRETS TABLE in"
  echo "#     scripts/generate-vault-secrets.sh — otherwise a regenerated file omits it."
  echo "# =============================================================================="
  for k in "${ORDER[@]}"; do printf '%s: %s\n' "$k" "${V[$k]}"; done
} > "$OUT"
chmod 600 "$OUT"

echo "Wrote $OUT (${#ORDER[@]} secrets, all freshly generated)."
echo "Next: review, then  ansible-vault encrypt '$OUT'  before committing."
