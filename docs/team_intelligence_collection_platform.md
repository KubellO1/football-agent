# Official Five-League Team Intelligence Collection Platform

## Scope and safety boundary

This component accumulates point-in-time evidence for team availability and tactical context. It does not alter the production model, completeness gate, EV/confidence/Kelly rules, recommendation gate, whitelist, production Scheduler, or football data tables. TASK-20261010-085 used an isolated file-backed archive only; production database writes were zero.

An official page being publicly readable or allowed by `robots.txt` is not treated as a licence. Terms, robots, authentication, storage rights, and commercial reuse are evaluated independently. A source must have explicit automation and storage permission before `CompliantHttpClient` will fetch it. Login, CAPTCHA, paywall, Cloudflare, private-token and rate-limit bypasses are unsupported by design.

## Evidence contract

Every normalized observation preserves:

- canonical `team_id`, optional `player_id`, optional `fixture_id`;
- source name and HTTPS URL;
- `published_at` when supplied by the source and independent `captured_at`;
- original title, text and direct quote without model-generated rewriting;
- language, parser version and SHA-256 raw-payload hash;
- data type, evidence level, source policy and capture window;
- source-scoped fixture/team/player identifiers for audit and remapping;
- normalized structured fields used only for display or future approved consumers.

The supported data types are `INJURY`, `RECOVERY_STATUS`, `ILLNESS`, `SUSPENSION`, `TRAINING_STATUS`, `SQUAD_CALLUP`, `SQUAD_OMISSION`, `CONFIRMED_LINEUP`, `SUBSTITUTES`, `FORMATION`, `ROTATION_PLAN`, `MINUTES_RESTRICTION`, `MANAGER_QUOTE`, `TACTICAL_CHANGE`, `PLAYER_RETURN`, `LATE_WITHDRAWAL`, `INTERNATIONAL_DUTY`, and `GENERAL_TEAM_NEWS`.

Evidence levels are exactly `OFFICIAL_CONFIRMED`, `DIRECT_COACH_QUOTE`, `OFFICIAL_MATCH_PAGE`, `OFFICIAL_MEDICAL_REPORT`, `OFFICIAL_DISCIPLINARY_NOTICE`, `INFERRED`, and `UNVERIFIED`. Provider claims remain `UNVERIFIED`; they are not promoted to an official evidence level merely because an API exposes them.

## Source audit

The machine-readable audit is in `research/team_intelligence/source_registry.json`.

| Source | Scope | Decision | Automation/storage conclusion |
|---|---|---|---|
| Premier League | league | REJECTED | Terms limit use to private/personal use and prohibit database reuse/commercial use without approval. |
| LaLiga | league | LICENSE_UNCLEAR | Robots allows public paths with a 30-second delay, but terms do not grant persistent automated reuse. |
| Lega Serie A | league | REJECTED | Terms expressly prohibit data mining, robots and similar extraction. |
| Bundesliga | league | REJECTED | Legal notice expressly prohibits automated programs/bots accessing, analysing or downloading content. |
| Ligue 1 / LFP | league | REJECTED | Terms restrict consultation to personal use and prohibit reuse/commercial exploitation without consent. |
| Arsenal | club | LICENSE_UNCLEAR | No explicit production automation and storage grant located. |
| Liverpool | club | LICENSE_UNCLEAR | Robots allows public paths, but no content licence for persistent automation was found. |
| Real Madrid | club | REJECTED | Legal notice prohibits automated scripts, scraping and systematic retrieval. |
| FC Barcelona | club | REJECTED | Legal notice restricts robot/software use and reproduction. |
| Juventus | club | LICENSE_UNCLEAR | Public sitemap exists; no explicit automation/storage licence located. |
| Inter | club | LICENSE_UNCLEAR | Public sitemap exists; no explicit automation/storage licence located. |
| FC Bayern | club | REJECTED | Terms limit use to personal purposes and prohibit commercial/further reuse. |
| Borussia Dortmund | club | LICENSE_UNCLEAR | Informational access is public; storage/commercial automation is not granted. |
| Paris Saint-Germain | club | REJECTED | Legal notice expressly prohibits data mining and scraping. |
| Olympique de Marseille | club | LICENSE_UNCLEAR | Generic crawling is not wholly disallowed, but no persistent reuse licence was located. |
| API-Football | existing provider API | APPROVED | Existing authenticated contract; documented injuries and lineup endpoints. Data may support an application but must not be resold directly. |
| Manual official evidence | user-reviewed official URL | APPROVED | No automated site access; stores only user-authorized official evidence and provenance. |

Official league and club pages can therefore be used only through manual evidence capture or after written permission, unless their classification changes after a new terms review. API-Football is the only current automated data path. Its coverage is incomplete by contract and every claim keeps provider provenance.

## Collection pipeline

```text
audited source registry
  -> terms + robots + URL-scope gate
  -> paced HTTPS / optional permitted renderer
  -> cache + ETag / Last-Modified + bounded retry / circuit breaker
  -> immutable raw bytes + append-only capture receipt
  -> static HTML / structured JSON / RSS-Atom / sitemap / API adapter
  -> exact external-ID or unique normalized-name matching
  -> immutable normalized observation
  -> duplicate, stale, conflict and source-health checks
  -> isolated append-only data set
```

The renderer boundary accepts already-rendered public HTML only when the source registry explicitly permits browser rendering. It never extracts private tokens or defeats access controls.

## Identity and conflict policy

Stable provider IDs are matched to reviewed canonical IDs first. Name fallback accepts only one exact normalized candidate; ambiguity returns a failure rather than a guess. Fixture matching requires competition, home team, away team and kickoff within ten minutes.

Conflicting observations are all retained. Display priority is:

1. official medical report;
2. league disciplinary notice or official match page;
3. official confirmation;
4. direct coach quote;
5. API-Football;
6. unverified or inferred evidence.

Priority selects a preferred display observation only; it never deletes or rewrites competing evidence.

## Collection schedule

| Window | Intended evidence | Natural-window rule |
|---|---|---|
| DAILY | fixture-linked injuries/suspensions, training/team-news review | once daily, cached |
| T-24H | current availability and squad news | ±30 minutes |
| T-6H | availability changes, travel/international-duty updates | ±15 minutes |
| T-90 | squad and late availability | ±10 minutes |
| T-60 | availability and initial lineup evidence | ±10 minutes |
| T-30 | confirmed lineup/substitutes/formation | ±5 minutes |
| T-15 | late withdrawal and final lineup change | ±5 minutes |
| POST_MATCH | post-match lineup/tactical evidence | only after authoritative completion |

Missed checkpoint windows are not backdated. A future production collector should select existing fixtures due in the current window, cap targets, and call only the fixture-scoped API endpoints.

## Isolated Canary

On 2026-10-11, the Canary selected ten future fixtures—two in each target league—and made 25 paced API-Football requests: five fixture discovery requests, ten fixture-scoped injury requests, and ten fixture-scoped lineup requests. Raw JSON was archived before parsing.

- Five-league fixture mapping: 10/10.
- Injury/illness normalized observations: 146 raw rows, 73 unique observations.
- API-Football supplied every injury row twice; the store identified 73 same-batch duplicates and persisted one immutable copy of each.
- Lineup observations: 0 because the selected fixtures were not yet near lineup publication; empty response was preserved, not fabricated.
- Mapping failures: 0.
- Conflicts: 0.
- Offline replay: 0 external requests, 0 new inserts, 146 duplicates identified.
- Production DB reads/writes: 0/0.

The Canary also found and fixed a real API-Football shape mismatch: current v3 injury responses nest `type` and `reason` under `player`; the existing adapter expected legacy top-level fields. The parser now accepts current nesting and retains the legacy fallback.

## Current coverage

| Category | Automated five-league coverage | Result |
|---|---:|---|
| injuries / illness / suspension | 5/5 provider endpoint coverage, fixture availability varies | AVAILABLE_WITH_PROVIDER_PROVENANCE |
| confirmed lineups / substitutes / formation | 5/5 endpoint coverage near kickoff, fixture availability varies | AVAILABLE_NEAR_KICKOFF |
| recovery / training / manager quote / rotation / minutes restriction | 0/5 automated legal official sources | MANUAL_ONLY |
| late withdrawal / tactical change / international duty / general news | 0/5 automated legal official sources | MANUAL_ONLY |

This platform is ready for another isolated near-kickoff team-intelligence Canary. It is not approved for production persistence or Scheduler deployment.
