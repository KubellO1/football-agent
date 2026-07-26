#!/usr/bin/env python3
"""Dry-run: Odds-API.io — direct API call, parse, and report.

Usage:
    python scripts/dry_run_odds_api_io.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from collections import Counter

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

from app.providers.impl.odds_api_io_provider import (
    OddsApiIoProvider,
    OddsAuthError,
    OddsProviderError,
    OddsRateLimitError,
)

API_KEY_VAR = "ODDS_API_IO_API_KEY"
BASE_URL_VAR = "ODDS_API_IO_BASE_URL"


def mask_key(key: str) -> str:
    if len(key) <= 8:
        return "****"
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


async def main() -> None:
    print("=" * 70)
    print("  Odds-API.io Dry-Run")
    print(f"  Timestamp: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    print("=" * 70)

    api_key = os.getenv(API_KEY_VAR, "")
    base_url = os.getenv(BASE_URL_VAR, "https://api.odds-api.io/v3")

    print(f"\n  BASE_URL: {base_url}")
    print(f"  API_KEY:  {mask_key(api_key)}")

    if not api_key:
        print("\n  ERROR: ODDS_API_IO_API_KEY not set in environment/.env")
        return

    provider = OddsApiIoProvider(
        api_key=api_key,
        base_url=base_url,
        timeout_seconds=30,
        max_retries=2,
        backoff_base_seconds=2.0,
    )

    print("\n[1] Fetching pre-match football 1X2 odds ...")
    events = []
    api_error = None
    remaining = "N/A"

    try:
        # The provider's _get_json sets rate_limit_info on the provider
        events = await provider.get_odds(sport="soccer", markets="1x2", regions="eu")
        remaining = getattr(provider, "rate_limit_info", {}).get("x-requests-remaining", "N/A")
    except OddsRateLimitError as e:
        api_error = f"RATE_LIMIT: {e}"
    except OddsAuthError as e:
        api_error = f"AUTH_FAILED: {e}"
    except OddsProviderError as e:
        api_error = f"PROVIDER_ERROR: {e}"
    except Exception as e:
        api_error = f"UNEXPECTED: {type(e).__name__}: {e}"

    if api_error:
        print(f"\n  ERROR: {api_error}")
        print("\n  Dry-run aborted.")
        return

    print(f"  API returned {len(events)} event(s)")
    print(f"  Requests remaining: {remaining}")

    if not events:
        print("\n  No events returned. Skipping analysis.")
        return

    # ── Event summary ────────────────────────────────────────────────────
    print(f"\n[2] Event summary (first 20):")
    for i, ev in enumerate(events[:20]):
        t = ev.commence_time.strftime("%m-%d %H:%M") if ev.commence_time else "N/A"
        bm_count = len(ev.bookmakers)
        print(f"  [{i+1:3d}] {ev.home_team} vs {ev.away_team}  |  {t}  |  {bm_count} bookmaker(s)")

    # ── 1X2 market analysis ──────────────────────────────────────────────
    print(f"\n[3] 1X2 market analysis:")
    with_1x2 = 0
    without_1x2 = 0
    total_bm = 0
    bm_set: set[str] = set()
    missing_draw = 0

    for ev in events:
        bm_count = len(ev.bookmakers)
        total_bm += bm_count
        if bm_count > 0:
            with_1x2 += 1
            for bm in ev.bookmakers:
                bm_set.add(bm.bookmaker_key)
                outcome_names = {o.name.lower() for o in bm.outcomes}
                if "draw" not in outcome_names:
                    missing_draw += 1
        else:
            without_1x2 += 1

    print(f"  Events WITH 1X2:    {with_1x2}")
    print(f"  Events WITHOUT 1X2: {without_1x2}")
    print(f"  Missing draw market: {missing_draw}")
    print(f"  Total bookmaker entries: {total_bm}")
    print(f"  Unique bookmakers:  {len(bm_set)}")
    if bm_set:
        print(f"  Bookmaker list:     {', '.join(sorted(bm_set)[:20])}")

    # ── Sport key / league distribution ──────────────────────────────────
    leagues: Counter = Counter()
    for ev in events:
        leagues[ev.sport_key] += 1
    print(f"\n[4] Sport key / league distribution:")
    for key, cnt in leagues.most_common(20):
        print(f"  {key}: {cnt}")

    # ── Fixture-mapping readiness (no DB, just stats) ────────────────────
    print(f"\n[5] Fixture-mapping readiness:")
    has_commence_time = sum(1 for ev in events if ev.commence_time is not None)
    has_home = sum(1 for ev in events if ev.home_team)
    has_away = sum(1 for ev in events if ev.away_team)
    print(f"  Events with commence_time: {has_commence_time}/{len(events)}")
    print(f"  Events with home_team:     {has_home}/{len(events)}")
    print(f"  Events with away_team:     {has_away}/{len(events)}")

    # ── Final report ─────────────────────────────────────────────────────
    print(f"\n{'=' * 70}")
    print(f"  DRY-RUN REPORT")
    print(f"{'=' * 70}")
    print(f"  API requests made:                1")
    print(f"  Events returned:                  {len(events)}")
    print(f"  Fixtures matched:                 0 (no DB access in dry-run)")
    print(f"  Mapping failures:                 N/A (requires API-Football fixtures)")
    print(f"  1X2 markets parsed:               {with_1x2}")
    print(f"  OddsSnapshot writes (est.):       {with_1x2} events × avg {total_bm // max(1, with_1x2)} bookmakers")
    print(f"  EV row calculations (est.):       {with_1x2 * 3}")
    print(f"  Gate-qualified bets (est.):       ~{max(0, with_1x2 // 4)} (placeholder)")
    print(f"  Remaining request allowance:      {remaining}")
    print(f"\n  Dry-run completed successfully.")
    print(f"{'=' * 70}")


if __name__ == "__main__":
    asyncio.run(main())
