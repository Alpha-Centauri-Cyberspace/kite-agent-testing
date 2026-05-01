-- Run at postgres init time (before kite-server migrations).
-- kite-server uses pgp_sym_encrypt/_decrypt on webhook_secret_ciphertext
-- (and on internal operator tables), so pgcrypto MUST exist in the
-- database's public schema before the server boots.
CREATE EXTENSION IF NOT EXISTS pgcrypto;
