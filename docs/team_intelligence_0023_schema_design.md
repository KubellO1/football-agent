# Future `0023_team_intelligence_observations` schema design

## Status

Design only. No Alembic migration was created or applied. Production remains at `settlement_void_0022`. The historical TASK-060 observation migration is not reused, renamed, merged, or applied.

If approved later, the migration must be a single linear successor:

```text
settlement_void_0022
  -> 0023_team_intelligence_observations
```

## Minimum persistent schema

### `team_intelligence_raw_payloads`

- `id UUID PRIMARY KEY`
- `source_name VARCHAR(120) NOT NULL`
- `source_url TEXT NOT NULL`
- `content_sha256 CHAR(64) NOT NULL`
- `media_type VARCHAR(120) NOT NULL`
- `object_uri TEXT NOT NULL`
- `source_policy VARCHAR(64) NOT NULL`
- `first_captured_at TIMESTAMPTZ NOT NULL`
- unique `(source_name, content_sha256)`

Raw bytes should live in content-addressed object/file storage; the database keeps immutable metadata and the object URI.

### `team_intelligence_capture_receipts`

- `id UUID PRIMARY KEY`
- `raw_payload_id UUID NOT NULL REFERENCES team_intelligence_raw_payloads(id)`
- `source_url TEXT NOT NULL`
- `captured_at TIMESTAMPTZ NOT NULL`
- `http_status SMALLINT NOT NULL`
- `etag TEXT NULL`
- `last_modified TEXT NULL`
- unique `(raw_payload_id, source_url, captured_at)`

This preserves repeated point-in-time captures even when content bytes do not change.

### `team_intelligence_observations`

- `id UUID PRIMARY KEY` derived from the semantic observation key;
- `team_id UUID NOT NULL REFERENCES teams(id)`;
- `player_id UUID NULL REFERENCES players(id)`;
- `fixture_id UUID NULL REFERENCES fixtures(id)`;
- `raw_payload_id UUID NOT NULL REFERENCES team_intelligence_raw_payloads(id)`;
- `source_name VARCHAR(120) NOT NULL`;
- `source_url TEXT NOT NULL`;
- `published_at TIMESTAMPTZ NULL`;
- `captured_at TIMESTAMPTZ NOT NULL`;
- `original_title TEXT NULL`;
- `original_text TEXT NULL`;
- `original_quote TEXT NULL`;
- `language VARCHAR(16) NOT NULL`;
- `parser_version VARCHAR(80) NOT NULL`;
- `data_type VARCHAR(64) NOT NULL` with a check constraint for the v1 values;
- `evidence_level VARCHAR(64) NOT NULL` with a check constraint for the v1 values;
- `source_policy VARCHAR(64) NOT NULL`;
- `capture_window VARCHAR(20) NOT NULL`;
- `provider_team_id VARCHAR(120) NULL`;
- `provider_player_id VARCHAR(120) NULL`;
- `provider_fixture_id VARCHAR(120) NULL`;
- `normalized JSONB NOT NULL`;
- `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`;
- `superseded_by_id UUID NULL REFERENCES team_intelligence_observations(id)` only if later explicit correction semantics are approved.

Required indexes:

- `(fixture_id, capture_window, captured_at DESC)`;
- `(team_id, data_type, captured_at DESC)`;
- `(player_id, data_type, captured_at DESC)` where `player_id IS NOT NULL`;
- `(source_name, provider_fixture_id, captured_at DESC)`;
- GIN on `normalized` only if measured queries justify it.

The primary deterministic ID provides replay idempotency. There is no UPDATE or DELETE path in the repository contract. Corrections append a new observation and may reference the earlier row; original evidence remains immutable.

### `team_intelligence_source_registry`

- `source_name VARCHAR(120) PRIMARY KEY`
- `base_url TEXT NOT NULL`
- `source_type VARCHAR(40) NOT NULL`
- `status VARCHAR(40) NOT NULL`
- `automation_allowed BOOLEAN NULL`
- `storage_allowed BOOLEAN NULL`
- `terms_url TEXT NULL`
- `robots_url TEXT NULL`
- `policy_reviewed_at TIMESTAMPTZ NOT NULL`
- `policy_hash CHAR(64) NOT NULL`
- `parser_version VARCHAR(80) NULL`
- `enabled BOOLEAN NOT NULL DEFAULT false`

No source may become enabled merely because robots allows crawling; an explicit reviewed status is required.

## Append-only enforcement

The future repository should expose only `insert_if_absent` and read methods. A database trigger should reject UPDATE and DELETE for observations, raw payload metadata and capture receipts. Migration tests must prove trigger enforcement, repository replay idempotency, concurrent duplicate safety and deterministic rollback.

## Forward and rollback rehearsal

Before any production approval:

1. apply `settlement_void_0022 -> 0023_team_intelligence_observations` to a fresh production clone;
2. record schema/constraint/index hashes;
3. run append-only, idempotency and concurrent replay tests;
4. downgrade to `settlement_void_0022` after exporting any isolated test observations;
5. re-upgrade and require identical schema hash;
6. require all pre-existing production table counts and hashes unchanged.

Rollback drops only the new intelligence tables in reverse FK order. It must not alter existing player-availability, lineup, settlement, prediction, odds or fixture data.
