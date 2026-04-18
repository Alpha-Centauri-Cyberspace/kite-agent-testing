"""
LLM-backed subscriber (AGENT_MODE=model).

Same WebSocket subscriber as scripted-subscriber.py but each incoming
event gets fed to an OpenRouter-served LLM which decides whether to
act, skip, or (for x402-onboarding scenario) sign a payment.

Emits the same JSON log-line contract the judge already understands,
plus a `cost_usd` field on every `decision` event so cost tracking
can aggregate across the whole run.

Required env (in addition to the scripted-subscriber set):
  OPENROUTER_API_KEY     https://openrouter.ai/keys
  AGENT_MODEL            e.g. "anthropic/claude-opus-4-7"
  MAX_COST_USD           optional; when cumulative cost exceeds, stop

OpenRouter billing is queried per-response via /generation?id=<id>.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
import uuid
from typing import Any

import httpx
import websockets


OPENROUTER_CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_GENERATION_URL = "https://openrouter.ai/api/v1/generation"


def log(event: str, **fields) -> None:
    payload = {
        "agent": os.environ.get("AGENT_NAME", "unknown"),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        "evt": event,
        **fields,
    }
    print(json.dumps(payload, separators=(",", ":")), flush=True)


def system_prompt(scenario: str) -> str:
    base = (
        "You are a webhook-event agent. For each event you are given, "
        "decide whether to: ACT (process normally), SKIP (ignore as noise), "
        "or SIGN (satisfy a payment requirement and retry). "
        "Reply ONLY with a single JSON object of the form "
        '{"decision":"ACT|SKIP|SIGN","reason":"<1 sentence>"}.'
    )
    if scenario == "x402-onboarding":
        return base + (
            " If the event payload contains a `payment_required` block, respond "
            "with decision=SIGN. Otherwise decision=ACT."
        )
    if scenario == "filter":
        return base + (
            " If the event payload indicates low importance (kite_test.importance "
            "== 'low'), respond with decision=SKIP. Otherwise decision=ACT."
        )
    return base


async def call_openrouter(
    http: httpx.AsyncClient,
    api_key: str,
    model: str,
    scenario: str,
    event_json: dict,
) -> tuple[str, str, str, float]:
    """
    Returns (decision, reason, generation_id, cost_usd).
    cost_usd is 0.0 if the /generation lookup fails.
    """
    messages = [
        {"role": "system", "content": system_prompt(scenario)},
        {
            "role": "user",
            "content": "Event:\n" + json.dumps(event_json, separators=(",", ":")),
        },
    ]
    resp = await http.post(
        OPENROUTER_CHAT_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/Alpha-Centauri-Cyberspace/kite-agent-testing",
            "X-Title": "kite-agent-testing",
        },
        json={
            "model": model,
            "messages": messages,
            "max_tokens": 120,
            "response_format": {"type": "json_object"},
        },
        timeout=30.0,
    )
    resp.raise_for_status()
    body = resp.json()
    gen_id = body.get("id", "")
    choice = (body.get("choices") or [{}])[0]
    content = (choice.get("message") or {}).get("content") or "{}"
    try:
        parsed = json.loads(content)
    except Exception:  # noqa: BLE001
        parsed = {"decision": "ACT", "reason": "parse_fallback"}
    decision = str(parsed.get("decision", "ACT")).upper()
    reason = str(parsed.get("reason", ""))[:160]

    # Cost: look up via /generation. Add a small delay — OpenRouter's
    # generation billing is eventually-consistent (usually 1-2s).
    cost = 0.0
    try:
        await asyncio.sleep(1.0)
        gresp = await http.get(
            f"{OPENROUTER_GENERATION_URL}?id={gen_id}",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=15.0,
        )
        if gresp.status_code == 200:
            gdata = (gresp.json() or {}).get("data") or {}
            cost = float(gdata.get("total_cost") or 0.0)
    except Exception:  # noqa: BLE001
        pass
    return decision, reason, gen_id, cost


async def run() -> int:
    ws_url = os.environ["KITE_WS_URL"]
    team = os.environ["KITE_TEAM_ID"]
    token = os.environ["KITE_API_KEY"]
    scenario = os.environ.get("SCENARIO", "x402-onboarding")
    stop_after = float(os.environ.get("AGENT_STOP_AFTER_SEC", "300"))

    api_key = os.environ.get("OPENROUTER_API_KEY")
    model = os.environ.get("AGENT_MODEL") or "anthropic/claude-haiku-4-5"
    max_cost = float(os.environ.get("MAX_COST_USD", "0") or 0)

    if not api_key:
        log("fatal", error="OPENROUTER_API_KEY not set for AGENT_MODE=model")
        return 2

    client_id = f"{os.environ.get('AGENT_NAME','agent')}-{uuid.uuid4()}"
    connect = {
        "type": "connect", "version": 1, "token": token,
        "team_id": team, "scopes": ["*"], "client_id": client_id,
    }

    log("connecting", url=ws_url, model=model, scenario=scenario, client_id=client_id)
    stop_at = time.time() + stop_after
    event_count = 0
    cumulative_cost = 0.0

    async with httpx.AsyncClient() as http:
        async for ws in websockets.connect(ws_url, ping_interval=20):
            try:
                await ws.send(json.dumps(connect))
                resp = await ws.recv()
                log("connected", server_msg=json.loads(resp))

                while True:
                    if time.time() > stop_at:
                        log("stop", reason="deadline", events=event_count,
                            cost_usd=round(cumulative_cost, 6))
                        return 0
                    if max_cost and cumulative_cost >= max_cost:
                        log("stop", reason="max_cost", events=event_count,
                            cost_usd=round(cumulative_cost, 6))
                        return 0
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue

                    try:
                        msg = json.loads(raw)
                    except Exception:  # noqa: BLE001
                        continue

                    event_count += 1
                    cloud_event = msg.get("event") or {}
                    raw_data = cloud_event.get("data")
                    if isinstance(raw_data, str):
                        try:
                            payload: dict[str, Any] = json.loads(raw_data)
                        except Exception:  # noqa: BLE001
                            payload = {}
                    elif isinstance(raw_data, dict):
                        payload = raw_data
                    else:
                        payload = {}
                    labels = payload.get("kite_test") or {}
                    seq = msg.get("seq") or event_count

                    log("received", seq=seq,
                        scenario_tag=labels.get("scenario_tag"),
                        importance=labels.get("importance"))

                    try:
                        decision, reason, gen_id, cost = await call_openrouter(
                            http, api_key, model, scenario, payload)
                    except Exception as e:  # noqa: BLE001
                        log("openrouter_error", seq=seq, error=str(e)[:200])
                        await ws.send(json.dumps({"type": "ack", "seq": seq}))
                        continue

                    cumulative_cost += cost
                    log("decision", seq=seq, decision=decision, reason=reason,
                        model=model, generation_id=gen_id,
                        cost_usd=round(cost, 6),
                        cumulative_cost_usd=round(cumulative_cost, 6),
                        scenario_tag=labels.get("scenario_tag"))

                    if decision == "SKIP":
                        log("skipped", seq=seq, reason="llm_skip",
                            scenario_tag=labels.get("scenario_tag"))
                    elif decision == "SIGN":
                        # Stub: pretend to sign + retry. A real implementation
                        # would POST back to a facilitator with a signed payload.
                        log("signed", seq=seq,
                            scenario_tag=labels.get("scenario_tag"))
                        log("responded", seq=seq,
                            scenario_tag=labels.get("scenario_tag"))
                    else:
                        log("responded", seq=seq,
                            scenario_tag=labels.get("scenario_tag"))

                    await ws.send(json.dumps({"type": "ack", "seq": seq}))
            except (websockets.ConnectionClosed, ConnectionError) as e:
                log("disconnected", error=str(e))
                if time.time() > stop_at:
                    return 0
                await asyncio.sleep(2)
                continue

    return 0


def main() -> int:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda: (log("shutdown"), loop.stop()))
    try:
        return loop.run_until_complete(run())
    except Exception as e:  # noqa: BLE001
        log("fatal", error=str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
