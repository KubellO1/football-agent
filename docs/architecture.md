# Architecture

Football Agent follows **Clean Architecture** and **Domain-Driven Design**. Dependencies
point inward; the domain core knows nothing about FastAPI, SQLAlchemy, Redis, or Claude.

## Layers

| Layer | Package | Responsibility | May import |
|-------|---------|----------------|-----------|
| API | `app/api` | HTTP, auth, serialization, request-scoped DI | services, schemas |
| Services | `app/services` | Use-case orchestration, transaction boundaries | domain, repo/provider **interfaces** |
| Agents | `app/agents` | Claude reasoning pipelines over quantitative signals | services, domain, `prompts/` |
| Domain | `app/models` | Entities + value objects, invariants (pure Python) | — |
| Repositories | `app/repositories` | `interfaces/` contracts + `sqlalchemy/` impls | domain, database |
| Providers | `app/providers` | `interfaces/` contracts + `impl/` external feeds | domain, schemas |
| Workers | `app/workers` | Background / scheduled jobs | services |
| Core | `app/core` | DI container, exceptions, logging (cross-cutting) | config |

## Key rules

1. **Dependency inversion** — services depend on `repositories/interfaces` and
   `providers/interfaces`, never on concrete implementations. The DI container
   (`app/core/container.py`) binds interface → implementation.
2. **Domain purity** — `app/models` is framework-free. ORM models live under
   `app/repositories/sqlalchemy` and are mapped to/from domain entities.
3. **Math produces probabilities; AI reasons over them.** Elo / Poisson / xG /
   Monte Carlo yield numeric probabilities. Claude explains and ranks
   recommendations using those numbers plus qualitative context. The LLM never
   emits raw probabilities used for staking.

## Data flow (planned)

```
providers → services (collect) → repositories (persist)
repositories → services (analytics: Elo/Poisson/xG/MC) → probabilities
probabilities + context → agents (Claude) → ranked value bets → API
```
