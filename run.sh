#!/usr/bin/env bash
# Kite agent integration test runner.
#
# Usage:
#   ./run.sh                                        # shared-bus, scripted, ping-pong
#   ./run.sh --scenario filter                      # pick a specific scenario
#   ./run.sh --scenario a2a-ping-pong               # exercise hosted A2A primitive
#   ./run.sh --duration 60                          # observation window seconds
#   ./run.sh --model-matrix                         # switch agents to LLM mode
#   ./run.sh --models anthropic/claude-haiku-4-5    # per-run model (single)
#   ./run.sh --max-cost-usd 1.00                    # hard cost cap (model mode)
#   ./run.sh --scenario x402-onboarding --model-matrix
#   ./run.sh --keep                                 # leave containers up for poking

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOPOLOGY="shared-bus"
SCENARIO="ping-pong"
DURATION="45"
MODE="scripted"
MODEL="${AGENT_MODEL:-anthropic/claude-haiku-4-5}"
MAX_COST="${MAX_COST_USD:-0}"
KEEP="${KITE_TEST_KEEP:-0}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --scenario)      SCENARIO="$2"; shift 2 ;;
    --duration)      DURATION="$2"; shift 2 ;;
    --model-matrix)  MODE="model"; shift ;;
    --models)        MODEL="$2"; shift 2 ;;
    --max-cost-usd)  MAX_COST="$2"; shift 2 ;;
    --keep)          KEEP=1; shift ;;
    -h|--help)       sed -n '2,14p' "$0"; exit 0 ;;
    *)               echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

# Model mode requires an API key. Fail early rather than at container start.
if [[ "$MODE" == "model" && -z "${OPENROUTER_API_KEY:-}" ]]; then
  echo "::error::--model-matrix needs OPENROUTER_API_KEY in the env" >&2
  exit 2
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="${KITE_TEST_RESULTS:-${ROOT}/results}/${SCENARIO}-${TOPOLOGY}-${STAMP}"
mkdir -p "$OUT_DIR"

echo "topology=$TOPOLOGY scenario=$SCENARIO mode=$MODE duration=${DURATION}s results=$OUT_DIR"
if [[ "$MODE" == "model" ]]; then
  echo "model=$MODEL max_cost_usd=$MAX_COST"
fi

COMPOSE_FILE="${ROOT}/compose/${TOPOLOGY}.yml"
[[ -f "$COMPOSE_FILE" ]] || { echo "::error::no compose file at $COMPOSE_FILE"; exit 1; }

export KITE_SERVER_IMAGE="${KITE_SERVER_IMAGE:-ghcr.io/alpha-centauri-cyberspace/kite-server-server:latest}"
export SCENARIO AGENT_MODE="$MODE" AGENT_MODEL="$MODEL" MAX_COST_USD="$MAX_COST"
export JUDGE_DURATION_SEC="$DURATION" OUT_DIR
# Pass through OPENROUTER_API_KEY if set (model mode).
export OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-}"
PROJECT="kite-test-${TOPOLOGY}"

cleanup() {
  local status=$?
  if [[ "$KEEP" != "1" ]]; then
    echo ""
    echo "=== Tearing down ==="
    docker compose -f "$COMPOSE_FILE" -p "$PROJECT" down -v --remove-orphans >/dev/null 2>&1 || true
  else
    echo ""
    echo "--keep: containers left running. Tear down with:"
    echo "    docker compose -f $COMPOSE_FILE -p $PROJECT down -v"
  fi
  return $status
}
trap cleanup EXIT

echo ""
echo "=== Starting stack ==="
if ! docker compose -f "$COMPOSE_FILE" -p "$PROJECT" up -d --build --wait 2>&1 | tail -30; then
  echo "::error::compose up failed — dumping recent logs" >&2
  docker compose -f "$COMPOSE_FILE" -p "$PROJECT" logs --tail 80 >&2 || true
  exit 1
fi

echo ""
echo "=== Services ==="
docker compose -f "$COMPOSE_FILE" -p "$PROJECT" ps

echo ""
echo "=== Running judge for ${DURATION}s ==="
judge_exit=0
docker compose -f "$COMPOSE_FILE" -p "$PROJECT" run --rm -T judge || judge_exit=$?

echo ""
echo "=== Summary ==="
latest=$(find "$OUT_DIR" -maxdepth 2 -name summary.md 2>/dev/null | head -1)
if [[ -n "$latest" ]]; then
  cat "$latest"
  echo ""
  echo "Full results: $OUT_DIR"
else
  echo "(no summary.md produced — check container logs)"
fi

exit $judge_exit
