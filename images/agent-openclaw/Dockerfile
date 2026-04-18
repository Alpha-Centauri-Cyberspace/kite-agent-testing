# Agent container: openclaw + kite-cli
#
# Chunk 2 of the test harness. Receives env config, installs kite-cli via the
# public installer, and runs in either scripted or model mode.

FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl jq bash \
  && rm -rf /var/lib/apt/lists/*

# Install openclaw — verify exact installer URL against openclaw docs before
# pinning (this may require `context7 resolve-library-id openclaw` at build time).
# TODO(Chunk 2): RUN curl -fsSL https://get.openclaw.dev/install.sh | sh

# Install kite via the public installer that the CLI ships.
RUN curl -fsSL https://getkite.sh/install | sh || \
    echo "::warning::getkite.sh/install not available yet — build will install at runtime"

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENV AGENT_NAME=openclaw

ENTRYPOINT ["/entrypoint.sh"]
