# syntax=docker/dockerfile:1
FROM ghcr.io/astral-sh/uv:0.12.5 AS uv
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_LINK_MODE=copy \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    PATH="/opt/venv/bin:$PATH" \
    HOME=/var/lib/backendscout/home \
    LANG=C.UTF-8 \
    TZ=Asia/Hebron

COPY --from=uv /uv /usr/local/bin/uv
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       ca-certificates tzdata libreoffice-writer fontconfig \
       fonts-crosextra-carlito fonts-dejavu-core fonts-liberation2 fonts-noto-core \
       xvfb xauth util-linux \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --no-editable --no-cache
RUN python -m playwright install --with-deps chromium \
    && chmod -R a+rX /opt/playwright \
    && rm -rf /var/lib/apt/lists/*
COPY README.md ./
COPY src/backend_scout/ ./src/backend_scout/
RUN uv sync --locked --no-dev --no-editable --no-cache
COPY config/candidate_profile.example.yaml config/career_evidence.example.yaml \
     config/cv_style.example.yaml config/target_companies.example.yaml \
     config/scouting_preferences.example.yaml config/repo_sources.example.yaml /app/examples/

RUN groupadd --gid 10001 backendscout \
    && useradd --uid 10001 --gid 10001 --no-create-home \
       --home-dir /var/lib/backendscout/home --shell /usr/sbin/nologin backendscout \
    && mkdir -p /var/lib/backendscout/data/runtime /var/lib/backendscout/applications \
       /var/lib/backendscout/artifacts /var/lib/backendscout/browser-profile \
       /var/lib/backendscout/home /var/lib/backendscout/secrets \
       /var/lib/backendscout/config \
    && chown -R 10001:10001 /var/lib/backendscout \
    && chmod 0700 /var/lib/backendscout /var/lib/backendscout/secrets

WORKDIR /var/lib/backendscout
USER 10001:10001

# Offline packaging check only: no Settings, CLI, credentials, DBOS or API clients.
CMD ["python", "-c", "import importlib.metadata as m, os, shutil, sqlite3, sys; from pathlib import Path; from playwright.sync_api import sync_playwright; assert sys.version_info[:2] == (3, 12); assert os.getuid() == 10001; assert Path.cwd() == Path('/var/lib/backendscout'); assert all(shutil.which(c) for c in ('soffice', 'xvfb-run', 'Xvfb', 'xauth', 'flock')); assert len(list(Path('/app/examples').glob('*.example.yaml'))) == 6; sqlite3.connect(':memory:').execute('SELECT 1'); assert callable(sync_playwright); assert any(p.is_file() for p in Path('/opt/playwright').glob('chromium-*/**/chrome')); print('Offline packaging check passed: backend-scout ' + m.version('backend-scout'))"]
