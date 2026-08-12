# ============================================================================
# LocalNetwork Ecosystem — Dockerfile
#
# Three modes:
#   docker build --target server   ...   → mediation server
#   docker build --target proxy    ...   → reverse proxy
#   docker build                   ...   → client (default)
#
# Usage:
#   docker build -t localnetwork-server --target server .
#   docker run -p 54000:54000 -p 54001:54001 localnetwork-server
#
#   docker build -t localnetwork-proxy --target proxy .
#   docker run -p 80:80 -p 54010:54010 -v ./proxy.yaml:/etc/localnetwork/proxy.yaml localnetwork-proxy
# ============================================================================

FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install only the runtime deps
COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy the application code
COPY common/ common/
COPY server/ server/
COPY client/ client/
COPY proxy/ proxy/
COPY pyproject.toml .

# Install the package in editable mode so entry points work
RUN pip install -e .

# Healthcheck endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import socket; s=socket.socket(); s.connect(('127.0.0.1', int('${PORT:-54000}'))); s.close()" || exit 1

# ============================================================================
# Server image
# ============================================================================
FROM base AS server

ENV LOCALNETWORK_SERVER_HOST=0.0.0.0 \
    LOCALNETWORK_SERVER_PORT=54000 \
    LOCALNETWORK_SERVER_WEB_PORT=54001

EXPOSE 54000 54001

ENTRYPOINT ["localnetwork-server"]
CMD ["--host", "0.0.0.0", "--port", "54000", "--web-port", "54001"]


# ============================================================================
# Client image
# ============================================================================
FROM base AS client

ENV LOCALNETWORK_SERVER_HOST=server \
    LOCALNETWORK_SERVER_PORT=54000

EXPOSE 54002

ENTRYPOINT ["localnetwork-client"]
CMD ["--server", "server:54000", "--web-port", "54002"]


# ============================================================================
# Proxy image
# ============================================================================
FROM base AS proxy

ENV LOCALNETWORK_PROXY_WEB_PORT=54010

EXPOSE 80 443 54010

ENTRYPOINT ["localnetwork-proxy"]
CMD ["--config", "/etc/localnetwork/proxy.yaml", "--workers", "4"]
