# Daily Report — 2026-07-02

> Regenerated from **actual database state** and today's **deterministic pipeline
> result**. No Claude call was made, and **no new `ValueBet` / `DecisionLog`** was
> created to produce this report — figures come from read-only analysis
> (`analyze_detailed` = math + gate) plus persisted-row counts.

## Pipeline

| Metric | Count | Note |
|---|---|---|
| Fixtures in window (2026-07-02) | 89 | 88 real (API-Football) + 1 synthetic-dev |
| — real (API-Football) | 88 | |
| — synthetic-dev | 1 | isolated test fixture, not a real match |
| Fixtures with odds | 4 | |
| Matches analyzed (deterministic math) | 89 | no Claude |
| **Qualified** (gate + EV≥5% + Kelly≥2% + Conf≥70%) | **1** | **synthetic-dev fixture only** — 0 real matches |
| Claude reviewed **today** (new) | 0 | the sole qualifier was already reviewed earlier → skipped, no Claude call |
| Recommendations persisted for today (`value_bets`) | 1 | synthetic-dev, from an earlier manual review |
| Decision logs for today (`decision_logs`) | 1 | synthetic-dev, pre-existing |
| Skipped (not qualified) | 88 | all real fixtures |

## Skip reasons

| Reason | Count |
|---|---|
| Insufficient two-sided history | 88 |
| No odds | 0 |
| Failed thresholds | 0 |

_All 88 real fixtures fell out at the very first stage (insufficient history), so
none reached the odds or threshold checks — hence `No odds = 0` and
`Failed thresholds = 0` here. (4 fixtures do have odds in the DB, but they still
lack team history, so they are counted under "insufficient history".)_

## World Cup note

Today's three real World Cup fixtures were **not analyzable** due to **insufficient
two-sided history** — the quantitative model needs prior finished matches for
**both** teams (to estimate strength via Poisson/Elo), and these national teams'
group-stage results are not in the database:

| Match | Odds in DB | Result |
|---|---|---|
| Spain vs Austria | yes | insufficient history → not analyzed |
| Portugal vs Croatia | yes | insufficient history → not analyzed |
| USA vs Bosnia & Herzegovina | no | insufficient history → not analyzed |

Even the two fixtures **with** odds could not be modelled, because odds alone do
not yield model probabilities without team history. **Zero real recommendations
were produced today.**

## Summary

The only "qualified / recommended" item today is the **synthetic development
fixture**, which exists solely to exercise the pipeline and is not a real match.
Across all 88 real fixtures, the system produced **0 recommendations** — correctly,
given the lack of historical data — and made **0 Claude calls** in the process.
