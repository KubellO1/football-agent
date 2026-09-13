# Zero-Cost Research Benchmark v1

## 状态

- 研究计划：已关闭
- 固定结论：`ZERO_COST_BETTING_EDGE_NOT_DEMONSTRATED`
- Production candidate：否
- 生产模型：未修改

## 数据与方法

基准使用 Football-Data.co.uk 的 15 份本地缓存 CSV，覆盖五大联赛三个赛季：

- 训练：2023/2024
- 模型选择与校准：2024/2025
- 最终未触碰 OOS：2025/2026
- completed fixtures：5,256
- historical odds matches：5,256

大型原始数据和实验输出保存在 task-artifacts 目录，不复制进 Git。仓库中的
`research/benchmarks/zero_cost_v1/manifest.json` 固化每个输入、输出和 runner 的
SHA-256；任何缺失或漂移都会阻止复现。

去除 overround 后的 1X2 市场概率是强制基准：

```text
raw = (1 / home_odds, 1 / draw_odds, 1 / away_odds)
market_probability = raw / sum(raw)
```

closing odds 只能用于评估市场基准和 CLV，不能作为更早决策时点的模型特征。
最终 OOS 不得用于模型选择、阈值优化、联赛筛选或任何形式的结果挑选。

## 已接受结果

### TASK-055

- 最佳 baseline：`C_WINDOW_20`
- OOS Brier：0.6195309914406985
- OOS Log Loss：1.0323687040304372
- OOS ROI：-12.455950540958269%

### TASK-056

- Market Brier：0.5980245398363591
- Market Log Loss：1.0008082091671908
- 选中模型：`MARKET_RESIDUAL_BOOST_PLATT`
- Model Brier：0.6009937728109958
- Model Log Loss：1.0049860376377655
- Model − Market Brier：+0.002969232974636693（更差）
- Model − Market Log Loss：+0.004177828470574685（更差）
- 结论：`NO_PREDICTIVE_EDGE_EVIDENCE`

### TASK-057

- 最佳已测试信号：`CROSS_BOOK_REST`
- Market Brier：0.5980245398363591
- Best Model Brier：0.6019025049015412
- Incremental Edge：-0.0038779650651821207
- Mean CLV：-5.297306301053395%
- OOS ROI：-5.215719063545149%
- ROI 95% CI：[-17.316053511705684%, 6.87123745819398%]
- Edge evidence：no

## 验证与复现

只验证资产和方法契约，不运行模型：

```powershell
python -m scripts.run_zero_cost_benchmark `
  --artifact-root C:\Users\ruowa\Projects\football-agent-task-artifacts `
  --mode verify
```

在隔离输出目录完整复现，不访问网络：

```powershell
python -m scripts.run_zero_cost_benchmark `
  --artifact-root C:\Users\ruowa\Projects\football-agent-task-artifacts `
  --mode reproduce `
  --output-dir C:\path\to\disposable-output
```

复现入口调用 TASK-055/056/057 已有 runner，先验证缓存和源码哈希，再执行同一
赛季切分和评估。它不会重新设计方法，也不会选择新的最佳算法。

## 重启规则

只在出现至少一项实质性新输入时允许重启：

- 更长的 point-in-time 历史赔率；
- 合法的历史首发；
- 合法的伤病/停赛历史；
- 带时间戳的盘口变化；
- 显著更丰富的开放事件数据；
- 其他真正新增的信息源。

以下理由不足以重启：新算法、调整阈值、筛选联赛、优化历史 ROI。

重启后仍必须保持相同的去水市场基准、严格时序/OOS 切分、数据泄漏约束和
最终 holdout 隔离。任何新信号必须先扩展 manifest 并保留 point-in-time 来源证据。
