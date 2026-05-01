# kite-agent-integration-tests

End-to-end integration test harness for the [Kite](https://github.com/Alpha-Centauri-Cyberspace) ecosystem. Validates the two highest-risk flows:

1. **Onboarding** — an agent container installs `kite-cli` (or stands in with a scripted subscriber), pulls its env contract from a shared volume populated by a bootstrap sidecar, and subscribes to a Kite WebSocket stream.
2. **Agent-to-agent communication** — exercises the hosted A2A primitive: agents on the same team exchange `com.kite.agent.message` events via `POST /api/v1/agents/messages` and the `agent_to:<agent_id>` WebSocket scope. See [Agent Messaging](https://getkite.sh/docs/concepts/agent-messaging) for the public-facing docs.

The harness runs entirely against a **local kite-server** spun up via Docker Compose (one postgres + one kite-server + bootstrap sidecar + drain + two agents + judge). This keeps CI hermetic — the tests don't depend on `api.getkite.sh` reachability or production quota.

## Quick start

Prerequisites:

- Docker + Docker Compose v2.
- `docker login ghcr.io -u <user>` with a PAT that has `read:packages` on `Alpha-Centauri-Cyberspace`. **The kite-server image referenced here is intentionally private** — this harness is for Kite contributors and contractors. A suitable token lives in Infisical at `/infrastructure/GH_PACKAGES_TOKEN`. Anonymous viewers of this public repo will get a `401` from GHCR if they try to pull it.
- On Apple Silicon: the kite-server image is x86_64-only, so everything runs under Rosetta emulation automatically.

```bash
# Default: shared-bus + ping-pong scenario
./run.sh

# Filter scenario (only high-importance events should be acted on)
./run.sh --scenario filter

# Hosted A2A round-trip — exercises POST /api/v1/agents/messages
# and the agent_to:<id> WebSocket scope end-to-end
./run.sh --scenario a2a-ping-pong

# Longer observation window (default 45s)
./run.sh --duration 90

# Leave the stack up after the run for inspection
./run.sh --keep
```

Results land in `results/<scenario>-<topology>-<ts>/{summary.md,summary.json,raw.ndjson}`.

### First run

`./run.sh` will:

1. Build the local images (bootstrap, fake-drain, agent-openclaw, agent-paperclip, judge) the first time — ~2 minutes.
2. `docker compose up -d --wait` the stack; kite-server cold-starts under emulation in ~90s on Apple Silicon, `start_period: 180s` accommodates that.
3. Run the judge inline for `$DURATION` seconds, tailing every container's JSON log lines.
4. Tear down automatically at exit (unless `--keep` is passed).

Exit code: **0** if the run passed its scenario criteria, **1** otherwise.

## Architecture

```
compose/
  shared-bus.yml          1 postgres + 1 kite-server + bootstrap + drain + 2 agents + judge
  postgres-init/          runs before kite-server: CREATE EXTENSION pgcrypto

images/
  bootstrap/              one-shot: seeds team, api_keys, hook_configs, subscriptions; writes
                          /run/kite-env/<agent>.env (incl. MY_AGENT_ID) + /run/kite-env/teams.env
  fake-drain/             python webhook firehose, signs with HMAC-SHA256 using the hook token
  agent-openclaw/         scripted WS subscriber tagged {"agent":"openclaw"}
  agent-paperclip/        scripted WS subscriber tagged {"agent":"paperclip"}

judge/                    docker-sdk log tailer + correlator + markdown/json report writer
scenarios/
  scripted/*.yaml         deterministic test definitions
  model-matrix/*.yaml     x402-centered LLM scenarios (scaffolded; scripted fallback today)
run.sh                    entry point
```

### Chunk status

| Chunk | Status |
|---|---|
| 1 — GHCR publish for kite-server | already satisfied by `kite-server`'s `docker.yml` |
| 2 — agent-openclaw container | scripted subscriber (model mode falls back to scripted) |
| 3 — agent-paperclip container | scripted subscriber |
| 4 — fake-drain | signs with HMAC-SHA256, configurable rate, 2 fixtures (high/low) |
| 5 — bootstrap sidecar | seeds team + api key + hook config (encrypted webhook secret) + active `free` subscription + agent ids |
| 6 — judge | correlates drain/agent events, computes delivery %, A2A round-trips, scenario breakdown, writes md + json |
| 7 — scripted scenarios + `run.sh` | ping-pong, filter, a2a-ping-pong; full up/run/tear-down lifecycle |

Model-matrix scenarios (x402 onboarding + multi-model judging) are scaffolded but currently exercise the scripted path — openclaw/paperclip CLI installer URLs and `ANTHROPIC_BASE_URL` overrides need to be pinned before turning them on.

## Env contract

Every agent container sources `/run/kite-env/<AGENT_NAME>.env`, populated by bootstrap:

```
KITE_TEAM_ID=<team>
KITE_API_KEY=kite_<prefix>_<secret>
KITE_HOOK_TOKEN=khk_<prefix>_<secret>
MY_AGENT_ID=agent-<openclaw|paperclip>
```

Plus these from docker-compose:

```
KITE_WS_URL=ws://kite-server:7700/ws
KITE_HTTP_URL=http://kite-server:7700
PEER_AGENT_ID=agent-<the-other-agent>
AGENT_MODE=scripted|model
SCENARIO=<name>
```

## Judge log contract

One JSON object per stdout line. Drain emits:

```json
{"drain_event_id":"...", "fixture":"github-push-high", "sent_at":"2026-...", "status_code":200, "latency_ms":42}
```

Agents emit, per lifecycle step:

```json
{"agent":"openclaw", "ts":"2026-...", "evt":"received",      "seq":42, "scenario_tag":"filter-match", "importance":"high"}
{"agent":"openclaw", "ts":"2026-...", "evt":"responded",     "seq":42, "scenario_tag":"filter-match"}
{"agent":"openclaw", "ts":"2026-...", "evt":"skipped",       "reason":"filter", "scenario_tag":"filter-noise"}
{"agent":"openclaw", "ts":"2026-...", "evt":"a2a_sent",      "to_agent":"agent-paperclip", "source_seq":42}
{"agent":"paperclip","ts":"2026-...", "evt":"a2a_received",  "from_agent":"agent-openclaw", "body":"seq=42", "is_echo":false, "source_seq":"42"}
{"agent":"paperclip","ts":"2026-...", "evt":"a2a_echoed",    "to_agent":"agent-openclaw", "source_seq":"42"}
{"agent":"openclaw", "ts":"2026-...", "evt":"a2a_received",  "from_agent":"agent-paperclip", "body":"re:seq=42", "is_echo":true, "source_seq":"42"}
```

## Pass criteria

Per-scenario, in `judge/judge.py`:

| Scenario | Criteria |
|---|---|
| `ping-pong` | both agents ≥ 90% delivery |
| `filter` | `filter-match` events recall ≥ 90%, `filter-noise` false-positive rate ≤ 5% |
| `a2a-ping-pong` | ≥ 90% of drain events complete the full openclaw→paperclip→openclaw round-trip |

Thresholds are deliberately lenient; tighten when the harness lands in CI.

## Known gotchas

- **pgcrypto must be installed before kite-server starts** — `compose/postgres-init/001-pgcrypto.sql` handles this. Without it, server-side encryption helpers spam `pgp_sym_decrypt(text, text) does not exist` and webhook verification may silently 403.
- **Rate limits** — the default `KITE_TEAM_RATE_LIMIT` is 100/window. The compose bumps it to 100k so the drain can fire at 5/sec without a flood of 429s. Real prod uses the default.
- **Subscription required** — the `/hooks` ingest *and* the `/api/v1/agents/messages` endpoint return 403 if the team has no active subscription. Bootstrap creates a `free`-plan subscription automatically.
- **Webhook secret** — github-source signatures are verified against `webhook_secret_ciphertext`. Bootstrap sets this to the same value as the hook token (encrypted with `KITE_HOOK_SECRET_CIPHER_KEY`), so the drain can sign with the hook token and the server can verify.
- **A2A endpoint version** — the `a2a-ping-pong` scenario requires a kite-server build that includes `POST /api/v1/agents/messages` and the `agent_to:<id>` WebSocket scope (kite-server PR #58 onward).

## Local dev loop

```bash
# tail one container
docker compose -f compose/shared-bus.yml -p kite-test-shared-bus logs -f agent-openclaw

# open a shell in a running agent
docker compose -f compose/shared-bus.yml -p kite-test-shared-bus exec agent-openclaw bash

# seed more scenarios: add a YAML under scenarios/scripted/ and a matching
# entry to PASS_CRITERIA in judge/judge.py
```

## License

MIT.
