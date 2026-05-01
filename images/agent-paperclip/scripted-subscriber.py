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
  KITE_HTTP_URL        http://kite-server:7700 — used for the new hosted
                       agent-to-agent endpoint POST /api/v1/agents/messages.
  MY_AGENT_ID          this agent's stable identity (defaults to AGENT_NAME).
                       Subscribed with scope `agent_to:<MY_AGENT_ID>` so the
                       server only fans com.kite.agent.message events for
                       this recipient down the WebSocket.
  PEER_AGENT_ID        the agent on the *same team* this agent will message
                       in the `a2a-ping-pong` scenario.
  AGENT_STOP_AFTER_SEC exit cleanly after this many seconds (default 300)
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import time
import uuid

import urllib.request
import urllib.error
import websockets


AGENT_MESSAGE_EVENT_TYPE = "com.kite.agent.message"


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


def post_agent_message(
    http_base: str,
    team_id: str,
    api_key: str,
    msg_from: str,
    msg_to: str,
    body: str,
) -> None:
    """POST a com.kite.agent.message via the hosted A2A endpoint.

    Synchronous urllib call — runs inside an asyncio task via run_in_executor
    so it doesn't block the WebSocket recv loop.
    """
    url = f"{http_base.rstrip('/')}/api/v1/agents/messages?team_id={team_id}"
    req = urllib.request.Request(
        url,
        data=json.dumps({"from": msg_from, "to": msg_to, "body": body}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except urllib.error.HTTPError as e:
        log("a2a_send_failed", to=msg_to, status=e.code, error=e.reason)
        raise
    except Exception as e:  # noqa: BLE001
        log("a2a_send_failed", to=msg_to, error=str(e))
        raise


async def run() -> int:
    ws_url = os.environ["KITE_WS_URL"]
    team = os.environ["KITE_TEAM_ID"]
    token = os.environ["KITE_API_KEY"]
    http_base = os.environ.get("KITE_HTTP_URL", "")
    scenario = os.environ.get("SCENARIO", "ping-pong")
    stop_after = float(os.environ.get("AGENT_STOP_AFTER_SEC", "300"))

    my_agent_id = os.environ.get("MY_AGENT_ID") or os.environ.get("AGENT_NAME", "unknown")
    peer_agent_id = os.environ.get("PEER_AGENT_ID", "")

    client_id = f"{os.environ.get('AGENT_NAME','agent')}-{uuid.uuid4()}"
    # Wildcard scope keeps the existing webhook-driven scenarios (ping-pong,
    # filter) working unchanged. Adding agent_to:<id> here would be redundant
    # since `*` already matches everything; the server-side filter is exposed
    # for clients that want to subscribe to *only* their agent traffic, which
    # an LLM-driven agent might want — for the harness we tail everything.
    scopes = ["*"]
    connect = {
        "type": "connect",
        "version": 1,
        "token": token,
        "team_id": team,
        "scopes": scopes,
        "client_id": client_id,
    }

    log(
        "connecting",
        url=ws_url,
        client_id=client_id,
        my_agent_id=my_agent_id,
        peer_agent_id=peer_agent_id or None,
    )
    stop_at = time.time() + stop_after
    event_count = 0
    loop = asyncio.get_event_loop()

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
                event_type = cloud_event.get("type") or ""
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
                seq = msg.get("seq") or msg.get("sequence") or event_count

                # Branch 1: inbound agent-to-agent message.
                if event_type == AGENT_MESSAGE_EVENT_TYPE and isinstance(payload, dict):
                    to_field = payload.get("to") or ""
                    from_field = payload.get("from") or ""
                    body_field = payload.get("body") or ""
                    if to_field != my_agent_id:
                        # Server-side scope filtering should have prevented
                        # this, but be defensive — wildcard subs see everything.
                        await ws.send(json.dumps({"type": "ack", "seq": seq}))
                        continue

                    # Distinguish a fresh incoming message from a returned echo.
                    is_echo = body_field.startswith("re:")
                    log(
                        "a2a_received",
                        seq=seq,
                        from_agent=from_field,
                        body=body_field,
                        is_echo=is_echo,
                        source_seq=_extract_source_seq(body_field),
                    )

                    if not is_echo and peer_agent_id and http_base:
                        # Reply once, prefixed with `re:` so the original
                        # sender doesn't bounce it back forever.
                        try:
                            await loop.run_in_executor(
                                None,
                                post_agent_message,
                                http_base,
                                team,
                                token,
                                my_agent_id,
                                from_field,
                                f"re:{body_field}",
                            )
                            log(
                                "a2a_echoed",
                                seq=seq,
                                to_agent=from_field,
                                source_seq=_extract_source_seq(body_field),
                            )
                        except Exception:  # noqa: BLE001
                            pass

                    await ws.send(json.dumps({"type": "ack", "seq": seq}))
                    continue

                # Branch 2: inbound webhook / drain event (existing path).
                labels = payload.get("kite_test") or {}

                # Debug: on the first N events, dump message shape so we can
                # verify the payload path once per container lifetime.
                if event_count <= 2:
                    log("debug_msg_shape", top_keys=list(msg.keys()),
                        event_keys=list(cloud_event.keys()) if isinstance(cloud_event, dict) else None,
                        data_type=type(raw_data).__name__,
                        payload_keys=list(payload.keys()) if isinstance(payload, dict) else None,
                        event_type=event_type)

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

                # In the a2a-ping-pong scenario, openclaw is the originator:
                # for every drain event it sends a com.kite.agent.message to
                # paperclip via the new hosted A2A endpoint. Paperclip then
                # echoes back through the inbound branch above.
                if (
                    scenario == "a2a-ping-pong"
                    and peer_agent_id
                    and http_base
                    and my_agent_id == "agent-openclaw"
                ):
                    try:
                        await loop.run_in_executor(
                            None,
                            post_agent_message,
                            http_base,
                            team,
                            token,
                            my_agent_id,
                            peer_agent_id,
                            f"seq={seq}",
                        )
                        log("a2a_sent", to_agent=peer_agent_id, source_seq=seq)
                    except Exception:  # noqa: BLE001
                        pass

                await ws.send(json.dumps({"type": "ack", "seq": seq}))
        except (websockets.ConnectionClosed, ConnectionError) as e:
            log("disconnected", error=str(e))
            if time.time() > stop_at:
                return 0
            await asyncio.sleep(2)
            continue

    return 0


def _extract_source_seq(body: str) -> str | None:
    """Pull `seq=<n>` out of a body like `seq=42` or `re:seq=42`."""
    if not body:
        return None
    text = body[3:] if body.startswith("re:") else body
    if text.startswith("seq="):
        return text[4:]
    return None


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
