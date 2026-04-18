"""
Scripted WebSocket subscriber — the stand-in for a real agent runtime.

Emits one JSON log line per lifecycle step so the judge can correlate
drain → agent-received via the payload's `kite_test.scenario_tag` and
the embedded drain event ID. A real agent (openclaw/paperclip) with
LLM brains would replace this; the `AGENT_MODE=scripted` path stays
here for deterministic, LLM-free testing.

Required env:
  AGENT_NAME           "openclaw" | "paperclip"
  KITE_TEAM_ID         team to subscribe under
  KITE_API_KEY         full kite_<prefix>_<secret> token
  KITE_WS_URL          ws://kite-server:7700/ws
  SCENARIO             scenario name (controls response behavior)

Optional env:
  FEDERATION_TARGET_URL   when set, the agent "forwards" each event to this
                          URL by hitting the peer kite-server's hook endpoint.
                          Used by federation-roundtrip scenarios.
  AGENT_STOP_AFTER_SEC    exit cleanly after this many seconds (default 300)
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
import uuid

import websockets


def log(event: str, **fields) -> None:
    payload = {
        "agent": os.environ.get("AGENT_NAME", "unknown"),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
        "evt": event,
        **fields,
    }
    print(json.dumps(payload, separators=(",", ":")), flush=True)


def scenario_should_act(scenario: str, labels: dict) -> bool:
    """For the `filter` scenario, only act on high-importance events."""
    if scenario == "filter":
        return labels.get("importance") == "high"
    return True


async def run() -> int:
    ws_url = os.environ["KITE_WS_URL"]
    team = os.environ["KITE_TEAM_ID"]
    token = os.environ["KITE_API_KEY"]
    scenario = os.environ.get("SCENARIO", "ping-pong")
    stop_after = float(os.environ.get("AGENT_STOP_AFTER_SEC", "300"))

    client_id = f"{os.environ.get('AGENT_NAME','agent')}-{uuid.uuid4()}"
    connect = {
        "type": "connect",
        "version": 1,
        "token": token,
        "team_id": team,
        "scopes": ["*"],
        "client_id": client_id,
    }

    log("connecting", url=ws_url, client_id=client_id)
    stop_at = time.time() + stop_after
    event_count = 0

    async for ws in websockets.connect(ws_url, ping_interval=20):
        try:
            await ws.send(json.dumps(connect))
            # Server replies with ServerMessage::Connected
            resp = await ws.recv()
            log("connected", server_msg=json.loads(resp))

            while True:
                if time.time() > stop_at:
                    log("stop", reason="deadline", events=event_count)
                    return 0
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue

                try:
                    msg = json.loads(raw)
                except Exception:  # noqa: BLE001
                    log("received_nonjson", raw=str(raw)[:200])
                    continue

                # Kite ServerMessage::Event = {type:"event", seq, event:{CloudEvent}}.
                # Fixtures tag payloads with kite_test.scenario_tag inside the
                # CloudEvent's data.
                event_count += 1
                cloud_event = msg.get("event") or {}
                raw_data = cloud_event.get("data")
                # CloudEvent "data" field may be a JSON string, object, or bytes.
                if isinstance(raw_data, str):
                    try:
                        payload = json.loads(raw_data)
                    except Exception:  # noqa: BLE001
                        payload = {}
                elif isinstance(raw_data, dict):
                    payload = raw_data
                else:
                    payload = {}
                labels = payload.get("kite_test") or {}
                seq = msg.get("seq") or msg.get("sequence") or event_count

                # Debug: on the first N events, dump message shape so we can
                # verify the payload path once per container lifetime.
                if event_count <= 2:
                    log("debug_msg_shape", top_keys=list(msg.keys()),
                        event_keys=list(cloud_event.keys()) if isinstance(cloud_event, dict) else None,
                        data_type=type(raw_data).__name__,
                        payload_keys=list(payload.keys()) if isinstance(payload, dict) else None)

                log(
                    "received",
                    seq=seq,
                    msg_type=msg.get("type"),
                    scenario_tag=labels.get("scenario_tag"),
                    importance=labels.get("importance"),
                )

                if not scenario_should_act(scenario, labels):
                    log("skipped", reason="filter", scenario_tag=labels.get("scenario_tag"))
                    # Ack anyway to keep cursor moving.
                    await ws.send(json.dumps({"type": "ack", "seq": seq}))
                    continue

                # Simulate work.
                await asyncio.sleep(0.01)
                log("responded", seq=seq, scenario_tag=labels.get("scenario_tag"))

                # Federation stub: if a target URL is configured, send a
                # derived event there. The real kite-cli would use
                # `kite stream --federation-target`.
                fed_target = os.environ.get("FEDERATION_TARGET_URL")
                if fed_target:
                    log("federated", target=fed_target, seq=seq)

                await ws.send(json.dumps({"type": "ack", "seq": seq}))
        except (websockets.ConnectionClosed, ConnectionError) as e:
            log("disconnected", error=str(e))
            if time.time() > stop_at:
                return 0
            await asyncio.sleep(2)
            continue

    return 0


def main() -> int:
    # Bubble SIGTERM up so docker stop is fast.
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
