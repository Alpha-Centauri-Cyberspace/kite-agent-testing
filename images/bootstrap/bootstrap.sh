#!/usr/bin/env bash
# Seed a fresh kite-server database with the teams, API keys, and hook
# configs required for a test scenario, then emit per-agent env files for
# the agent containers to consume.
#
# Env required:
#   BOOTSTRAP_DATABASE_URL   postgres://... (no schema= param)
#   BOOTSTRAP_AGENT_TEAMS    space-separated pairs of <agent-name>=<team-id>,
#                            e.g. "openclaw=shared paperclip=shared" for the
#                            shared-bus topology where both agents share a
#                            team and exchange agent-to-agent messages.
#   BOOTSTRAP_OUT_DIR        path to the shared env volume (default /run/kite-env)
#
# Emits one file per agent into $BOOTSTRAP_OUT_DIR:
#   <agent-name>.env   KITE_TEAM_ID, KITE_API_KEY, KITE_HOOK_TOKEN
#
# Idempotent: UPSERTs on conflict, re-running doesn't wipe state.

set -euo pipefail

: "${BOOTSTRAP_DATABASE_URL:?required}"
: "${BOOTSTRAP_AGENT_TEAMS:?required}"
OUT_DIR="${BOOTSTRAP_OUT_DIR:-/run/kite-env}"

mkdir -p "$OUT_DIR"

echo "bootstrap: waiting for postgres..."
for _ in $(seq 1 60); do
  if psql "$BOOTSTRAP_DATABASE_URL" -c 'SELECT 1' >/dev/null 2>&1; then
    echo "bootstrap: postgres reachable"
    break
  fi
  sleep 2
done

echo "bootstrap: waiting for kite-server migrations to finish (teams table)..."
for _ in $(seq 1 60); do
  if psql "$BOOTSTRAP_DATABASE_URL" -c "SELECT 1 FROM teams LIMIT 1" >/dev/null 2>&1; then
    echo "bootstrap: schema present"
    break
  fi
  sleep 2
done

echo "bootstrap: ensuring pgcrypto extension..."
psql "$BOOTSTRAP_DATABASE_URL" -v ON_ERROR_STOP=1 -c 'CREATE EXTENSION IF NOT EXISTS pgcrypto;' >/dev/null

# Seed teams + one hook_config per team (idempotent). Track hook tokens by
# team so both agents in a shared topology share the same inbound hook.
declare -A HOOK_TOKEN_FOR_TEAM

for pair in $BOOTSTRAP_AGENT_TEAMS; do
  agent="${pair%%=*}"
  team="${pair#*=}"
  if [ -z "$agent" ] || [ -z "$team" ] || [ "$agent" = "$team" ]; then
    echo "::error::bad BOOTSTRAP_AGENT_TEAMS pair: $pair (expected agent=team)" >&2
    exit 2
  fi

  # Generate an API key unique to this agent+team.
  mapfile -t api_parts < <(python3 /bootstrap/gen-token.py kite)
  API_TOKEN="${api_parts[0]}"
  API_PREFIX="${api_parts[1]}"
  API_HASH="${api_parts[2]}"

  # Hook token is team-scoped (one per team). Reuse if we already minted one
  # for this team in the loop.
  if [ -z "${HOOK_TOKEN_FOR_TEAM[$team]+set}" ]; then
    mapfile -t hook_parts < <(python3 /bootstrap/gen-token.py khk)
    HOOK_TOKEN="${hook_parts[0]}"
    HOOK_PREFIX="${hook_parts[1]}"
    HOOK_HASH="${hook_parts[2]}"
    HOOK_TOKEN_FOR_TEAM[$team]="$HOOK_TOKEN"
    SEED_HOOK=1
  else
    HOOK_TOKEN="${HOOK_TOKEN_FOR_TEAM[$team]}"
    SEED_HOOK=0
  fi

  psql "$BOOTSTRAP_DATABASE_URL" -v ON_ERROR_STOP=1 \
       -v team_id="$team" \
       -v agent="$agent" \
       -v api_prefix="$API_PREFIX" \
       -v api_hash="$API_HASH" \
       -v hook_prefix="${HOOK_PREFIX:-}" \
       -v hook_hash="${HOOK_HASH:-}" \
       -v seed_hook="$SEED_HOOK" >/dev/null <<'SQL'
BEGIN;

INSERT INTO teams (id, name)
VALUES (:'team_id', :'team_id')
ON CONFLICT (id) DO NOTHING;

INSERT INTO api_keys
  (team_id, name, key_prefix, key_hash, scopes, permissions, expires_at, revoked)
VALUES
  (:'team_id', :'agent', :'api_prefix', :'api_hash',
   '["*"]'::jsonb, '["read","write"]'::jsonb, NULL, FALSE)
ON CONFLICT (key_prefix) DO UPDATE
  SET team_id    = EXCLUDED.team_id,
      key_hash   = EXCLUDED.key_hash,
      scopes     = EXCLUDED.scopes,
      permissions= EXCLUDED.permissions,
      expires_at = NULL,
      revoked    = FALSE;

-- kite-server's /hooks ingest requires an active subscription for the team
-- (metering.rs:77 → 403 otherwise). Attach the free plan (seeded by
-- migration 0002) to the team. Idempotent via WHERE NOT EXISTS.
INSERT INTO subscriptions
  (team_id, plan_id, provider, status,
   current_period_start, current_period_end,
   events_used, events_limit_snapshot)
SELECT :'team_id', p.id, 'x402'::subscription_provider, 'active'::subscription_status,
       NOW(), NOW() + INTERVAL '365 days',
       0, p.events_per_month
FROM subscription_plans p
WHERE p.name = 'free'
  AND NOT EXISTS (
    SELECT 1 FROM subscriptions s
    WHERE s.team_id = :'team_id' AND s.status = 'active'
  );

COMMIT;
SQL

  if [ "$SEED_HOOK" = "1" ]; then
    # Use the hook token itself as the GitHub webhook secret — the drain
    # signs with this, kite-server decrypts it from webhook_secret_ciphertext
    # and verifies the X-Hub-Signature-256 matches. Real deployments would
    # use a separately minted secret.
    psql "$BOOTSTRAP_DATABASE_URL" -v ON_ERROR_STOP=1 \
         -v team_id="$team" \
         -v hook_prefix="$HOOK_PREFIX" \
         -v hook_hash="$HOOK_HASH" \
         -v hook_token="$HOOK_TOKEN" \
         -v cipher_key="${KITE_HOOK_SECRET_CIPHER_KEY:-0123456789abcdef0123456789abcdef}" \
         >/dev/null <<'SQL'
INSERT INTO hook_configs
  (team_id, source, token_prefix, token_hash, webhook_secret_ciphertext, active)
VALUES
  (:'team_id', 'github', :'hook_prefix', :'hook_hash',
   pgp_sym_encrypt(:'hook_token', :'cipher_key'),
   TRUE)
ON CONFLICT (team_id, source) DO UPDATE
  SET token_prefix              = EXCLUDED.token_prefix,
      token_hash                = EXCLUDED.token_hash,
      webhook_secret_ciphertext = EXCLUDED.webhook_secret_ciphertext,
      active                    = TRUE;
SQL
  fi

  # Stable agent identifier for the hosted A2A primitive
  # (POST /api/v1/agents/messages + agent_to:<id> WS scope). The harness
  # uses the agent's container name verbatim so the judge's correlation
  # log keys are predictable.
  AGENT_ID="agent-$agent"

  umask 077
  cat > "$OUT_DIR/$agent.env" <<EOF
# Auto-generated by the bootstrap sidecar for agent=$agent team=$team.
KITE_TEAM_ID=$team
KITE_API_KEY=$API_TOKEN
KITE_HOOK_TOKEN=$HOOK_TOKEN
MY_AGENT_ID=$AGENT_ID
EOF
  chmod 0600 "$OUT_DIR/$agent.env"
  echo "bootstrap: wrote $OUT_DIR/$agent.env (agent=$agent team=$team agent_id=$AGENT_ID)"
done

# Also write a summary file that drain / judge can read to discover the
# set of teams + their hook tokens (needed to POST to /hooks/{team}/...).
: > "$OUT_DIR/teams.env"
for team in "${!HOOK_TOKEN_FOR_TEAM[@]}"; do
  token="${HOOK_TOKEN_FOR_TEAM[$team]}"
  # Emit as TEAM_<upper>_HOOK_TOKEN so drain/judge can pick whichever they want.
  upper=$(echo "$team" | tr '[:lower:]-' '[:upper:]_')
  echo "TEAM_${upper}_HOOK_TOKEN=$token" >> "$OUT_DIR/teams.env"
done
chmod 0600 "$OUT_DIR/teams.env"

echo "bootstrap: done."
