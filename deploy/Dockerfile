# Build both the customized console and the Harness v3 Node runtime.
FROM node:22-bookworm-slim AS runtime-builder
RUN apt-get update && apt-get install -y --no-install-recommends python3 git ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY . .
RUN python3 prepare.py --without-backend --no-cli-link

FROM python:3.11-slim-bookworm AS runner
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ULTRARAG_DATA_DIR=/app/data \
    DATABASE_URL=sqlite:////app/data/skill_agent_loop.db \
    STAFFDECK_DISTRIBUTION_MANIFEST=/app/distribution/manifest.json \
    STAFFDECK_INSTALL_ROOT=/app \
    ULTRARAG_HOST=0.0.0.0 \
    ULTRARAG_PORT=5173 \
    STAFFDECK_HEADLESS=true
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates bubblewrap bash procps libstdc++6 \
    && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 -s /bin/bash staffdeck
COPY --from=runtime-builder /usr/local/bin/node /usr/local/bin/node
WORKDIR /app
COPY --from=runtime-builder /app /app
RUN pip install --no-cache-dir ./backend && python packaging/distribution/install_dependencies.py
RUN mkdir -p /app/data && chown -R staffdeck:staffdeck /app
USER staffdeck
EXPOSE 5173
CMD ["python", "launch.py"]
