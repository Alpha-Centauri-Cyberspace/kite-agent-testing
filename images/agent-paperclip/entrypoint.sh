#!/usr/bin/env bash
# Same env contract as agent-openclaw. Emits JSON lines tagged
# {"agent":"paperclip",...}.
#
# Kite → paperclip sink shape is already known from kite-cli:
#   crates/kite-cli/src/sinks/paperclip.rs
#   sink: { type: paperclip, api_url, company_id, agent_id }

set -euo pipefail

: "${KITE_API_KEY:?required}"
: "${KITE_TEAM_ID:?required}"
: "${KITE_WS_URL:?required}"
: "${AGENT_MODE:=scripted}"

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
    # TODO(Chunk 3): kite stream + scenario-driven paperclip action
    exec kite stream --json
    ;;
  model)
    : "${OPENROUTER_API_KEY:?required for model mode}"
    : "${AGENT_MODEL:?required for model mode}"
    echo '{"agent":"paperclip","evt":"model-mode-not-implemented","model":"'"${AGENT_MODEL}"'"}'
    exit 0
    ;;
esac
