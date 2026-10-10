#!/usr/bin/env python3
"""Idempotently ensure declared acme-dns accounts exist in the acme-dns sqlite DB.

Usage: ensure_accounts.py <db_path> <accounts_json_path>
  accounts_json: [{ "username", "subdomain", "password" }, ...]

Creates any MISSING account (matched by Subdomain OR Username): inserts one row in
`records` (Password = bcrypt hash) + two empty rows in `txt` (acme-dns keeps two
rotating TXT values per subdomain, exactly like its /register does). Existing
accounts are left untouched (password is NOT rotated here). Prints CHANGED/OK.
"""
import json
import sqlite3
import sys

try:
    import crypt  # deprecated but present on the target python (glibc/libxcrypt bcrypt)
    HAVE_CRYPT = hasattr(crypt, "METHOD_BLOWFISH")
except Exception:
    HAVE_CRYPT = False


def bcrypt_hash(password: str) -> str:
    if HAVE_CRYPT:
        return crypt.crypt(password, crypt.mksalt(crypt.METHOD_BLOWFISH))  # $2b$...
    try:
        import bcrypt as _bcrypt
        return _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()
    except Exception as e:  # noqa: BLE001
        sys.stderr.write("no bcrypt method available (crypt/METHOD_BLOWFISH or bcrypt module): %s\n" % e)
        sys.exit(2)


def main() -> None:
    db_path = sys.argv[1]
    # accounts JSON from arg 2 (a file path), or from stdin when '-' / omitted.
    src = sys.argv[2] if len(sys.argv) > 2 else "-"
    accounts = json.load(sys.stdin if src == "-" else open(src))
    con = sqlite3.connect(db_path, timeout=15)
    cur = con.cursor()
    added = 0
    for a in accounts:
        user, sub, pw = a["username"], a["subdomain"], a["password"]
        exists = cur.execute(
            "SELECT 1 FROM records WHERE Subdomain=? OR Username=?", (sub, user)
        ).fetchone()
        if exists:
            continue
        cur.execute(
            "INSERT INTO records (Username, Password, Subdomain, AllowFrom) VALUES (?,?,?,?)",
            (user, bcrypt_hash(pw), sub, "[]"),
        )
        cur.execute("INSERT INTO txt (Subdomain, Value, LastUpdate) VALUES (?,?,0)", (sub, ""))
        cur.execute("INSERT INTO txt (Subdomain, Value, LastUpdate) VALUES (?,?,0)", (sub, ""))
        added += 1
    con.commit()
    con.close()
    print("CHANGED accounts_added=%d" % added if added else "OK accounts_added=0")


if __name__ == "__main__":
    main()
