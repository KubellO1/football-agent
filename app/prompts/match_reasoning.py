"""比赛推理 Agent 的提示词模板。

system prompt 在所有比赛间保持稳定（会被缓存），并固化最核心的规则：
数学模型是唯一真相来源，LLM 的职责只是定性评审——而不是预测。
详见 docs/agent-constitution.md（系统宪法）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.schemas.reasoning import ReasoningContext

# 提示词版本号：中文版第 1 版。改动提示词时递增，便于回溯。
PROMPT_VERSION = "match-reasoning/zh-v1"

SYSTEM_PROMPT = """\
你是一套专业足球投资决策系统中的「评审与审议层」，不是聊天机器人。

你的唯一目标：帮助用户长期稳定盈利。不是预测比赛，不是提高命中率，而是
识别长期正期望值（Positive EV）机会。如果没有价值，就直接说明没有价值，
绝不为了给出答案而强行推荐。

【铁律，不可违反】
1. 上下文中提供的概率、盘口价值、期望值(EV)、Elo、预期进球(xG)、Kelly
   下注单位等所有数值，均由经过验证的数学模型计算得出，是唯一真相来源。
   你【绝对不得】重新计算、修改、或新增任何数值，也【绝对不得】输出你自己
   的概率、EV 或下注单位。
2. 你只做定性评审。针对每个候选投注，判断定性证据（伤停、首发、盘口/赔率
   变化、近期状态、战意等）是「支持」还是「反驳」模型，并给出裁决：
   保留(keep) / 降低(reduce) / 放弃(discard)。降低或放弃是对信心的判断，
   不是给出新的概率。
3. 一切结论必须基于所提供的证据。不要使用关于具体球员或比赛的外部知识——
   你可能没有最新信息，编造比不说更糟。证据不足时，明确说出来，并倾向于
   reduce 或 discard。
4. 认真对待反向信号：关键球员确认缺阵、首发与模型假设矛盾、聪明钱(Sharp
   Money)反向流动，都是降低信心的理由。

【审议方式：决策委员会】
以多个专家视角审议后由首席分析师(Chief Analyst)汇总，视角包括：数据分析师、
战术分析师、球员分析师、首发分析师、战意分析师、盘口分析师、赔率分析师、
EV 分析师、风险经理、以及 Red Team（反方）。其中 EV 分析师与风险经理引用的
数值同样来自数学模型，你只做解读与权衡，不生成数值。

【Red Team（反方分析）】
在给出最终评审前，必须回答「这场为什么可能错」，至少提出 3 条反对理由，
并据此重新评估信心。

【自我审查】
警惕并主动排查：近期偏差(Recency Bias)、大众偏差(Popularity Bias)、
过度相信热门球队、过度相信近期状态。发现逻辑冲突时，倾向降低信心。

【表达要求】
- 使用简体中文；
- 简洁、具体、诚实面对不确定性；
- 绝不使用「稳赢 / 100% / 必胜」等绝对措辞；
- 绝不为了迎合用户而修改结论。

当风险与预测/盘口价值发生冲突时，优先相信风险控制。

你将以系统要求的结构化格式输出评审结论（各角色意见、裁决、信心、推荐理由、
反对理由、未知因素、以及哪些因素会改变结论）。输出中【不得包含任何数值】——
所有数值均由数学模型负责。\
"""


def build_user_prompt(context: ReasoningContext) -> str:
    """将证据包渲染为可读的提示词正文。"""
    lines: list[str] = []
    lines.append(f"# 比赛\n{context.fixture_summary}")
    lines.append(f"赛事：{context.competition}")
    lines.append(f"开赛时间(UTC)：{context.kickoff_iso}")

    # 预期进球（模型产出）
    if context.expected_goals_home is not None or context.expected_goals_away is not None:
        lines.append(
            f"\n# 预期进球 xG（模型）\n"
            f"主队 xG：{context.expected_goals_home}  |  客队 xG：{context.expected_goals_away}"
        )
    if context.elo_home is not None or context.elo_away is not None:
        lines.append(f"\n# Elo 评分\n主队：{context.elo_home}  |  客队：{context.elo_away}")

    # 模型概率 vs 市场隐含概率
    if context.outcome_probabilities:
        lines.append("\n# 胜平负概率（模型 vs 市场）")
        for op in context.outcome_probabilities:
            lines.append(
                f"- {op.outcome}：模型={op.model_probability:.3f}"
                + (f"，市场隐含={op.implied_probability:.3f}" if op.implied_probability else "")
                + (f"，赔率={op.decimal_odds}" if op.decimal_odds else "")
            )

    # 模型标记的候选价值投注——数值权威，不可改动
    if context.candidate_bets:
        lines.append("\n# 候选价值投注（模型标记，数值权威、不可改动）")
        for b in context.candidate_bets:
            stake = f"，建议单位={b.recommended_stake}" if b.recommended_stake is not None else ""
            book = f"，{b.bookmaker}" if b.bookmaker else ""
            lines.append(
                f"- {b.selection_label}：赔率={b.decimal_odds}，"
                f"模型概率={b.model_probability:.3f}，edge={b.edge:.3f}，"
                f"EV={b.expected_value:.3f}，kelly={b.kelly_fraction:.3f}{stake}{book}"
            )

    # 盘口/赔率变化
    if context.market_movements:
        lines.append("\n# 盘口变化")
        for m in context.market_movements:
            lines.append(
                f"- {m.selection_label}：{m.opening_odds} -> {m.current_odds}（{m.direction}）"
            )

    # 近期状态
    if context.team_form:
        lines.append("\n# 近期状态")
        for f in context.team_form:
            lines.append(
                f"- {f.team}：近{f.matches_played}场 {f.wins}胜{f.draws}平{f.losses}负，"
                f"进{f.goals_for}/失{f.goals_against}，xGF {f.xg_for}/xGA {f.xg_against}"
            )

    # 伤停与出场情况
    if context.injuries:
        lines.append("\n# 伤停 / 出场情况")
        for i in context.injuries:
            note = f" — {i.note}" if i.note else ""
            lines.append(f"- {i.team}：{i.player}（{i.status}）{note}")

    # 首发阵容
    if context.lineups:
        lines.append("\n# 首发阵容")
        for lu in context.lineups:
            status = "已确认" if lu.is_confirmed else "预测"
            absences = f"；缺阵：{', '.join(lu.key_absences)}" if lu.key_absences else ""
            lines.append(f"- {lu.team}（{status}）{lu.formation or ''}{absences}")

    lines.append(
        "\n# 任务\n评审每一个候选投注。不要改动任何数值。"
        "针对每个候选给出裁决（保留/降低/放弃）、信心，以及仅基于上述证据的"
        "理由；随后给出整体汇总、Red Team 反方理由与未知因素。"
    )
    return "\n".join(lines)
