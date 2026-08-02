# 赛前状态解析器

本批次只引入纯状态模型，不接入数据库、仓储、调度器、Provider 或 API。

解析器以不可变快照为输入，按以下优先级返回单一状态：

1. 历史数据不足：`INSUFFICIENT_HISTORY`
2. 首发缺失：`WAITING_FOR_LINEUP`
3. 赔率缺失：`ODDS_MISSING`
4. 数据完整且 EV、置信度、Recommendation Gate、风险检查全部通过：`BET`
5. 完整评估未通过且尚未到 T-30：`WATCH`
6. 完整评估未通过且已到 T-30：`FINAL_NO_BET`

模型概率、EV 或置信度缺失时，评估仍不完整，因此不会生成 `FINAL_NO_BET`。历史状态不锁定当前状态；后续快照可以把早期 `BET` 降级为等待状态。

EV 和置信度门槛由现有 `Settings` 提供，解析器不复制或降低生产默认值。

## 下一批 P0

以下问题仅记录，本批不修复：

- 赛前调度当前缺少 T-60 检查点。
- 赛前调度当前使用 `confidence > 0.5`，没有使用生产配置门槛。
- `KellyCalculator` 当前默认单场上限为 5%，目标上限应为 3%。
- Recommendation Gate 当前收到 probability edge，而不是单位 Expected Value。
