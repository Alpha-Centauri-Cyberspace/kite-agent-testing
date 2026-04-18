"""
Fake webhook drain — sends fixture payloads to a Kite hook endpoint on a timer.

Env:
  DRAIN_SCHEDULE     requests/sec, default "5/sec"
  KITE_HOOK_URL      e.g. http://kite-server:8080
  KITE_HOOK_TOKEN    hook token (team-scoped)
  KITE_TEAM_ID       team id
  DRAIN_SOURCE       github | stripe | generic (default: github)

Emits one JSON log line per send:
  {"drain_event_id": "...", "sent_at": "2026-..."}
"""

import hashlib
import hmac
import json
import os
import pathlib
import sys
import time
import urllib.request
import uuid


def _rate(spec: str) -> float:
    # e.g. "5/sec" -> 5.0
    n, _, _ = spec.partition("/")
    return float(n)


def _sign_github(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def main() -> int:
    base = os.environ["KITE_HOOK_URL"].rstrip("/")
    token = os.environ["KITE_HOOK_TOKEN"]
    team = os.environ["KITE_TEAM_ID"]
    source = os.environ.get("DRAIN_SOURCE", "github")
    rps = _rate(os.environ.get("DRAIN_SCHEDULE", "5/sec"))

    fixtures = list(pathlib.Path("/app/fixtures").glob("*.json"))
    if not fixtures:
        print("::error::no fixtures found in /app/fixtures", file=sys.stderr)
        return 1

    interval = 1.0 / rps
    idx = 0
    while True:
        fx = fixtures[idx % len(fixtures)]
        idx += 1
        body = fx.read_bytes()
        url = f"{base}/hooks/{team}/{source}/{token}"
        headers = {"Content-Type": "application/json"}
        if source == "github":
            headers["X-Hub-Signature-256"] = _sign_github(token, body)
            headers["X-GitHub-Event"] = "push"

        event_id = str(uuid.uuid4())
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            urllib.request.urlopen(req, timeout=10).read()
            print(json.dumps({"drain_event_id": event_id, "sent_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}), flush=True)
        except Exception as e:  # noqa: BLE001
            print(json.dumps({"drain_event_id": event_id, "error": str(e)}), flush=True)

        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
