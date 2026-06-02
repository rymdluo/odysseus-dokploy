# Read-only MCP sidecar for the trackers feature. Tiny: stdlib sqlite3 + mcp.
FROM python:3.12-slim

# Non-root. uid 1000 aligns with the default Odysseus PUID so it can read app.db
# even if that file is owner-only (0600). /state holds the persisted throttle and
# is pre-created owned by `app` so a fresh named volume inherits that ownership.
RUN useradd -u 1000 -m -s /bin/sh app

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY server.py .
RUN mkdir -p /state && chown -R app:app /app /state

USER app

# SSE server. Reads Odysseus's app.db from a READ-ONLY mount at /data (compose).
EXPOSE 8585
CMD ["python", "server.py"]
