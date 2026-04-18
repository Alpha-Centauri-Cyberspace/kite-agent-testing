# Fake log-drain webhook firehose
#
# Chunk 4. Sends fixture payloads to a Kite hook endpoint at a configured rate.
# Reference HMAC-SHA256 signing for github source:
#   kite-server: crates/kite-server/src/routes/hooks.rs:143-167
#   kite:        scripts/test-roundtrip.sh:270-276

FROM python:3.13-slim

WORKDIR /app

COPY drain.py /app/drain.py
COPY fixtures/ /app/fixtures/

CMD ["python", "-u", "drain.py"]
