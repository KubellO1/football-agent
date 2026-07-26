# 系统架构

Football Agent 采用 Clean Architecture、领域驱动设计（DDD）、Repository
Pattern、Service Layer 和依赖注入。依赖方向始终指向内部，领域层不依赖
FastAPI、SQLAlchemy、Redis 或 OpenAI SDK。

## 分层职责

| 层 | 目录 | 职责 | 允许依赖 |
|---|---|---|---|
| API | `app/api` | HTTP 路由、序列化、请求级依赖注入 | services、schemas |
| Services | `app/services` | 用例编排、事务边界、准入与风险流程 | 领域对象、Repository/Provider 接口 |
| Agents | `app/agents` | 使用 GPT 对确定性模型结果进行评审和解释 | services、领域对象、prompts |
| Domain | `app/models` | 实体、值对象和领域不变量 | Python 标准库 |
| Repositories | `app/repositories` | 仓储接口及 SQLAlchemy 实现 | 领域对象、database |
| Providers | `app/providers` | 外部数据源接口及其实现 | 领域对象、schemas |
| Workers | `app/workers` | 后台任务和定时任务入口 | services |
| Core | `app/core` | 依赖注入、异常、日志等横切能力 | config |
| Config | `app/config` | 环境变量与类型安全配置 | Pydantic Settings |
| Database | `app/database` | 异步数据库会话与基础设施 | SQLAlchemy |

## 核心约束

1. **依赖倒置**

   Service 只依赖 `repositories/interfaces` 和 `providers/interfaces`，
   不直接依赖具体实现。`app/core/container.py` 负责绑定接口与实现。

2. **领域纯净**

   领域对象不引用 Web 框架、ORM 或第三方 API 客户端。SQLAlchemy ORM
   模型及领域对象转换位于仓储基础设施层。

3. **数学模型是数值事实来源**

   Elo、Poisson、xG、攻防强度、Monte Carlo、Kelly Criterion 和 EV
   模型负责产生概率、价值与仓位相关数值。LLM 不得生成或覆盖这些数值。

4. **GPT 只负责受约束的评审与解释**

   Agent 通过 OpenAI Responses API 获取结构化评审结果。GPT 可以识别
   冲突、总结证据、执行 Red Team 分析并生成解释，但不得绕过准入 Gate、
   风险规则或数学模型。

5. **可追溯与可复现**

   推荐应记录输入快照、模型版本、Prompt 版本、评审结果及最终决策，
   以支持复盘、审计和回测。

## 主要数据流

```text
Providers
  -> Collection Services
  -> Repositories
  -> PostgreSQL
  -> Mathematical Models
  -> Admission Gate / Risk Controls
  -> GPT Structured Review
  -> Decision Log
  -> API
```

Redis 用于缓存、任务协调和短期运行状态，不作为推荐事实的永久来源。

## 故障处理原则

- 外部数据不足、过期或冲突时，停止推荐。
- 数学模型、市场价值与风险评估冲突时，风险控制优先。
- OpenAI 不可用、输出被拒绝或结构化校验失败时，不允许降级为盲目推荐。
- 所有基础设施异常必须转换为明确的应用异常，并保留可观测日志。
