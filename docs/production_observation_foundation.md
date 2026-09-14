# Production Observation Foundation

## Scope and invariants

Alembic 0022 adds a provider-neutral, append-only ledger for point-in-time football
intelligence. It does not alter predictions, odds, recommendations, models, or betting
rules. Existing fixtures, teams, and competitions remain canonical; source-specific
identities are recorded as mapping decisions rather than duplicated entities.

The immutable ledger tables are guarded by PostgreSQL `BEFORE UPDATE OR DELETE`
triggers. Repository writes use deterministic keys and `ON CONFLICT DO NOTHING`, so
replaying the same payload, observation, or mapping is idempotent. Unknown or ambiguous
identity evidence is retained with `UNMAPPED` or `AMBIGUOUS` status and is never written
through as a guessed canonical identity.

Weather normalization requires a uniquely matched venue with coordinates from a
recorded source URL. Missing or ambiguous coordinates produce `WEATHER_UNAVAILABLE`;
the collector must not geocode by inference.

## Retention policy

| Data | Retention | Rule |
|---|---:|---|
| Normalized observations | Indefinite | Required for longitudinal point-in-time research; append only. |
| Identity and venue mappings | Indefinite | Audit trail for every deterministic or rejected mapping decision. |
| Raw payload metadata | Indefinite | Keeps provenance and content hash even after payload archival. |
| Permitted raw payload bytes | 90 days hot, then archive | Archive only when the recorded source policy permits storage; never retain beyond source terms. |
| HTTP cache | Provider headers, maximum 24 hours | Honour `ETag`, `Last-Modified`, and cache expiry. |
| Failed payloads | 30 days | Enough for parser diagnosis; redact secrets before archival. |
| Parser regression fixtures | Indefinite | Minimal, licensed samples referenced by content hash and parser version. |

Deletion is a separately approved governance operation. It must never mutate observation
rows; raw bytes may be removed only after retention/licensing review while preserving
their immutable metadata and hash.

The TASK-059 canary artifact was 1,961,033 bytes for 1,889 observations and six source
responses. At one equivalent five-league capture per day, the measured 90-day projection
is about 177 MB. Reserve 1 GB for the currently approved fixture/result/weather data
types, then remeasure before adding checkpoint-dense news or manual evidence. Actual
storage must still be measured during the separately approved 10-fixture production
canary before scheduling recurring collection.

## Proposed collection commands (design only)

One dispatcher should evaluate due windows and execute only the latest due checkpoint
per fixture. Suggested commands are:

- `intelligence_daily` for OpenFootball fixture/result baseline.
- `intelligence_t24h`, `intelligence_t6h`, `intelligence_t90`, `intelligence_t60`, and
  `intelligence_t30` for point-in-time evidence available at each window.
- `intelligence_post_match` for completed results/statistics.

No Windows task is created by TASK-060. With one daily OpenFootball refresh, conditional
cache revalidation, MET requests only for uniquely mapped venues, and batches capped at
10 fixtures, the planning budget is 20 external requests/day (about 600/month), maximum
concurrency two, expected cost EUR 0. Overlapping windows must reuse cached source
payloads and deterministic observation keys.

## Future 10-fixture canary gate

After migration approval and a second explicit canary approval, select at most 10
naturally upcoming five-league fixtures. Allowed writes are limited to source, raw
payload metadata, observation, and deterministic mapping rows. The canary must verify
provenance, hashes, timestamps, zero duplicate natural keys, zero inserts on replay,
staleness, source health, and that no paid API is called. It must not run predictions,
bets, backfills, or recurring scheduler jobs.
