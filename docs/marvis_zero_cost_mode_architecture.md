# Marvis Zero-Cost Mode Architecture

## 1. 决策摘要

`NEW_PAID_PROVIDER_BUDGET = €0` 可以支持合法、可复现的研究模式，以及不含正式投注建议的赛前初步分析；不能仅凭当前免费数据持续支持生产级自动 BET。

零成本路线不修改现有生产模型、完整度公式、90% Gate 或任何风控规则。它新增的是一条彼此隔离的研究路线：

```text
开放/已持有数据
  -> 来源、许可、时间边界登记
  -> Zero-Cost Research Dataset
  -> MARVIS_ZERO_COST_V1 离线实验
  -> 概率与校准评估
  -> PRELIMINARY_ANALYSIS

用户手工输入可信临场信息
  -> 严格校验和来源标记
  -> 现有模型/EV/Kelly/Gate
  -> 仅当现有生产规则全部通过时才可能 BET
```

## 2. 当前零增量成本资产

结构化清单见 `marvis_zero_cost_asset_inventory.json`。本次无法连接本地 PostgreSQL/Redis，因此数据库数量均标记为历史核验快照，而不是当前实时事实。

| 资产 | 可研究 | 可直接用于生产 | 关键限制 |
|---|---:|---:|---|
| Production PostgreSQL 已保存数据 | 有条件 | 仅限已批准现有链路 | 必须保留上游 provenance、retention 和 as-of 时间边界 |
| Fixtures、team/competition mappings | 是 | 是 | 映射和时间必须可审计 |
| Predictions | 是 | 是 | 属于派生数据，训练时防止标签/时间泄漏 |
| Odds snapshots | 有条件 | 有条件 | 最近核验快照为 0 行；使用权受现有供应商条款约束 |
| 仓库历史/回测数据集 | 否 | 否 | 未发现受版本控制的数据集 |
| TASK-051 本地样本 | 是 | 否 | 10 场共同样本，StatsBomb 部分仅研究使用 |
| StatsBomb Open Data | 是 | 否 | 选择性历史覆盖；归因要求；不是当前五大联赛实时源 |
| Wyscout 公开事件数据集 | 是 | 否 | 静态研究数据，无实时 SLA |
| openfootball | 是 | 有条件 | CC0，但社区准确性和无 SLA，不应作为唯一权威源 |
| OpenLigaDB | 是 | 有条件 | ODbL、社区维护、五大联赛覆盖不完整且无 SLA |
| football-data.org 免费层 | 候选 | 有条件 | 注册 token、比赛/积分榜延迟、限速且无高级事件指标 |
| Football-Data.co.uk | 是 | 否 | 适合历史结果/赔率回测；生产与再分发权未确认 |

既有 API 订阅或已购买权限属于已有资产，但本架构不能依赖续费，也不能把“曾经获取”自动解释成永久保存或商业再利用权。每批数据必须保留具体供应商许可状态。

## 3. 零成本来源准入规则

只有以下来源可以进入候选池：官方开放下载、明确免费 API、免费公开下载，或明确许可自动访问的公开网页。本次没有批准任何 `PUBLIC_WEB_AUTOMATION_ALLOWED` 来源。

来源分级：

- `A_OPEN_DATA`：官方或明确开放授权的数据集。
- `B_FREE_OFFICIAL_API`：官方免费接口，仍受注册、限速和条款约束。
- `C_FREE_PUBLIC_DOWNLOAD`：公开静态下载。
- `D_PUBLIC_WEB_AUTOMATION_ALLOWED`：必须有明确自动化许可；本次为空。
- `E_RESEARCH_ONLY`：只能用于研究、验证或教学。
- `F_UNSAFE_REJECT`：无明确自动化/商业授权、可能触发登录、付费墙、CAPTCHA 或反爬限制。

FBref、Sofascore、FotMob、Understat 等网页在没有明确授权前属于 `F_UNSAFE_REJECT`，不得因浏览器可打开就系统抓取。

## 4. Zero-Cost Research Dataset

研究数据层必须与生产实时完整度隔离。最低数据契约：

- `source`、`source_version`、`license_mode`、`captured_at`、`as_of_cutoff` 必填。
- 研究专用记录必须显式设置 `RESEARCH_ONLY=true`。
- 未知值保持 `NULL/UNKNOWN`，不使用 goals 替代 xG，不使用近似值冒充 Frozen 指标。
- 时间切分按 league/season 做 rolling-origin；目标比赛开赛后的信息不能进入训练输入。
- 不同来源的数据只能在语义等价且 fixture identity 已核验时合并。

StatsBomb Open Data 可用于 PPDA、Header Shots、Set-Piece Shots 的事件实现验证；Big Chances 没有符合现有 contract 的等价字段，不能以 xG 阈值代替。

## 5. ZERO_COST_FEATURE_SET_V1

详细机器可读定义见 `marvis_zero_cost_feature_set_v1.json`。

生产无新增费用时的稳定核心特征：

- goals for / against
- home / away
- recent form
- rest days
- league baseline

以下只有在存在可信来源和明确 provenance 时才作为可选特征：

- real xG / xGA
- shots / shots on target
- possession
- goalkeeper saves
- conversion rate

PPDA、Big Chances、Set-Piece Shots、Header Shots 不再是零成本实验模型的 required 输入。它们可以进入研究变体，但不能提高生产 live completeness，也不能改变现有 90% Gate。

## 6. MARVIS_ZERO_COST_V1

该模型只是独立实验设计，不替换生产模型：

| 变体 | 特征增量 | 用途 |
|---|---|---|
| A | goals、失球、form、主客场、休息天数、联赛基线 | 最小基线 |
| B | A + xG/xGA | 衡量真实 xG 的增益 |
| C | B + shots/SOT | 衡量射门质量前的数量信息 |
| D | C + possession/saves/conversion | 扩展免费常见统计 |
| E | D + 开放事件特征 | 仅研究，不进入生产实时完整度 |

所有变体在完全相同的 OOS fixture 集合上比较：Brier Score、Log Loss、校准曲线/ECE、准确率（次要）、样本量与置信区间、跨联赛稳定性。只有存在合法保留且与决策时点一致的历史赔率时，才计算 OOS ROI 和 Max Drawdown。

当前不能宣称模型已经具有正 ROI。合理结论是：不依赖 PPDA/Big Chances 的概率模型具有可检验的研究价值，但是否具有真实投注价值必须由足够样本、严格 OOS 校准和同期赔率证明。

## 7. 零成本实时模式

| 条件 | 状态 | EV | Kelly | BET |
|---|---|---|---|---|
| 历史/核心数据不足 | `INSUFFICIENT_DATA` | unavailable | unavailable | 禁止 |
| 历史足够但首发缺失 | `WAITING_FOR_LINEUP` | unavailable | unavailable | 禁止 |
| 数据可建模但无可靠实时赔率 | `PRELIMINARY_ANALYSIS` | unavailable | unavailable | 禁止 |
| 用户输入已验证赔率和临场数据 | `EXISTING_GATE_EVALUATION` | 按现有公式 | 按现有公式 | 仅现有 Gate 全部通过 |

免费模式可以完整保留：历史建模、概率输出、校准、风险解释、缺失数据披露和赛后复盘。它会退化或不能提供：持续自动实时赔率、自动确认首发/伤停、正式 EV/Kelly、自动 BET。

## 8. 人工输入模式

机器可读契约见 `marvis_manual_input_contract.json`。用户可以输入：

- 同一 canonical bookmaker 的三项 decimal odds；
- 带时间和证据来源的 injuries；
- 带确认时间和证据来源的 confirmed lineup。

接口必须验证 fixture、市场、三项价格、时区、freshness、bookmaker identity、自然键幂等以及证据时间。人工输入必须与 provider 数据明确区分，不能被视为独立交叉验证。输入有效后仍运行现有 EV、Kelly 和 Recommendation Gate；任何生产阈值保持原值。

## 9. 成本与能力边界

`MONTHLY_EXTERNAL_DATA_COST = €0`，不包含既有本地计算、存储和运维成本。

| 能力 | 结果 |
|---|---|
| 离线研究、特征验证、回测框架 | 可行 |
| 基于已持有数据的概率预览 | 可行 |
| 五大联赛持续高质量实时数据 | 不保证 |
| 无人工赔率的正式投注模式 | 不可行 |
| 人工提供可信赔率后的正式评估 | 有条件可行 |
| 自动高级事件特征生产覆盖 | 不可行 |

## 10. 迁移计划

本任务不修改 production。建议下一任务为：

`Zero-Cost Research Dataset Manifest + Baseline Experiment Runner`

范围应限定为隔离数据目录和离线实验：建立许可/provenance manifest，生成统一时序切分，先运行 A–D 变体，输出 OOS 指标和样本充分性；E 变体只在开放事件数据覆盖的配对样本中运行。任务完成后再次停在批准门。

## 11. 最终状态

```text
ZERO_COST_MODE_FEASIBLE=YES_WITH_REDUCED_AUTOMATION
RESEARCH_MODE_FEASIBLE=YES
LIVE_PRELIMINARY_MODE_FEASIBLE=YES
FORMAL_BET_MODE_WITHOUT_MANUAL_ODDS=NO
MANUAL_ODDS_MODE_FEASIBLE=YES_CONDITIONAL
RECOMMENDED_NEXT_TASK=Zero-Cost Research Dataset Manifest + Baseline Experiment Runner
```

## 12. 官方来源

- StatsBomb Open Data: https://github.com/hudl/open-data/blob/master/README.md
- Wyscout public event dataset: https://figshare.com/collections/Soccer_match_event_dataset/4415000/2
- openfootball CC0 license: https://github.com/openfootball/football.json/blob/master/LICENSE.md
- OpenLigaDB: https://openligadb.de/
- football-data.org pricing/coverage/policies: https://www.football-data.org/pricing
- Football-Data.co.uk: https://www.football-data.co.uk/data.php
