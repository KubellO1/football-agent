"""Dashboard and configuration labels follow the approved odds-age policy."""

import pytest

from app.config.settings import Settings
from app.core.service_factory import build_market_quote_policy
from app.dashboard.renderer import DashboardRenderer
from app.dashboard.types import DailyDashboardData


@pytest.mark.unit
def test_odds_policy_defaults_are_80_and_180_minutes() -> None:
    assert Settings.model_fields["analysis_odds_max_age_minutes"].default == 80
    assert Settings.model_fields["analysis_odds_preliminary_max_age_minutes"].default == 180
    policy = build_market_quote_policy(
        Settings(
            _env_file=None,
            analysis_odds_max_age_minutes=80,
            analysis_odds_preliminary_max_age_minutes=180,
        )
    )
    assert policy.maximum_age.total_seconds() == 80 * 60
    assert policy.preliminary_maximum_age.total_seconds() == 180 * 60


@pytest.mark.unit
def test_dashboard_labels_final_preliminary_and_too_stale_from_data() -> None:
    html = DashboardRenderer().render_daily_overview(
        DailyDashboardData(
            date="2026-09-21",
            final_odds_max_age_minutes=80,
            preliminary_odds_max_age_minutes=180,
        )
    )

    assert "≤80 分钟可进入 FINAL 审核" in html
    assert ">80–180 分钟仅 PRELIMINARY/WATCH" in html
    assert ">180 分钟 ODDS_TOO_STALE" in html
