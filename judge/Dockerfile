# Judge / orchestrator
#
# Chunk 6. Tails logs from agent + drain containers, correlates events, writes
# summary.md + summary.json to /out.

FROM python:3.13-slim

WORKDIR /app
RUN pip install --no-cache-dir docker==7.1.0

COPY judge.py /app/judge.py

CMD ["python", "-u", "judge.py"]
