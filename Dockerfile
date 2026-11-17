# syntax=docker/dockerfile:1

# ---- Stage 1: build the web UI ------------------------------------------------------------
FROM node:22-slim AS web
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
# vite.config.ts writes to ../src/incidentops/static, i.e. /app/src/incidentops/static
RUN npm run build

# ---- Stage 2: the API, serving the built UI -----------------------------------------------
FROM python:3.15-slim
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY --from=web /app/src/incidentops/static ./src/incidentops/static
COPY runbooks ./runbooks
RUN pip install --no-cache-dir .

RUN useradd --create-home appuser && mkdir -p /app/.data && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
  CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8000/health', timeout=3)"
CMD ["uvicorn", "incidentops.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
