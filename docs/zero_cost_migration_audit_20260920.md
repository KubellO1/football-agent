# Zero-cost migration: source and cutover audit (2026-09-20)

This is a data-acquisition audit, not evidence of betting edge or permission to
relax any production gate. No existing API key is printed or removed.

## Current production acquisition

| Path | Current source | Credential configured | Automatic write path |
| --- | --- | --- | --- |
| Daily fixtures | API-Football | yes | `daily_job` → `build_ingestion_service` → `sync_today` |
| Daily odds | Odds-API.io primary, The Odds API fallback | both yes | `daily_job` → odds ingestion |
| Pre-kickoff | API-Football + configured odds providers | yes | `scheduler_runner` pre-kickoff |
| Weather | WeatherAPI | yes | Container provider registration |
| Sportmonks | removed/deprecated | no | none found |
| OpenAI | no release credential | no | unrelated to fixture/odds ingestion |

The production `.env` identifies Odds-API.io as `paid`. Provider registration
does not itself prove an HTTP request. The 2026-09-20 emergency containment left
the four database-writer Scheduler tasks Disabled; `DailyProductionRun` was the
only newly disabled task. Keys and the old clean release remain available for
rollback. Internal sync HTTP endpoints are additional *manual* paths to paid
providers and must be retired or guarded before zero-cost deployment.

## Source decisions

| Source | Use decision | Reason and limits |
| --- | --- | --- |
| [OpenFootball football.json](https://github.com/openfootball/football.json) | APPROVED for CC0 fixture/result observations | Five-league public JSON; generated daily but upstream updates are not guaranteed. Missing kickoff time is `DATE_ONLY`, never noon. Not a T-90 authority by itself. |
| [MET Norway](https://api.met.no/doc/TermsOfService) | APPROVED for weather forecast observations | CC BY 4.0 attribution, identifiable User-Agent, cache headers and rate courtesy required. Venue coordinates must be verified before real-fixture weather mapping. |
| User-supplied official evidence | APPROVED as MANUAL only | Retain URL, capture/publication time and raw hash. A user-supplied predicted XI must never be relabelled as confirmed. No automatic coverage claim. |
| [Premier League site](https://www.premierleague.com/en/news/58915) | REJECT automated production extraction | Terms restrict commercial use/database re-use without approval. [Robots](https://www.premierleague.com/robots.txt) is not a license override. |
| [Lega Serie A site](https://app.legaseriea.it/oidp/service_terms?cdOrg=SERIEA&hl=it) | REJECT automated extraction | Terms prohibit robots/data mining without written consent. |
| [Bundesliga registered services](https://www.bundesliga.com/en/bundesliga/info/terms-of-use-services) | REJECT registered-content automation | Terms prohibit automated access/analysis/download; no login workaround. Public club-specific permissions remain unverified. |
| [LALIGA website](https://www.laliga.com/en-DE/legal/legal-web) | NOT APPROVED for production extraction | [Robots](https://www.laliga.com/robots.txt) allows some paths with crawl-delay 30, but that alone does not establish reuse/commercial rights. |
| Ligue 1 / individual club news sites | NOT APPROVED pending per-site evidence | No blanket league-wide automation and commercial-use permission established. Human-provided article evidence remains possible. |
| [football-data.org Free](https://www.football-data.org/pricing) | CONDITIONAL fixture alternative | Five leagues covered, but match endpoints require free registration; no current token. Odds and lineups are paid add-ons, so not a free odds/lineup source. |
| [BSD / GoalDir Free](https://www.goaldir.com/docs/conventions/) | CONDITIONAL research candidate | Free registration required. Free odds expose consensus, not per-bookmaker prices; those need Football Unlimited. Published [license](https://www.goaldir.com/docs/api-license/) has a future effective date and third-party rights disclaimer. Cannot satisfy current bookmaker-identity gate. |
| [SkipOdds Demo](https://skipodds.com/) | REJECT for formal odds snapshots | Free output is de-vigged consensus/fair odds; no bookmaker identities. Terms could not be independently retrieved. Do not fabricate bookmaker odds from consensus. |
| [StatsBomb Open Data](https://github.com/statsbomb/open-data) | RESEARCH_ONLY | Not a continuous live five-league injury/lineup/odds feed. |
| football-data.co.uk historical CSV | RESEARCH_ONLY | Historical prices are not a lawful, current point-in-time bookmaker feed; commercial automation rights not established. |

No official injury, suspension or confirmed-lineup site has yet passed the
combined automation/rights and five-league point-in-time tests. Therefore no
automatic official-news parser was enabled or deployed. Manual evidence is not
counted as automated source redundancy.

## Live public fixture probe and mapping gate

Five public OpenFootball JSON downloads on 2026-09-20 (five requests, no paid
API) found future entries and explicit times as follows:

| League | Future entries | With explicit time | Date-only |
| --- | ---: | ---: | ---: |
| Premier League | 334 | 334 | 0 |
| La Liga | 315 | 25 | 290 |
| Serie A (Italy) | 335 | 75 | 260 |
| Bundesliga (Germany) | 273 | 75 | 198 |
| Ligue 1 (France) | 264 | 72 | 192 |

The production database, read-only queried at this audit, has **zero future
fixtures** in the *country-qualified* five leagues. A name-only query would
mistake Brazil's Serie A and other homonymous competitions for the target
leagues. The bounded Canary evaluated at most ten timed public fixtures and
matched zero to production: `NO_EXACT_FIXTURE_MATCH=10`; it inserted zero
observations. Cache replay made zero new HTTP requests and zero inserts, but
this does **not** meet the positive Canary acceptance criterion.

The isolated adapter now uses `openfootball-json-v2`: a missing time is never
filled, and the stable source fixture identity includes country, competition,
season and the home/away team pair. Existing v1 observations are not rewritten.

## Cutover gate

No production cutover, PR merge, Scheduler re-enable, key removal or bookmaker
configuration retirement is justified yet. Required next evidence:

1. An independently sourced and permissioned five-league fixture feed with
   exact kickoff times, or refreshed production fixture references that match
   the CC0 feed without guessing.
2. Permissioned, timestamped official injury/suspension/confirmed-lineup
   sources at meaningful coverage, or explicit missing-data behavior.
3. A no-cost lawful current odds source returning real bookmaker identity,
   1X2 outcomes, decimal prices and observation timestamps; consensus fair
   probabilities are insufficient.
4. A positive <=10-fixture isolated Canary with exact mapping, source policy,
   provenance and replay idempotency. Then a separate clean-release cutover
   validation proving `PAID_API_REQUESTS=0` in controlled jobs.

Until then, the four writer Scheduler tasks remain Disabled. This is a
deliberate fail-closed state, not a claim that zero-cost production parity was
achieved.
