#!/usr/bin/env bash
# Pinned env contract for agent containers:
#   AGENT_NAME           openclaw | paperclip
#   AGENT_ENV_FILE       /run/kite-env/<agent>.env  (written by bootstrap)
#   KITE_WS_URL          ws://kite-server:7700/ws
#   KITE_HTTP_URL        http://kite-server:7700
#   AGENT_MODE           scripted | model   (default scripted)
#   SCENARIO             ping-pong | filter | a2a-ping-pong | ...
#   PEER_AGENT_ID        agent on the same team this agent messages in the
#                        a2a-ping-pong scenario (set in compose).
#
# Scripted mode: runs scripted-subscriber.py, a plain python websockets client
# emitting JSON log lines per lifecycle step.
# Model mode: placeholder — falls back to scripted until the openclaw
# installer + ANTHROPIC_BASE_URL override is verified.

set -euo pipefail

: "${AGENT_NAME:?required}"
: "${KITE_WS_URL:?required}"
: "${SCENARIO:=ping-pong}"
: "${AGENT_MODE:=scripted}"

env_file="${AGENT_ENV_FILE:-/run/kite-env/${AGENT_NAME}.env}"
if [[ ! -f "$env_file" ]]; then
  echo "::error::agent env file missing at $env_file — did bootstrap run?" >&2
  exit 1
fi
# shellcheck disable=SC1090
set -a; . "$env_file"; set +a

case "$AGENT_MODE" in
  scripted)
    exec python3 /opt/scripted-subscriber.py
    ;;
  model)
    : "${OPENROUTER_API_KEY:?AGENT_MODE=model needs OPENROUTER_API_KEY}"
    : "${AGENT_MODEL:=anthropic/claude-haiku-4-5}"
    exec python3 /opt/model-subscriber.py
    ;;
  *)
    echo "::error::unknown AGENT_MODE: $AGENT_MODE" >&2
    exit 1
    ;;
esac
