# WES Backend 当前文件索引

> 本索引只记录当前工作区的稳定入口和目录职责，不复制完整文件树。历史变更由 Git 与项目外
> `../archive_docs/wes_backend/` 保存；实时文件以 `rg --files` 为准。

**最后更新**：2026-08-06

## 1. 真源与入口

| 路径 | 职责 |
| --- | --- |
| `AGENTS.md` | 项目规则主真源 |
| `CLAUDE.md` | Claude/GStack/Skill 行为入口 |
| `GEMINI.md` | AGY/Antigravity/Gemini 入口 |
| `README.md` | 项目概览与本地启动入口 |
| `main.py` | FastAPI 应用入口 |
| `pyproject.toml` / `uv.lock` | Python、工具和测试配置真源 |
| `Dockerfile` | development/testing/production 镜像构建 |
| `docker-compose.yml` / `docker-compose.deploy.yml` | 本地与生产部署编排 |
| `docker-compose.test-deploy.yml` | TEST 环境部署编排 |
| `docker-compose.ci-heavy.yml` | MR HEAVY 的隔离 PostgreSQL/Redis 编排 |
| `Jenkinsfile.backend-ci` | 后端 QUALITY、验收、HEAVY、镜像构建与发布入口 |
| `Jenkinsfile.test-deploy` | TEST 环境部署入口 |
| `TODOS.md` | 已有真实触发条件但尚未排期的独立工作 |

## 2. 架构文档

| 路径 | 职责 |
| --- | --- |
| `docs/architecture/SRS.md` | 产品需求、范围和参与方职责基线 |
| `docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md` | WES 最小执行架构顶层 SPEC |
| `docs/superpowers/specs/2026-08-06-wes-outbound-operation-top-level-design.md` | 自动出库 `PickingTask`、双面货架、NG/补料/恢复与完成业务真源 |
| `docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md` | 十一阶段架构收敛总控计划 |
| `docs/superpowers/plans/2026-08-05-wes-wms-thin-access-convergence.md` | Phase 3 无状态 WMS 业务 ACL 暗构建计划；Task 1 四项跨能力外部门禁未关闭，关闭后按 operation 单独批准与实施 |
| `docs/superpowers/plans/2026-07-31-wes-test-semantics-and-weight-convergence.md` | 测试语义、所有权和重量治理计划 |
| `docs/contracts/wms-northbound-interaction-contract.md` | Phase 3 WMS 业务 ACL 合同；目标态消费者矩阵、单项批准模板、范围排除项与阻断清单真源 |
| `docs/hardware/wms_rcs_interface_requirements.md` | WMS 交互约定初稿；只读差异清洗输入，不是当前实现真源 |
| `docs/architecture/device-command-contract.md` | DeviceCommand 与 Adapter 边界 |
| `docs/plugin_development_guide.md` | 插件 SPI、封闭 Decision 与独立包交付指南 |
| `docs/superpowers/README.md` | 当前文档生命周期与项目外归档索引 |

ADR 位于 `docs/architecture/adr/`。业务输入、外部合同、运维和联调资料分别位于
`docs/business/`、`docs/contracts/`、`docs/operations/`、`docs/runbooks/` 与 `docs/integration/`。
`docs/hardware/` 保存厂商原始资料及其可检索转写，不是 WES 核心架构真源。

## 3. 生产代码

生产代码遵循固定调用方向：

```text
API → Service → Repository → Database
```

| 路径 | 职责 |
| --- | --- |
| `src/register.py` | 路由、中间件和异常处理组装 |
| `src/core/` | 配置、认证、响应、基础 Service/API、运行时开关 |
| `src/core/outbound_http/` | 框架无关的出站 HTTP 合同、单次发送与共享 Client 生命周期；不包含业务或厂商语义 |
| `src/database/` | Session、Repository、模型工厂、关系、缓存和 schema 基础设施 |
| `src/middleware/` | 请求日志、限流和性能中间件 |
| `src/celery_app/` | Celery 应用、队列路由、任务和进程运行时 |
| `src/app/*/v1/` | API facade；不得直接访问 Repository 或数据库 |
| `src/app/*/services/` | 业务协调和事务边界 |
| `src/app/*/repositories/` | 数据访问 |
| `src/app/*/models/` | SQLModel/Pydantic 模型与 DTO |
| `src/app/runtime/` | 当前 Runtime implementation baseline 与目标最小能力的实施区域 |
| `src/app/wms_integration/` | WMS typed port、Gateway、transport 与证据边界 |
| `workline_plugins/` | 具体工作线插件独立包，不属于核心运行时 |
| `device_adapters/` | 具体厂商 Adapter 独立包，不属于核心运行时 |

新 Service 必须从所在 `services/__init__.py` 导出。时间处理、Mixin 继承和零代码 CRUD 约束以
`AGENTS.md` 为准。

## 4. 测试所有权

完整目录规则以 `tests/README.md` 为准。

| 路径 | 所有权与执行方式 |
| --- | --- |
| `tests/api/` | route、permission、response model 与 API facade |
| `tests/workline/` | 通用 WorkLine 身份、拓扑和配置校验 |
| `tests/runtime/` | 与具体插件无关的最小执行能力与可靠性 |
| `tests/contracts/` | 跨模块、跨系统合同 |
| `tests/core/` / `tests/database/` / `tests/sys/` / `tests/deployment/` | 对应基础设施边界；默认 FAST 必须无真实外部服务依赖 |
| `tests/architecture/` / `tests/scripts/` | QUALITY 显式运行，不进入默认 FAST |
| `tests/integration/` / `tests/e2e/` / `tests/resilience/` / `tests/load/` / `tests/mock/` | 显式 HEAVY 或人工入口，默认不收集 |
| `workline_plugins/<plugin_key>/tests/` | 具体插件独立测试树 |
| `device_adapters/<adapter_key>/tests/` | 具体厂商 Adapter 独立测试树 |

核心默认测试不得导入具体插件或 Adapter 包。人类阅读文档不作为 pytest 或质量门禁正文输入。

## 5. 运维与质量脚本

| 路径 | 职责 |
| --- | --- |
| `scripts/init-env.sh` | 从环境 profile 生成 worktree-local `.env` |
| `scripts/migrate.sh` | Alembic 迁移包装入口 |
| `scripts/git-quality-gate.sh` | 本地与 CI 的 canonical QUALITY profile |
| `scripts/architecture-guardrails.sh` | 分层与架构边界静态门禁 |
| `scripts/select_heavy_tests.py` | 根据 Git 差异和机器可读映射选择 HEAVY |
| `scripts/run_selected_heavy_tests.py` | 执行选中 HEAVY 并拒绝零执行/跳过 |
| `docs/architecture/heavy-test-impact.toml` | HEAVY selector 机器可读映射真源 |
| `scripts/check_business_legacy_absence_gate.py` | 旧业务平台缺席门禁 |
| `scripts/workline_inbox_retirement_guardrail.py` | 退役 WorkLineInbox 缺席门禁 |
| `scripts/install-git-hooks.sh` | 安装仓库管理的提交门禁 |

## 6. 快速查找

```bash
# 当前文件树
rg --files

# 查模块生产代码和测试
rg --files src/app/<module> tests | sort

# 查符号及调用文本
rg -n "<symbol>" src tests

# 查 API 层越界
rg -n "from sqlalchemy import select|db\.execute\(" src/app/*/v1

# 默认 FAST 实时收集
uv run pytest --collect-only -q -o addopts='' | tail -5

# 当前差异对应的 HEAVY
uv run scripts/select_heavy_tests.py --scope unstaged
```

修改函数、类或方法前必须先运行 GitNexus upstream impact；提交前运行 GitNexus detect changes。

## 7. 常用命令

```bash
./scripts/init-env.sh dev
uv sync --dev
uv run pytest tests/
./scripts/git-quality-gate.sh --profile quality
uv run ruff format .
uv run ruff check .
uv run bandit -r src/
```

纯文档变更只执行与文档相称的格式、引用、路径和 `git diff --check` 验证，不新增测试代码。
