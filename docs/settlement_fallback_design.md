# SettlementFallback design

Status: implementation candidate only. The canonical production task remains disabled.

## Settlement contract

- Discover unsettled `value_bets` before any provider request.
- With zero unsettled rows, make zero API requests and zero writes.
- Refresh only candidate fixture IDs through API-Football.
- Settle `FT`, `AET`, and `PEN` only when the regulation-time score is authoritative.
- Standard three-way 1X2 never produces `PUSH`.
- Defer `NOT_STARTED`, `LIVE`, `POSTPONED`, `CANCELLED`, `ABANDONED`,
  `AWARDED/WALKOVER`, malformed, and unknown states.
- Commit the settlement and matching bankroll entry in one database transaction.

Alembic 0021 cannot represent a formal `VOID` settlement result. Those fixtures
therefore remain unsettled and fail closed. Adding `VOID` requires separate schema
approval.

## Proposed scheduler settings

- Daily at 23:15 Europe/Paris.
- Execution time limit: 10 minutes.
- `MultipleInstances=IgnoreNew`.
- Existing retry policy retained.
- Settlement acquires the `pre_kickoff` execution lock for its whole run. A live
  PreKickoff run therefore defers settlement, and a new PreKickoff invocation skips
  while settlement owns the lock.
- The production task must not be enabled until a separate controlled-canary
  approval is granted.
