# Football Agent

Production-grade **football betting intelligence platform**. An AI agent that analyzes
football matches and produces high-quality, auditable betting recommendations for
professional bettors.

> **Design principle:** quantitative models (Elo, Poisson, xG, Monte Carlo) produce the
> probabilities. Claude reasons over those numbers plus qualitative context (injuries,
> lineups, market movement) to explain and rank recommendations. The LLM is never the
> source of raw probability.

## Architecture

Clean Architecture + Domain-Driven Design. Dependencies point inward; the domain core is
framework-free and testable in isolation.

```
API (FastAPI)          → HTTP, auth, serialization
Services / Agents      → use-cases, orchestration, AI reasoning
Domain (models)        → entities + value objects (pure Python)
Repositories/Providers → interfaces (contracts) + infrastructure impls
Infrastructure         → Postgres, Redis, external feeds, Claude client
```

See [`docs/architecture.md`](docs/architecture.md) for the full rationale.

## Tech stack

- **Python 3.12**, FastAPI, Uvicorn
- PostgreSQL + async SQLAlchemy 2.0 + Alembic
- Redis
- Anthropic Claude (AI reasoning)
- Docker / Docker Compose
- Pytest, Ruff, Black, MyPy

## Getting started

```bash
# 1. Configure environment
cp .env.example .env        # then fill in secrets

# 2. Run the full stack
docker compose up --build

# API docs available at http://localhost:8000/docs
```

### Local development (without Docker)

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -e ".[dev]"

uvicorn app.main:app --reload
```

### Database migrations

```bash
alembic revision --autogenerate -m "message"
alembic upgrade head
```

### Quality gates

```bash
ruff check .
black --check .
mypy app
pytest
```

## Project layout

```
app/
  api/           HTTP layer (routers, endpoints, request deps)
  core/          cross-cutting: DI container, exceptions, logging
  config/        settings (env-driven)
  database/      engine, session, redis factories
  models/        domain entities + value objects (pure)
  schemas/       pydantic DTOs
  services/      use-case orchestration
  repositories/  persistence contracts + SQLAlchemy impls
  providers/     external-feed contracts + impls
  workers/       background / scheduled jobs
  agents/        Claude reasoning pipelines
  prompts/       versioned prompt templates
tests/           unit + integration
scripts/         dev/ops helpers
docs/            architecture & design docs
alembic/         migrations
```

## Status

🚧 Skeleton only — architecture and infrastructure scaffolding. No football/betting
logic implemented yet.
