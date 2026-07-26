# 足球投资决策 Agent

面向专业足球投资者的可审计足球投注情报平台。系统通过确定性数学模型计算概率、
Expected Value（EV）和下注仓位，再由 GPT 对模型结果进行定性评审、风险解释和
Red Team 反方分析。

> 核心原则：数学模型是所有数值的唯一真相来源。GPT 不生成、不重算、也不修改
> 概率、赔率、EV、Kelly 仓位、Elo、xG 或准入 Gate 结论。

本项目不承诺盈利，也不以提高短期命中率为目标。数据不足、证据不足、风险过高或
不存在正 EV 时，系统应拒绝推荐。

## 技术栈

- Python 3.12
- FastAPI、Uvicorn
- PostgreSQL
- Async SQLAlchemy 2.0、asyncpg
- Alembic
- Redis
- OpenAI Responses API、GPT-5.6
- Docker、Docker Compose
- Pytest、Ruff、Black、MyPy

## 架构原则

项目采用 Clean Architecture、DDD、Repository Pattern、Service Layer 和依赖注入。
依赖方向始终指向领域核心。

```text
API
  ↓
Services / Agents
  ↓
Domain entities / value objects
  ↑
Repository and provider interfaces
  ↑
PostgreSQL / Redis / external providers / OpenAI
```

主要边界：

- `models/`：纯 Python 领域实体和值对象，不依赖 FastAPI 或 SQLAlchemy。
- `services/models/`：Poisson、Elo、Monte Carlo、Kelly、价值检测等数学模型。
- `services/`：用例编排、准入 Gate、每日 Top-N 和风险控制。
- `agents/`：GPT 结构化评审，只输出解释、风险和反方意见。
- `repositories/`：仓储接口和 SQLAlchemy 实现。
- `providers/`：比赛、赔率、天气和伤停等外部数据接口。
- `workers/`：同步、每日分析和计划任务。

完整架构说明见 `docs/architecture.md`，系统决策宪法见
`docs/agent-constitution.md`。

## 当前能力

- 比赛和赔率数据同步
- 异步 PostgreSQL 持久化
- Redis 连接与健康检查
- Poisson 比分与市场概率
- Elo 评分
- Monte Carlo 模拟
- xG/攻防强度到 Poisson λ 的估计
- Value Detection 和 Positive EV 判断
- Fractional Kelly 与单注上限
- 确定性推荐准入 Gate
- 每日 Top-N 成本控制
- GPT 决策委员会与 Red Team 评审
- DecisionLog 决策追踪
- ValueBet 持久化
- 结算与表现追踪数据结构
- 每日 worker 和健康状态 Dashboard

## 环境配置

先复制环境变量模板：

```cmd
cd C:\Users\ruowa\Projects\football-agent
copy .env.example .env
```

至少配置：

```env
OPENAI_API_KEY=你的_OpenAI_API_Key
OPENAI_MODEL=gpt-5.6-sol
OPENAI_REASONING_EFFORT=high

POSTGRES_USER=football
POSTGRES_PASSWORD=请替换为安全密码
POSTGRES_DB=football

REDIS_HOST=localhost
REDIS_PORT=6379
```

按实际使用的数据源配置：

```env
API_FOOTBALL_KEY=
ODDS_API_KEY=
ODDS_API_IO_API_KEY=
WEATHERAPI_KEY=
```

不要提交 `.env`。仓库只提交 `.env.example`。

## Windows CMD：Docker 启动

以下命令均在 Windows CMD 中执行。

```cmd
cd C:\Users\ruowa\Projects\football-agent
copy .env.example .env
notepad .env
```

先启动基础设施：

```cmd
docker compose up -d postgres redis
```

构建镜像并运行迁移：

```cmd
docker compose build api worker
docker compose run --rm api alembic upgrade head
```

启动 API 和 worker：

```cmd
docker compose up -d api worker
```

查看状态：

```cmd
docker compose ps
docker compose logs -f api
```

停止：

```cmd
docker compose down
```

删除数据库和 Redis 持久卷会清空本地数据，除非明确需要重建环境，否则不要执行
`docker compose down -v`。

## 测试 API 健康状态

存活检查：

```cmd
curl http://localhost:8000/api/v1/health
```

数据库和 Redis 就绪检查：

```cmd
curl http://localhost:8000/api/v1/ready
```

预期响应：

```json
{
  "status": "ready",
  "database": "ok",
  "redis": "ok"
}
```

交互式 API 文档：

```text
http://localhost:8000/docs
```

## 主要 API

所有路径默认以 `/api/v1` 开头。

```text
GET  /health
GET  /ready
POST /sync/today
POST /sync/odds/today
GET  /fixtures/today
POST /fixtures/{fixture_id}/analyze
POST /fixtures/{fixture_id}/review
POST /recommendations/today/run
GET  /recommendations/today
```

`analyze` 是确定性数学分析；`review` 才会调用 LLM。读取已有推荐不会触发新的
OpenAI 请求。

## Alembic 迁移

升级到最新迁移：

```cmd
docker compose run --rm api alembic upgrade head
```

查看当前版本和迁移历史：

```cmd
docker compose run --rm api alembic current
docker compose run --rm api alembic history
```

生成新迁移：

```cmd
docker compose run --rm api alembic revision --autogenerate -m "change description"
```

生成后必须人工检查迁移内容，不要直接信任 autogenerate。

## 质量检查

在容器中运行：

```cmd
docker compose run --rm api ruff check .
docker compose run --rm api black --check .
docker compose run --rm api mypy app
docker compose run --rm api pytest -m unit
```

运行全部测试：

```cmd
docker compose run --rm api pytest
```

集成测试会创建和删除数据库表，必须使用独立测试库，禁止指向生产数据库。

首次创建测试库：

```cmd
docker compose exec postgres createdb -U football football_test
```

执行集成测试：

```cmd
docker compose run --rm -e TEST_DATABASE_URL=postgresql+asyncpg://football:changeme@postgres:5432/football_test api pytest -m integration
```

如果修改了 `.env` 中的 `POSTGRES_USER` 或 `POSTGRES_PASSWORD`，需要同步修改上述测试 DSN。

## Windows CMD：不使用 Docker

需要本机已安装 Python 3.12、PostgreSQL 和 Redis。

```cmd
cd C:\Users\ruowa\Projects\football-agent
py -3.12 -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
copy .env.example .env
notepad .env
alembic upgrade head
uvicorn app.main:app --reload
```

## 项目目录

```text
app/
  agents/          GPT 推理和委员会评审
  api/             FastAPI 路由和请求依赖
  config/          环境配置与白名单
  core/            DI、异常和日志
  database/        PostgreSQL 与 Redis 连接
  models/          领域实体和值对象
  prompts/         版本化中文提示词
  providers/       外部数据源接口与实现
  repositories/    仓储接口与 SQLAlchemy 实现
  schemas/         Pydantic DTO
  services/        业务编排和数学模型
  workers/         后台与计划任务
alembic/           数据库迁移
docs/              架构、宪法和运行文档
scripts/           运维、健康检查和数据处理脚本
tests/             单元测试与集成测试
```

## 风险边界

- 不使用 LLM 生成模型概率。
- 不因热门球队或用户偏好修改结论。
- 不推荐负 EV 投注。
- 高风险可以一票否决。
- 不建议重仓。
- 数据缺失、过期或冲突时停止推荐。
- 所有推荐必须能够追溯到模型版本、prompt 版本和 DecisionLog。
- 历史表现不能保证未来收益。
