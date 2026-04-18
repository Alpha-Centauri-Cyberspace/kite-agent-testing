"""
Judge — runs for JUDGE_DURATION_SEC, tails every container's stdout in
parallel, correlates drain → agent-received → agent-responded using the
embedded drain_event_id + scenario tags, and writes a per-run report.

Writes:
  /out/summary.json   machine-readable metrics + pass/fail
  /out/summary.md     human-readable
  /out/raw.ndjson     every log record it collected

Exit code: 0 on pass, 1 on fail.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import threading
import time
from collections import defaultdict

import docker


SCENARIO = os.environ.get("SCENARIO", "ping-pong")
TOPOLOGY = os.environ.get("JUDGE_TOPOLOGY", "shared-bus")
DURATION = int(os.environ.get("JUDGE_DURATION_SEC", "45"))
OUT_ROOT = pathlib.Path("/out")
STAMP = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
OUT = OUT_ROOT / f"{SCENARIO}-{TOPOLOGY}-{STAMP}"
OUT.mkdir(parents=True, exist_ok=True)

PASS_CRITERIA = {
    "ping-pong": {
        "min_delivery_openclaw": 0.90,
        "min_delivery_paperclip": 0.90,
    },
    "filter": {
        "min_recall_high": 0.90,
        "max_false_positive": 0.05,
    },
    "federation-roundtrip": {
        "min_delivery_openclaw": 0.85,
        "min_delivery_paperclip_via_federation": 0.75,
    },
    "x402-onboarding": {
        # Percentage of x402-sign-required events the agent correctly
        # responded to with a SIGN decision (model-mode only).
        "min_sign_decision_rate": 0.80,
        # Hard cost cap — run aborts from the subscriber side when hit.
        "max_cost_usd": 2.00,
    },
}


def _tail_container(container, sink):
    try:
        for chunk in container.logs(stream=True, follow=True, since=int(time.time()) - 5):
            try:
                line = chunk.decode("utf-8", errors="replace").rstrip()
            except Exception:  # noqa: BLE001
                continue
            for sub in line.split("\n"):
                sub = sub.strip()
                if not sub.startswith("{"):
                    continue
                try:
                    rec = json.loads(sub)
                except Exception:  # noqa: BLE001
                    continue
                rec["_source_container"] = container.name
                sink.append(rec)
    except Exception as e:  # noqa: BLE001
        sink.append({"_source_container": container.name, "judge_error": str(e)})


def _collect(duration: int) -> list[dict]:
    client = docker.from_env()
    targets = []
    for c in client.containers.list():
        name = c.name
        if any(tag in name for tag in ("drain", "agent-openclaw", "agent-paperclip", "kite-server")):
            targets.append(c)

    events: list[dict] = []
    for c in targets:
        threading.Thread(target=_tail_container, args=(c, events), daemon=True).start()

    deadline = time.time() + duration
    while time.time() < deadline:
        time.sleep(1)
    time.sleep(1)
    return events


def _classify(events: list[dict]) -> dict:
    drain_sent: dict[str, dict] = {}
    received: dict[str, list[dict]] = defaultdict(list)
    responded: dict[str, list[dict]] = defaultdict(list)
    skipped: dict[str, list[dict]] = defaultdict(list)
    signed: dict[str, list[dict]] = defaultdict(list)
    decisions: dict[str, list[dict]] = defaultdict(list)
    federated: list[dict] = []
    errors: list[dict] = []
    total_cost_usd: float = 0.0

    for e in events:
        if "drain_event_id" in e and "sent_at" in e:
            drain_sent[e["drain_event_id"]] = e
        elif "error" in e and "drain_event_id" in e:
            errors.append(e)
        agent = e.get("agent")
        evt = e.get("evt")
        if agent and evt == "received":
            received[agent].append(e)
        elif agent and evt == "responded":
            responded[agent].append(e)
        elif agent and evt == "skipped":
            skipped[agent].append(e)
        elif agent and evt == "signed":
            signed[agent].append(e)
        elif agent and evt == "decision":
            decisions[agent].append(e)
            try:
                total_cost_usd += float(e.get("cost_usd") or 0)
            except Exception:  # noqa: BLE001
                pass
        elif agent and evt == "federated":
            federated.append(e)

    return {
        "drain_sent": drain_sent,
        "received": dict(received),
        "responded": dict(responded),
        "skipped": dict(skipped),
        "signed": dict(signed),
        "decisions": dict(decisions),
        "federated": federated,
        "drain_errors": errors,
        "total_cost_usd": total_cost_usd,
    }


def _summary(c: dict) -> dict:
    sent = len(c["drain_sent"])
    rec = {a: len(c["received"].get(a, [])) for a in ("openclaw", "paperclip")}
    resp = {a: len(c["responded"].get(a, [])) for a in ("openclaw", "paperclip")}
    skip = {a: len(c["skipped"].get(a, [])) for a in ("openclaw", "paperclip")}

    def pct(a: str) -> float:
        return round(100 * rec[a] / sent, 2) if sent else 0.0

    tag_counts: dict[str, int] = defaultdict(int)
    for evts in c["received"].values():
        for e in evts:
            tag_counts[e.get("scenario_tag") or "unknown"] += 1

    signed_ct = {a: len(c.get("signed", {}).get(a, [])) for a in ("openclaw", "paperclip")}
    decisions_ct = {a: len(c.get("decisions", {}).get(a, [])) for a in ("openclaw", "paperclip")}
    total_cost = round(c.get("total_cost_usd", 0.0), 6)

    out = {
        "scenario": SCENARIO,
        "topology": TOPOLOGY,
        "duration_sec": DURATION,
        "events_sent": sent,
        "drain_errors": len(c["drain_errors"]),
        "received": rec,
        "responded": resp,
        "skipped": skip,
        "signed": signed_ct,
        "decisions": decisions_ct,
        "federated_messages": len(c["federated"]),
        "delivery_pct": {a: pct(a) for a in ("openclaw", "paperclip")},
        "scenario_tag_breakdown": dict(tag_counts),
        "total_cost_usd": total_cost,
    }

    crit = PASS_CRITERIA.get(SCENARIO, {})
    pf: dict[str, bool] = {}
    if "min_delivery_openclaw" in crit:
        pf["openclaw_delivery"] = (pct("openclaw") / 100) >= crit["min_delivery_openclaw"]
    if "min_delivery_paperclip" in crit:
        pf["paperclip_delivery"] = (pct("paperclip") / 100) >= crit["min_delivery_paperclip"]
    if "min_recall_high" in crit:
        highs_received = tag_counts.get("filter-match", 0)
        expected_high = sent / 2 if sent else 0
        recall = (highs_received / expected_high) if expected_high else 0.0
        pf["recall_high"] = recall >= crit["min_recall_high"]
    if "min_sign_decision_rate" in crit:
        sign_required = tag_counts.get("x402-sign-required", 0)
        signed_total = sum(signed_ct.values())
        rate = (signed_total / sign_required) if sign_required else 0.0
        pf["sign_decision_rate"] = rate >= crit["min_sign_decision_rate"]
    if "max_cost_usd" in crit:
        pf["within_cost_cap"] = total_cost <= crit["max_cost_usd"]
    out["pass_criteria"] = crit
    out["checks"] = pf
    out["pass"] = bool(pf) and all(pf.values())
    return out


def _render(summary: dict, raw: list[dict]) -> None:
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2))
    (OUT / "raw.ndjson").write_text("\n".join(json.dumps(e) for e in raw))

    status = "✅ PASS" if summary.get("pass") else "❌ FAIL"
    cost_line = (
        f"- LLM cost: **${summary.get('total_cost_usd', 0):.4f}**"
        if summary.get("total_cost_usd") else ""
    )
    md = [
        f"# {status} — {SCENARIO} / {TOPOLOGY}",
        "",
        f"- Duration: **{DURATION}s**",
        f"- Events sent: **{summary['events_sent']}**",
        f"- Drain errors: **{summary['drain_errors']}**",
        f"- Federation forwards: **{summary['federated_messages']}**",
    ]
    if cost_line:
        md.append(cost_line)
    md += [
        "",
        "## Delivery",
        "",
        "| Agent | Received | Responded | Skipped | Delivery % |",
        "|---|---|---|---|---|",
    ]
    for a in ("openclaw", "paperclip"):
        md.append(
            f"| {a} | {summary['received'][a]} | {summary['responded'][a]} | "
            f"{summary['skipped'][a]} | {summary['delivery_pct'][a]}% |"
        )
    md += ["", "## Scenario tags observed", "", "| tag | count |", "|---|---|"]
    for tag, n in summary["scenario_tag_breakdown"].items():
        md.append(f"| `{tag}` | {n} |")
    md += ["", "## Pass/fail checks", ""]
    if summary["checks"]:
        for k, v in summary["checks"].items():
            md.append(f"- {'✅' if v else '❌'} **{k}**")
    else:
        md.append("_(no pass criteria configured for this scenario)_")
    (OUT / "summary.md").write_text("\n".join(md) + "\n")


def main() -> int:
    print(f"judge: running for {DURATION}s  scenario={SCENARIO}  topology={TOPOLOGY}", flush=True)
    raw = _collect(DURATION)
    print(f"judge: collected {len(raw)} log records", flush=True)
    classified = _classify(raw)
    summary = _summary(classified)
    _render(summary, raw)
    print(json.dumps(summary), flush=True)
    print(f"judge: wrote {OUT}", flush=True)
    return 0 if summary.get("pass", False) else 1


if __name__ == "__main__":
    sys.exit(main())
