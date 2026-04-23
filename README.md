<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://getkite.sh/logo-on-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="https://getkite.sh/logo-on-light.svg">
    <img alt="Kite" src="https://getkite.sh/logo-on-dark.svg" width="220">
  </picture>

  <h3>End-to-end integration tests for the Kite ecosystem</h3>

  <p>
    <a href="https://getkite.sh"><img alt="Website" src="https://img.shields.io/badge/getkite.sh-00ff9d?style=flat-square&labelColor=0a0a0f"></a>
    <a href="https://github.com/Alpha-Centauri-Cyberspace/kite-cli"><img alt="kite-cli" src="https://img.shields.io/badge/cli-kite--cli-00d4ff?style=flat-square&labelColor=0a0a0f"></a>
    <a href="./LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-e4e4e7?style=flat-square&labelColor=0a0a0f"></a>
  </p>
</div>

---

Dockerized harness that spins up a full Kite stack, runs two agent containers against it, and judges the result. It validates the two highest-risk flows in the Kite ecosystem:

1. **Onboarding** — an agent installs `kite-cli` (or a scripted subscriber), pulls its env contract from a shared volume populated by a bootstrap sidecar, and subscribes to a Kite WebSocket stream.
2. **Agent-to-agent communication** — in both supported topologies:
   - **Shared bus** — both agents on one `kite-server`, same team.
   - **Federated** — each agent on its own `kite-server`, bridged via federation primitives.

## Quick start

Prerequisites:

- Docker + Docker Compose v2.
- `docker login ghcr.io -u <user>` with a PAT that has `read:packages` on `Alpha-Centauri-Cyberspace` (the `kite-server` image is private). A suitable token lives in Infisical at `/infrastructure/GH_PACKAGES_TOKEN`.
- Apple Silicon: the `kite-server` image is x86_64-only, so everything runs under Rosetta emulation automatically.

```
$ ./run.sh                          # shared-bus + ping-pong scenario (default)
$ ./run.sh --scenario filter        # only high-importance events should be acted on
$ ./run.sh --federated              # federated topology
$ ./run.sh --duration 90            # longer observation window (default 45s)
$ ./run.sh --keep                   # leave the stack up for inspection
```

Results land in `results/<scenario>-<topology>-<ts>/{summary.md,summary.json,raw.ndjson}`.

### What `./run.sh` does on first run

1. Builds the local images (bootstrap, fake-drain, agent-openclaw, agent-paperclip, judge) — ~2 minutes.
2. `docker compose up -d --wait` the stack; `kite-server` cold-starts under emulation in ~90s on Apple Silicon (`start_period: 180s` covers that).
3. Runs the judge inline for `$DURATION` seconds, tailing every container's JSON log lines.
4. Tears down automatically at exit (unless `--keep`).

Exit code is **0** if the scenario criteria pass, **1** otherwise.

## Architecture

```
compose/
  shared-bus.yml          1 postgres + 1 kite-server + bootstrap + drain + 2 agents + judge
  federated.yml           2 postgres + 2 kite-server + 2 bootstraps + drain + 2 agents + judge
  postgres-init/          runs before kite-server: CREATE EXTENSION pgcrypto

images/
  bootstrap/              one-shot: seeds team, api_keys, hook_configs, subscriptions; writes
                          /run/kite-env/<agent>.env + /run/kite-env/teams.env
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

| Chunk                                    | Status                                                              |
| ---------------------------------------- | ------------------------------------------------------------------- |
| 1 — GHCR publish for kite-server         | satisfied by `kite-server`'s `docker.yml`                           |
| 2 — agent-openclaw container             | scripted subscriber (model mode falls back to scripted)             |
| 3 — agent-paperclip container            | scripted subscriber                                                 |
| 4 — fake-drain                           | HMAC-SHA256 signing, configurable rate, 2 fixtures (high / low)     |
| 5 — bootstrap sidecar                    | seeds team + api key + hook config + active `free` subscription     |
| 6 — judge                                | correlates drain/agent events, delivery %, md + json report         |
| 7 — scripted scenarios + `run.sh`        | ping-pong, filter, federation-roundtrip; full up/run/tear-down      |

Model-matrix scenarios (x402 onboarding + multi-model judging) are scaffolded but currently exercise the scripted path — openclaw/paperclip CLI installer URLs and `ANTHROPIC_BASE_URL` overrides need to be pinned before turning them on.

## Env contract

Every agent container sources `/run/kite-env/<AGENT_NAME>.env`, populated by bootstrap:

```
KITE_TEAM_ID=<team>
KITE_API_KEY=kite_<prefix>_<secret>
KITE_HOOK_TOKEN=khk_<prefix>_<secret>
```

Plus these from docker-compose:

```
KITE_WS_URL=ws://kite-server:7700/ws
KITE_HTTP_URL=http://kite-server:7700
AGENT_MODE=scripted|model
SCENARIO=<name>
FEDERATION_TARGET_URL=<optional>
```

## Judge log contract

One JSON object per stdout line. Drain emits:

```json
{"drain_event_id":"...", "fixture":"github-push-high", "sent_at":"2026-...", "status_code":200, "latency_ms":42}
```

Agents emit, per lifecycle step:

```json
{"agent":"openclaw", "ts":"2026-...", "evt":"received",   "seq":42, "scenario_tag":"filter-match", "importance":"high"}
{"agent":"openclaw", "ts":"2026-...", "evt":"responded",  "seq":42, "scenario_tag":"filter-match"}
{"agent":"openclaw", "ts":"2026-...", "evt":"skipped",    "reason":"filter", "scenario_tag":"filter-noise"}
```

## Pass criteria

Per-scenario, in `judge/judge.py`:

| Scenario                 | Criteria                                                                             |
| ------------------------ | ------------------------------------------------------------------------------------ |
| `ping-pong`              | both agents ≥ 90% delivery                                                           |
| `filter`                 | `filter-match` events recall ≥ 90%, `filter-noise` false-positive rate ≤ 5%          |
| `federation-roundtrip`   | openclaw delivery ≥ 85%, paperclip delivery (via federation) ≥ 75%                   |

Thresholds are deliberately lenient; tighten when the harness lands in CI.

## Known gotchas

- **pgcrypto must be installed before kite-server starts** — `compose/postgres-init/001-pgcrypto.sql` handles this. Without it, kite-server's federation outbox worker spams `pgp_sym_decrypt(text, text) does not exist` and webhook verification may silently 403.
- **Rate limits** — the default `KITE_TEAM_RATE_LIMIT` is 100/window. The compose bumps it to 100k so the drain can fire at 5/sec without a flood of 429s. Real prod uses the default.
- **Subscription required** — the `/hooks` ingest returns 403 if the team has no active subscription. Bootstrap creates a `free`-plan subscription automatically.
- **Webhook secret** — github-source signatures are verified against `webhook_secret_ciphertext`. Bootstrap sets this to the same value as the hook token (encrypted with `KITE_HOOK_SECRET_CIPHER_KEY`), so the drain can sign with the hook token and the server can verify.

## Local dev loop

```
# tail one container
$ docker compose -f compose/shared-bus.yml -p kite-test-shared-bus logs -f agent-openclaw

# open a shell in a running agent
$ docker compose -f compose/shared-bus.yml -p kite-test-shared-bus exec agent-openclaw bash

# seed more scenarios: add a YAML under scenarios/scripted/ and a matching
# entry to PASS_CRITERIA in judge/judge.py
```

## Related

- **[kite-cli](https://github.com/Alpha-Centauri-Cyberspace/kite-cli)** — the CLI under test.
- **[kite-protocol](https://github.com/Alpha-Centauri-Cyberspace/kite-protocol)** — wire format the agents speak.
- **[kite-mesh](https://github.com/Alpha-Centauri-Cyberspace/kite-mesh)** — the P2P discovery companion.

## License

MIT — see [`LICENSE`](./LICENSE).

---

<div align="center">
  <sub>
    <a href="https://getkite.sh">getkite.sh</a> ·
    <a href="https://github.com/Alpha-Centauri-Cyberspace">github</a> ·
    <a href="https://getkite.sh/docs">docs</a>
  </sub>
</div>
