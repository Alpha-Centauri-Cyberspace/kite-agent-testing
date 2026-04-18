"""
Fake webhook drain — sends fixture payloads to a Kite hook endpoint on a timer.

Env:
  KITE_HOOK_URL      e.g. http://kite-server:7700
  KITE_TEAM_ID       team to send to
  DRAIN_ENV_FILE     path to the bootstrap-written env file
  DRAIN_ENV_VAR      name of the env var holding this team's hook token
                     (e.g. TEAM_SHARED_HOOK_TOKEN)
  DRAIN_SCHEDULE     requests/sec, default "5/sec"
  DRAIN_SOURCE       github | generic (default: github)
  DRAIN_FIXTURE_DIR  override fixture directory (default /app/fixtures)

Emits one JSON log line per send:
  {"drain_event_id": "...", "sent_at": "...", "status_code": 200}
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import pathlib
import random
import sys
import time
import urllib.error
import urllib.request
import uuid


def _rate(spec: str) -> float:
    n, _, _ = spec.partition("/")
    return float(n)


def _load_env_file(path: str) -> dict[str, str]:
    env: dict[str, str] = {}
    p = pathlib.Path(path)
    if not p.exists():
        return env
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def _resolve_hook_token() -> str:
    env_file = os.environ.get("DRAIN_ENV_FILE")
    var_name = os.environ.get("DRAIN_ENV_VAR")
    if env_file and var_name:
        env = _load_env_file(env_file)
        if var_name in env:
            return env[var_name]
    return os.environ["KITE_HOOK_TOKEN"]


def _sign_github(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _load_fixtures() -> list[tuple[str, bytes]]:
    fx_dir = pathlib.Path(os.environ.get("DRAIN_FIXTURE_DIR", "/app/fixtures"))
    out: list[tuple[str, bytes]] = []
    for p in sorted(fx_dir.glob("*.json")):
        out.append((p.stem, p.read_bytes()))
    return out


def _log(payload: dict) -> None:
    print(json.dumps(payload, separators=(",", ":")), flush=True)


def main() -> int:
    base = os.environ["KITE_HOOK_URL"].rstrip("/")
    team = os.environ["KITE_TEAM_ID"]
    source = os.environ.get("DRAIN_SOURCE", "github")
    rps = _rate(os.environ.get("DRAIN_SCHEDULE", "5/sec"))

    hook_token = _resolve_hook_token()
    fixtures = _load_fixtures()
    if not fixtures:
        print("::error::no fixtures in DRAIN_FIXTURE_DIR", file=sys.stderr)
        return 1

    _log({"drain": "start", "team": team, "source": source, "rps": rps, "fixture_count": len(fixtures)})

    interval = 1.0 / rps
    i = 0
    while True:
        name, body = fixtures[i % len(fixtures)]
        i += 1

        url = f"{base}/hooks/{team}/{source}/{hook_token}"
        headers = {"Content-Type": "application/json"}
        if source == "github":
            headers["X-Hub-Signature-256"] = _sign_github(hook_token, body)
            headers["X-GitHub-Event"] = "push"
            headers["X-GitHub-Delivery"] = str(uuid.uuid4())

        event_id = str(uuid.uuid4())
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = resp.getcode()
                _log({
                    "drain_event_id": event_id,
                    "fixture": name,
                    "sent_at": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t0)),
                    "status_code": status,
                    "latency_ms": int((time.time() - t0) * 1000),
                })
        except urllib.error.HTTPError as e:
            _log({"drain_event_id": event_id, "fixture": name, "error": f"HTTP {e.code}: {e.reason}"})
        except Exception as e:  # noqa: BLE001
            _log({"drain_event_id": event_id, "fixture": name, "error": str(e)})

        time.sleep(max(0.01, interval * random.uniform(0.8, 1.2)))


if __name__ == "__main__":
    sys.exit(main())
