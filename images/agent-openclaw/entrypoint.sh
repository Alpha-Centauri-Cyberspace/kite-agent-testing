#!/usr/bin/env bash
# Pinned env contract for all agent containers (Chunk 2, 3):
#   KITE_API_KEY           — scoped API key from bootstrap sidecar
#   KITE_TEAM_ID           — team to subscribe against
#   KITE_WS_URL            — ws://kite-server:7700/ws (or wss:// for prod)
#   KITE_HOOK_BASE_URL     — http://kite-server:8080/hooks/...
#   KITE_SERVER            — hostname (A or B in federated mode)
#   AGENT_MODE             — scripted | model
#   OPENROUTER_API_KEY     — required when AGENT_MODE=model
#   AGENT_MODEL            — model ID for model mode
#   SCENARIO_DIR           — /scenarios mounted volume
#
# Emits one JSON log line per event with: {"agent":"openclaw", "ts", "evt", ...}

set -euo pipefail

: "${KITE_API_KEY:?required}"
: "${KITE_TEAM_ID:?required}"
: "${KITE_WS_URL:?required}"
: "${AGENT_MODE:=scripted}"

# If kite isn't installed from build-time, install now.
if ! command -v kite >/dev/null 2>&1; then
  curl -fsSL https://getkite.sh/install | sh
  export PATH="$PATH:$HOME/.kite/bin:/usr/local/bin"
fi

mkdir -p "$HOME/.config/kite"
cat > "$HOME/.config/kite/config.toml" <<EOF
api_key = "${KITE_API_KEY}"
team_id = "${KITE_TEAM_ID}"
ws_url  = "${KITE_WS_URL}"
EOF

case "${AGENT_MODE}" in
  scripted)
    # TODO(Chunk 2): Tail kite stream, exec /scenarios/scripted-handler.sh per event.
    exec kite stream --json
    ;;
  model)
    : "${OPENROUTER_API_KEY:?required for model mode}"
    : "${AGENT_MODEL:?required for model mode}"
    # TODO(Chunk 2): Run openclaw with ANTHROPIC_BASE_URL=https://openrouter.ai/api/v1
    # and ANTHROPIC_AUTH_TOKEN=$OPENROUTER_API_KEY. Verify openclaw honors these
    # env overrides before finalizing.
    echo '{"agent":"openclaw","evt":"model-mode-not-implemented","model":"'"${AGENT_MODEL}"'"}'
    exit 0
    ;;
  *)
    echo "::error::unknown AGENT_MODE: ${AGENT_MODE}"
    exit 1
    ;;
esac
