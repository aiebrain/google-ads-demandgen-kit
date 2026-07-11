FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY google_ads_mcp_server.py .

# Cloud Run sends traffic to $PORT. Serve the Starlette `app` (streamable HTTP MCP).
CMD ["sh", "-c", "uvicorn google_ads_mcp_server:app --host 0.0.0.0 --port ${PORT:-8080}"]
