-- Run at postgres init time (before kite-server migrations).
-- kite-server uses pgp_sym_encrypt/_decrypt on federation peer tokens and
-- on webhook_secret_ciphertext, so pgcrypto MUST exist in the database's
-- public schema before the server boots.
CREATE EXTENSION IF NOT EXISTS pgcrypto;
