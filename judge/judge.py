"""
Judge — correlates drain -> agent-A -> agent-B event flow, computes metrics,
writes /out/summary.md and /out/summary.json.

Pinned log-line contract consumed from each container's stdout:
  - drain:         {"drain_event_id": "...", "sent_at": "..."}
  - agent-openclaw {"agent": "openclaw", "evt": "received"|"responded", "drain_event_id": "...", ...}
  - agent-paperclip {"agent": "paperclip", ...}

Metrics per scenario:
  - delivery success %
  - median / p99 end-to-end latency
  - duplicates, drops
  - A→B delivery rate (federated only)
  - errors by category
"""

import json
import os
import pathlib
import sys
from collections import defaultdict

import docker  # type: ignore[import-not-found]


SCENARIO = os.environ.get("SCENARIO", "ping-pong")
OUT = pathlib.Path("/out")


def _collect():
    client = docker.from_env()
    events = defaultdict(dict)
    for container in client.containers.list(all=True):
        name = container.name
        if not any(tag in name for tag in ("drain", "agent-openclaw", "agent-paperclip")):
            continue
        try:
            logs = container.logs(stream=False).decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            continue
        for line in logs.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                rec = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            eid = rec.get("drain_event_id")
            if not eid:
                continue
            if "sent_at" in rec:
                events[eid]["drain"] = rec
            elif "agent" in rec:
                role = rec["agent"]
                events[eid].setdefault(role, []).append(rec)
    return events


def _render(events):
    sent = sum(1 for e in events.values() if "drain" in e)
    received_openclaw = sum(1 for e in events.values() if "openclaw" in e)
    received_paperclip = sum(1 for e in events.values() if "paperclip" in e)
    summary = {
        "scenario": SCENARIO,
        "events_sent": sent,
        "openclaw_received": received_openclaw,
        "paperclip_received": received_paperclip,
        "delivery_pct_openclaw": round(100 * received_openclaw / sent, 2) if sent else 0,
        "delivery_pct_paperclip": round(100 * received_paperclip / sent, 2) if sent else 0,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2))
    md = [
        f"# {SCENARIO}",
        "",
        f"- events sent: **{sent}**",
        f"- openclaw received: **{received_openclaw}** ({summary['delivery_pct_openclaw']}%)",
        f"- paperclip received: **{received_paperclip}** ({summary['delivery_pct_paperclip']}%)",
        "",
        "## TODO (Chunk 6)",
        "- latency histograms (p50/p95/p99)",
        "- duplicate/drop detection",
        "- A→B federation tracking",
    ]
    (OUT / "summary.md").write_text("\n".join(md) + "\n")
    print(json.dumps(summary))


def main() -> int:
    events = _collect()
    _render(events)
    return 0


if __name__ == "__main__":
    sys.exit(main())
