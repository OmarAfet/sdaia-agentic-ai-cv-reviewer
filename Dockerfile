# Simulated production deployment: one container, no secrets in the image.
#   docker build -t ai-cv-reviewer .
#   docker run --rm --env-file .env -v "$PWD/data/cvs:/app/data/cvs:ro" \
#     -v "$PWD/reports:/app/reports" -v "$PWD/logs:/app/logs" \
#     ai-cv-reviewer review data/cvs/strong_backend.pdf --job backend-engineer --auto-approve
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY data/jobs.json ./data/jobs.json
RUN uv sync --frozen --no-dev
RUN useradd --create-home app && mkdir -p logs reports && chown -R app /app
USER app
ENTRYPOINT ["uv", "run", "--no-sync", "cv-review"]
CMD ["--help"]
