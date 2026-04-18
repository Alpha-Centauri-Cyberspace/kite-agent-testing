# Bootstrap sidecar
#
# Chunk 5. One-shot container: waits for postgres, seeds team + API key +
# hook token, writes the env contract to /run/kite-env for agent containers.

FROM postgres:17-alpine

WORKDIR /bootstrap

# bcrypt for key hashing (matches kite-server's bcrypt cost=12 expectations);
# openssl for token generation; bash for the script.
RUN apk add --no-cache bash openssl py3-bcrypt

COPY bootstrap.sh /bootstrap/bootstrap.sh
COPY gen-token.py  /bootstrap/gen-token.py
RUN chmod +x /bootstrap/bootstrap.sh

ENTRYPOINT ["/bootstrap/bootstrap.sh"]
