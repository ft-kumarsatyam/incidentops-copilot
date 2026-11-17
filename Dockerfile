FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY runbooks ./runbooks
RUN pip install --no-cache-dir .

RUN useradd --create-home appuser && mkdir -p /app/.checkpoints \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
CMD ["uvicorn", "workflow_automation.api:app", "--host", "0.0.0.0", "--port", "8000"]
