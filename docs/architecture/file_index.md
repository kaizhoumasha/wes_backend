# WES Backend 当前文件索引

> 本索引只记录当前工作区的稳定入口和目录职责，不复制完整文件树。历史变更由 Git 与项目外
> `../archive_docs/wes_backend/` 保存；实时文件以 `rg --files` 为准。

**最后更新**：2026-08-30

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
| `docker-compose.wms-acceptance.yml` | 预构建 WMS Transport Mock 镜像的 provider-local 验收编排；不构建镜像、不挂载宿主源码 |
| `Jenkinsfile.backend-ci` | 后端 QUALITY、验收、HEAVY、镜像构建与发布入口 |
| `Jenkinsfile.release-checker-ci` | 独立 release checker 测试、最小镜像构建与不可变制品发布入口；不调用前后端 producer 或部署作业 |
| `Jenkinsfile.test-deploy` | TEST 独立 release orchestrator；按 scope 选择候选、执行方向兼容与 FAST/FULL，不由 producer 自动触发 |
| `TODOS.md` | 已有真实触发条件但尚未排期的独立工作 |

## 2. 架构文档

| 路径 | 职责 |
| --- | --- |
| `docs/architecture/SRS.md` | 产品需求、范围和参与方职责基线 |
| `docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md` | WES 最小执行架构顶层 SPEC |
| `docs/superpowers/specs/2026-08-06-wes-outbound-operation-top-level-design.md` | 评审中的自动出库 PickingTask 和人工分拣 Bin 流转设计；包含 Task 驱动入站、PDA/WMS 分界、跨任务退料和物理清场 |
| `docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md` | 十四阶段架构收敛总控计划 |
| `docs/superpowers/plans/2026-08-20-phase8-dual-remote-governance.md` | GitHub/GitLab develop 汇合、Phase 8 状态真源与不可变 RC 证据治理 |
| `docs/integration/rough-sorter-joint-acceptance.md` | Phase 8 后端 RC、不可变镜像证据与供应商/现场边界的唯一当前状态真源 |
| `docs/superpowers/plans/2026-08-03-rough-sorter-plugin-convergence.md` | Phase 8 后端功能实现、本机 Mock 验收和 RC 关闭门禁的实施历史 |
| `docs/superpowers/plans/2026-08-19-rough-sorter-workline-epoch-activation.md` | WorkLine Epoch 激活与多 Endpoint 派发增量实施真源；后端工程包 1–4 已提交，前端按独立计划推进 |
| `docs/superpowers/plans/2026-08-03-wes-legacy-production-path-removal.md` | Phase 10 旧 Runtime/Intent/Outbox/Hold/Provider 生产路径原子退役与联调 cutover 退出证据；#187 已合入 `develop@97e6887a`，此前 `834fe59e` candidate 已部署联调，merge commit 未证明重新部署，供应商/设备物理/业务验收未完成 |
| `docs/superpowers/plans/2026-07-31-wes-test-semantics-and-weight-convergence.md` | 测试语义、所有权和重量治理计划 |
| `docs/superpowers/plans/2026-08-18-wes-onsite-data-recovery.md` | PostgreSQL 小时级备份、异机副本、真实恢复演练与恢复手册实施入口 |
| `docs/superpowers/plans/2026-08-18-wes-onsite-runtime-hardening.md` | Beat、Redis、Nginx 与 PostgreSQL 现场运行约束的独立加固计划 |
| `docs/superpowers/specs/2026-08-25-frontend-backend-release-decoupling-design.md` | 前后端独立 producer、方向性兼容、release checker、FAST/FULL 与独立 orchestrator 的当前设计真源 |
| `docs/superpowers/specs/2026-08-26-development-workflow-optimization-design.md` | 前后端 Agent、验证所有权、HEAVY 与发布运行静默的流程优化设计真源 |
| `docs/superpowers/specs/2026-08-26-phase9-14-guided-development-resequence-design.md` | 开发流程、运输诊断、Phase 9 最小基础、Phase 10/11 收敛与 Phase 12 用户主导插件开发的阶段重排设计 |
| `docs/superpowers/plans/2026-08-27-phase9-minimum-execution-foundation.md` | Phase 9 最小执行基础、七项 successor、测试所有权与 Phase 10 handoff 的实施计划；已合入 `develop@c5a93872` |
| `docs/superpowers/plans/2026-08-27-phase12-manual-bin-processing-guided-development.md` | Phase 12 用户亲自完成 `manual_bin_processing` 合同、代码、migration、Composition 与验收的教学计划 |
| `docs/superpowers/plans/2026-08-26-development-workflow-efficiency.md` | 前后端默认直接工作、证据复用、手术式规则修正与 HEAVY 治理实施计划 |
| `docs/superpowers/plans/2026-08-26-release-operational-readiness.md` | 后端 FULL 发布在线预检、Nginx/API/Beat admission closure 与维护态稳定静默门禁实施计划；Tasks 1–4 已在 Phase 10 分支完成并纳入候选镜像，Task 5 TEST Deploy 未执行 |
| `docs/superpowers/specs/2026-08-26-transport-integration-diagnostics-design.md` | Transport 最近任务、按需详情、共享 SSE 与四类现场调试的前后端设计真源 |
| `docs/superpowers/plans/2026-08-26-transport-integration-diagnostics.md` | 运输接入诊断的风险匹配实施、验证和现场交接计划 |
| `docs/integration/wes-wms-interface-requirements.md` | 面向 WMS/WES 初级开发人员的场景化对接入口；Phase 13 自动出库/上架待评审，Phase 12 人工分拣目前仅登记业务设计、尚无 wire |
| `docs/contracts/openapi/wes-wms-transport.openapi.json` | 面向 WMS 交付的 Transport OpenAPI 3.0.3 机器合同；覆盖容器中间位置事件和搬运最终结果的客户端生成，搬运提交服务端合同由 WMS 交付 |
| `docs/contracts/wms-northbound-interaction-contract.md` | Phase 3 WMS HTTP Client 使用合同；定义共享访问标准和后续业务 API 开发步骤，不定义具体 wire |
| `docs/contracts/wms-async-callback-envelope-contract.md` | WMS → WES 异步回调统一信封与持久化后接收 ACK；不定义 operation 专属 DTO 或其他方向交互 |
| `docs/contracts/transport-fulfillment-contract.md` | Phase 4 TransportTask、冻结提交请求、WMS 转发提交 ACK、持久化 callback receipt、成员位置事实与异步终态评审基线 |
| `docs/contracts/wms-outbound-picking-task-integration-requirements.md` | WMS/WES 自动出库严格交互评审基线；正常 Bin 通过 Epoch 级 `return_batch` FIFO 回库，可识别但非预期 Bin 冻结并等待独立恢复 wire；停线排空货架面决定 wire 未获批 |
| `docs/contracts/wms-rough-sorter-inbound-integration-requirements.md` | Phase 8 粗分逐盘入库的获批业务合同；目标 Cell 晚绑定、五态生命周期和两个既有 `RACK_MOVE` 的唯一真源 |
| `docs/contracts/wms-inbound-putaway-integration-requirements.md` | Phase 13 自动上架的满箱交换、执行任务、机械臂执行、业务完成、执行级 Bin 回流、独立清场、严格 DTO 与联调评审基线；停线排空货架面决定 wire 未获批，当前为 `ReviewRequired` |
| `docs/integration/third_party_integration_whitepaper.md` | 所有第三方固定式设备供应商长期遵循的顶层统一接口（wire）真源 |
| `docs/hardware/wms_rcs_interface_requirements.md` | WMS 交互约定初稿；只读差异清洗输入，不是当前实现真源 |
| `docs/architecture/device-command-contract.md` | DeviceCommand、设备统一接口与 WorkLine 插件边界 |
| `docs/plugin_development_guide.md` | 执行插件 SPI、WMS 结果到封闭 Decision 的映射与独立包交付指南 |
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
| `src/app/execution/` | Phase 9 最小执行对象、RACK/BIN 当前位置投影、可靠 WMS 确认与静态插件事实处理 |
| `src/app/runtime/` | 保留当前 Session/Timeline/位置事件、诊断与最小能力合同；Phase 10 旧 Runtime/Intent/Effect/Hold/Provider 应用消费者与 Phase 11 终裁删除的 legacy model identity 均已移出活动源码 |
| `src/app/transport/` | AGV/CTU 通用搬运合同、可靠聚合与 Phase 6 生产运行时；带冻结 execution authority 的终态 Evidence 通过注入 port 更新核心位置投影；核心本身不拥有业务 producer，当前由粗分插件 `OLD_OUT/NEW_IN` 通过 port 消费 |
| `src/app/device/` | Phase 7 DeviceCommand/ECS 可靠聚合、统一 wire Adapter、callback、evidence 与唯一 composition root；不包含供应商私有协议或业务 Decision |
| `src/app/workline/models/line_run_epoch.py` | 工作线连续可信运行代际及设备合同绑定；不拥有业务任务生命周期 |
| `src/app/wms_adapter/` | WMS HTTP/JSON 薄访问层和业务系统 ACL；具体业务 API 由对应业务 owner 按获批合同实现 |
| `src/app/wms_integration/` | 保留 typed fulfillment/inventory ports 与 schema-deferred models；Phase 10 Provider/Profile/Manifest/query/effect/status 运行时已退役，不是业务插件模板 |
| `workline_plugins/` | 具体工作线插件独立包，不属于核心运行时 |

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
| 供应商一致性验收 | 在供应商 ECS/网关交付边界独立验证白皮书和设备合同附录，不进入核心 pytest |

核心默认测试不得导入具体插件或包含供应商私有协议。人类阅读文档不作为 pytest 或质量门禁正文输入。

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
| `scripts/export_release_provider.py` | 从后端唯一真源确定性导出 provider OpenAPI、provided permissions 与生产输入指纹 |
| `scripts/verify_wms_northbound_feasibility.py` | 通过公开 HTTP 面验证 provider-local WMS Transport 搬运提交合同；不替代真实 WMS 或现场验收 |
| `docs/runbooks/transport-operations.md` | Transport 本地 API、结构化日志与 PostgreSQL 事实的诊断入口；联调环境支持按 `transport_task_id` 定向清理完整本地链路 |
| `docs/runbooks/device-command-operations.md` | DeviceCommand、设备 evidence、状态观察与 Epoch fencing 的只读诊断入口 |
| `docs/devops/rocky-linux-server-inspection.md` | 现场服务器现状只读采集表；不执行安装、配置修改或服务重启 |
| `docs/devops/rocky-linux-server-initialization.md` | 检查通过后的 Docker、TimescaleDB/PostgreSQL 与 Redis 基础支撑环境初始化手册；不代表业务系统已部署或验收 |
| `docs/devops/prod-release-deploy.md` | 生产独立 release orchestrator 的 scope、FAST/FULL、兼容报告、维护态和恢复 Runbook |
| `scripts/wait_for_http.py` | 生产发布入口恢复后的 HTTP health/frontend 等待门禁；由 Runbook 直接调用 |
| `scripts/check_bootstrap_admin_login.py` | 生产发布固定版本的超级管理员真实登录门禁；由 Runbook 直接调用 |
| `scripts/check_business_legacy_absence_gate.py` | 旧业务平台缺席门禁 |
| `docs/architecture/phase10-legacy-cutover-manifest.json` | Phase 10 producer seal、legacy task/broker identity 与 Task 7 维护态 cutover 机器清单；不代表现场已执行 |
| `scripts/workline_inbox_retirement_guardrail.py` | 退役 WorkLineInbox 缺席门禁 |
| `scripts/install-git-hooks.sh` | 安装仓库管理的提交门禁 |
| `tools/release_checker/` | 独立、stdlib-only 的前端 consumer → 后端 provider 方向兼容检查器；固定 oasdiff，运行时不导入 WES 应用或前端源码 |

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
