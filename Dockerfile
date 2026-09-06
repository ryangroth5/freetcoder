# syntax=docker/dockerfile:1
# Every step here was verified interactively -- see docs/container-discovery.md

# ---------------------------------------------------------------- base
FROM python:3.12-slim-bookworm AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    NODE_MAJOR=22

# libseccomp2 backs pyseccomp, which is how the runner blocks network egress
# without needing CAP_SYS_ADMIN. gcc/make are needed by some wheels.
RUN apt-get update -qq \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends \
      curl ca-certificates gcc make procps libseccomp2 \
 && curl -fsSL https://deb.nodesource.com/setup_${NODE_MAJOR}.x | bash - \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*

# Pinned: 1.1.406 is the version whose LSP handshake we verified.
# tsc compiles TypeScript submissions (type errors become compile errors);
# typescript-language-server backs the editor for .js and .ts.
RUN npm i -g --no-audit --no-fund \
      pyright@1.1.406 \
      typescript@5.7.3 \
      typescript-language-server@4.3.3 \
 && pyright-langserver --help >/dev/null 2>&1 || true

# The unprivileged identity every submission executes as. Nologin: it is an
# execution target, never a login account.
RUN groupadd -r runner \
 && useradd -r -g runner -m -d /home/runner -s /usr/sbin/nologin runner

WORKDIR /srv/app

# ---------------------------------------------------------- python deps
FROM base AS pydeps
COPY backend/pyproject.toml /srv/app/backend/
RUN pip install --no-cache-dir -e "/srv/app/backend[dev]" 2>/dev/null \
 || pip install --no-cache-dir \
      "fastapi>=0.115" "uvicorn[standard]>=0.32" "pydantic>=2.9" \
      "pydantic-settings>=2.6" "openai>=1.54" "aiosqlite>=0.20" \
      "PyYAML>=6.0" "httpx>=0.27" "pyseccomp>=0.1.2"

# -------------------------------------------------------------- library
# Its own service: accounts, voting and auth belong here later, so the
# boundary is real from the start.
FROM pydeps AS library
COPY library/ /srv/app/library/
ENV PYTHONPATH=/srv/app \
    LIBRARY_DB_PATH=/data/library.db
RUN mkdir -p /data
EXPOSE 8090
CMD ["uvicorn", "library.app:app", "--host", "0.0.0.0", "--port", "8090"]

# ------------------------------------------------------------------ dev
# Source is bind-mounted READ-ONLY at runtime (see docker-compose.yml):
# a submission escaped through a writable mount during discovery.
FROM pydeps AS dev
RUN pip install --no-cache-dir \
      pytest pytest-asyncio pytest-timeout ruff mypy types-PyYAML

# Playwright browser, baked in so `make e2e` works from a clean clone rather
# than needing an ad-hoc install into a running container.
RUN npm i -g --no-audit --no-fund @playwright/test@^1.56 \
 && npx --yes playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*
ENV PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright
ENV FREETCODER_DEV=1
EXPOSE 8080 5173
CMD ["uvicorn", "freetcoder.app:app", "--host", "0.0.0.0", "--port", "8080", \
     "--reload", "--reload-dir", "/srv/app/backend"]

# ------------------------------------------------------- frontend build
FROM base AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm ci --no-audit --no-fund 2>/dev/null || npm i --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ----------------------------------------------------------------- prod
FROM pydeps AS prod
COPY backend/ /srv/app/backend/
COPY --from=frontend /build/dist /srv/app/static
# Root-owned, world-readable, not world-writable: verified that uid=runner
# cannot write anywhere outside its per-run tmpdir.
RUN chown -R root:root /srv/app && chmod -R go-w /srv/app \
 && mkdir -p /data && chown root:root /data
ENV FREETCODER_STATIC_DIR=/srv/app/static \
    FREETCODER_DB_PATH=/data/freetcoder.db \
    PYTHONPATH=/srv/app/backend
EXPOSE 8080
CMD ["uvicorn", "freetcoder.app:app", "--host", "0.0.0.0", "--port", "8080"]
