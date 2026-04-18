#!/usr/bin/env bash
# Kite agent integration test runner.
#
# Usage:
#   ./run.sh                                         # shared-bus, scripted
#   ./run.sh --federated                             # federated, scripted
#   ./run.sh --model-matrix --models a/b,c/d         # model-matrix (LLM) scenarios
#   ./run.sh --scenario ping-pong                    # single scenario
#   ./run.sh --max-cost-usd 2.50                     # abort if spend exceeds
#
# Entry point for all test flows. Composes the right topology up, hands off
# to judge/, tears down, surfaces exit code.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOPOLOGY="shared-bus"
MODE="scripted"
SCENARIO=""
MODELS=""
MAX_COST_USD=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --federated)       TOPOLOGY="federated"; shift ;;
    --model-matrix)    MODE="model-matrix"; shift ;;
    --scenario)        SCENARIO="$2"; shift 2 ;;
    --models)          MODELS="$2"; shift 2 ;;
    --max-cost-usd)    MAX_COST_USD="$2"; shift 2 ;;
    -h|--help)         sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "unknown flag: $1" >&2; exit 2 ;;
  esac
done

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="${ROOT}/results/${STAMP}"
mkdir -p "${OUT_DIR}"

echo "topology: ${TOPOLOGY}"
echo "mode: ${MODE}"
echo "results: ${OUT_DIR}"

export KITE_SERVER_IMAGE="${KITE_SERVER_IMAGE:-ghcr.io/alpha-centauri-cyberspace/kite-server-server:latest}"
export SCENARIO MODE MODELS MAX_COST_USD OUT_DIR

COMPOSE_FILE="${ROOT}/compose/${TOPOLOGY}.yml"
if [[ ! -f "${COMPOSE_FILE}" ]]; then
  echo "::error::compose file not found: ${COMPOSE_FILE}"
  exit 1
fi

# TODO(Chunk 5): bring up compose, bootstrap team/tokens, export env to agents.
# TODO(Chunk 6): run judge, tail logs, correlate events, write summary.
# TODO(Chunk 7): per-scenario harness that iterates the ${MODELS} matrix for
# model-matrix mode with OpenRouter cost tracking.

echo "::warning::run.sh is a skeleton — Chunks 5, 6, 7 not yet implemented."
exit 0
