# Odds freshness policy (TASK-20260921-076)

The explicitly approved production final-odds age limit is **80 minutes** (previously 30). The separate preliminary limit is **180 minutes**. Age is measured as analysis `as_of` minus the persisted snapshot `captured_at`; ingestion sets that value from the provider market's `last_update`, not from the request clock. Future-dated snapshots are rejected.

| Latest complete, validated 1X2 market age | Classification | Decision use |
| --- | --- | --- |
| 0–80 minutes, inclusive | FINAL-eligible | May enter the existing EV, confidence, Kelly and RecommendationGate path. All other production gates, including at least two distinct canonical bookmakers, remain required. |
| Greater than 80 through 180 minutes, inclusive | PRELIMINARY/WATCH | Model probabilities may be shown for information. No formal quote is passed to the betting model; no formal EV, Kelly stake or BET is produced. Reference EV is not currently implemented. |
| Greater than 180 minutes | ODDS_TOO_STALE | Not used for formal betting decisions; no formal EV, Kelly stake or BET. |

Absent, incomplete, outlier-only or single-bookmaker markets retain their existing rejection behavior. The preliminary tier also requires a complete validated market; it does not weaken bookmaker, selection or outlier checks. `ODDS_TOO_STALE` applies when the latest supported snapshots are all older than 180 minutes. Dashboard labels read the configured limits and describe eligibility, not a guaranteed recommendation.

Configuration: `ANALYSIS_ODDS_MAX_AGE_MINUTES=80` and `ANALYSIS_ODDS_PRELIMINARY_MAX_AGE_MINUTES=180`. Deployment must check for environment overrides before activating a release. No other model, EV, confidence, Kelly, Gate, whitelist or checkpoint thresholds change.

Research scripts on separate unmerged TASK-072/075 branches retain their original 30-minute benchmark for comparability; their captured timestamps and ages must be reclassified under this policy before making any production suitability claim. This task does not alter the scheduled research collector or production runtime.
