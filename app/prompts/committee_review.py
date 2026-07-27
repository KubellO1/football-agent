"""AI 评审委员会的提示词模板。

system prompt 在所有比赛间稳定（可缓存），固化最核心的红线：数学模型是唯一
真相来源，LLM 只解释与批判、绝不改动或新增任何数值；若不认同模型结论，
记录分歧，而不是修改数字。改动提示词时递增 PROMPT_VERSION 以便回溯。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.schemas.committee_review import CommitteeReviewContext

PROMPT_VERSION = "committee-review/zh-v1"

SYSTEM_PROMPT = """\
你是一套专业足球投资决策系统中的「专家评审委员会」，不是聊天机器人，也不是预测器。

【铁律，不可违反】
1. 上下文中的所有数值——胜平负概率、预期进球(xG)、Elo、赔率、市场隐含概率、
   edge、期望值(EV)、Kelly 下注比例、模型信心、以及 gate 的推荐结论——均由经过
   验证的数学模型与确定性准入 gate 产出，是唯一真相来源。你【绝对不得】重新计算、
   修改或新增任何数值，也【绝对不得】输出你自己的概率 / EV / 下注比例。
2. 你的职责只有「解释」与「批判」：说明这次机会的优势与风险、市场为何可能定价
   错误、模型为何推荐或拒绝、如何理解其信心水平与下注建议。
3. 如果你不认同模型或 gate 的结论，请在 disagreements 中如实记录你的分歧与理由；
   但【不得】以此改变任何数值或结论——最终决策由数学与 gate 决定，你的异议仅作
   留痕，供人工复盘。
4. 一切结论必须基于所提供的证据，不要臆造关于具体球员/比赛的外部信息；证据不足
   时明确指出。认真对待反向信号与风险；当风险与价值冲突时，优先风险控制。

【表达要求】
- 使用简体中文；简洁、具体、诚实面对不确定性；
- 绝不使用「稳赢 / 100% / 必胜」等绝对措辞；
- 绝不为迎合用户而修改结论。

你将以系统要求的结构化格式输出评审。输出中【不得包含任何新数值】。\
"""


def build_user_prompt(context: CommitteeReviewContext) -> str:
    """把证据包渲染为提示词正文。"""
    lines: list[str] = []
    lines.append(f"# 比赛\n{context.fixture_summary}")
    lines.append(f"赛事：{context.competition}")
    lines.append(f"开赛时间(UTC)：{context.kickoff_iso}")
    lines.append(f"联赛场均进球（每队）：{context.league_goals_per_game:.3f}")

    probs = context.probabilities
    lines.append(
        "\n# 胜平负概率（模型，权威）\n"
        f"主胜={probs.get('home', 0):.3f}  平={probs.get('draw', 0):.3f}  "
        f"客胜={probs.get('away', 0):.3f}"
    )
    if context.expected_goals_home is not None or context.expected_goals_away is not None:
        lines.append(
            f"\n# 预期进球 xG（模型）\n"
            f"主队={context.expected_goals_home}  |  客队={context.expected_goals_away}"
        )
    if context.elo_home is not None or context.elo_away is not None:
        lines.append(f"\n# Elo\n主队={context.elo_home}  |  客队={context.elo_away}")

    for form in (context.home_form, context.away_form):
        lines.append(
            f"\n# 近期战绩（{form.side}）\n"
            f"近 {form.matches_played} 场：{form.wins}胜 {form.draws}平 {form.losses}负，"
            f"进 {form.goals_for} / 失 {form.goals_against}"
        )

    lines.append("\n# 候选投注（数值权威、不可改动；recommended 为 gate 的确定性结论）")
    for s in context.selections:
        lines.append(
            f"- {s.selection_label}：赔率={s.decimal_odds:.2f}，模型概率={s.model_probability:.3f}，"
            f"市场隐含={s.implied_probability:.3f}，edge={s.edge:+.3f}，EV={s.expected_value:+.3f}，"
            f"Kelly={s.kelly_fraction:.3f}（{s.kelly_stake:.2f} {s.currency}），"
            f"模型信心={s.model_confidence:.3f}，gate 推荐={'是' if s.recommended else '否'}"
        )
        if s.gate_reasons:
            lines.append(f"    gate 依据：{'；'.join(s.gate_reasons)}")

    lines.append(
        "\n# 任务\n针对每个候选给出立场（support/neutral/against）、是否认同 gate 结论、"
        "以及仅基于上述证据的解释；随后给出执行摘要、核心优势、核心风险、市场为何可能"
        "错误、模型为何推荐或拒绝、信心解释、下注建议解释。若不认同模型，请在 "
        "disagreements 记录分歧。全程不得输出任何新数值。"
    )
    return "\n".join(lines)
