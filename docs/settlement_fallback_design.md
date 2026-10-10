# SettlementFallback design

Status: implementation candidate only. The canonical production task remains disabled.

## Settlement contract

- Discover unsettled `value_bets` before any provider request.
- With zero unsettled rows, make zero API requests and zero writes.
- Refresh only candidate fixture IDs through API-Football.
- Settle `FT`, `AET`, and `PEN` only when the regulation-time score is authoritative.
- Standard three-way 1X2 never produces `PUSH`.
- Defer `NOT_STARTED`, `LIVE`, `INTERRUPTED`, `POSTPONED`, `CANCELLED`, `ABANDONED`,
  `AWARDED/WALKOVER`, malformed, and unknown states.
- Commit the settlement and matching bankroll entry in one database transaction.

Active revision `settlement_void_0022` (down-revision `0021`) adds terminal `V`,
nullable scores, a required `void_reason_code` for VOID rows, and database shape
constraints. It is not a production deployment approval.

## VOID evidence and ledger contract

- Fixture lifecycle is never sufficient to infer VOID. CANCELLED, POSTPONED, ABANDONED,
  INTERRUPTED, AWARDED/WALKOVER, and UNKNOWN remain deferred unless an injected trusted
  market/bookmaker evidence provider returns an instruction bound to the exact value bet,
  fixture, and bookmaker.
- No production VOID evidence provider is configured by default, so the runtime remains
  fail-closed until an authoritative source is separately approved.
- A valid VOID writes one settlement (`result=V`, no score, zero P&L) and one zero-amount
  bankroll audit entry in the same transaction. The balance is unchanged; no refund credit
  is fabricated because stakes are not pre-debited by the current ledger.
- `PENDING` and `DEFERRED` are workflow states and never occupy the terminal settlements
  table.

## Alembic topology isolation

- Active graph: `0021 -> settlement_void_0022`.
- Historical TASK-060 revision `0022` is not an alias or ancestor. Alembic cannot resolve
  that historical ID in the active graph; its four disposable databases remain quarantined.
- Future observation work must port the historical schema semantics into a new migration
  based on the then-current master head (for example `observation_layer_0023`). The frozen
  TASK-060 migration and branch remain unchanged.

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
