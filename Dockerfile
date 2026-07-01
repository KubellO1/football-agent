# ---------------------------------------------------------------------------
# Football Agent — application image
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# System deps (build tools for asyncpg etc.)
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl \
    && rm -rf /var/lib/apt/lists/*

# ---- Dependency layer (cached unless pyproject changes) ----
COPY pyproject.toml README.md ./
RUN pip install --upgrade pip \
    && pip install -e ".[dev]"

# ---- Application code ----
COPY . .

EXPOSE 8000

# Default: run the API. Overridden by docker-compose for the worker service.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
