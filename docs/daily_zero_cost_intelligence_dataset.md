# Daily Zero-Cost Football Intelligence Dataset

## 定位

本组件只负责从现在开始积累新的 point-in-time 足球情报，不参与模型优化，也不直接接入生产决策。所有输出先写入隔离的追加式归档；进入生产前，必须经过独立的数据库、部署和计划任务审批。

月度新增外部数据成本目标为 `€0`。任何需要付费订阅、绕过登录、验证码、付费墙、反爬控制，或自动化授权不明确的来源，都不能进入自动采集。

## 数据契约

每条标准化 observation 必须包含：

- `fixture_id`
- `source`
- `source_url`
- `published_at`（来源未提供时为 `null`）
- `captured_at`
- `raw_payload_hash`（SHA-256）
- `parser_version`
- `data_type`
- `source_policy`
- `capture_window`

原始响应按内容哈希追加归档；相同内容即使 HTTP `Content-Type` 不同也只保存一份。标准化 observation 使用确定性 ID 去重，重复运行不会覆盖或重复追加已有观察。

## 来源决策

| 来源 | 决策 | 自动化/授权依据 | 可覆盖数据 | 五大联赛 | 限制 |
|---|---|---|---|---|---|
| OpenFootball `football.json` | APPROVED | 官方仓库声明 CC0、无需 API key、提供 raw JSON | fixtures、完场比分 | 5/5 | 社区维护、无 SLA；不能作为官方阵容或高级统计来源 |
| MET Norway Locationforecast 2.0 | APPROVED | 官方 API，CC BY 4.0，允许商业使用 | 天气预报 | 5/5（需要已验证球场坐标） | 必须署名、设置可识别 User-Agent、遵守 `Expires`/缓存；无 SLA |
| 用户提供的官方证据 URL | APPROVED_MANUAL | 用户手工提供公开官方来源，不自动抓取 | 球队新闻、伤停、停赛、首发、阵容变化、赔率 | 5/5（人工覆盖） | 必须保留 URL 和时间；不能声称自动覆盖 |
| OpenLigaDB | CONDITIONAL_NOT_SELECTED | 无认证公开 API，数据为 ODbL | fixtures、比分 | 不稳定/不完整 | 社区数据且联赛 shortcut 不统一，不作为五大联赛主源 |
| Football-Data.co.uk | RESEARCH_ONLY | 公开下载，但生产商业自动化授权未得到明确确认 | 历史赛果、部分统计、历史赔率 | 5/5 | 只保留现有研究基准，不进入 live production completeness |
| StatsBomb Open Data | RESEARCH_ONLY | 开放仓库许可适用于指定公开比赛数据 | 静态事件数据 | 非连续 5/5 | 不是五大联赛实时全覆盖来源 |
| Open-Meteo hosted Free API | REJECTED | 免费托管层条款限定非商业使用 | 天气 | 技术上可覆盖 | 商业产品不能使用其免费托管层 |
| FBref / Sofascore / FotMob / Understat | REJECTED_AUTOMATION | 未取得满足本项目商业自动采集要求的明确授权，且存在 robots/访问控制风险 | 各类统计 | 不计 | 不因页面可打开就视为允许自动化 |
| 公共赔率网页 | REJECTED_AUTOMATION | 未找到明确允许商业自动采集且不需绕过访问控制的零成本来源 | 赔率、赔率变化 | 0/5 | 只允许合规 API 或用户手工输入 |

MET Norway 数据对外展示时必须标注来源与 CC BY 4.0；不得使用 Yr 品牌或徽标暗示官方背书。

## 采集窗口计划

调度器每次只选择“当前已越过且尚未完成的最新窗口”，避免延迟运行时补跑多个过期窗口。`fixture_id + capture_window + source + payload hash` 共同保证可审计和幂等。

| 窗口 | 自动数据 | 手工/待审批数据 | 说明 |
|---|---|---|---|
| `DAILY` | OpenFootball 五个联赛 fixtures/完场比分 | 官方赛程更正 | 每日一次，建议在上游约 05:00 UTC 更新后错峰执行 |
| `T-24H` | MET 天气 | 官方球队新闻、伤停、停赛 | 仅对数据库已有且已匹配球场坐标的比赛 |
| `T-6H` | MET 天气（按缓存头决定是否发请求） | 官方球队新闻、伤停、停赛 | 不重复下载未过期响应 |
| `T-90` | MET 天气 | 官方阵容信息、用户赔率 | 不自动抓取赔率网站 |
| `T-60` | MET 天气缓存/条件请求 | 阵容变化、伤停变化、用户赔率 | 只执行最新 due window |
| `T-30` | MET 天气缓存/条件请求 | 已确认首发、临场变化、用户赔率 | 缺首发/赔率必须保留缺失状态 |
| `POST_MATCH` | OpenFootball 完场比分（下次 `DAILY`） | 官方赛后更正 | 当前零成本自动源不提供完整赛后技术统计 |

## 请求预算

- OpenFootball：固定最多 5 次/日（每联赛一次），12 小时本地缓存。
- MET Norway：按唯一球场坐标和响应缓存头请求；客户端不早于 `Expires` 再取，并在有 `Last-Modified` 时使用条件请求。按平均每天约 5 场五大联赛、每场最多 3 次实际刷新估算约 15 次/日。
- 预计合计：约 20 次 HTTP 请求/日、约 600 次/月。
- 建议硬预算：60 次/日；预算耗尽时记录来源失败，不绕过限流。
- 费用：`€0/月`。

上述估算不包含浏览器页面抓取、赔率 API 或付费 API；这些均不在方案内。

## 数据流

```text
合法公开来源 / 手工官方证据
  -> HTTPS + User-Agent + 缓存 + 请求预算
  -> immutable raw payload archive (SHA-256)
  -> provider adapter + parser version
  -> fixture/team conservative matching
  -> normalized point-in-time observation
  -> duplicate/stale/source-health checks
  -> isolated append-only dataset
  -> [Production Data Collection Approval Gate]
```

匹配只接受联赛、主客队和开赛时间同时一致的唯一候选。别名或多候选不做猜测，分别返回 `NO_EXACT_FIXTURE_MATCH` 和 `AMBIGUOUS_FIXTURE_MATCH`。

## 当前覆盖和缺口

- Fixtures：自动覆盖 5/5。
- 完场比分：自动覆盖 5/5，但受社区更新时间和正确性影响。
- Weather：技术覆盖 5/5；正式运行前还需要可靠的球场坐标映射。
- 官方球队新闻、伤停、停赛、首发、阵容变化：只提供可审计的手工输入边界，自动覆盖 0/5。
- 合法公开赔率及赔率移动：自动覆盖 0/5；当前只能手工输入。
- 完整赛后 shots/xG/事件统计：自动覆盖 0/5。

因此，该数据集可以开始积累新赛程、结果和天气快照，但不能声称已具备完整的实时投注输入。

## 隔离 Canary

2026-09-13 对 OpenFootball 五大联赛 2026/27 raw JSON 和一个 MET Norway 巴黎坐标技术天气样本执行低频 Canary：

- 首次真实网络运行：6 个请求，6 个来源成功，无来源错误。
- 解析：1,752 条 fixture observations、136 条 post-match observations、1 条 weather observation，共 1,889 条。
- 缓存重放：0 个网络请求。
- 第一次写入干净隔离目录：1,889 inserted。
- 第二次相同重放：0 inserted、1,889 duplicates、0 个新 raw payload。

Canary 天气记录是巴黎坐标的技术解析验证，不是生产 fixture，也不能写入生产数据库。

## 进入生产前仍需审批的变化

1. 设计并审计 point-in-time observation 的 PostgreSQL schema/migration，或批准独立对象存储作为正式数据层。
2. 建立生产 fixture 与 OpenFootball identity、球场坐标的人工审核映射。
3. 明确 raw payload 的保留周期、MET 署名展示和数据治理规则。
4. 审批 Scheduler 的 `DAILY` 与 checkpoint 采集任务、每源请求预算和失败告警。
5. 先做不超过 10 场的生产数据采集 Canary，再单独审批持续运行。

本任务未执行生产数据库写入、部署或 Scheduler 变更。
