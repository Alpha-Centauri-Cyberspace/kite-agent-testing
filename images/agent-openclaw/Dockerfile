# Agent container: openclaw persona + kite-cli
#
# Chunk 2. Receives env config via /run/kite-env/openclaw.env, subscribes to
# kite-server's WebSocket, runs in scripted or model mode.

FROM debian:bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    AGENT_NAME=openclaw \
    PATH="/root/.kite/bin:/usr/local/bin:/usr/bin:/bin"

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl jq bash python3 python3-pip python3-websockets \
  && rm -rf /var/lib/apt/lists/*

# Install kite CLI via the public installer (falls back silently; the
# scripted path doesn't require the kite binary today).
RUN set -e; \
    if curl -fsSL --retry 3 --max-time 30 https://getkite.sh/install -o /tmp/install.sh; then \
      sh /tmp/install.sh || true; \
      ln -sf /root/.kite/bin/kite /usr/local/bin/kite 2>/dev/null || true; \
    else \
      echo '::warning:: getkite.sh/install unreachable at build time'; \
    fi; \
    rm -f /tmp/install.sh

COPY entrypoint.sh /entrypoint.sh
COPY scripted-subscriber.py /opt/scripted-subscriber.py
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/entrypoint.sh"]
