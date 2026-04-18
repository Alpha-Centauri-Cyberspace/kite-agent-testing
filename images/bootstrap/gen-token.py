#!/usr/bin/env python3
"""Generate a token in kite's format and its bcrypt(12) hash.

Usage:
    python3 gen-token.py kite   # API key:   kite_<prefix>_<secret>
    python3 gen-token.py khk    # Hook token: khk_<prefix>_<secret>

Writes three lines to stdout:
    <full-token>
    <prefix>          (first 8 chars after the scheme prefix)
    <bcrypt-hash>     (cost 12, $2a$12$... or $2b$12$...)
"""
import secrets
import sys

import bcrypt


def main() -> int:
    scheme = sys.argv[1] if len(sys.argv) > 1 else "kite"
    if scheme not in ("kite", "khk"):
        print(f"bad scheme: {scheme!r}; expected 'kite' or 'khk'", file=sys.stderr)
        return 2

    prefix = secrets.token_hex(4)            # 8 hex chars
    secret = secrets.token_hex(16)           # 32 hex chars
    token = f"{scheme}_{prefix}_{secret}"
    hashed = bcrypt.hashpw(token.encode(), bcrypt.gensalt(rounds=12)).decode()

    print(token)
    print(prefix)
    print(hashed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
