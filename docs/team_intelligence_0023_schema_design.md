# Team Intelligence 0023 persistence foundation

## Status

Implemented on `codex/TASK-20261011-086` and validated only against isolated,
disposable PostgreSQL databases. The migration has not been applied to
production, no production observations were written, and no collection task is
enabled. Production remains at `settlement_void_0022` until a separate migration
approval.

The active migration graph is linear:

```text
settlement_void_0022
  -> team_intelligence_0023
```

The historical frozen observation-layer migration is not reused, renamed,
merged, or applied.

## Persistent boundary

### `team_intelligence_source_registry`

Stores the reviewed source identity, policy, review timestamp/hash, parser
version, permission fields, and enabled state. A database check permits an
enabled source only when it is `APPROVED`, storage is explicitly allowed, and
automation is explicitly allowed for every non-manual source.

The migration creates no source rows. Registration is explicit and
idempotent. `LICENSE_UNCLEAR`, `RESEARCH_ONLY`, and `REJECTED` sources cannot be
enabled by the persistence contract.

### `team_intelligence_raw_payloads`

Stores immutable content-addressed metadata: deterministic UUID, canonical
source identity, source URL, SHA-256 digest, media type, object URI, reviewed
source policy, and first capture time. Raw bytes remain in the permitted
content-addressed archive; the database preserves its location and digest.

The natural key is `(source_name, content_sha256)`. A composite foreign key to
the source registry proves the exact policy under which the payload was
accepted.

### `team_intelligence_capture_receipts`

Stores one immutable point-in-time receipt for each source URL and capture
time, including HTTP status, ETag, and Last-Modified metadata. Repeated capture
of unchanged content creates a new receipt but not a duplicate semantic
observation.

The natural key is `(raw_payload_id, source_url, captured_at)`. A database
policy trigger resolves the receipt's raw payload back to the current reviewed
source and rejects inserts from disabled or non-approved sources.

### `team_intelligence_observations`

Stores immutable normalized evidence with complete source, raw-payload, exact
capture-receipt, team/player/fixture, parser, publication, capture-window, and
provider identity provenance. Composite foreign keys require the observation's
source/policy and URL/capture time to match the referenced evidence exactly.

`semantic_key` is SHA-256 over all semantic and provenance fields except
`captured_at`. The row UUID is deterministic UUIDv5 over that semantic key.
Consequences:

- exact replay inserts zero rows;
- unchanged evidence captured later preserves a new receipt but inserts zero
  observations;
- changed evidence or evidence from another source appends a distinct row;
- conflicts remain side by side and no historical row is mutated.

## Database enforcement

PostgreSQL triggers reject `UPDATE`, `DELETE`, and `TRUNCATE` on raw payloads,
capture receipts, and observations. Separate insert triggers enforce reviewed
source policy for raw payloads, capture receipts, and observations. The
repository exposes registration, append-if-absent, and read methods only; it has
no evidence update/delete API.

## Rehearsal and rollback

Two independent disposable databases each completed:

1. migration from `settlement_void_0022` to `team_intelligence_0023`;
2. a 10-fixture isolated persistence Canary;
3. exact and later-capture replay;
4. source-conflict preservation;
5. database-level mutation and disabled-source bypass attempts;
6. downgrade to `settlement_void_0022`;
7. re-upgrade to `team_intelligence_0023`;
8. `alembic check`.

Both produced schema hash
`192bbbffbbd23bd20b1b726a325b608feea242b3589609d7312c27d099068b64`.
All pre-existing table counts and content hashes were identical before the
migration, after downgrade, and after re-upgrade. Downgrade removed all four
Team Intelligence tables and only those tables.

## Storage estimate

The 10-fixture Canary stored 17 source-policy rows, 11 immutable observations,
and 12 capture receipts in 286,720 bytes of PostgreSQL relation storage. The
simple conservative projection is 28,672,000 bytes per 1,000 fixtures. This
small sample includes PostgreSQL minimum-page and index overhead, so it is not a
linear production forecast. Raw payload bytes are external and must be sized
separately from the database metadata.

## Production boundary

This foundation does not connect Team Intelligence to model inputs, does not
automate official websites, does not enable collection schedules, and does not
alter Daily, PreKickoff, Settlement, Recovery, or any Frozen betting/model rule.
Production migration and production observation writes require a separate
approval.
