"""CLI — 从归一化 JSON 数据生成 HTML 仪表盘。

用法：
    # 日概览
    python scripts/generate_dashboard.py daily --input data/daily_20260711.json --output output/daily.html

    # 单场详情
    python scripts/generate_dashboard.py match --input data/match_spain_vs_belgium.json --output output/match.html

输入 JSON 结构见 app/dashboard/types.py 各 dataclass 字段名（snake_case）。
所有缺失字段统一渲染为 "Not Available"。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


def _dict_to_fixture(data: dict[str, Any]) -> Any:
    from app.dashboard.types import FixtureInfo

    return FixtureInfo(
        home_team=data.get("home_team", ""),
        away_team=data.get("away_team", ""),
        home_score=data.get("home_score"),
        away_score=data.get("away_score"),
        start_time=datetime.fromisoformat(data["start_time"]) if data.get("start_time") else None,
        venue=data.get("venue"),
        competition=data.get("competition"),
        status=data.get("status"),
    )


def _dict_to_odds(data: dict[str, Any]) -> Any:
    from app.dashboard.types import OddsInfo

    return OddsInfo(
        home_odds=data.get("home_odds"),
        draw_odds=data.get("draw_odds"),
        away_odds=data.get("away_odds"),
        bookmaker=data.get("bookmaker"),
    )


def _dict_to_probabilities(data: dict[str, Any]) -> Any:
    from app.dashboard.types import ModelProbabilities

    return ModelProbabilities(
        poisson_home=data.get("poisson_home"),
        poisson_draw=data.get("poisson_draw"),
        poisson_away=data.get("poisson_away"),
        elo_home=data.get("elo_home"),
        elo_draw=data.get("elo_draw"),
        elo_away=data.get("elo_away"),
        ensemble_home=data.get("ensemble_home"),
        ensemble_draw=data.get("ensemble_draw"),
        ensemble_away=data.get("ensemble_away"),
    )


def _dict_to_value(data: dict[str, Any]) -> Any:
    from app.dashboard.types import ValueInfo

    return ValueInfo(
        expected_value=data.get("expected_value"),
        edge=data.get("edge"),
        kelly_fraction=data.get("kelly_fraction"),
    )


def _dict_to_decision(data: dict[str, Any]) -> Any:
    from app.dashboard.types import DecisionInfo

    return DecisionInfo(
        classification=data.get("classification"),
        confidence_score=data.get("confidence_score"),
        why_not_bet=data.get("why_not_bet"),
        confidence_killer=data.get("confidence_killer"),
    )


def _dict_to_model_availability(data: dict[str, Any]) -> Any:
    from app.dashboard.types import ModelAvailability

    return ModelAvailability(
        poisson=bool(data.get("poisson", False)),
        elo=bool(data.get("elo", False)),
        ensemble=bool(data.get("ensemble", False)),
        monte_carlo=bool(data.get("monte_carlo", False)),
        kelly=bool(data.get("kelly", False)),
    )


def _dict_to_scenarios(data: dict[str, Any]) -> Any:
    from app.dashboard.types import ScenarioInfo

    return ScenarioInfo(items=data.get("items", []))


def _dict_to_match(data: dict[str, Any]) -> Any:
    from app.dashboard.types import MatchDashboardData

    return MatchDashboardData(
        fixture=_dict_to_fixture(data.get("fixture", {})),
        odds=_dict_to_odds(data.get("odds", {})),
        probabilities=_dict_to_probabilities(data.get("probabilities", {})),
        value=_dict_to_value(data.get("value", {})),
        decision=_dict_to_decision(data.get("decision", {})),
        model_availability=_dict_to_model_availability(data.get("model_availability", {})),
        weather=data.get("weather"),
        injuries=data.get("injuries"),
        scenarios=_dict_to_scenarios(data.get("scenarios", {})),
        data_completeness=data.get("data_completeness"),
        evidence_level=data.get("evidence_level"),
        generated_at=datetime.fromisoformat(data["generated_at"]) if data.get("generated_at") else None,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate football dashboard HTML from JSON data.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_daily = sub.add_parser("daily", help="Generate daily overview dashboard")
    p_daily.add_argument("--input", required=True, help="Path to daily JSON data file")
    p_daily.add_argument("--output", required=True, help="Output HTML path")

    p_match = sub.add_parser("match", help="Generate single-match detail dashboard")
    p_match.add_argument("--input", required=True, help="Path to match JSON data file")
    p_match.add_argument("--output", required=True, help="Output HTML path")

    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        raw = json.load(f)

    from app.dashboard import DashboardRenderer, DailyDashboardData

    renderer = DashboardRenderer()

    if args.command == "daily":
        matches = [_dict_to_match(m) for m in raw.get("matches", [])]
        daily = DailyDashboardData(
            date=raw.get("date", ""),
            matches=matches,
            generated_at=datetime.fromisoformat(raw["generated_at"]) if raw.get("generated_at") else None,
            pipeline_version=raw.get("pipeline_version"),
        )
        html = renderer.render_daily_overview(daily)
    else:
        match = _dict_to_match(raw)
        html = renderer.render_match_detail(match)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    print(f"Dashboard written: {output_path} ({len(html)} chars)")


if __name__ == "__main__":
    main()
