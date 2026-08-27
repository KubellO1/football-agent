# TASK-20260827-021 — Production Reconciliation Report

## 结论

已在用户批准范围内完成 production PostgreSQL Alembic ledger reconciliation。生产 schema 先精确对齐 canonical `0019`，随后成功执行 `stamp 0019` 和 `upgrade 0020`；最终与 canonical `0020` 的严格比较为：

- `missing = 0`
- `unexpected = 0`
- `divergent = 0`
- `exact = true`

业务数据行数和 sequence 前后完全一致，没有数据行丢失或重复。计划任务已恢复，临时维护锁为 0。现已 **STOP**，不会自动开始 TASK-20260826-020。

## Task 与 Git

- Task ID：`TASK-20260827-021`
- Branch：`codex/TASK-20260827-021`
- 使用且未改写的 commits：`ec8878991fd24d6181f9dbc92e803cef25db0114`、`8fbf62c0785fc08f5168eea2d7ed0d13d2ee30f8`
- 分支已推送；未创建/合并 PR，未部署应用。

## Backup

- 时间：`2026-08-27T20:26:31+02:00`
- Full dump：`backups/TASK-20260827-021/production-approved-20260827T202631+0200/production-20260827T202631+0200.dump`
- 大小：545,396 bytes
- SHA-256：`B58C5F0B1B6CBA78DDD16E3A579A571AAB4CFDB5C4F0B8A086F680183CA0FC6F`
- Schema SHA-256：`F19C8CFB4CD9CAF43D9F48DD720B3581E6B74A4C6B3354DE8D9C00B4E6280336`
- `pg_restore --list`：通过，125 TOC entries；包含 schema、data、sequences 与 `alembic_version`。

## 维护窗口

- 开始：`2026-08-27T20:25:44+02:00`
- 结束：约 `2026-08-27T20:50:00+02:00`
- 使用独立持锁进程建立 13 个可逆 scheduler locks。
- DDL 前运行中的 FootballAgent writer：0；其他 production DB sessions：0。
- Windows 任务前后均为 `Enabled=True / Ready`，任务定义未修改。
- 恢复后锁数量：0。
- 未运行 `daily_job`、`pre_kickoff`、backfill 或 model。

## Production preflight

新鲜 production backup clone 与两轮 rehearsal 的起始状态完全一致：

- `alembic_version = 0002`
- 22 个 FK 仅名称不同，22/22 语义一致
- 4 个 `decision_logs` JSON server defaults 缺失
- `uq_seasons_competition_label` 不存在
- schema diff：`missing=22 / unexpected=22 / divergent=4`
- 没有任何额外 material drift

## DDL 与 exact 0019 gate

单一 PostgreSQL transaction 使用 `lock_timeout=5s`、`statement_timeout=60s`，重命名 22 个已验证 FK，并为以下列设置 canonical `'[]'::json` default：

- `decision_logs.supporting_evidence`
- `decision_logs.risks`
- `decision_logs.rejected_alternatives`
- `decision_logs.change_conditions`

事务完整提交，没有部分执行。生产 post-reconciliation schema dump 与 canonical 0019 比较：

`missing=0 / unexpected=0 / divergent=0 / exact=true`

只有此门槛通过后才继续 Alembic ledger 操作。

## Stamp、upgrade 与 exact 0020

- `alembic stamp 0019`：成功
- `alembic upgrade 0020`：成功
- `alembic current`：`0020 (head)`
- `uq_seasons_competition_label UNIQUE (competition_id, label)`：存在且已验证
- post-0020 与 canonical 0020：`missing=0 / unexpected=0 / divergent=0 / exact=true`

22 个目标 FK 均为 canonical 名称/定义；另两个 settlement FK 保持 canonical migration 的原名称。四个 JSON defaults 全部匹配。

## 数据完整性

| Table | Before | After |
|---|---:|---:|
| competitions | 365 | 365 |
| fixtures | 4,949 | 4,949 |
| predictions | 139 | 139 |
| teams | 4,241 | 4,241 |

其余被核对的核心表均保持原行数（当前为 0）；`alembic_version` 始终为 1 行。

- `bankroll_entries_sequence_seq` 前后相同，`last_value = NULL`
- 24/24 FK validated
- invalid/unready indexes：0
- unvalidated constraints：0
- season natural-key duplicate groups：0
- application rows lost / duplicated：0 / 0

## Recovery 状态

没有触发 rollback/recovery，因为全部生产 gate 均通过。恢复权威仍是本次维护窗口前取得并验证可读的 custom-format full backup。clone 中 rollback/recovery 此前已通过两轮。

## 独立后续问题

额外执行的 `alembic check` 返回非零：当前 ORM metadata 尚未声明 migration 0020 新增的 `uq_seasons_competition_label`，因此 autogenerate 把该 canonical 约束误判为待删除。

这不是 production upgrade/replay failure；canonical 0020 strict diff 已全零通过。本任务不允许修改代码，因此未修复。应创建独立、最小的 ORM metadata alignment task，在下一次依赖 `alembic check` 或 autogenerate 前处理。

## Production impact 与 Frozen

- Production DB 写入：22 个 FK rename、4 个 JSON defaults、ledger stamp 0019、upgrade 0020。
- Application data INSERT/UPDATE/DELETE：0。
- API calls：0。
- Frozen 项全部未变：model weights、alpha、EV threshold、confidence threshold、Kelly formula/cap、Recommendation Gate、production whitelist、betting decision rules。

## Approval Gate

生产 ledger blocker 已清除，但 **TASK-20260826-020 仍未获授权**。

**STOP：等待用户明确批准是否解除 TASK-20260826-020；不得自动开始 Canary、backfill、daily_job、pre_kickoff、merge 或 deployment。**
