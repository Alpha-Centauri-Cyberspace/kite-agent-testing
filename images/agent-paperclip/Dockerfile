# Agent container: paperclip + kite-cli
#
# Chunk 3 of the test harness. Symmetric to agent-openclaw but uses paperclip's
# runtime. Installer URL TBD — verify against paperclipai CLI docs at build time.

FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl jq bash \
  && rm -rf /var/lib/apt/lists/*

# TODO(Chunk 3): install paperclip
# RUN curl -fsSL https://install.paperclip.ai/ | sh

RUN curl -fsSL https://getkite.sh/install | sh || \
    echo "::warning::getkite.sh/install will install at runtime"

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV AGENT_NAME=paperclip

ENTRYPOINT ["/entrypoint.sh"]
