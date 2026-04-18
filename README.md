# kite-agent-integration-tests

End-to-end integration test suite for the [Kite](https://github.com/Alpha-Centauri-Cyberspace) ecosystem. Validates the two highest-risk flows:

1. **Agent onboarding** — an AI agent installs `kite-cli` from the public installer, configures itself via env vars (no interactive login), subscribes to a webhook stream, and starts doing work.
2. **Agent-to-agent communication** — tested in both topologies kite supports:
   - **Shared bus** (both agents on one kite-server)
   - **Federated** (each agent on its own kite-server, bridged via `kite stream --federation-target`)

## Quick start

```bash
./run.sh                                # shared-bus scripted scenarios (fast, deterministic)
./run.sh --federated                    # federated topology scripted scenarios
./run.sh --model-matrix \
  --models anthropic/claude-opus-4-7,openai/gpt-5.2   # x402-centered LLM scenarios
```

Results land in `results/<timestamp>/summary.md`.

## Architecture

```
compose/
  shared-bus.yml         1 postgres + 1 kite-server + 2 agents + drain + judge
  federated.yml          2 postgres + 2 kite-server + 2 agents + drain + judge

images/
  agent-openclaw/        Debian + openclaw + kite install + entrypoint
  agent-paperclip/       Debian + paperclip + kite install + entrypoint
  fake-drain/            ~50-line webhook firehose

scenarios/
  scripted/*.yaml        Deterministic; no LLM calls
  model-matrix/*.yaml    x402-gated access tests against OpenRouter models

judge/                   Log tailer + correlator + report generator
run.sh                   Entry point
```

Kite server image source: `ghcr.io/alpha-centauri-cyberspace/kite-server-server:<tag>` (published by the `kite-server` repo on every push to main).

## Status

- [x] Chunk 1 — GHCR publish workflow (already satisfied by `kite-server`'s `docker.yml`)
- [ ] Chunk 2 — `images/agent-openclaw/` Dockerfile + entrypoint
- [ ] Chunk 3 — `images/agent-paperclip/` Dockerfile + entrypoint
- [ ] Chunk 4 — `images/fake-drain/` webhook firehose
- [ ] Chunk 5 — `compose/shared-bus.yml` + `compose/federated.yml`
- [ ] Chunk 6 — `judge/` orchestrator + report generator
- [ ] Chunk 7 — `scenarios/scripted/*.yaml` + `scenarios/model-matrix/*.yaml` + `run.sh`

Skeletons are in place for parallel agent development on git worktrees. Each chunk has a pinned interface contract in its directory's `README.md`.

## Reference

- Plan: [`/Users/john/.claude/plans/do-you-think-we-lively-otter.md`](https://github.com/Alpha-Centauri-Cyberspace) (local)
- Kite CLI: https://github.com/Alpha-Centauri-Cyberspace/kite-cli
- Kite server: internal
- Kite protocol: https://crates.io/crates/kite-protocol

## License

MIT — see [`LICENSE`](./LICENSE).
