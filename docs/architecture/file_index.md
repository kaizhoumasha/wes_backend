# P9 WES Backend 项目文件索引

> 本文件顶部版本记录保留历史变更说明；正文目录只描述当前代码和文档职责。

**最后更新**: 2026年7月26日（WorkLine 插件跨环境 migration matrix、digest-bound 批准与 preflight 输入）
**同步状态**: ✅ RuntimeInbox 与 WorkLine 插件 inventory 当前架构已同步；历史版本日志保留当时路径，不代表当前入口

---

## 版本更新日志

| 日期 | 版本 | 变更内容 |
|------|------|----------|
| 2026-07-26 | workline-plugin-inventory-t1 | 单环境 inventory 增加 generated Plugin/System Capability digest，并从静态 capability index 派生逐 WorkLine Provider admission 与 Port requirement；新增跨环境 migration matrix、digest-bound 批准证据、稳定 matrix digest、fail-closed preflight 复用服务和 CLI。 |
| 2026-07-13 | runtime-inbox-acceptance-closure T1–T10 | 固化 audit-only 数据库合同、正式 Service/replay 边界、reset 与 current-doc scanner；补齐隔离 PostgreSQL migration/processing/crash/benchmark runner、commit-bound evidence 和 `Jenkinsfile.backend-ci` artifact 归档。T10 默认全量、静态/质量门禁与隔离 PostgreSQL 正式验收均已完成，artifact 在最终 HEAD 上生成且不提交。 |
| 2026-07-11 | runtime-inbox-single-source-of-truth | callback/device/internal/timer producer 统一写入 `wes_runtime.runtime_inbox`；唯一 repository、五态 claim/fencing 与三阶段 processor 落地；旧 WorklineInbox model/repository/table、InboxBatchProcessor、RuntimeInboxConsumer facade 与 enqueue shim 物理删除；补齐 Revision A/B 回环、两个 crash window、1000 backlog/4 worker benchmark、SLI snapshot 与稳定观测 signal。 |
| 2026-07-01 | 0.10.4.0 / phase3-execution-safety-recovery | Phase 3 执行安全与恢复 PR（`feature/phase3-execution-safety-recovery`）：callback 外部入口升级为 body-bound HMAC（`X-Nonce` / `X-Body-SHA256` / 30s 窗口），nonce 使用 Redis `SET NX EX` 固定 TTL 原子消费并在 Redis 不可校验时 fail closed；RuntimeInbox 增加 provider_code + event_type + source_event_id 幂等接收、唯一冲突后重读比对 payload_hash、不同 hash 409 审计、DEAD_LETTER 人工重放新建记录；新增 ActiveObject 归属仲裁与 Reconciliation owner-scoped 决议；新增 DeviceCommand lease 过期策略与 RuntimeInbox backpressure 策略；WMS 增加 11 态 fulfillment 状态机、终态保护、typed evidence envelope 与 lifecycle service；WorkLine 配置域新增 plane scene/snapshot 读模型与 manifest 激活前 queue/device/capability 引用校验；新增 `docs/contracts/observability-contract.md` 与 `docs/contracts/runtime-toggle-governance.md`，并重新生成 legacy cleanup matrix（679 条）。版本 0.10.3.0 → 0.10.4.0 patch |
| 2026-06-30 | 0.10.3.0 / phase2-f1-f2-burndown-docs | Phase 2 burn-down F-1/F-2 收尾 PR（`feature/phase2-burndown-f1-f2`）：workline 域 14 个运行态 model（`bin_cell_reservation` / `diagnostic` / `dispatch_attempt` / `inbox` / `material_unit` / `object_transition_event` / `operation` / `rack_position` / `runtime` / `runtime_hold` / `runtime_hold_api` / `session` / `smt_inbound_handoff` / `timeline`）+ 10 个运行态 repository 物理迁入 `src/app/runtime/orchestration/{models,repositories}/`（`git mv` 整体迁移,`__tablename__` 不变,数据库 schema 不变）；81 文件 262 条跨域 import 批量改写 `from src.app.workline.{models,repositories}.<待迁>` → `from src.app.runtime.orchestration.{models,repositories}.<待迁>`；`workline/models/__init__.py` + `workline/repositories/__init__.py` 收缩为纯配置域聚合（workline + safety + rack 透传）；`migrations/env.py` mapper 注册链拆分（5 个已迁 symbol 改指 runtime.orchestration.models,WorkLine 保留 workline.models）；2 个 xfail 契约测试转硬断言（`test_workline_models_shrunk_to_workline_only_after_stage6` + `test_workline_repositories_shrunk_to_workline_only_after_stage6`）；`scripts/architecture-guardrails.allowlist` R-I3b path 字段同步 + `scripts/generate_legacy_matrix.py` 扩展 MIGRATED_REPOSITORIES 映射保持 audit trace 稳定；`docs/architecture/legacy-cleanup-matrix.{md,csv}` 重新生成（668 条）。**Phase 2 唯一未完成门禁 `WorkLine 不再拥有运行状态` 全部关闭**。版本 0.10.2.1 → 0.10.3.0 patch (清理性变更,无功能新增/破坏性 API) |
| 2026-06-30 | 0.10.2.1 / phase2-stage5+6-burndown-docs | Phase 2 burn-down 阶段 5+6 合并 PR（`feature/phase2-burndown-stage5-6`）诚实披露版：版本经 rework 由 0.10.3.0 回退至 0.10.2.1 patch。**阶段 5** 物理删除 `RuntimeReconciliationFacade`（launch PR `d5b88562` 过渡桥接，0 调用方）— device/callback 域全部直连 `src.app.runtime.orchestration.services.*` impl。**阶段 6**（commit `d138f369` + `5646d701`）：workline 域 service shim 物理删除 22 个（`inbox_*` / `operation_*` / `runtime_hold_*` / `runtime_query_*` / `timeline_sequence_*` / `trace_*` / `smt_inbound_handoff_*` / `outbox_dispatch_*` / `dispatch_attempt_*` / `object_transition_event_*` / `runtime_reconciliation_*` / `single_layer_rack_orchestration_*` / `bin_cell_reservation_*` / `ng_return_item_*` / `start_admission_*` / `station_lease_*` / `workline_bin_cell_reservation_*` / `write_back_*` / `outbox_dispatch_support` / `inbox_claim_bucket` / `diagnostic_support` / `runtime_services`） + 4 v1 router 物理删除（`runtime` / `runtime_hold` / `trace` / `inbound_handoff`）+ 5 dead test 物理删除（test_runtime_hold_api / test_smt_inbound_handoff_api / test_workline_runtime_api / test_resource_projection_service / test_object_transition_event）；27 caller 文件改写为直连 `src.app.runtime.orchestration.*` 与 `src.app.runtime.capabilities.phase4.*`；`device_command_gateway.py`（30.4K）从 workline 域迁出至 `src/app/runtime/orchestration/services/device_command_gateway.py`；`workline_service.py` 拆分保留配置 CRUD（start_admission / runtime_query / runtime_hold 调用迁出至 phase4 capabilities）。`__all__` / `_LAZY_SHIM_MAP` 收敛到 9 个真实 module export + 3 个死引用 tombstone。**Plan deviation（阶段 6 未完成门禁）**：workline models 16 文件（`runtime.py` / `inbox.py` / `session.py` / `timeline.py` / `runtime_hold*.py` / `dispatch_attempt.py` / `object_transition_event.py` / `operation.py` / `safety.py` / `rack_position.py` / `diagnostic.py` / `smt_inbound_handoff.py` / `bin_cell_reservation.py` / `material_unit.py`）+ repositories 11 文件未在本 PR 物理删除（53+ workline model 引用 + 7 workline_repository 引用仍在 runtime 域 import 链，物理删除会破坏 import 链）。契约测试 `test_workline_repositories_shrunk_to_workline_only_after_stage6` + `test_workline_models_shrunk_to_workline_only_after_stage6` 标 `pytest.xfail`，由后续 PR 收尾。**isawaitable 防御**修复 `outbox_dispatch_service._escalate_status_precheck_wait_if_needed` + `_dispatch_blocked_resource_heads` 在 sync repo fallback 路径抛 TypeError 的 bug（回归测试 `test_outbox_dispatch_async_guard.py` 4 个全过）。**Phase 2 唯一未完成门禁 `WorkLine 不再拥有运行状态` 实际仅部分关闭**：service / v1 router 域整体清空完成，model / repository 子门禁 follow-up（FOLLOWS-UP F-1 + F-2）转交后续 PR。版本 0.10.2.0 → 0.10.2.1 patch (清理性变更,无功能新增/破坏性 API) |
| 2026-06-30 | 0.10.1.0 / phase2-stage3-burndown-docs | Phase 2 burn-down 阶段 3：物理删除 `src/workline_runtime/` (50 文件) + `tests/workline_runtime/` (117 文件) + `tests/integration/workline_runtime/` (6 文件) 共 178 个 wlr 源文件；`diagnostics` 子目录从 `consumers/` 迁出至 `src/app/runtime/orchestration/diagnostics/` (5 子模块 + 聚合层)；`consumers/diagnostics_bridge.py` 改名为 `consumers/` 同级 `diagnostics.py` 并更新全部调用方 import；`consumers/` 退出 R-WLR trust zone (`EXCLUDED_PREFIXES = ()` 已为终态);9 tests + 2 scripts wlr import 重定向到当时的 compat mirror / characterization 覆盖；`src/app/workline/models/runtime_hold.py` 内联 `_LocalNgReasonSource` 避免引入 domain 触发反向循环;版本 0.10.0.0 → 0.10.1.0 patch (清理性变更,无功能新增/破坏性 API) |
| 2026-06-29 | 0.10.0.0 / phase2-stage2-burndown-docs | Phase 2 burn-down 阶段 2 (C1–C6) 文档同步：`src/app/runtime/orchestration/` 新增 6 个 wlr 平级镜像（`enums.py` / `device_ordering.py` / `effect_result.py` / `material_target_resolver.py` / `runtime_intent.py` / `runtime_intent_effects.py` / `timeline_generator.py`）+ 7 个 wlr bridge 门面（`business_identity_bridge.py` / `events_bridge.py` / `lock_bridge.py` / `orchestrator_bridge.py` / `resource_wait_evidence_bridge.py` / `sandbox_catalog_bridge.py` / `topology_bridge.py`）+ `consumers/` 子包（`__init__.py` / `runtime_inbox_consumer.py` / `diagnostics_bridge.py`）；`src/app/workline/` 新增 `utils.py` / `trace_context.py` / `diagnostic_support.py` / `outbox_dispatch_support.py` / `runtime_services.py` 镜像 + `domain/` 子包（`ng_reason` / `material_identity` / `plugin_manifest` / `contracts/` / `models/` / `services/`）+ `plugins/` 子包（`plugin_base` / `plugin_context` / `session_resolver` / `null_plugin` / `plugin_next` / `run_mode` + `plugin_sdk/`）；28 处 R-WLR production import 跨域重定向完成 + `scripts/architecture-guardrails.allowlist` R-WLR 条目全清（仅保留 R-I3a/b/C1/C2 等规则）；新增 6 个 mirror 一致性测试（`tests/architecture/test_{orchestration_bridges_mirror, plugin_mirrors_mirror, workline_compat_mirror, workline_domain_mirror, workline_plugins_mirror}.py`）+ `tests/runtime/orchestration/test_runtime_inbox_consumer.py` |
| 2026-06-23 | docs-workline-plugin-restructuring-v4 | WORKLINE + PLUGIN 重构顶层设计改用 GB/T 8567 概要/详细设计 13 章结构：1.引言 2.系统概述 3.体系结构 4.数据设计 5.接口设计 6.状态机 7.安全设计 8.非功能性 9.模块设计 10.实施计划 11.执行规范 12.风险 13.附录；1,800+ 行含数据模型 / 状态机 / 模块 API / Phase roadmap |
| 2026-06-27 | phase1-runtime-orchestration-spec | Phase 1 SPEC（feature/workline-phase-1-spec）：补全 9 个 runtime/orchestration 实体（ExecutionSession / ExecutionCorrelation / ExecutionWorkItem / RuntimeInbox / RuntimeTimeline / RuntimeHold / RuntimeIntentLog / IdempotencyKey / ConveyorQueueMembership）；新增 BC-02 RuntimeSnapshot 合同与 RuntimeSnapshotAssembler 服务；引入 H4 反注入边界（callback/event/result 三个入口顶层字段白名单）与 H5 幂等键命名规范 `WES-{OPERATION_KIND}-{HASH}`；FK ring dissolve（device ↔ workline_sessions 循环依赖解除）；架构守卫 C1–C5 与 R-I3a/b phase-aware 模式；API 强化：`/runtime-holds/{id}/resolve` 接受 `Idempotency-Key` Header，列表端点补 `Query()` 校验与 description；conveyor queue 增 `CheckConstraint`；Alembic 动态发现 pg_constraint 名称 |
| 2026-06-28 | phase2-launch-pr-docs | Phase 2 launch PR：新增 [`runtime-ownership-map.md`](./runtime-ownership-map.md) 与 [`adr/0001-phase2-runtime-ownership.md`](./adr/0001-phase2-runtime-ownership.md)；runtime/orchestration/ 索引补全（IdempotencyKey Repository + IdempotencyGuard + RuntimeSnapshotAssembler + 新增 RuntimeReconciliationFacade）；wlr allowlist 严格型与 R-I3c 5 域扩展均落地；wlr 索引保留至 Phase 2 T3 整目录删除 |
| 2026-06-28 | phase2-launch-pr-spec | Phase 2 launch PR 同步：新增 [`legacy-runtime-migration-spec.md`](./legacy-runtime-migration-spec.md) burn-down 6 阶段执行契约 + 主计划 §10.3 启动条件勾选 + 完成门禁追踪；新增 [`tests/contracts/workline/`](../../tests/contracts/workline/) 8 个 Phase 2 behavior contract gap 索引（test_runtime_inbox_lifecycle / test_runtime_intent_log_dispatch / test_runtime_session_advance / test_runtime_timeline_query / test_runtime_hold / test_device_command_dispatch / test_wms_fulfillment_request / test_manual_replay_audit，共 +76 tests，107 passed, 2 xfailed） |
| 2026-06-23 | docs-workline-plugin-restructuring-v3 | WORKLINE + PLUGIN 重构顶层设计采用 4 章结构（总体设计目标 / 约束条件 / 执行规范 / 实施阶段），645 行自包含；不预先拆 SPEC，Phase 启动时按需展开；autoplan 28 decision 存档 reviews/ |
| 2026-06-23 | docs-workline-plugin-restructuring | 新增 WORKLINE + PLUGIN 体系全面重构顶层设计（`docs/architecture/workline-and-plugin-restructuring.md`），父目标 + 6 个子目标 + 25 implementation task + capability freeze + authority matrix + 4 方案决策表 |
| 2026-06-17 | docs-smt-handoff-manifest-flow | 补充 SMT 分拣入库 handoff/manifest 闭环、插件、Celery 兜底和测试索引入口 |
| 2026-05-27 | docs-wms-integration | 补充 WMS 对接辅助域、迁移、测试和接入 checklist 索引入口 |
| 2026-04-16 | docs-hotfix | 修正文档入口路径与失效链接，并为历史提案类文档补充状态说明 |
| 2026-03-31 | v0.1.1.0 | 文档清理：删除 36 个过程文档，补全 callback/device 模块，修复 Mixin 继承示例 |
| 2026-03-23 | v0.1.0.0 | 初始生产版本：完整 3 层架构、JWT 认证、RBAC 权限、设备管理、作业线模块、254 个测试通过 |
| 2026-03-17 | v2.3 | Workline Phase 1 完成：Inbox/Outbox 模型、Repository、Service、Callback 集成、幂等性控制 |
| 2026-03-02 | v2.2 | 摄像头 Mock 服务新增传感器模拟 API（手动/自动触发、状态查询、事件历史） |
| 2026-03-02 | v2.1 | 新增 Mock 设备服务（摄像头、机械臂）Docker 封装 |
| 2026-02-27 | v2.0 | 项目结构完整索引 |

---

## 1. 文档目的

本文件为 `wes_backend`（P9 WES Backend）项目提供代码结构索引，帮助开发者快速定位文件、理解架构。

> 说明：仓库中的文档已逐步按主题拆分到 `docs/architecture/`、`docs/devops/`、`docs/integration/` 等子目录；若本文与实际目录不一致，请以当前文件树为准。

### 核心设计原则

- **DRY**: 避免重复代码，通过抽象和封装实现复用
- **KISS**: 保持设计和代码的简洁性
- **SOLID**: 单一职责、开闭原则、里氏替换、接口隔离、依赖倒置
- **YAGNI**: 只实现当前需要的功能

### 文档同步

- 同步工具: Serena MCP
- 同步频率: 代码结构变更时手动更新
- 版本控制: Git 追踪所有变更

### 文件分类说明

| 分类 | 说明 | 图标 |
|------|------|------|
| **架构核心** | 整个系统依赖的基础设施，改动影响全局 | 🔧 |
| **必读文档** | 新成员必须了解的文档 | 📖 |
| **常用功能** | 日常开发频繁使用 | 🔄 |
| **参考资料** | 需要时查阅 | 📚 |
| **示例代码** | 开发参考示例 | 🎯 |

---

## 2. 核心目录与文件索引

### 2.1 项目根目录

#### 🏗️ 基础设施配置

| 文件 | 用途 | 分类 |
|------|------|------|
| `pyproject.toml` | 项目 Python 依赖管理（uv），技术栈唯一真实来源 | 🔧 架构核心 |
| `docker-compose.yml` | 服务编排（API, DB, Redis, Celery） | 🔧 架构核心 |
| `Dockerfile` | 主应用容器构建定义 | 🔧 架构核心 |
| `.env.dev` | 开发环境变量配置 | 🔧 架构核心 |
| `.env.prod` | 生产环境变量配置 | 🔧 架构核心 |
| `.env.test` | 测试环境变量配置（含 WES_EVENT_CALLBACK_URL、SENSOR_* 配置） | 📚 参考资料 |
| `alembic.ini` | Alembic 数据库迁移配置 | 🔧 架构核心 |
| `uv.lock` | uv 依赖版本锁定文件 | 📚 参考资料 |

#### 📖 项目文档

| 文件 | 用途 | 分类 |
|------|------|------|
| `README.md` | 项目概述、环境设置、快速开始指南 | 📖 必读文档 |
| `CLAUDE.md` | Claude Code 开发指南（架构、规范、最佳实践） | 📖 必读文档 |
| `DOCKER.md` | Docker 使用说明 | 📚 参考资料 |
| `Jenkinsfile` | Jenkins CI/CD 配置 | 📚参考资料 |
| `Jenkinsfile.backend-ci` | 后端 CI 主入口；包含隔离 PG17 RuntimeInbox 严格验收、JUnit/evidence/log/diagnostic 归档与清理 | 🔧 架构核心 |
| `docs/architecture/adr/2026-05-13-wes-wms-rcs-resource-boundary.md` | WES/WMS/RCS 运行时资源、库存权责和回调入口 ADR | 📖 必读文档 |
| `docs/architecture/adr/2026-05-26-wms-integration-domain.md` | WMS 对接辅助域 ADR：反腐层边界、证据留痕、熔断和调用方合同 | 📖 必读文档 |
| `docs/architecture/adr/0001-phase2-runtime-ownership.md` | Runtime ownership ADR：runtime 域所有权与 inbound normalizer ownership guardrail | 📖 必读文档 |
| `docs/architecture/runtime-ownership-map.md` | Runtime 域 ownership map：entity/repository/service 三层归属与 production import guardrail | 📖 必读文档 |
| `docs/architecture/legacy-runtime-migration-spec.md` | 运行时物理迁移历史记录；不作为当前实现或插件开发入口 | 📚 历史对照 |
| `docs/architecture/legacy-cleanup-execution-plan.md` | technical cleanup scope 旧 plugin runtime/import 框架清理执行记录：顺序、范围、验收、business blocker 与回滚 | 📖 必读文档 |
| `docs/contracts/observability-contract.md` | Runtime 稳定观测合同：callback / RuntimeInbox / intent / device command / WMS breaker 的 span、metric、log event 和 attribute 口径 | 📖 必读文档 |
| `docs/contracts/runtime-toggle-governance.md` | Runtime toggle 治理合同：owner、expiry、scope、default、rollback、test_matrix 与安全边界 | 📖 必读文档 |
| `docs/integration/wms_caller_checklist.md` | WMS 同步调用方接入 checklist：RuntimeHold/诊断、错误处理和证据传播要求 | 📖 必读文档 |
| `docs/business/smt_sorter_inbound_workflow_guide.md` | SMT 分拣入库工作流指南，含 v0.7.0.0 后端 handoff/manifest P0 闭环状态 | 📖 必读文档 |
| `docs/superpowers/archive/specs/2026-06-16-smt-sorting-inbound-manifest-flow-spec.md` | SMT 分拣入库 handoff/manifest 后端闭环合同：两阶段 claim、ledger、READY recovery | 📚 历史对照 |
| `docs/superpowers/archive/plans/2026-06-16-smt-sorting-inbound-manifest-flow.md` | SMT 分拣入库 handoff/manifest 后端闭环 T0-T8 实施和验证记录 | 📚 历史对照 |
| `docs/devops/prod-release-deploy.md` | 生产环境手动发布与回滚 Runbook | 📖 必读文档 |

#### 🚀 应用入口

| 文件 | 用途 | 分类 |
|------|------|------|
| `main.py` | ASGI 应用启动文件，整个应用的起点 | 🔧 架构核心 |
| `src/register.py` | 应用组装器：注册中间件、路由、异常处理器 | 🔧 架构核心 |

#### 🔧 运维脚本

| 脚本 | 用途 | 分类 |
|------|------|------|
| `start_init.sh` | 启动前初始化脚本 | 🔄 常用功能 |
| `ruff_analysis.sh` | Ruff 代码质量分析脚本 | 📚 参考资料 |

#### 📁 配置目录

| 目录 | 用途 | 分类 |
|------|------|------|
| `scripts/` | 运维脚本集合（迁移、性能测试、部署） | 🔧 架构核心 |
| `postgresql/` | PostgreSQL 初始化脚本和配置 | 📚 参考资料 |
| `redis/` | Redis 配置文件 | 📚 参考资料 |
| `nginx/` | 反向代理配置 | 📚 参考资料 |

---

### 2.2 核心源代码 (src/)

#### 🧠 核心抽象层 (src/core/)

| 文件 | 用途 | 分类 |
|------|------|------|
| `base_api.py` | 通用 CRUD API 基类（零代码生成） | 🔧 架构核心 |
| `base_service.py` | 通用服务层基类（业务协调、缓存） | 🔧 架构核心 |
| `query_builder.py` | 查询构建器（FilterGroup → SQLAlchemy） | 🔧 架构核心 |
| `schema_loader.py` | Schema 加载器（自动关系加载） | 🔧 架构核心 |
| `exceptions.py` | 全应用自定义异常类 | 🔧 架构核心 |
| `error_handlers.py` | 全局异常处理器 | 🔧 架构核心 |
| `rbac.py` | RBAC 权限验证、超级用户检查 | 🔧 架构核心 |
| `security.py` | JWT 认证、密码哈希、Token 管理 | 🔧 架构核心 |
| `api_security.py` | 外部 API 签名认证逻辑；callback body HMAC、nonce replay guard 和短时间窗校验入口 | 🔄 常用功能 |
| `runtime_toggles.py` | Typed runtime toggle 定义与 owner/expiry/security-bypass validator | 🔧 架构核心 |
| `runtime_toggle_release_gate.py` | Runtime release toggle 发布阻塞决策：default-off 与 test_matrix evidence 校验 | 🔧 架构核心 |
| `runtime_toggle_catalog.py` | Runtime toggle typed catalog；quality gate 的唯一检查清单 | 🔧 架构核心 |
| `conf.py` | Pydantic Settings 配置管理 | 🔧 架构核心 |
| `logger.py` | 统一日志记录器 | 📚 参考资料 |
| `context.py` | 请求上下文管理 | 📚 参考资料 |
| `path_conf.py` | 项目路径配置 | 📚 参考资料 |
| `encryption.py` | 数据加密功能 | 📚 参考资料 |
| `tree_service.py` | 树形结构业务逻辑 | 📚 参考资料 |
| `tree_api.py` | 树形结构 API | 📚参考资料 |

#### 📦 响应系统 (src/core/response/)

| 文件 | 用途 | 分类 |
|------|------|------|
| `response_code.py` | 响应码枚举系统（1xxx-9xxx） | 🔧 架构核心 |
| `response_schema.py` | 统一响应数据结构 | 🔧 架构核心 |
| `response_util.py` | 响应构建工具函数 | 🔧 架构核心 |

**响应码规范**:
- `1xxx`: 成功响应
- `2xxx`: 客户端错误（参数、权限）
- `3xxx`: 资源错误（不存在、冲突）
- `4xxx`: 业务逻辑错误
- `5xxx`: 服务器内部错误
- `8xxx`: 第三方服务错误
- `9xxx`: 其他错误

#### 🔧 Mixin 系统 (src/core/mixins/)

| 文件 | Mixin | 用途 | 分类 |
|------|-------|------|------|
| `base.py` | `BaseMixin` | Schema 复用基类 | 🔧 架构核心 |
| `datatable.py` | `DataTableMixin` | 标准表字段（id, created_at, updated_at） | 🔧 架构核心 |
| `audit.py` | `EnterpriseMixin` | 审计字段（created_by, updated_by, remark） | 🔧 架构核心 |
| `soft_delete.py` | `SoftDeleteMixin` | 软删除字段 | 🔧 架构核心 |
| `tree.py` | `TreeMixin` | 树形结构字段 | 🔄 常用功能 |
| `optimistic_lock.py` | `OptimisticLockMixin` | 乐观锁字段 | 🔄 常用功能 |
| `composite.py` | `StandardMixin`, `AuditableMixin` | 组合 Mixin | 📚 参考资料 |
| `primary_key.py` | `PrimaryKeyMixin` | 主键字段 | 📚 参考资料 |
| `timestamp.py` | `TimestampMixin` | 时间戳字段 | 📚 参考资料 |
| `repr.py` | `ReprMixin` | __repr__ 方法 | 📚 参考资料 |
| `schema.py` | `SchemaMixin` | Schema 相关 Mixin | 📚 参考资料 |

#### 🗄️ 数据访问层 (src/database/)

| 文件 | 用途 | 分类 |
|------|------|------|
| `db.py` | 数据库连接池、Session 管理 | 🔧 架构核心 |
| `base_repository.py` | 通用 Repository（CRUD + Hook + 关系加载） | 🔧 架构核心 |
| `model_factory.py` | 动态 Schema 工厂 | 🔧 架构核心 |
| `schema_conf.py` | PostgreSQL Schema 隔离配置 | 🔧 架构核心 |
| `status_mixins.py` | 状态验证 Mixin（DocumentStatusMixin 等） | 🔧 架构核心 |
| `document_status.py` | 单据状态机（DocStatus, DocumentStateMachine） | 🔧 架构核心 |
| `tree_repository.py` | 树形结构数据访问 | 📚 参考资料 |
| `relation_metadata.py` | 自动发现关联关系 | 🔧 架构核心 |
| `cache_decorator.py` | 缓存装饰器 | 📚 参考资料 |
| `redis_client.py` | Redis 连接管理 | 📚 参考资料 |
| `redis_cache.py` | Redis 缓存实现；安全/幂等场景使用 `set_if_absent()` 执行固定 TTL 的 Redis `SET NX EX` | 📚 参考资料 |
| `dependencies.py` | FastAPI 依赖注入（AsyncSessionDep, CacheDep） | 🔧 架构核心 |

#### 🔄 关系处理 (src/database/relations/)

| 文件 | 用途 | 分类 |
|------|------|------|
| `relation_loader.py` | 自动加载 SQLAlchemy 关联对象 | 🔧 架构核心 |
| `relation_manager.py` | 关系元数据管理 | 🔧 架构核心 |
| `relation_crud.py` | 关联对象的创建、更新、删除 | 🔧 架构核心 |

#### 🪝 Hook 系统 (src/database/hooks/)

| 文件 | 用途 | 分类 |
|------|------|------|
| `hook_system.py` | Hook 基础设施（HookContext, HookType, HookExecutor） | 🔧 架构核心 |

#### 📋 审计系统 (src/database/audit/)

| 文件 | 用途 | 分类 |
|------|------|------|
| `hook_registrar.py` | 审计日志 Hook 自动注册器 | 📚 参考资料 |

#### ⚠️ 错误处理 (src/database/handlers/)

| 文件 | 用途 | 分类 |
|------|------|------|
| `error_translator.py` | IntegrityError → 友好中文提示 | 🔧 架构核心 |

#### 🚦 中间件 (src/middleware/)

| 文件 | 用途 | 分类 |
|------|------|------|
| `performance.py` | API 性能监控 | 📚 参考资料 |
| `rate_limit.py` | API 速率限制 | 📚 参考资料 |
| `request_log.py` | API 请求日志 | 🔧 架构核心 |

#### 🛠️ 工具类 (src/utils/)

| 文件 | 用途 | 分类 |
|------|------|------|
| `timezone.py` | 时区转换（⚠️ CRITICAL: naive vs aware datetime） | 🔧 架构核心 |
| `password_hasher.py` | 密码哈希和验证 | 🔧 架构核心 |
| `snowflake.py` | 分布式 ID 生成（雪花算法） | 🔄 常用功能 |
| `background_tasks.py` | 后台任务工具 | 📚 参考资料 |
| `audit.py` | 审计日志工具 | 📚 参考资料 |
| `health.py` | 系统健康检查 | 📚 参考资料 |
| `request_parse.py` | 请求解析工具 | 📚 参考资料 |
| `permission_scanner.py` | 权限扫描器 | 📚 参考资料 |
| `event_publisher.py` | 事件发布器 | 📚 参考资料 |

#### 🔗 公共模块 (src/common/)

| 文件 | 用途 | 分类 |
|------|------|------|
| `cache_config.py` | 缓存配置管理 | 📚 参考资料 |

#### 📱 静态资源 (src/static/)

| 目录/文件 | 用途 | 分类 |
|-----------|------|------|
| `swagger-ui/` | 自定义 Swagger UI 资源 | 📚 参考资料 |
| `ip2region.xdb` | IP 地址定位数据库 | 📚 参考资料 |

#### 📬 后台任务 (src/celery_app/)

| 文件 | 用途 | 分类 |
|------|------|------|
| `app.py` | Celery 应用实例 | 📚 参考资料 |
| `config.py` | Celery 配置 | 📚 参考资料 |
| `tasks/core.py` | 核心后台任务 | 📚 参考资料 |
| `tasks/runtime_inbox.py` | RuntimeInbox claim-one/process-one、lease recovery、三阶段 processor 与批次 SLI 的 Celery 主入口 | 🔧 架构核心 |
| `tasks/workline.py` | 作业线保留任务入口（Outbox 派发、timeout scanner 等）；不消费 RuntimeInbox | 🔧 架构核心 |

---

### 2.3 业务功能层 (src/app/)

#### 👤 认证模块 (src/app/auth/)

| 目录 | 文件 | 用途 | 分类 |
|------|------|------|------|
| `models/` | `auth.py` | 登录、Token 相关 Schema | 🔧 架构核心 |
| `services/` | `auth_service.py` | 认证业务逻辑（登录、登出、刷新） | 🔧 架构核心 |
| `v1/` | `auth.py` | 认证 API 路由 | 🔧 架构核心 |

#### 🔐 API 认证模块 (src/app/api_auth/)

外部 API 客户端认证（签名验证）

| 目录 | 文件 | 用途 | 分类 |
|------|------|------|------|
| `models/` | `api_application.py` | API 应用模型 | 🔧 架构核心 |
| | `api_access_log.py` | API 访问日志模型 | 📚 参考资料 |
| | `relationships.py` | 关系定义 | 📚 参考资料 |
| `repositories/` | `app_application_repository.py` | API 应用仓库 | 🔧 架构核心 |
| | `api_access_log_repository.py` | 访问日志仓库 | 📚 参考资料 |
| `services/` | `app_service.py` | API 应用服务 | 🔧 架构核心 |
| | `signature_service.py` | 签名验证服务 | 🔧 架构核心 |
| | `permission_service.py` | API 权限服务 | 🔧 架构核心 |
| | `api_access_log_service.py` | 访问日志服务 | 📚 参考资料 |
| `v1/` | `api_application.py` | API 应用路由 | 🔧 架构核心 |
| | `api_access_log.py` | 访问日志路由 | 📚 参考资料 |
| 根目录 | `constants.py` | API 认证常量 | 📚 参考资料 |

#### 👥 管理员模块 (src/app/admin/)

系统用户、角色、权限管理

| 目录 | 文件 | 用途 | 分类 |
|------|------|------|------|
| `models/` | `user.py` | 用户模型 | 🔧 架构核心 |
| | `role.py` | 角色模型 | 🔧 架构核心 |
| | `perm.py` | 权限模型 | 🔧 架构核心 |
| | `menu.py` | 菜单模型 | 🔧 架构核心 |
| | `relationships.py` | 关系定义 | 📚 参考资料 |
| `repositories/` | `user_repository.py` | 用户仓库 | 🔧 架构核心 |
| | `role_repository.py` | 角色仓库 | 🔧 架构核心 |
| | `perm_repository.py` | 权限仓库 | 🔧 架构核心 |
| | `menu_repository.py` | 菜单仓库 | 🔧 架构核心 |
| `services/` | `user_service.py` | 用户服务 | 🔧 架构核心 |
| | `role_service.py` | 角色服务 | 🔧 架构核心 |
| | `perm_service.py` | 权限服务 | 🔧 架构核心 |
| | `menu_service.py` | 菜单服务 | 🔧 架构核心 |
| `v1/` | `user.py` | 用户路由 | 🔧 架构核心 |
| | `role.py` | 角色路由 | 🔧 架构核心 |
| | `perm.py` | 权限路由 | 🔧 架构核心 |
| | `menu.py` | 菜单路由 | 🔧 架构核心 |
| | `performance.py` | 性能监控路由 | 📚 参考资料 |

#### 🛠️ 系统模块 (src/app/sys/)

| 目录 | 文件 | 用途 | 分类 |
|------|------|------|------|
| `models/` | `audit_log.py` | 审计日志模型 | 📚 参考资料 |
| `repositories/` | `audit_log_repository.py` | 审计日志仓库 | 📚 参考资料 |
| `services/` | `audit_service.py` | 审计日志服务 | 📚 参考资料 |
| `v1/` | `audit_log.py` | 审计日志路由 | 📚 参考资料 |
| | `events.py` | 事件系统路由 | 📚 参考资料 |

#### 🎯 ActiveObject 归属投影 (src/app/active_objects/)

Active projection 归属仲裁层，对多来源 active object fact 做唯一 owner 判定；多 owner 或 transient 超窗时输出 RECONCILING，不直接修改业务 owner 状态。

| 目录 | 文件 | 用途 | 分类 |
|------|------|------|------|
| 根目录 | `registry.py` | ActiveObjectRegistry：3 路 active projection UNION 后的唯一归属仲裁，输出 ACTIVE / TRANSIENT / RECONCILING 与 evidence refs | 🔧 架构核心 |
| 根目录 | `__init__.py` | ActiveObjectFact / ActiveObjectResolution / ActiveObjectRegistry 导出 | 🔧 架构核心 |

#### 🧭 对账决议模块 (src/app/reconciliation/)

RECONCILING 冲突登记与 owner-scoped 决议层，只产出 hold/freeze/manual resolution action，不跨域直接改写 owner 终态。

| 目录 | 文件 | 用途 | 分类 |
|------|------|------|------|
| 根目录 | `manager.py` | ReconciliationManager：登记冲突、生成 owner-scoped decision、按滞留时间升级 WARNING / ERROR / CRITICAL | 🔧 架构核心 |
| 根目录 | `__init__.py` | Reconciliation decision / severity / action 类型导出 | 🔧 架构核心 |

#### 🔧 作业线模块 (src/app/workline/)

> 📐 **WORKLINE + PLUGIN 体系全面重构顶层设计**（父目标：对当前 WORKLINE + PLUGIN 体系进行全面重构/重做）：
> - 顶层设计（GB/T 8567 概要/详细设计 13 章）：[`docs/architecture/workline-and-plugin-restructuring.md`](./workline-and-plugin-restructuring.md)（1,800+ 行：1.引言 2.系统概述 3.体系结构 4.数据设计 5.接口设计 6.状态机 7.安全设计 8.非功能性 9.模块设计 10.实施计划 11.执行规范 12.风险 13.附录）
> - 关键决策（ADR）：`docs/architecture/adr/workline-restructuring/`（8 个 ADR）
> - 评审存档：`docs/architecture/reviews/`（autoplan CEO/Design/Eng 评审全文 + 28 决策记录）
> - 实施细节（SPEC）按对应实施范围启动时展开；active code / gate / test 命名策略见 `docs/architecture/process-naming-policy.md`
>
> 包含 WES 顶层领域边界、WMS 反腐层 (wms_integration ACL 6 套 port)、Authority Matrix、Capability Freeze、4 方案决策表、实施 roadmap、数据模型、状态机图、模块 API 设计。


当前 `workline` 域负责 WorkLine 配置、Binding、诊断与安全；generated 插件、运行态模型、
Repository 和 Service 位于 `src/app/runtime/`。架构 guardrail 固化配置域与运行域的依赖边界。

| 目录 | 文件 | 用途 | 分类 |
|------|------|------|------|
| `models/` | `workline.py` | WorkLine 模型（配置域 — plugin 容器 / 运行时配置 / 诊断归属） | 🔧 架构核心 |
| | `plane.py` | WorkLine plane scene/snapshot 读模型（`plane.scene.v1` / `plane.snapshot.v1`） | 🔧 架构核心 |
| | `safety.py` | WorkLine 安全事件审计与请求 schema（`WorklineSafetyIncident` / `WorklineSafetyIncidentStatus` / `ClearWorkLineEstopRequest` / `SimulateWorkLineEstopRequest`） | 🔧 架构核心 |
| | `__init__.py` | WorkLine 配置、安全与 rack model 导出 | 🔧 架构核心 |
| `repositories/` | `workline_repository.py` | WorkLine Repository（按 line_code 查询 — 配置域） | 🔧 架构核心 |
| | `safety_incident_repository.py` | WorklineSafetyIncidentRepository：配置域安全审计仓储 | 🔧 架构核心 |
| | `__init__.py` | WorkLine 配置、安全与 rack repository 导出 | 🔧 架构核心 |
| `services/` | `workline_service.py` | WorkLine 配置 CRUD、激活与停用 service | 🔧 架构核心 |
| | `manifest_validator.py` | WorkLine manifest 激活前引用完整性校验（queue / device role / capability blocker） | 🔧 架构核心 |
| | `plane_service.py` | WorkLine plane scene/snapshot 读服务，从配置和后续 active projection 派生态势视图 | 🔧 架构核心 |
| | `diagnostic_service.py` | WorklineDiagnosticService：配置域诊断服务 | 🔧 架构核心 |
| | `safety_service.py` | WorkLineSafetyService：配置域安全服务 | 🔧 架构核心 |
| | `write_back_service.py` | `RuntimeIntentEffectApplier` 与 `EffectApplyState`：generated decision 的 typed effect 落地边界 | 🔧 架构核心 |
| | `plugin_binding_service.py` | immutable Plugin Binding 激活、撤权、准入与运行态 pin snapshot；激活时固定 generated index digest 与 provider profile identity | 🔧 架构核心 |
| | `diagnosis_verdict_builder_service.py` | WorkLine 诊断 verdict 构造器 | 🔧 架构核心 |
| | `rack_position_service.py` | WorkLine rack 位置 service | 🔧 架构核心 |
| | `__init__.py` | 当前配置域 service 导出；运行态 service 不从 `workline.services` 暴露 | 🔧 架构核心 |
| `v1/` | `workline.py` | WorkLine CRUD 路由（配置域）+ plugin manifest 查询 + activate/deactivate + plane scene/snapshot 读端点 | 🔧 架构核心 |
| | `operation.py` | WorkLine 启动/停止 admission、manifest 校验与沙箱 endpoint | 🔧 架构核心 |
| | `__init__.py` | v1 路由聚合（workline / operation） | 🔧 架构核心 |
| `unit_of_work.py` | WorklineUnitOfWork：workline 配置域事务边界 | 🔧 架构核心 |
| `constants.py` | workline 域常量 | 🔧 架构核心 |
| `__init__.py` | workline 域顶层导出 | 🔧 架构核心 |
| `domain/` | WorkLine 配置域合同、模型与纯领域服务；执行能力位于 `src/app/runtime/` | 🔧 架构核心 |
| `domain/contracts/__init__.py` | domain contracts 子包导出 | 🔧 架构核心 |
| `domain/contracts/device_error_codes.py` | 设备错误码统一规范 | 🔧 架构核心 |
| `domain/models/__init__.py` | domain models 子包导出 | 🔧 架构核心 |
| `domain/models/barcode_decision.py` | 扫码决策模型（与 `domain/services/barcode_decision_service` 配套） | 🔧 架构核心 |
| `domain/services/__init__.py` | domain services 子包导出 | 🔧 架构核心 |
| `domain/services/barcode_decision_service.py` | 配置域扫码决策 service | 🔧 架构核心 |
| `domain/services/session_lifecycle_service.py` | WorklineSession 生命周期字段的集中变更规则 | 🔧 架构核心 |
| `domain/services/smt_rack_bin_scheduling_service.py` | SMT rack/bin 配置域调度判断 | 🔧 架构核心 |
| `domain/run_mode.py` | WorkLine 运行模式枚举（PRODUCTION / SIMULATION） | 🔧 架构核心 |

**核心设计模式**：
- **Inbox 模式**：统一六类编排入口（`COMMAND_RESULT / DEVICE_EVENT / EXTERNAL_HTTP / INTERNAL_EVENT / TIMER_TIMEOUT / REPLAY_REQUEST`）
- **幂等性控制**：白皮书 6.3.1 节（厂商 ID 优先 + hash 备选），统一使用 `WES-{OPERATION_KIND}-{HASH}` 命名
- **Outbox 模式**：统一调度出口（设备指令、外部回调、状态记录）

#### 🔧 Runtime 编排层 (src/app/runtime/orchestration/)

> Runtime 是运行态事实与编排的唯一 owner。Generated plugin definition/handler 负责业务判断；
> RuntimeInbox 校验 Binding 与上下文，派发 generated decision，并通过 typed effect 写回状态。
> Device 与 callback 由正式 service 入口接入；运行态 service 不从 `workline` 域导出。

| 文件 | 用途 | 分类 |
|------|------|------|
| `execution_session.py` | ExecutionSession 会话聚合根；`id` 为唯一主键，`workline_id` / `plugin_key` 为查询索引，并固定完整 Binding pins | 🔧 架构核心 |
| `execution_correlation.py` | ExecutionCorrelation 实体（一次性 correlation 跨实体锚点；含历史回填） | 🔧 架构核心 |
| `execution_work_item.py` | ExecutionWorkItem 实体（work item 状态机） | 🔧 架构核心 |
| `runtime_inbox.py` | RuntimeInbox 实体（持久化入口契约；H4 边界守门） | 🔧 架构核心 |
| `runtime_timeline.py` | RuntimeTimeline 实体（事件溯源） | 🔧 架构核心 |
| `runtime_hold.py` | RuntimeHold 实体（Manual / Safety E-Stop / Material Conflict 等 hold 状态） | 🔧 架构核心 |
| `runtime_intent_log.py` | RuntimeIntentLog 实体（plugin 产出 RuntimeIntent 的 ledger） | 🔧 架构核心 |
| `idempotency_key.py` | IdempotencyKey 实体（`WES-{OPERATION_KIND}-{HASH}` 唯一约束） | 🔧 架构核心 |
| `conveyor_queue_membership.py` | ConveyorQueueMembership 实体（含 `CheckConstraint` 限定 `membership_status` 取值） | 🔧 架构核心 |
| `models/__init__.py` | WorkLine 运行态投影 model 聚合导出；RuntimeInbox 使用顶层 `runtime_inbox.py` | 🔧 架构核心 |
| `models/bin_cell_reservation.py` | BinCellReservation 运行态 model | 🔧 架构核心 |
| `models/diagnostic.py` | Diagnostic 运行态 model | 🔧 架构核心 |
| `models/dispatch_attempt.py` | DispatchAttempt 运行态 model | 🔧 架构核心 |
| `models/material_unit.py` | MaterialUnit 运行态 model | 🔧 架构核心 |
| `models/object_transition_event.py` | ObjectTransitionEvent 运行态 model | 🔧 架构核心 |
| `models/operation.py` | Operation 运行态 model | 🔧 架构核心 |
| `models/rack_position.py` | RackPosition 运行态 model | 🔧 架构核心 |
| `models/runtime.py` | Runtime 运行态 model | 🔧 架构核心 |
| `models/runtime_hold.py` | RuntimeHold 运行态投影 model | 🔧 架构核心 |
| `models/runtime_hold_api.py` | RuntimeHold API 读模型 | 🔧 架构核心 |
| `models/session.py` | WorklineSession 运行态 model | 🔧 架构核心 |
| `models/smt_inbound_handoff.py` | SmtInboundHandoff 运行态 model | 🔧 架构核心 |
| `models/timeline.py` | Timeline 运行态 model | 🔧 架构核心 |
| `repositories/__init__.py` | Repository 聚合导出（含 canonical `RuntimeInboxRepository` 与运行态投影 repository） | 🔧 架构核心 |
| `repositories/idempotency_key_repository.py` | IdempotencyKey Repository:upsert 语义封装 (`claim_if_absent` + `get_by_identity`) | 🔧 架构核心 |
| `repositories/bin_cell_reservation_repository.py` | BinCellReservation Repository | 🔧 架构核心 |
| `repositories/diagnostic_repository.py` | Diagnostic Repository | 🔧 架构核心 |
| `repositories/dispatch_attempt_repository.py` | DispatchAttempt Repository | 🔧 架构核心 |
| `repositories/runtime_inbox_repository.py` | RuntimeInbox 唯一仓储：canonical persistence、同桶 FIFO `SKIP LOCKED` claim、lease reclaim、fenced terminal、typed query 与 SLI snapshot | 🔧 架构核心 |
| `repositories/material_unit_repository.py` | MaterialUnit Repository | 🔧 架构核心 |
| `repositories/object_transition_event_repository.py` | ObjectTransitionEvent Repository | 🔧 架构核心 |
| `repositories/rack_position_repository.py` | RackPosition Repository | 🔧 架构核心 |
| `repositories/runtime_hold_repository.py` | RuntimeHold Repository | 🔧 架构核心 |
| `repositories/session_repository.py` | WorklineSession Repository | 🔧 架构核心 |
| `repositories/smt_inbound_handoff_repository.py` | SmtInboundHandoff Repository | 🔧 架构核心 |
| `services/idempotency_guard.py` | IdempotencyGuard:outbound effect 幂等闸门（`ClaimResult.NEW/MATCH` + `IdempotencyConflict`） | 🔧 架构核心 |
| `services/runtime_snapshot_assembler.py` | RuntimeSnapshotAssembler：按 BC-02 合同把 session + timeline + inbox + hold + intent log 拼装成 RuntimeSnapshot 输出 | 🔧 架构核心 |
| `services/device_command_gateway.py` | DeviceCommandGateway：设备命令治理、派发准入与持久化 effect 边界 | 🔧 架构核心 |
| `services/device_command_lease.py` | DeviceCommand lease 策略：基于 sent_at / timeout_ms / 默认 TTL 判定过期、重放和取消许可 | 🔧 架构核心 |
| `services/inbox/` | 出站 intent/outbox dispatch、dispatch attempt、object transition 与 backpressure 服务；不承担入站 Inbox 消费 | 🔧 架构核心 |
| `services/inbox/backpressure.py` | RuntimeInbox backpressure 策略：pending / dead-letter backlog 下的 NORMAL / DEGRADED / OPERATOR_ATTENTION 判定 | 🔧 架构核心 |
| `services/runtime_inbox/` | RuntimeInbox 三阶段处理：context load、validation、generated dispatch、typed effect write-back 与 fenced terminal | 🔧 架构核心 |
| `services/hold/` | RuntimeHold 创建、查询与解除 service | 🔧 架构核心 |
| `services/intent/` | Operation、SMT handoff 与 System Capability intent/effect service | 🔧 架构核心 |
| `services/query/` | Runtime、活动对象、物料位置与北向 operation 只读查询 | 🔧 架构核心 |
| `services/reconciliation/` | RuntimeReconciliationServiceImpl：运行态对账实现 | 🔧 架构核心 |
| `services/trace/` | Trace 查询、response/resource view 组装与 timeline sequence | 🔧 架构核心 |
| `services/__init__.py` | Runtime 服务层正式导出 | 🔧 架构核心 |
| `exceptions.py` | Runtime 领域异常与 RuntimeInbox 状态机错误 | 🔧 架构核心 |
| `enums.py` | FailureDomain / DecisionType 等运行时契约枚举 | 🔧 架构核心 |
| `device_ordering.py` | 基于 source device、topology 与 role 的命令目标排序 | 🔧 架构核心 |
| `effect_result.py` | RuntimeIntent effect 落地结果模型 | 🔧 架构核心 |
| `material_target_resolver.py` | 物料目标解析器 | 🔧 架构核心 |
| `runtime_intent.py` | RuntimeIntent dataclass 与校验 | 🔧 架构核心 |
| `runtime_intent_effects.py` | RuntimeIntent effect 落地入口 | 🔧 架构核心 |
| `timeline_generator.py` | RuntimeTimeline 生成器 | 🔧 架构核心 |
| `business_identity_bridge.py` | Runtime business identity helpers | 🔧 架构核心 |
| `events_bridge.py` | 平台保留事件、控制事件、安全事件与生产事件判定 | 🔧 架构核心 |
| `lock_bridge.py` | Redis 分布式锁与 PostgreSQL advisory 降级 | 🔧 架构核心 |
| `resource_wait_evidence_bridge.py` | RESOURCE_WAIT evidence helper | 🔧 架构核心 |
| `sandbox_catalog_bridge.py` | SANDBOX / MOCK 确定性样例 catalog | 🔧 架构核心 |
| `topology_bridge.py` | WORKLINE 运行时拓扑视图与 `validate_topology_schema`；消费 generated Definition 的 typed schema | 🔧 架构核心 |
| `consumers/` | RuntimeInbox callback ACK-before-processing 幂等入口 | 🔧 架构核心 |
| `consumers/__init__.py` | 仅导出 CallbackRuntimeInboxWriter adapter；RuntimeInboxService 正式导出边界为 `services/runtime_inbox/__init__.py` | 🔧 架构核心 |
| `consumers/callback_runtime_inbox_writer.py` | callback ingress 的 RuntimeInbox 薄写入适配器，保持 API → Service → Repository 分层 | 🔧 架构核心 |
| `services/runtime_inbox/runtime_inbox_service.py` | RuntimeInboxService 正式边界：六类 ingress、五态 claim/retry/fencing、audit-only 排除、`request_id`/认证 `actor`/`reason` 扁平 `REPLAY_REQUEST`、typed domain errors 与审计 | 🔧 架构核心 |
| `diagnostics/` | Runtime diagnostics builder、codes、failure mapper 与 typed models | 🔧 架构核心 |
| `diagnostics.py` | Runtime diagnostics 正式顶层门面 | 🔧 架构核心 |
| `__init__.py` | 模块导出（9 entity） | 🔧 架构核心 |

#### 🔧 Runtime 能力面 (src/app/runtime/)

Runtime 顶层 capability / normalizer registry：业务能力注入（query/effect）与入站 normalizer（callback/event/result）的注册表 SSOT；与 `src/app/runtime/orchestration/` 实体层严格分层，由 import-linter `capability-isolation` contract 守护边界。

| 文件 | 用途 | 分类 |
|------|------|------|
| `src/app/runtime/capability_port_registry.py` | CapabilityPortRegistry：runtime capability 注入只暴露 query/effect port contract；静态扫描拒绝 provider implementation、HTTP client、DTO、service locator 与入站 normalizer | 🔧 架构核心 |
| `src/app/runtime/inbound_normalizer_registry.py` | InboundNormalizerRegistry：与 CapabilityPortRegistry 严格分离的入站 normalizer 注册表；singleton per-port；仅正式 consumer 通过 RuntimeCapabilityContext 访问 | 🔧 架构核心 |

**关键约束**：
- **H4 反注入边界**：callback / event / result 三个入口接受 payload 时，**仅允许**白名单顶层字段（`callback_type` / `data` / `trace_id` / `event_id` / `causation_id` / `source_system` / `source_version` / `occurred_at` / `request_id` / `timestamp` / `signature`）；业务追溯字段（如 `provider_code` / `source_event_id`）必须放入 `data` 内。外部回调 (`/callback/external`) 顶层白名单额外覆盖 WMS/RCS 协议业务元数据（`dispatch_key` / `status` / `exchange_*` / `rack_*` / `operation_key` / `operation_type` / `position_code` / `source_position_code` / `target_position_code` / `target_position_role` / `task_type` / `workline_code` / `bin_mounts` / `material` / `actions` / `sequence_no` / `source` / `station` / `target` / `active_bin_rack` / `error_code` / `error_message`）与 AGV 执行回执（`command_code` / `result` / `finish_time` / `device_code` / `task_status` / `reason_code` / `reason_message`）。H4 的真正安全屏障是子层 `_FORBIDDEN_PARAM_KEYS` 递归扫描（阻断 `plc_address` / `coordinate` 等设备控制字段），顶层白名单扩展不削弱 H4 安全语义。
- **H5 幂等键命名**：`WES-{OPERATION_KIND}-{HASH}`，唯一约束落在 `idempotency_keys` 表。

**相关文档**：
- 运行时语义 SSOT：`docs/business/workline_business_data_event_flow_spec.md` v0.1
- 架构设计：`docs/business/workline_plugin_architecture_design.md` v3.2
- SMT 分拣入库工作流：`docs/business/smt_sorter_inbound_workflow_guide.md`
- SMT handoff/manifest 闭环合同：`docs/superpowers/archive/specs/2026-06-16-smt-sorting-inbound-manifest-flow-spec.md`
- SMT handoff/manifest 实施记录：`docs/superpowers/archive/plans/2026-06-16-smt-sorting-inbound-manifest-flow.md`
- 历史 SMT 粗分机资料：`docs/archive/legacy-smt-classifier/`

#### 🧩 Generated 作业线插件实现 (src/app/runtime/workline_plugins/)

| 文件 | 用途 | 分类 |
|------|------|------|
| `definition.py` | Generated definition 基础合同与 typed schema | 🔧 架构核心 |
| `dispatcher.py` | RuntimeInbox generated plugin dispatcher | 🔧 架构核心 |
| `handler_registry.py` | Generated handler 注册表 | 🔧 架构核心 |
| `generated_index.py` | 构建期生成的插件索引与 digest | 🔧 架构核心 |
| `rough_sorter/definition.py` | 粗分机工作线 generated definition | 🔄 常用功能 |
| `rough_sorter/handlers.py` | 粗分机 typed handler 集合 | 🔄 常用功能 |
| `rough_sorter/domain_contract.py` | 粗分机业务合同、事件与命令类型 | 🔄 常用功能 |
| `smt_sorting_inbound/contracts.py` | SMT 分拣入库 typed 业务合同 | 🔧 架构核心 |
| `smt_sorting_inbound/definition.py` | SMT 分拣入库 generated definition | 🔧 架构核心 |
| `smt_sorting_inbound/handlers.py` | SMT 分拣入库 typed handler 集合 | 🔧 架构核心 |

**插件开发文档**：
- **插件开发指南**：`docs/plugin_development_guide.md` 📖 必读文档
- **旧插件模板说明**：`docs/archive/legacy-workline-plugins/workline_plugin_template/README.md` 📚 历史对照
- **RuntimeIntent 架构设计**：`docs/business/workline_plugin_architecture_design.md` 📖 必读文档
- **Runtime 工作流指南**：`docs/business/workline_runtime_workflow_guide.md` 📖 必读文档
- **旧 PluginResult 资料归档**：`docs/archive/legacy-plugin-result/README.md` 📚 历史对照

**核心特性**：
- **Generated 路由**：Definition 声明 route 与 typed input；`ROUTE_HANDLERS` 绑定纯 handler，generated index 固定身份与 digest，handler registry 执行注册校验
- **Pydantic 自动验证**：Payload 自动解析和类型安全
- **RuntimeIntent 输出**：插件只声明上下文更新、命令、等待、业务 NG、完成或阻断意图
- **运行时拥有副作用**：拓扑解析、Session 生命周期、命令/outbox、等待状态和终态写入集中在 Runtime
- **单一执行合同**：generated handler 只返回 Runtime decision，状态推进和副作用由 Runtime 负责

#### 🔔 回调模块 (src/app/callback/)

外部系统回调处理

| 目录 | 文件 | 用途 | 分类 |
|------|------|------|------|
| `models/` | `callback_log.py` | 回调入口日志模型（ingress audit；记录 request_id、ingress_outcome、failure_stage，不复制 workflow trace 事实） | 🔧 架构核心 |
| | `event.py` | 回调事件模型 | 🔧 架构核心 |
| `repositories/` | `callback_log_repository.py` | 回调日志仓库 | 🔧 架构核心 |
| `services/` | `callback_service.py` | 回调处理服务 | 🔧 架构核心 |
| `v1/` | `callback.py` | 回调 API 路由（入口校验、early return logging、request_id 入口锚点） | 🔧 架构核心 |

#### 🔌 WMS 能力面 ports (src/app/wms_integration/ports/)

7 个 WMS port Protocol + typed data classes：
- 3 query port: MasterData / InventoryQuery / ReconciliationQuery
- 1 effect port: InventoryTransaction
- 1 effect port: Fulfillment
- 1 event normalizer port: Event（含 InboundEventPort 基协议 + WmsEventPort 4 normalizer）
- 1 document port: Document

| 目录 | 文件 | 用途 | 分类 |
|------|------|------|------|
| `ports/` | `master_data.py` | WmsMasterDataPort Protocol + 6 typed data classes | 🔧 架构核心 |
| | `query_inventory_operation.py` | InventoryQueryOperationPort + Decimal typed authority snapshot | 🔧 架构核心 |
| | `inventory_transaction.py` | WmsInventoryTransactionPort Protocol + 3 typed data classes | 🔧 架构核心 |
| | `document.py` | WmsDocumentPort Protocol + 6 typed data classes | 🔧 架构核心 |
| | `fulfillment.py` | WmsFulfillmentPort Protocol + 2 typed data classes | 🔧 架构核心 |
| | `event.py` | InboundEventPort 基协议 + WmsEventPort Protocol + 5 typed data classes | 🔧 架构核心 |
| | `reconciliation_query.py` | WmsReconciliationQueryPort Protocol + 1 typed data class | 🔧 架构核心 |

#### 🔗 WMS 对接辅助域 (src/app/wms_integration/)

WMS Anti-Corruption Layer，统一 typed QUERY transport、异步 WMS/RCS 派发合同、回调标准化、DB-backed 熔断、脱敏证据留痕和调用方错误合同。QUERY 禁止跨请求缓存。该域不提供公开 `/api/v1/wms/...` 代理接口，也不接管库存主账、SystemOutbox 派发或 RuntimeHold 创建。

| 目录 | 文件 | 用途 | 分类 |
|------|------|------|------|
| `models/` | `evidence.py` | WMS 调用证据模型：脱敏快照、canonical hash、trace/correlation 字段 | 🔧 架构核心 |
| | `circuit_breaker.py` | WMS 熔断状态模型：operation 级共享失败计数、OPEN/HALF_OPEN/CLOSED 状态 | 🔧 架构核心 |
| | `ports.py` | WMS typed ports 请求/响应合同模型 | 🔧 架构核心 |
| `repositories/` | `evidence_repository.py` | WMS evidence Repository | 🔧 架构核心 |
| | `circuit_breaker_repository.py` | WMS circuit breaker state Repository | 🔧 架构核心 |
| `services/` | `http_client.py` | 同步 WMS HTTP client，暴露 typed exception hierarchy | 🔧 架构核心 |
| | `typed_ports.py` | WMS effect typed ports 门面；不承载 QUERY | 🔧 架构核心 |
| | `query_transport.py` | 无 operation 分支的 WMS QUERY HTTP、预算、分页与 evidence executor | 🔧 架构核心 |
| | `fulfillment_lifecycle.py` | WMS fulfillment lifecycle service：基于状态机推进履约状态、保护终态、输出 RuntimeInbox 需求 | 🔧 架构核心 |
| | `evidence_service.py` | WMS evidence 脱敏、hash 和记录服务 | 🔧 架构核心 |
| | `circuit_breaker_service.py` | DB-backed WMS 熔断状态转换服务 | 🔧 架构核心 |
| | `callback_normalizer.py` | WMS/RCS 回调最小包络校验和字段标准化 | 🔧 架构核心 |
| | `transport_contract.py` | rack/handling WMS/RCS 外部派发 payload 合同辅助 | 🔧 架构核心 |
| | `endpoint_config.py` | WMS endpoint operation path、timeout 和 operation name 配置 | 🔧 架构核心 |
| | `redaction.py` | WMS request/response 脱敏规则 | 🔧 架构核心 |
| | `exceptions.py` | WMS typed errors：timeout、5xx、business reject、circuit-open | 🔧 架构核心 |
| `evidence/` | `envelope.py` | typed EvidenceEnvelope / ExternalReference，锁定外部事实证据 envelope 和 hash 字段 | 🔧 架构核心 |
| 根目录 | `state_machine.py` | WMS fulfillment 11 态状态机，保护 SUCCEEDED / REJECTED / FAILED / TIMEOUT / CANCELLED 等终态不被迟到事件覆盖 | 🔧 架构核心 |

#### 📡 设备模块 (src/app/device/)

设备（摄像头、机械臂等）管理

| 目录 | 文件 | 用途 | 分类 |
|------|------|------|------|
| `models/` | `device.py` | 设备模型（能力声明、通信治理、诊断配置） | 🔧 架构核心 |
| | `command.py` | 设备指令模型 | 🔧 架构核心 |
| `repositories/` | `device_repository.py` | 设备仓库 | 🔧 架构核心 |
| | `command_repository.py` | 指令仓库 | 🔧 架构核心 |
| `services/` | `device_service.py` | 设备服务 | 🔧 架构核心 |
| | `command_service.py` | 指令服务 | 🔧 架构核心 |
| `v1/` | `device.py` | 设备路由 | 🔧 架构核心 |
| | `command.py` | 指令路由 | 🔧 架构核心 |

---

### 2.4 测试目录 (tests/)

#### 📁 测试结构

| 目录/文件 | 用途 | 分类 |
|-----------|------|------|
| `conftest.py` | Pytest Fixtures（数据库、测试客户端） | 🔧 架构核心 |
| `README.md` | 测试文档 | 📖 必读文档 |
| `test_base_repository_crud.py` | Repository CRUD 测试 | 🔧 架构核心 |
| `test_base_repository_hooks.py` | Repository Hook 测试 | 🔧 架构核心 |
| `test_base_repository_error_handling.py` | Repository 错误处理测试 | 🔧 架构核心 |
| `test_optimistic_lock.py` | 乐观锁测试 | 🔄 常用功能 |
| `test_soft_delete_feature.py` | 软删除功能测试 | 🔄 常用功能 |
| `test_document_status.py` | 单据状态测试 | 🔄 常用功能 |
| `test_partial_unique_index.py` | 部分唯一索引测试 | 📚 参考资料 |
| `test_relation_metadata.py` | 关系元数据测试 | 🔧 架构核心 |
| `test_schema_loader.py` | Schema 加载器测试 | 🔧 架构核心 |
| `test_snowflake.py` | 雪花算法测试 | 📚 参考资料 |
| `test_exceptions.py` | 异常处理测试 | 📚 参考资料 |
| `test_request_parse.py` | 请求解析测试 | 📚 参考资料 |

#### 🧪 子测试目录

| 目录 | 用途 | 分类 |
|------|------|------|
| `auth/` | 认证模块测试 | 🔧 架构核心 |
| `api_auth/` | API 认证测试 | 🔧 架构核心 |
| `api/` | API 测试（如签名测试） | 📚 参考资料 |
| `core/` | 核心安全与基础设施测试（API security、Redis cache 等） | 🔧 架构核心 |
| `active_objects/` | ActiveObject 归属投影和冲突仲裁测试 | 🔧 架构核心 |
| `reconciliation/` | Reconciliation owner-scoped 决议合同测试 | 🔧 架构核心 |
| `benchmark/` | 性能基准测试 | 📚 参考资料 |
| `load/` | 显式运行的负载/基准测试（Locust + runtime benchmark gate 四场景） | 📚 参考资料 |
| `resilience/` | 显式运行的弹性/恢复测试（Redis 重连、降级、runtime scenario replay fixture） | 📚参考资料 |
| `e2e/` | E2E 测试（流水线料盘搬运流程） | 🔄 常用功能 |
| `workline_runtime/` | Runtime capability、投影、对账与 material-flow 纯逻辑回归 | 🔧 架构核心 |
| `wms_integration/` | WMS 对接辅助域测试（typed QUERY transport、client、typed effects、evidence、breaker、callback normalizer、caller contract） | 🔧 架构核心 |
| `architecture/` | 架构守卫测试（import-linter 合同 + runtime public-surface / boundary / prohibited-import guardrail） | 🔧 架构核心 |
| `runtime/orchestration/` | Runtime orchestration 单元/合同测试（RuntimeInbox persistence、五态 claim、三阶段 processor、SLI） | 🔧 架构核心 |
| `contracts/` | 跨模块合同测试（workline behavior contract + runtime ops contract 文档存在性） | 🔧 架构核心 |
| `contracts/workline/` | Runtime boundary behavior contract 测试 | 🔧 架构核心 |
| `workline/` | WorkLine 配置域测试（manifest activation validator、plane read model） | 🔧 架构核心 |
| `workline_plugins/` | 作业线插件测试（rough_sorter / smt_sorting_inbound / barcode_decision 等） | 🔧 架构核心 |
| `integration/workline_capabilities/` | generated plugin、binding、System Capability 与 PostgreSQL 性能闭环 | 🔧 架构核心 |

**runtime boundary / guardrail 测试文件**（`tests/architecture/`）:

| 文件 | 用途 | 分类 |
|------|------|------|
| `architecture/test_legacy_runtime_import_guardrail.py` | 禁止 `src.workline_runtime` production import，并校验 runtime public surface / diagnostics 导出 | 🔧 架构核心 |
| `architecture/test_plugin_mirrors_mirror.py` | Generated 插件边界自包含与禁止入口校验 | 🔧 架构核心 |
| `architecture/test_workline_domain_boundary.py` | WorkLine domain boundary 守卫：runtime material-flow 业务合同只位于 runtime capability | 🔧 架构核心 |
| `architecture/test_workline_plugins_mirror.py` | WorkLine 配置侧插件模式与 generated 插件实现边界校验 | 🔧 架构核心 |
| `architecture/test_legacy_matrix_contract.py` | Legacy cleanup matrix 生成契约：service inventory、目标能力字段、CSV/Markdown 与生成器一致性 | 🔧 架构核心 |
| `architecture/test_runtime_status_owner_guardrail.py` | Runtime status ownership 守卫：运行态写入集中在 runtime/orchestration projection，WorkLine/material-flow 只通过 snapshot/readiness 读取 | 🔧 架构核心 |

**Runtime orchestration 测试文件**（`tests/runtime/orchestration/`）:

| 文件 | 用途 | 分类 |
|------|------|------|
| `runtime/orchestration/test_runtime_inbox_consumer_service.py` | RuntimeInboxService 幂等接收、唯一冲突重读、payload conflict 409 和人工重放审计测试 | 🔧 架构核心 |
| `runtime/orchestration/test_runtime_inbox_claim_repository.py` | canonical repository FIFO claim、lease、fencing、命名空间与 SLI snapshot 测试 | 🔧 架构核心 |
| `integration/test_runtime_inbox_processing_postgresql.py` | 真实 PostgreSQL producer → claim → 三阶段 processor → effects → terminal 闭环 | 🔧 架构核心 |
| `integration/test_runtime_inbox_migration_postgresql.py` | Revision A/B 与 A→parent→A 毫秒值保留回环 | 🔧 架构核心 |
| `resilience/test_runtime_inbox_crash_recovery_postgresql.py` | claim 后崩溃、write-back 后终态前崩溃的 lease/fencing/事务恢复 | 🔧 架构核心 |
| `load/test_runtime_inbox_claim_benchmark.py` | 1000 条混合 backlog、4 worker 真实 PostgreSQL claim 性能门禁 | 📚 参考资料 |
| `runtime/orchestration/test_conveyor_queue_membership_writer_service.py` | ConveyorQueueMembershipWriter DB-backed 写入、幂等、placeholder resolve、RECONCILING、诊断和 PostgreSQL `FOR UPDATE` 合同测试 | 🔧 架构核心 |
| `runtime/orchestration/test_idempotency_audit_contract.py` | IdempotencyGuard conflict audit payload 测试 | 🔧 架构核心 |
| `runtime/orchestration/test_runtime_recovery_policies.py` | RuntimeInbox backpressure 与 DeviceCommand lease 恢复策略测试 | 🔧 架构核心 |
| `integration/test_conveyor_queue_membership_concurrency.py` | ConveyorQueueMembershipWriter opt-in PostgreSQL partial unique index 并发冲突与 existing 重读测试 | 🔧 架构核心 |

**RuntimeInbox 严格验收与文档门禁**：

| 文件 | 用途 | 分类 |
|------|------|------|
| `tests/deployment/test_runtime_inbox_postgresql_acceptance_ci.py` | CI 隔离数据库、runner 顺序、失败非零、artifact/cleanup 与 commit 绑定合同 | 🔧 架构核心 |
| `tests/deployment/test_runtime_inbox_documentation_consistency.py` | Current docs 六 kind/五态、路径、计划状态、TODO 与本地链接一致性 | 🔧 架构核心 |
| `tests/architecture/test_workline_inbox_retirement_guardrail.py` | Python/Shell/current Markdown scanner 自测与窄 allowlist | 🔧 架构核心 |
| `tests/integration/test_runtime_inbox_migration_postgresql.py` | Revision A/B fresh、parent、audit-only、约束与回环矩阵 | 🔧 架构核心 |
| `tests/integration/test_runtime_inbox_processing_postgresql.py` | 真实 PostgreSQL producer → processor → effect → terminal 闭环 | 🔧 架构核心 |
| `tests/resilience/test_runtime_inbox_crash_recovery_postgresql.py` | claim 后与 write-back 后的两个 crash window | 🔧 架构核心 |
| `tests/load/test_runtime_inbox_claim_benchmark.py` | 1000 backlog/4 worker 正式 benchmark 与 evidence artifact | 📚 参考资料 |

**Runtime 执行安全与恢复测试文件**：

| 文件 | 用途 | 分类 |
|------|------|------|
| `active_objects/test_active_object_registry.py` | ActiveObjectRegistry 单 owner、transient handoff、超窗 RECONCILING、多 owner 冲突测试 | 🔧 架构核心 |
| `reconciliation/test_reconciliation_manager_contract.py` | ReconciliationManager owner-scoped decision、resource freeze 和升级阈值测试 | 🔧 架构核心 |
| `core/test_api_security_body_hmac.py` | callback body HMAC canonical signature、必填 header、短时间窗和 API_PATH 前缀测试 | 🔧 架构核心 |
| `core/test_redis_cache_set_if_absent.py` | RedisCache `set_if_absent()` 固定 TTL、NX 语义和 Redis 不可用返回 None 测试 | 🔧 架构核心 |
| `contracts/test_runtime_ops_contract_docs.py` | Runtime observability / runtime toggle governance 合同文档存在性与关键字段测试 | 🔧 架构核心 |
| `workline/test_manifest_activation_validator.py` | WorkLine manifest 激活前 queue/device/capability 引用 blocker 测试 | 🔧 架构核心 |
| `workline/test_plane_read_model.py` | WorkLine plane scene/snapshot 读模型 schema 和 queue 节点派生测试 | 🔧 架构核心 |

**Workline Runtime 测试文件**（当前纯逻辑集合）：

| 文件 | 用途 | 分类 |
|------|------|------|
| `workline_runtime/test_runtime_inbox_projection_query_contract.py` | RuntimeInbox 投影查询、状态统计和 audit-only 边界 | 🔧 架构核心 |
| `workline_runtime/test_runtime_inbox_repository_consumers.py` | query/trace/reconciliation 等 consumer 统一 repository 合同 | 🔧 架构核心 |
| `workline_runtime/test_runtime_capability_dispatcher.py` | Runtime capability dispatch 与 intent 边界 | 🔧 架构核心 |
| `workline_runtime/test_workline_runtime_status_projection_service.py` | Runtime-owned status projection 行为 | 🔧 架构核心 |

**Runtime behavior contract 测试文件**（`tests/contracts/workline/`）：

| 文件 | 用途 | 分类 |
|------|------|------|
| `contracts/workline/test_runtime_inbox_lifecycle_contract.py` | BC-XX RuntimeInbox claim/process/retry/dead-letter 5 态状态机 + lease 过期回退 + 非法转移断言 | 🔧 架构核心 |
| `contracts/workline/test_runtime_intent_log_dispatch_contract.py` | BC-XX IdempotencyGuard claim_or_match 三态 + WES-{OPERATION_KIND}-{HASH} 命名 + 归一化 + 边界校验 | 🔧 架构核心 |
| `contracts/workline/test_runtime_session_advance_contract.py` | BC-XX ExecutionSession 不持 work item step_status + ExecutionWorkItem 状态机 + ExecutionCorrelation 桥接 1:N | 🔧 架构核心 |
| `contracts/workline/test_runtime_timeline_query_contract.py` | BC-XX RuntimeTimeline 按 trace_id/correlation_id/event_type 过滤 + append-only 不持 owner 状态 | 🔧 架构核心 |
| `contracts/workline/test_runtime_hold_contract.py` | BC-XX RuntimeHold NARROW_SCOPES (WORK_ITEM/OBJECT/DEVICE/RESOURCE/QUEUE) 默认 + WIDE_SCOPES 仅整线安全 | 🔧 架构核心 |
| `contracts/workline/test_device_command_dispatch_contract.py` | BC-XX DeviceCommand 状态机 PENDING → SENT → ACK_RECEIVED → COMPLETED + H4 反注入 10 字段阻断 + correlation_id 跨域稳定 | 🔧 架构核心 |
| `contracts/workline/test_wms_fulfillment_request_contract.py` | BC-XX WmsFulfillmentPort 7 effect 方法全实现 + accepted/reason 互斥语义 + pallet binding 全字段必填 | 🔧 架构核心 |
| `contracts/workline/test_manual_replay_audit_contract.py` | BC-XX DEAD_LETTER 终态不可就地重置 + 重放新建 inbox + H5 审计 (actor + reason 必填) + causation_id 因果链 | 🔧 架构核心 |

**WMS 对接辅助域测试文件**：

| 文件 | 用途 | 分类 |
|------|------|------|
| `wms_integration/test_wms_client.py` | WMS HTTP client typed error、evidence_key 和熔断交互测试 | 🔧 架构核心 |
| `wms_integration/test_caller_contract.py` | 首个真实 caller 接入前的 RuntimeHold/diagnostic 合同保护测试 | 🔧 架构核心 |
| `wms_integration/test_evidence.py` | WMS evidence 脱敏、hash、关联 ID 和保存行为测试 | 🔧 架构核心 |
| `wms_integration/test_circuit_breaker.py` | DB-backed WMS breaker 状态转换测试 | 🔧 架构核心 |
| `wms_integration/test_cache.py` | WMS read-only 短缓存、坏缓存清理和降级回源测试 | 🔄 常用功能 |
| `wms_integration/test_callback_normalizer.py` | WMS/RCS 回调包络校验和字段标准化测试 | 🔧 架构核心 |
| `wms_integration/test_transport_contract.py` | rack/handling WMS/RCS 派发 payload 合同防漂移测试 | 🔧 架构核心 |
| `wms_integration/test_fulfillment_state_machine.py` | Fulfillment 11 态状态机、callback inbox requirement、终态保护和 CB-blocked late callback 测试 | 🔧 架构核心 |
| `wms_integration/test_fulfillment_lifecycle_service.py` | Fulfillment lifecycle service 状态推进、终态忽略和 RuntimeInbox 需求测试 | 🔧 架构核心 |
| `wms_integration/test_typed_evidence_envelope.py` | Typed EvidenceEnvelope / ExternalReference 字段和 extra forbid 合同测试 | 🔧 架构核心 |

**E2E 测试文件**：

| 文件 | 用途 | 分类 |
|------|------|------|
| `e2e/__init__.py` | E2E 测试模块导出 | 🔧 架构核心 |
| `e2e/test_conveyor_robot_arm.py` | 流水线料盘搬运 E2E 测试（使用 ECS Mock 事件 API） | 🔄 常用功能 |

#### 🎭 Mock 设备服务

| 目录/文件 | 用途 | 分类 |
|-----------|------|------|
| `mock/` | E2E 测试 Mock 设备服务 | 🔧 架构核心 |
| `mock/Dockerfile` | Mock 服务 Docker 镜像构建 | 🔧 架构核心 |
| `mock/__init__.py` | Mock 服务模块导出 | 🔧 架构核心 |
| `mock/ecs_mock_catalog.py` | ECS Mock 多设备目录、能力和默认回调数据 | 🔧 架构核心 |
| `mock/ecs_mock_server.py` | ECS Mock 单服务多设备协议入口（端口 8010） | 🔧 架构核心 |
| `mock/wms_mock_server.py` | WMS Mock 服务（主数据、库存、预约释放） | 🔧 架构核心 |

**Mock 服务 API 端点**：

**ECS Mock (端口 8010)**：
- `POST /api/v1/device/command` - 接收 WES 下发命令，顶层必须包含 `device_code`
- `GET /api/v1/device/status?device_code=...` - 查询单设备状态；不传 `device_code` 返回全部设备
- `POST /api/v1/mock/event` - 手动上报设备事件
- `POST /api/v1/mock/devices/{device_code}/scenario` - 设置 `success`、`fail`、`timeout` 故障注入场景

---

### 2.5 文档目录 (docs/)

| 文件 | 用途 | 分类 |
|------|------|------|
| `SRS.md` | 软件需求规格说明书 | 📖 必读文档 |
| `ARCHITECTURE_EVOLUTION_ROADMAP.md` | 架构演进路线图 | 📖 必读文档 |
| `architecture/file_index.md` | 代码库动态索引（本文档） | 📖 必读文档 |
| `permission-model.md` | RBAC 权限模型文档 | 📖 必读文档 |
| `CLAUDE.md` | Claude Code 开发指南 | 📖 必读文档 |
| `devops/database_migration.md` | 数据库迁移指南 | 🔄 常用功能 |
| `menu-api-usage.md` | 菜单 API 使用指南 | 📚 参考资料 |
| `REPOSITORY_GUIDE.md` | Repository 使用指南 | 📚 参考资料 |
| `integration/interact_backend.md` | 后端交互需求草案（历史提案） | 📚 参考资料 |
| `integration/callback_event_validation_principles.md` | callback/event 前置校验边界说明 | 📖 必读文档 |
| `integration/wms_caller_checklist.md` | WMS 同步调用方接入 checklist：错误处理、RuntimeHold/诊断和 evidence_key 传播 | 📖 必读文档 |
| `integration/workline_device_error_code_standardization.md` | Workline 插件体系硬件错误码统一规划与迁移表 | 📖 必读文档 |
| `business/smt_sorter_inbound_workflow_guide.md` | SMT 分拣入库业务流程、资源边界、事件口径和 v0.7.0.0 后端 handoff/manifest 落地状态 | 📖 必读文档 |
| `superpowers/specs/2026-06-16-smt-sorting-inbound-manifest-flow-spec.md` | SMT 分拣入库 handoff/manifest 后端闭环合同 | 📖 必读文档 |
| `superpowers/plans/2026-06-16-smt-sorting-inbound-manifest-flow.md` | SMT 分拣入库 handoff/manifest 后端闭环实施计划与验证记录 | 📚 参考资料 |
| `contracts/observability-contract.md` | Runtime / callback / device / WMS 稳定观测合同 | 📖 必读文档 |
| `business/workline_business_data_event_flow_spec.md` | 当前业务 SSOT：RuntimeInbox 六类 ingress、五态、replay 与单轨编排 | 📖 必读文档 |
| `business/workline_runtime_workflow_guide.md` | Runtime 工作流、排障、reset 与严格 PostgreSQL 验收指南 | 📖 必读文档 |
| `architecture/runtime-orchestration-spec.md` | Runtime 实体、正式 Service/Repository、五态与 heavy/CI 验收索引 | 📖 必读文档 |
| `contracts/runtime-toggle-governance.md` | Typed runtime toggle 治理和安全边界合同 | 📖 必读文档 |
| `architecture/adr/2026-05-26-wms-integration-domain.md` | WMS 对接辅助域 ADR | 📖 必读文档 |
| `api_authentication_design.md` | API 认证设计文档 | 📚 参考资料 |
| `api_authentication_summary.md` | API 认证功能摘要 | 📚 参考资料 |
| `third_party_integration_whitepaper.md` | 第三方集成指南 | 📚 参考资料 |
| `archive/legacy-smt-classifier/` | 已归档的旧 SMT 粗分机插件资料 | 📚 历史资料 |
| `hardware/SMT粗分机接口调用说明书20260321-v1.md` | 当前 SMT 粗分机联调协议说明（现行标准） | 📖 必读文档 |
| `hardware/SMT流水线接口调用说明书20260320-v1.md` | 当前 SMT 流水线联调协议说明（现行标准） | 📖 必读文档 |
| `hardware/SMT分拣机ECS接口调用说明书V1-20260318.md` | 历史 ECS 协议转写稿，仅用于历史比对与偏差分析，非当前标准 | 📚 参考资料 |
| `devops/JENKINS.md` | Jenkins 使用指南 | 📚 参考资料 |
| `devops/jenkins-setup-current-env.md` | Jenkins 环境配置 | 📚 参考资料 |
| `devops/jenkins-checklist.md` | Jenkins 部署检查清单 | 📚 参考资料 |
| `系统架构图.eddx` | 系统架构图（图形文件） | 📚 参考资料 |

---

### 2.6 运维脚本目录 (scripts/)

| 脚本 | 用途 | 分类 |
|------|------|------|
| `migrate.sh` | Alembic 迁移控制（upgrade/downgrade/current） | 🔧 架构核心 |
| `generate_migration.sh` | 生成新的 Alembic 迁移脚本 | 🔧 架构核心 |
| `init-env.sh` | 开发环境初始化 | 🔄 常用功能 |
| `start_e2e_env.sh` | E2E 测试环境管理（启动/停止/日志） | 🔄 常用功能 |
| `run_performance_test.sh` | 运行 Locust/AB 性能测试 | 📚 参考资料 |
| `test_api_signature.sh` | API 签名验证测试 | 📚 参考资料 |
| `check_runtime_toggle_release_gate.py` | Runtime toggle 发布门禁入口，供 `git-quality-gate.sh --check runtime-toggle-release` 调用 | 🔧 架构核心 |
| `scripts/data/reset_runtime_data.py` | schema-qualified 运行数据 reset；dry-run、`--yes`、主数据保护与 Mock fail-closed | 🔧 架构核心 |
| `scripts/workline_migration_inventory.py` | 在单环境只读快照中生成 WorkLine plugin/binding/runtime reference inventory | 🔧 架构核心 |
| `scripts/workline_migration_matrix.py` | 聚合跨环境 inventory 与 digest-bound 批准证据，输出 T8 可复用 matrix | 🔧 架构核心 |
| `scripts/workline_inbox_retirement_guardrail.py` | 扫描 active Python/Shell/current docs，阻止已退役 Inbox 入口回流 | 🔧 架构核心 |
| `scripts/run_runtime_inbox_postgresql_acceptance.py` | 严格验收 runner：preflight 后顺序执行 migration、processing、两个 crash window、benchmark 和 evidence validator | 🔧 架构核心 |
| `scripts/run_runtime_inbox_postgresql_acceptance_ci.sh` | CI 隔离 PG17 生命周期、clean checkout、secret env、artifact mount 与强制 cleanup | 🔧 架构核心 |
| `docker-deploy-simple.sh` | 简化 Docker 部署 | 📚 参考资料 |
| `init-deploy-servers.sh` | 部署服务器初始化 | 📚 参考资料 |

---

### 2.7 数据库迁移 (migrations/)

| 目录 | 用途 | 分类 |
|------|------|------|
| `versions/` | 数据库结构变更历史 | 🔧 架构核心 |

**WMS 对接相关迁移**：

| 文件 | 用途 | 分类 |
|------|------|------|
| `versions/20260527_0025_793f8773f841_add_wms_call_evidence.py` | 新增 WMS call evidence 表与索引 | 🔧 架构核心 |
| `versions/20260527_0105_07be7a97f4a6_add_wms_circuit_breaker_state.py` | 新增 WMS circuit breaker state 表与索引 | 🔧 架构核心 |

**Runtime orchestration 相关迁移**：

| 文件 | 用途 | 分类 |
|------|------|------|
| `versions/20260626_1200_0e9de1e6c7e3_phase1_device_fk_ring_dissolve.py` | Phase 0→1 FK ring dissolve：动态发现 pg_constraint 名称后 drop device ↔ workline_sessions 循环外键，保留字段用于业务追溯 | 🔧 架构核心 |
| `versions/20260626_1719_f04718a3f04f_add_remaining_runtime_orchestration_.py` | 新增 9 个 runtime/orchestration 实体表（execution_sessions / execution_correlations / execution_work_items / runtime_inboxes / runtime_timelines / runtime_holds / runtime_intent_logs / idempotency_keys / conveyor_queue_memberships），含 `CheckConstraint` 限定 `membership_status` 取值；ExecutionCorrelation 历史回填默认列 | 🔧 架构核心 |
| `migrations/versions/20260711_1815_b8a28e1bfec8_extend_runtime_inbox.py` | Revision A：canonical envelope、六 kind/五态命名 CHECK 与 audit-only 分类；canonical 行降级 fail-closed | 🔧 架构核心 |
| `migrations/versions/20260711_1819_ec426c628516_retire_workline_inbox.py` | Revision B：将旧 FK 映射到 audit-only RuntimeInbox 后退役旧表；有引用降级 fail-closed | 🔧 架构核心 |
| `migrations/versions/20260714_1103_e0d58415afc9_create_runtime_inbox_indexes_.py` | Revision C：在 Alembic autocommit block 内并发创建/删除 RuntimeInbox 热索引 | 🔧 架构核心 |

---

## 3. 目录结构树形图

```plaintext
/
├───.dockerignore
├───.env.dev                  # 开发环境配置
├───.env.prod                 # 生产环境配置
├───.env.test                 # 测试环境配置
├───.gitignore
├───.python-version
├───alembic.ini               # Alembic 数据库迁移配置
├───CLAUDE.md                 # Claude Code 开发指南
├───docker-compose.yml        # 服务编排
├───Dockerfile                # 容器构建文件
├───Jenkinsfile               # 通用 CI/CD 配置
├───Jenkinsfile.backend-ci    # 后端 CI 与 RuntimeInbox 隔离 PostgreSQL 验收
├───main.py                   # 应用主入口
├───pyproject.toml            # 项目依赖管理
├───README.md                 # 项目说明
├───ruff_analysis.sh          # 代码分析脚本
├───start_init.sh             # 初始化脚本
├───uv.lock                   # 依赖锁定文件
│
├───docs/                     # 项目文档
│   ├───ARCHITECTURE_EVOLUTION_ROADMAP.md
│   ├───devops/JENKINS.md
│   ├───REPOSITORY_GUIDE.md
│   ├───SRS.md
│   ├───api_authentication_design.md
│   ├───api_authentication_summary.md
│   ├───devops/database_migration.md
│   ├───architecture/file_index.md  # 本文档
│   ├───integration/callback_event_validation_principles.md
│   ├───integration/interact_backend.md
│   ├───devops/jenkins-checklist.md
│   ├───devops/jenkins-setup-current-env.md
│   ├───menu-api-usage.md
│   ├───permission-model.md
│   ├───third_party_integration_whitepaper.md
│   └───系统架构图.eddx
│
├───migrations/               # 数据库迁移脚本
│   └───versions/
│
├───nginx/                    # Nginx 配置
├───postgresql/               # PostgreSQL 配置
├───redis/                    # Redis 配置
├───scripts/                  # 运维脚本
│   ├───docker-deploy-simple.sh
│   ├───generate_migration.sh
│   ├───init-deploy-servers.sh
│   ├───init-env.sh
│   ├───migrate.sh
│   ├───run_performance_test.sh
│   └───test_api_signature.sh
│
└───src/                      # 源代码
    ├───register.py           # 应用组装器
    │
    ├───app/                  # 业务功能层
    │   ├───active_objects/   # ActiveObject 归属投影
    │   ├───admin/            # 后台管理
    │   ├───api_auth/         # API 认证
    │   ├───auth/             # 用户认证
    │   ├───callback/         # 外部回调入口
    │   ├───reconciliation/   # RECONCILING 冲突决议
    │   ├───runtime/          # Runtime 编排层
    │   ├───sys/              # 系统管理
    │   ├───workline/         # WorkLine 配置域
    │   └───wms_integration/  # WMS 对接辅助域
    │
    ├───common/               # 公共模块
    │
    ├───celery_app/           # 后台任务
    │
    ├───core/                 # 核心抽象层
    │   ├───mixins/           # Mixin 系统
    │   └───response/         # 响应系统
    │
    ├───database/             # 数据访问层
    │   ├───audit/            # 审计系统
    │   ├───handlers/         # 错误处理
    │   ├───hooks/            # Hook 系统
    │   └───relations/        # 关系处理
    │
    ├───middleware/           # 中间件
    ├───static/               # 静态资源
    │
    └───utils/                # 工具类
│
└───tests/                    # 测试
    ├───api/
    ├───api_auth/
    ├───auth/
    ├───benchmark/
    ├───load/
    └───resilience/
```

---

## 4. 快速查找索引

### 4.1 按功能查找

| 功能需求 | 文件位置 |
|----------|----------|
| **认证授权** | |
| JWT Token 管理 | `src/core/security.py` |
| RBAC 权限验证 | `src/core/rbac.py` |
| API 签名认证 | `src/core/api_security.py` |
| Callback body HMAC / nonce replay | `src/core/api_security.py`, `src/database/redis_cache.py` |
| 用户登录/登出 | `src/app/auth/services/auth_service.py` |
| **数据访问** | |
| 通用 CRUD | `src/database/base_repository.py` |
| 查询构建 | `src/core/query_builder.py` |
| 关系加载 | `src/database/relations/relation_loader.py` |
| Schema 驱动加载 | `src/core/schema_loader.py` |
| 错误翻译 | `src/database/handlers/error_translator.py` |
| **业务逻辑** | |
| 通用 Service | `src/core/base_service.py` |
| Hook 系统 | `src/database/hooks/hook_system.py` |
| 状态验证 | `src/database/status_mixins.py` |
| 单据状态机 | `src/database/document_status.py` |
| RuntimeInbox 幂等接收 | `src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_service.py` |
| ActiveObject 归属仲裁 | `src/app/active_objects/registry.py` |
| Reconciliation 冲突决议 | `src/app/reconciliation/manager.py` |
| WMS 履约状态机 | `src/app/wms_integration/state_machine.py` |
| WorkLine plane 读模型 | `src/app/workline/services/plane_service.py` |
| **API 层** | |
| 通用 API 基类 | `src/core/base_api.py` |
| 路由注册 | `src/register.py` |
| 统一响应 | `src/core/response/` |
| **工具类** | |
| 时区处理 | `src/utils/timezone.py` ⚠️ |
| 密码哈希 | `src/utils/password_hasher.py` |
| 雪花算法 | `src/utils/snowflake.py` |
| 审计工具 | `src/utils/audit.py` |
| **模型复用** | |
| DataTableMixin | `src/core/mixins/datatable.py` |
| EnterpriseMixin | `src/core/mixins/audit.py` |
| SoftDeleteMixin | `src/core/mixins/soft_delete.py` |
| TreeMixin | `src/core/mixins/tree.py` |
| OptimisticLockMixin | `src/core/mixins/optimistic_lock.py` |

### 4.2 按模块查找

| 模块 | 位置 | 说明 |
|------|------|------|
| **用户管理** | `src/app/admin/` | 用户 CRUD、角色分配 |
| **角色管理** | `src/app/admin/` | 角色 CRUD、权限分配 |
| **权限管理** | `src/app/admin/` | 权限 CRUD、权限树 |
| **菜单管理** | `src/app/admin/` | 菜单 CRUD、菜单树 |
| **认证** | `src/app/auth/` | 登录、登出、刷新 Token |
| **API 认证** | `src/app/api_auth/` | API 应用、签名验证 |
| **审计日志** | `src/app/sys/` | 操作日志查询 |
| **运行时编排** | `src/app/runtime/orchestration/` | RuntimeInbox、IdempotencyGuard、DeviceCommand lease、RuntimeSnapshot |
| **ActiveObject 归属** | `src/app/active_objects/` | active projection 归属仲裁与 RECONCILING 判定 |
| **对账决议** | `src/app/reconciliation/` | owner-scoped 冲突登记和升级决议 |
| **作业线配置域** | `src/app/workline/` | WorkLine CRUD、manifest 校验、plane scene/snapshot |
| **WMS 对接辅助域** | `src/app/wms_integration/` | WMS typed ports、evidence、breaker、callback normalizer |

---

## 5. 关键设计模式

### 5.1 零代码开发模式

```
1. 定义 Base (业务字段) → UserBase
2. 定义 Model (Base + Mixins) → User
3. ModelFactory 自动生成 Schema → UserCreate, UserUpdate
4. 定义 Repository (继承 BaseRepository)
5. 定义 Service (继承 BaseService)
6. 定义 API (继承 BaseAPI) → 零代码 CRUD
```

### 5.2 分层架构

```
API 层 (BaseAPI)
    ↓ 依赖注入
Service 层 (BaseService)
    ↓ 协调
Repository 层 (BaseRepository)
    ↓ 操作
数据库 (SQLModel/SQLAlchemy)
```

### 5.3 Hook 系统

```python
# 自动注册的 Hook
1. 状态验证 Hook → validate_xxx_status()
2. 审计字段 Hook → created_by/updated_by
3. 乐观锁 Hook → version
4. 审计日志 Hook → AuditableMixin
```

### 5.4 时区处理 (CRITICAL)

| 场景 | 正确方法 | 返回类型 |
|------|----------|----------|
| 数据库存储 | `timezone.now_for_db()` | naive UTC datetime |
| API 响应 | `timezone.now_utc().isoformat()` | ISO 8601 字符串 |
| 时间戳计算 | `timezone.now_utc().timestamp()` | Unix 时间戳 |
| 时间戳转换 | `timezone.to_utc(timestamp)` | aware UTC datetime |

---

## 6. 同步更新日志

| 日期 | 版本 | 变更内容 |
|------|------|----------|
| 2026-07-01 | 0.10.4.0 | 同步 Phase 3 执行安全与恢复模块、测试与 ops contract 索引 |
| 2026-02-27 | v2.1 | 移除优先级字段，改用功能分类 |
| 2026-02-27 | v2.0 | 完整重构，基于 Serena MCP 分析 |

---

## 7. 附录

### 7.1 响应码速查表

| 代码范围 | 类型 | HTTP 状态 |
|----------|------|-----------|
| 1xxx | 成功 | 200-202 |
| 2xxx | 客户端错误 | 400-403 |
| 3xxx | 资源错误 | 404-410 |
| 4xxx | 业务错误 | 400 |
| 5xxx | 服务器错误 | 500-503 |
| 8xxx | 第三方服务 | 502-504 |
| 9xxx | 其他 | 429-500 |

**RuntimeInbox replay 响应合同**：

| 领域原因 | 统一响应 | HTTP | 说明 |
|----------|----------|------|------|
| `RUNTIME_INBOX_NOT_FOUND` | `ResourceErrorCode.NOT_FOUND` (`3000`) | 404 | source inbox 不存在 |
| `RUNTIME_INBOX_REPLAY_NOT_ALLOWED`（typed error 类别） | `BusinessErrorCode.INVALID_STATE` (`4001`) | 400 | 非死信、audit-only、非法 envelope 或归属不允许；`SOURCE_WORKLINE_NOT_FOUND` 特例映射 3000 |
| `RUNTIME_INBOX_AUDIT_PERSISTENCE_FAILED` | 同名稳定错误码 | 503 | 重放审计无法持久化，拒绝返回成功 |
| payload hash conflict | `ResourceErrorCode.CONFLICT` (`3012`) | 409 | 同 `request_id` 内容不一致 |

### 7.2 文档维护

**同步工具**: Serena MCP

**更新触发条件**:
1. 新增/删除核心模块
2. 架构重大调整
3. 新增关键文件/目录

---

**文档结束**

*本文档由 Claude Code + Serena MCP 生成和维护*
