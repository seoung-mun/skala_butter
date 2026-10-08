# Single container: FastAPI serving + CSV operations store + local MLflow (SQLite) in /app/runtime.
# Runs as-is on a Hugging Face Docker Space (port 7860, non-root user 1000) and with docker compose.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    MPLCONFIGDIR=/tmp/mpl MLFLOW_DISABLE_AGENT_HINT=1 TF_CPP_MIN_LOG_LEVEL=2 \
    BUTTERCAST_DATA_DIR=/app/data LOADING_MODE=lazy HOME=/home/user GIT_PYTHON_REFRESH=quiet

# Spaces run the container as uid 1000, so the app folder (runtime store, MLflow DB) must be writable by it.
RUN useradd -m -u 1000 user && mkdir -p /app/runtime && chown -R user /app
WORKDIR /app

# Dependencies first (stub package) so code edits do not reinstall TensorFlow.
COPY --chown=user pyproject.toml ./
RUN mkdir buttercast && touch buttercast/__init__.py && pip install --no-cache-dir -e '.[ml]' && chown -R user /app

COPY --chown=user data ./data
COPY --chown=user buttercast ./buttercast

USER user
EXPOSE 7860
CMD ["uvicorn", "buttercast.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "7860", "--workers", "1"]
