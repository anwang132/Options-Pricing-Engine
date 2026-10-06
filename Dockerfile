# Single-container deployment: FastAPI serves the API and the built UI.
# Build:  docker build -t options-workbench .
# Run:    docker run --rm -p 8000:8000 options-workbench

FROM node:22-slim AS ui
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY ui/ ./
RUN npm run build

FROM python:3.13-slim AS app
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH"
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src/ src/
COPY config/ config/
COPY fixtures/ fixtures/
COPY examples/ examples/
RUN uv sync --frozen --no-dev
COPY --from=ui /ui/dist ui/dist
RUN useradd --create-home app && chown -R app /app
USER app
ENV OPTIONS_ENGINE_WORKERS=2 OPTIONS_ENGINE_MAX_IN_FLIGHT=8 OPTIONS_ENGINE_TIMEOUT_SECONDS=30
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health')"
CMD ["options-engine", "serve", "--host", "0.0.0.0", "--port", "8000"]
