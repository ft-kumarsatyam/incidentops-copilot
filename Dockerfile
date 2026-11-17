FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY runbooks ./runbooks
RUN pip install --no-cache-dir .

RUN useradd --create-home appuser && mkdir -p /app/.data && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=5 \
  CMD python -c "import urllib.request as u; u.urlopen('http://localhost:8000/health', timeout=3)"
CMD ["uvicorn", "incidentops.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
