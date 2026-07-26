"""Dashboard data builder — queries the DB for daily production data and builds
data structures that the DashboardRenderer can consume.

All values come from real DB data. No demo / placeholder values are fabricated.
If no BETs exist, the dashboard still displays WATCH / NO_ODDS counts, missing-data
reasons, and provider health telemetry.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

from app.dashboard.types import (
    DailyDashboardData,
    DailyExecutiveSummary,
    DataQuality,
    DataQualityItem,
    DecisionInfo,
    FixtureInfo,
    MatchDashboardData,
    ModelAvailability,
    ModelProbabilities,
    NoBetCheckItem,
    NoBetChecks,
    OddsInfo,
    TopPick,
    TopRecommendation,
    ValueInfo,
)
from app.repositories.sqlalchemy.models import (
    FixtureORM,
    OddsSnapshotORM,
    PredictionORM,
    ValueBetORM,
    DecisionLogORM,
    SettlementORM,
    PerformanceSnapshotORM,
)
async def build_daily_dashboard(
    session: AsyncSession,
    on_date: date,
    *,
    pipeline_version: str | None = None,
) -> DailyDashboardData:
    """Query DB for on_date and build the complete DailyDashboardData."""

    day_start = datetime(on_date.year, on_date.month, on_date.day, tzinfo=timezone.utc)
    day_end = datetime(on_date.year, on_date.month, on_date.day, 23, 59, 59, tzinfo=timezone.utc)

    # ── Fixtures for today ──────────────────────────────────────────────
    fixture_rows = (
        await session.execute(
            select(FixtureORM).where(
                FixtureORM.kickoff >= day_start,
                FixtureORM.kickoff <= day_end,
            )
        )
    ).scalars().all()

    fixture_lookup: dict[str, FixtureORM] = {str(f.id): f for f in fixture_rows}

    # ── Whitelist filter: only show accepted competitions ─────────────────
    from app.config.whitelist import get_whitelist
    from app.repositories.sqlalchemy.reference_repositories import (
        SqlAlchemyCompetitionRepository,
    )
    whitelist = get_whitelist()
    comp_repo = SqlAlchemyCompetitionRepository(session)
    accepted_fixtures: list[FixtureORM] = []
    skipped_whitelist = 0
    for f in fixture_rows:
        try:
            comp = await comp_repo.get(f.competition_id)
            comp_name = comp.name if comp else ""
            # Resolve league_id + country for exact-match whitelist
            league_id: int | None = None
            country = comp.country if comp else None
            if comp and comp.external_id:
                try:
                    league_id = int(comp.external_id)
                except (ValueError, TypeError):
                    pass
        except Exception:
            comp_name = ""
            league_id = None
            country = None
        if whitelist.is_allowed(comp_name, league_id=league_id, country=country):
            accepted_fixtures.append(f)
        else:
            skipped_whitelist += 1

    logger.info(
        "Dashboard whitelist filter: %d total fixtures → %d accepted, %d skipped",
        len(fixture_rows), len(accepted_fixtures), skipped_whitelist,
    )
    fixture_rows = accepted_fixtures
    fixture_lookup = {str(f.id): f for f in fixture_rows}

    # ── Predictions for today's fixtures ─────────────────────────────────
    fixture_ids = [f.id for f in fixture_rows]
    predictions: list[PredictionORM] = []
    if fixture_ids:
        predictions = (
            await session.execute(
                select(PredictionORM).where(
                    PredictionORM.fixture_id.in_(fixture_ids)
                )
            )
        ).scalars().all()

    preds_by_fixture: dict[str, list[PredictionORM]] = {}
    for p in predictions:
        fid = str(p.fixture_id)
        preds_by_fixture.setdefault(fid, []).append(p)

    # ── Odds snapshots for today's fixtures ─────────────────────────────
    odds_count = 0
    if fixture_ids:
        odds_count = (
            await session.scalar(
                select(func.count(OddsSnapshotORM.id)).where(
                    OddsSnapshotORM.fixture_id.in_(fixture_ids)
                )
            )
        ) or 0

    # ── Value bets for today's fixtures ─────────────────────────────────
    value_bet_count = 0
    if fixture_ids:
        value_bet_count = (
            await session.scalar(
                select(func.count(ValueBetORM.id)).where(
                    ValueBetORM.fixture_id.in_(fixture_ids)
                )
            )
        ) or 0

    # ── Decision logs for today ─────────────────────────────────────────
    decision_log_count = 0
    if fixture_ids:
        decision_log_count = (
            await session.scalar(
                select(func.count(DecisionLogORM.id)).where(
                    DecisionLogORM.fixture_id.in_(fixture_ids)
                )
            )
        ) or 0

    # ── Settlements for today's fixtures ─────────────────────────────────
    settled_count = 0
    settled_pl = 0.0
    if fixture_ids:
        settled_count = (
            await session.scalar(
                select(func.count(SettlementORM.id)).where(
                    SettlementORM.fixture_id.in_(fixture_ids)
                )
            )
        ) or 0
        pl_result = (
            await session.execute(
                select(func.sum(SettlementORM.profit_loss)).where(
                    SettlementORM.fixture_id.in_(fixture_ids)
                )
            )
        ).scalar()
        settled_pl = float(pl_result) if pl_result else 0.0

    # ── Latest performance snapshot ─────────────────────────────────────
    latest_perf = (
        await session.execute(
            select(PerformanceSnapshotORM)
            .order_by(PerformanceSnapshotORM.created_at.desc())
            .limit(1)
        )
    ).scalars().first()

    # ── Decision breakdown ──────────────────────────────────────────────
    # Gate Approved: gate.approved=True → final_decision="BET"
    # (WATCH = gate rejected for non-risk reasons; NO_BET = gate rejected
    #  for HIGH risk or EV ≤ 0; NO_ODDS = not evaluated due to missing odds)
    gate_approved_count = sum(
        1 for p in predictions if p.final_decision == "BET"
    )
    # Final Value Bets: from value_bets table (authoritative source)
    bet_count = value_bet_count
    watch_count = sum(1 for p in predictions if p.final_decision == "WATCH")
    no_bet_count = sum(1 for p in predictions if p.final_decision == "NO_BET")
    no_odds_count = sum(
        1 for p in predictions
        if p.why_not_bet and "odds" in (p.why_not_bet or "").lower()
    )

    # ── Data quality ────────────────────────────────────────────────────
    fixtures_with_odds = len({
        str(p.fixture_id) for p in predictions if p.odds is not None
    })
    fixtures_with_prob = len({
        str(p.fixture_id) for p in predictions if p.model_probability is not None
    })
    data_quality = DataQuality(
        items=[
            DataQualityItem(
                field="odds_available",
                coverage=fixtures_with_odds / max(len(predictions), 1),
                source="The Odds API",
                note=f"{fixtures_with_odds}/{len(predictions)} predictions have real odds",
            ),
            DataQualityItem(
                field="model_probability",
                coverage=fixtures_with_prob / max(len(predictions), 1),
                source="Poisson/Elo/Ensemble",
                note=f"{fixtures_with_prob}/{len(predictions)} predictions have model probs",
            ),
        ]
    )

    # ── Executive summary ───────────────────────────────────────────────
    perf_summary = {}
    if latest_perf:
        perf_summary["total_bets"] = latest_perf.total_bets
        perf_summary["win_rate"] = latest_perf.win_rate
        perf_summary["total_pl"] = float(latest_perf.total_pl) if latest_perf.total_pl else 0.0
        perf_summary["roi"] = latest_perf.roi

    summary = DailyExecutiveSummary(
        date=on_date.isoformat(),
        fixtures_total=len(fixture_rows),
        odds_snapshots=odds_count,
        predictions_total=len(predictions),
        gate_approved_count=gate_approved_count,
        bet_count=bet_count,
        watch_count=watch_count,
        no_bet_count=no_bet_count,
        no_odds_count=no_odds_count,
        value_bets_created=value_bet_count,
        decision_logs=decision_log_count,
        settlements=settled_count,
        settled_pl=settled_pl,
        performance=perf_summary,
    )

    # ── Per-match details ───────────────────────────────────────────────
    matches: list[MatchDashboardData] = []
    for fix in fixture_rows:
        fid = str(fix.id)
        preds = preds_by_fixture.get(fid, [])

        # Build fixture info
        fixture_info = FixtureInfo(
            home_team=preds[0].home_team if preds else "",
            away_team=preds[0].away_team if preds else "",
            home_score=fix.score_home,
            away_score=fix.score_away,
            start_time=fix.kickoff,
            competition=preds[0].competition if preds else "",
            status=fix.status,
        )

        # Build odds info from first prediction that has odds
        best_odds = None
        for p in preds:
            if p.odds is not None:
                best_odds = OddsInfo(
                    home_odds=None,  # per-selection, not 1X2
                    draw_odds=None,
                    away_odds=None,
                    bookmaker=None,
                )
                break

        # Build decision summary from predictions
        decisions = []
        for p in preds:
            decisions.append(
                DecisionInfo(
                    classification=p.final_decision,
                    confidence_score=float(p.confidence) if p.confidence else None,
                    why_not_bet=p.why_not_bet,
                    confidence_killer=p.confidence_killer,
                )
            )

        # Primary decision (most actionable)
        primary = decisions[0] if decisions else DecisionInfo()

        # Build NoBetChecks from prediction data — surfaces why_not_bet / confidence_killer
        nobet_items: list[NoBetCheckItem] = []
        nobet_catch_all = ""
        seen_labels: set[str] = set()
        for p in preds:
            if p.why_not_bet:
                label = p.confidence_killer or "数据不足"
                if label not in seen_labels:
                    seen_labels.add(label)
                    nobet_items.append(NoBetCheckItem(
                        label=label,
                        detail=p.why_not_bet[:200],
                        passed=False,
                    ))
            elif p.confidence_killer and p.confidence_killer not in seen_labels:
                seen_labels.add(p.confidence_killer)
                nobet_items.append(NoBetCheckItem(
                    label=p.confidence_killer[:60],
                    detail="",
                    passed=False,
                ))
        if nobet_items:
            nobet_checks = NoBetChecks(items=nobet_items, catch_all=nobet_catch_all)
        else:
            nobet_checks = None

        # Value info from first prediction with EV
        value_info = ValueInfo()
        for p in preds:
            if p.expected_value is not None:
                value_info = ValueInfo(
                    expected_value=p.expected_value,
                    kelly_stake=float(p.kelly_stake) if p.kelly_stake else None,
                    kelly_fraction=None,  # PredictionORM does not store kelly_fraction; only kelly_stake (EUR) is persisted
                )
                break

        # Model probabilities from first prediction with prob fields
        probs = ModelProbabilities()
        for p in preds:
            if p.prob_home is not None or p.prob_draw is not None or p.prob_away is not None:
                probs = ModelProbabilities(
                    poisson_home=p.prob_home,
                    poisson_draw=p.prob_draw,
                    poisson_away=p.prob_away,
                )
                break

        # Model availability
        has_odds = any(p.odds is not None for p in preds)
        has_probs = any(p.model_probability is not None for p in preds)
        has_ev = any(p.expected_value is not None for p in preds)
        model_avail = ModelAvailability(
            poisson=has_probs,
            elo=has_probs,
            ensemble=has_probs,
            monte_carlo=False,
            kelly=has_ev,
        )

        data_completeness = (
            1.0
            if (has_odds and has_probs and has_ev and preds)
            else (0.5 if (has_probs or has_odds) else 0.0)
        )

        # Override classification to INSUFFICIENT DATA when prediction exists but the
        # model could not evaluate due to insufficient historical data (all numeric
        # fields are NULL). This distinguishes "data pipeline OK but model can't evaluate"
        # from "prediction engine explicitly chose WATCH after full evaluation".
        if (
            preds
            and primary.classification == "WATCH"
            and not has_probs
            and not has_ev
            and primary.confidence_score is None
        ):
            primary.classification = "INSUFFICIENT DATA"

        matches.append(
            MatchDashboardData(
                fixture=fixture_info,
                odds=best_odds or OddsInfo(),
                probabilities=probs,
                value=value_info,
                decision=primary,
                model_availability=model_avail,
                data_completeness=data_completeness,
                nobet_checks=nobet_checks,
                generated_at=datetime.now(timezone.utc),
            )
        )

    # ── Top Picks / Recommendations from value_bets ──────────────────────
    top_picks: list[TopPick] = []
    top_recommendations: list[TopRecommendation] = []
    if fixture_ids:
        value_bets = (
            await session.execute(
                select(ValueBetORM).where(
                    ValueBetORM.fixture_id.in_(fixture_ids)
                )
            )
        ).scalars().all()

        # Build fixture_id → (home_team, away_team) lookup from predictions
        team_lookup: dict[str, tuple[str, str]] = {}
        for p in predictions:
            fid = str(p.fixture_id)
            if fid not in team_lookup and p.home_team and p.away_team:
                team_lookup[fid] = (p.home_team, p.away_team)

        # Sort by EV descending before converting to TopPick
        value_bets = sorted(
            value_bets,
            key=lambda vb: vb.model_probability * float(vb.odds_decimal) - 1.0,
            reverse=True,
        )

        for vb in value_bets:
            teams = team_lookup.get(str(vb.fixture_id), ("?", "?"))
            match_label = f"{teams[0]} vs {teams[1]}"
            ev = vb.model_probability * float(vb.odds_decimal) - 1.0

            top_picks.append(TopPick(
                match_label=match_label,
                market=vb.selection_market,
                odds=float(vb.odds_decimal),
                model_prob=vb.model_probability,
                ev=ev,
                confidence=vb.confidence,
                stake=vb.stake_fraction,
                reason=vb.rationale or "",
            ))

            top_recommendations.append(TopRecommendation(
                match_label=match_label,
                market=vb.selection_market,
                selection=vb.selection_code,
                odds=float(vb.odds_decimal),
                model_prob=vb.model_probability,
                ev=ev,
                confidence=vb.confidence,
                stake=vb.stake_fraction,
                reason=vb.rationale or "",
                category="精选",
                risk_level="",
            ))

    return DailyDashboardData(
        date=on_date.isoformat(),
        executive_summary=summary,
        data_quality=data_quality,
        matches=matches,
        top_picks=top_picks,
        top_recommendations=top_recommendations,
        generated_at=datetime.now(timezone.utc),
        pipeline_version=pipeline_version,
    )
