# P9 WES Backend 项目文件索引

> Legacy notes: 本文件索引存在历史条目，涉及旧插件 builder 的说明仅供定位旧文档；当前运行时以 `RuntimeIntent` 为准。

**最后更新**: 2026年6月29日（phase2-stage2-burndown-docs: Phase 2 burn-down 阶段 2 — orchestration 7 bridge 镜像 + consumers/RuntimeInboxConsumer 单点入口 + workline utils/trace_context/domain/plugins 全量镜像 + 6 个 mirror 测试 + R-WLR allowlist 全清）
**同步状态**: ⚠️ WORKLINE + PLUGIN 体系全面重构顶层设计采用 GB/T 8567 概要设计说明书 + 详细设计 13 章结构（`docs/architecture/workline-and-plugin-restructuring.md`），1,800+ 行；含数据模型、状态机图、模块 API、接口设计、Phase 实施 roadmap；不预先拆 SPEC；关键决策 9 个 ADR（含 Phase 2 launch PR ADR-0001）；autoplan 评审存档；其余内容请以实际仓库结构为准

---

## 版本更新日志

| 日期 | 版本 | 变更内容 |
|------|------|----------|
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
| `docs/architecture/adr/2026-05-13-wes-wms-rcs-resource-boundary.md` | WES/WMS/RCS 运行时资源、库存权责和回调入口 ADR | 📖 必读文档 |
| `docs/architecture/adr/2026-05-26-wms-integration-domain.md` | WMS 对接辅助域 ADR：反腐层边界、证据留痕、熔断和调用方合同 | 📖 必读文档 |
| `docs/architecture/adr/0001-phase2-runtime-ownership.md` | Phase 2 launch PR ADR:runtime 域所有权固化 + wlr 严格型 allowlist + R-I3c 5 域扩展 | 📖 必读文档 |
| `docs/architecture/runtime-ownership-map.md` | Phase 2 launch PR Runtime 域 ownership map:entity/repository/service 三层归属,wlr allowlist 严格型入口 | 📖 必读文档 |
| `docs/architecture/legacy-runtime-migration-spec.md` | Phase 2 launch PR 迁移规格:burn-down 6 阶段执行契约 + 9 处跨域 import 修复路径 + wlr allowlist 严格型 + 8 contract gap TDD 同步 + 主计划 §10.3 启动条件 + 完成门禁追踪 | 📖 必读文档 |
| `docs/integration/wms_caller_checklist.md` | WMS 同步调用方接入 checklist：RuntimeHold/诊断、错误处理和证据传播要求 | 📖 必读文档 |
| `docs/business/smt_sorter_inbound_workflow_guide.md` | SMT 分拣入库工作流指南，含 v0.7.0.0 后端 handoff/manifest P0 闭环状态 | 📖 必读文档 |
| `docs/superpowers/specs/2026-06-16-smt-sorting-inbound-manifest-flow-spec.md` | SMT 分拣入库 handoff/manifest 后端闭环合同：两阶段 claim、ledger、READY recovery | 📖 必读文档 |
| `docs/superpowers/plans/2026-06-16-smt-sorting-inbound-manifest-flow.md` | SMT 分拣入库 handoff/manifest 后端闭环 T0-T8 实施和验证记录 | 📚 参考资料 |
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
| `api_security.py` | 外部 API 签名认证逻辑 | 🔄 常用功能 |
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
| `redis_cache.py` | Redis 缓存实现 | 📚 参考资料 |
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
| `tasks/workline.py` | 作业线 Celery 任务入口（Inbox 消费、Outbox 派发、Phase 2 effect handlers；消费 supports_command_types / maintenance_mode / callback_path） | 🔧 架构核心 |

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

#### 🔧 作业线模块 (src/app/workline/)

> 📐 **WORKLINE + PLUGIN 体系全面重构顶层设计**（父目标：对当前 WORKLINE + PLUGIN 体系进行全面重构/重做）：
> - 顶层设计（GB/T 8567 概要/详细设计 13 章）：[`docs/architecture/workline-and-plugin-restructuring.md`](./workline-and-plugin-restructuring.md)（1,800+ 行：1.引言 2.系统概述 3.体系结构 4.数据设计 5.接口设计 6.状态机 7.安全设计 8.非功能性 9.模块设计 10.实施计划 11.执行规范 12.风险 13.附录）
> - 关键决策（ADR）：`docs/architecture/adr/workline-restructuring/`（8 个 ADR）
> - 评审存档：`docs/architecture/reviews/`（autoplan CEO/Design/Eng 评审全文 + 28 决策记录）
> - 实施细节（SPEC）暂不拆，对应 Phase 启动时按需展开
>
> 包含 WES 顶层领域边界、WMS 反腐层 (wms_integration ACL 6 套 port)、Authority Matrix、Capability Freeze、4 方案决策表、5 Phase 实施 roadmap、数据模型、状态机图、模块 API 设计。


作业线运行时系统，遵循白皮书 v3.1 架构设计（插件化、状态机、幂等性）

| 目录 | 文件 | 用途 | 分类 |
|------|------|------|------|
| `models/` | `inbox.py` | WorklineInbox 收件箱模型（统一编排入口） | 🔧 架构核心 |
| | `outbox.py` | WorklineOutbox 发件箱模型（统一派发出口） | 🔧 架构核心 |
| | `session.py` | WorklineSession 会话模型（含等待态、trace 锚点、ingress_count、last_request_id/last_ingress_at） | 🔧 架构核心 |
| | `timeline.py` | WorklineTimeline 时间轴模型 | 🔧 架构核心 |
| | `runtime.py` | 运行监控 / Trace 查询响应模型（overview / workline / device / trace） | 🔧 架构核心 |
| | `workline.py` | WorkLine 模型（插件容器、运行时配置、诊断归属） | 🔧 架构核心 |
| | `smt_inbound_handoff.py` | SMT 入库 handoff demand/source item 账本模型，记录 claim、source-pick、terminal ledger 和恢复证据 | 🔧 架构核心 |
| | `runtime_hold.py` | RuntimeHold 模型（Manual / Safety E-Stop / Material Conflict 等 hold 状态机） | 🔧 架构核心 |
| | `runtime_hold_api.py` | Runtime Hold API Schema（ResolveRuntimeHoldRequest / Response / Summary / Detail） | 🔧 架构核心 |
| | `integration_debug.py` | 非生产集成调试案件 Schema（Case / StageCheck / NextAction / EvidenceLink） | 🔧 架构核心 |
| | `operation.py` | Workline operation 沙箱 Schema（pending / completed 响应） | 🔧 架构核心 |
| `repositories/` | `inbox_repository.py` | Inbox Repository（幂等键计算） | 🔧 架构核心 |
| | `outbox_repository.py` | Outbox Repository（派发状态与重试管理） | 🔧 架构核心 |
| | `session_repository.py` | Session Repository（按 business_key / trace_id / awaiting_command_id 查询） | 🔧 架构核心 |
| | `workline_repository.py` | WorkLine Repository（按 line_code 查询） | 🔧 架构核心 |
| | `smt_inbound_handoff_repository.py` | SMT 入库 handoff Repository：READY claim 候选、phase 2 re-lock、target WorkLine 串行查询和 post-claim recovery 查询 | 🔧 架构核心 |
| | `runtime_hold_repository.py` | Runtime Hold Repository（按 hold_id / workline_id / session_id / material_identity_key 查询，含 NG Return Items） | 🔧 架构核心 |
| | `__init__.py` | Repository 导出（workline / inbox / outbox / session） | 🔧 架构核心 |
| `services/` | `inbox_service.py` | Inbox Service（创建 Inbox 消息） | 🔧 架构核心 |
| | `trace_query_service.py` | TraceQueryService（只读 TRACE 聚合查询：callback / inbox / session / command / outbox / timeline） | 🔧 架构核心 |
| | `runtime_query_service.py` | RuntimeQueryService（运行监控总览、工作线/设备运行态、Trace 列表聚合） | 🔧 架构核心 |
| | `single_layer_rack_orchestration_service.py` | 单层货架 release fact 编排入口，接入 SMT handoff demand evaluate 与 demand-scoped claim | 🔧 架构核心 |
| | `smt_inbound_handoff_service.py` | SMT 入库 handoff service：release/evaluate、两阶段 claim、source-pick/terminal ledger、demand 聚合和 READY recovery | 🔧 架构核心 |
| | `runtime_hold_query_service.py` | Runtime Hold 查询 service：list_holds / get_detail / list_ng_reasons / list_ng_return_items | 🔧 架构核心 |
| | `runtime_hold_creation_service.py` | Runtime Hold 创建 service：register / attach_to_session，封装 hold 状态机初始化 | 🔧 架构核心 |
| | `runtime_hold_release_service.py` | Runtime Hold 解除 service：resolve_hold（接受 `idempotency_key`），写 outbox inbox `WES-RESOLVE_HOLD-{key}` 幂等键 | 🔧 架构核心 |
| | `integration_debug_service.py` | 非生产集成调试案件定位 service：把 Trace 证据归纳成现场可读案件（latest / lookup），含 `build_case` / `_has_case_evidence` | 🔧 架构核心 |
| | `ng_return_item_service.py` | NG Return Item service：material conflict 物料理赔单登记与查询 | 🔧 架构核心 |
| | `operation_service.py` | Workline operation 沙箱 service：sandbox_pending / sandbox_completed 聚合 | 🔧 架构核心 |
| | `__init__.py` | Service 导出（inbox_service / trace_query_service / runtime_query_service） | 🔧 架构核心 |
| `v1/` | `workline.py` | WorkLine CRUD 路由 | 🔧 架构核心 |
| | `trace.py` | Trace 详情与 Trace 列表查询路由 | 🔧 架构核心 |
| | `runtime.py` | 运行监控 overview / workline / device 只读路由 | 🔧 架构核心 |
| | `inbound_handoff.py` | SMT 入库 handoff 查询与处置路由，API 层只调用 service，不直接访问 Repository 或 DB | 🔧 架构核心 |
| | `runtime_hold.py` | Runtime Hold 路由：列表 / 详情 / NG 原因 / resolve（接受 `Idempotency-Key` Header）；query 参数补 `Query()` 校验 | 🔧 架构核心 |
| | `integration_debug.py` | 非生产集成调试案件定位路由（latest / lookup），返回现场可读案件结构 | 🔧 架构核心 |
| | `operation.py` | Workline operation 沙箱路由：pending / completed 列表查询，`Query()` 校验与 description 完整 | 🔧 架构核心 |
| | `__init__.py` | v1 路由聚合（workline / trace / runtime） | 🔧 架构核心 |
| `utils.py` | **Phase 2 burn-down 阶段 2 (C2)** wlr `utils` 全量镜像:`JsonDict` payload helpers（`ensure_dict` / `non_empty_str` / `string_value` 等）；workline 域消费侧统一从此处 import，wlr 副本由阶段 3 整目录删除统一清理 | 🔧 架构核心 |
| `trace_context.py` | **Phase 2 burn-down 阶段 2 (C2)** wlr `trace_context` 全量镜像:轻量 TRACE 传播上下文（request_id / trace_id / session / command / outbox 绑定） | 🔧 架构核心 |
| `diagnostic_support.py` | **Phase 2 burn-down 阶段 2 (C2/C5a)** wlr `diagnostic_support` 镜像:workline 域诊断支持 helpers（通过 consumers/diagnostics_bridge 聚合 re-export） | 🔧 架构核心 |
| `outbox_dispatch_support.py` | **Phase 2 burn-down 阶段 2 (C5a)** wlr `outbox_dispatch_support` 镜像:outbox 派发支持 helpers | 🔧 架构核心 |
| `runtime_services.py` | **Phase 2 burn-down 阶段 2 (C5a)** wlr services 平级镜像入口:workline 域对 runtime services 的统一 re-export 入口（避开与 `services/` 子目录同名碰撞,因此使用单数 `runtime_services.py`） | 🔧 架构核心 |
| `domain/` | **Phase 2 burn-down 阶段 2 (C3/C5b)** workline 业务概念镜像子包:`ng_reason` / `material_identity` / `plugin_manifest` / `contracts/` / `models/` / `services/` 等 | 🔧 架构核心 |
| `domain/__init__.py` | domain 子包导出（`WorklinePluginManifest` / `material_identity` / `ng_reason`） | 🔧 架构核心 |
| `domain/ng_reason.py` | NG 原因枚举与判定 helpers | 🔧 架构核心 |
| `domain/material_identity.py` | 物料身份识别 helpers（业务键派生） | 🔧 架构核心 |
| `domain/plugin_manifest.py` | WorklinePluginManifest 模型（plugin key / capabilities / role / contract_version） | 🔧 架构核心 |
| `domain/contracts/__init__.py` | domain contracts 子包导出 | 🔧 架构核心 |
| `domain/contracts/device_error_codes.py` | 设备错误码统一规范（Phase 1 Packet D 接入点） | 🔧 架构核心 |
| `domain/contracts/six_in_one.py` | `SixInOne` SSOT 业务模型（统一 payload contract） | 🔧 架构核心 |
| `domain/models/__init__.py` | domain models 子包导出 | 🔧 架构核心 |
| `domain/models/barcode_decision.py` | 扫码决策模型（与 `domain/services/barcode_decision_service` 配套） | 🔧 架构核心 |
| `domain/services/__init__.py` | domain services 子包导出 | 🔧 架构核心 |
| `domain/services/barcode_decision_service.py` | 扫码决策 service（依赖注入到 plugin_base / plugin_next / plugin_sdk normalizers） | 🔧 架构核心 |
| `plugins/` | **Phase 2 burn-down 阶段 2 (C4/C5b)** 插件开发抽象镜像子包:`plugin_base` / `plugin_context` / `session_resolver` / `null_plugin` / `plugin_next` / `run_mode` / `plugin_sdk/` | 🔧 架构核心 |
| `plugins/__init__.py` | plugins 子包导出（`null_plugin` / `RunMode`） | 🔧 架构核心 |
| `plugins/plugin_base.py` | 插件基类 + 装饰器 + Builder（核心框架；含标准化命令结果公共 helper） | 🔧 架构核心 |
| `plugins/plugin_context.py` | 插件上下文（依赖注入、运行时快照、标准化输入、诊断上下文、TraceContext） | 🔧 架构核心 |
| `plugins/session_resolver.py` | Session 解析器（workline session lookup 与 trace anchor 维护） | 🔧 架构核心 |
| `plugins/null_plugin.py` | 空实现插件（Phase 1 简化下非 opt-in 时抛错） | 🔧 架构核心 |
| `plugins/plugin_next.py` | plugin_next 装饰器 + 路由（与 plugin_base 配合） | 🔧 架构核心 |
| `plugins/run_mode.py` | 插件运行模式枚举（PRODUCTION / SANDBOX / MOCK） | 🔧 架构核心 |
| `plugins/plugin_sdk/` | **Phase 2 burn-down 阶段 2 (C5b)** 插件 SDK 镜像子包（classifiers / contracts / normalizers） | 🔧 架构核心 |
| `plugins/plugin_sdk/__init__.py` | plugin_sdk 子包导出 | 🔧 架构核心 |
| `plugins/plugin_sdk/classifiers/__init__.py` | plugin_sdk classifiers 子包导出 | 🔧 架构核心 |
| `plugins/plugin_sdk/classifiers/result_classifier.py` | 结果分类器（PRODUCTION / SANDBOX / MOCK 三态） | 🔧 架构核心 |
| `plugins/plugin_sdk/contracts/__init__.py` | plugin_sdk contracts 子包导出 | 🔧 架构核心 |
| `plugins/plugin_sdk/contracts/normalized_event.py` | 标准化事件合同 | 🔧 架构核心 |
| `plugins/plugin_sdk/contracts/normalized_external.py` | 标准化外部事件合同（WMS / RCS 接入点） | 🔧 架构核心 |
| `plugins/plugin_sdk/contracts/normalized_result.py` | 标准化结果合同 | 🔧 架构核心 |
| `plugins/plugin_sdk/contracts/runtime_config.py` | 运行时配置合同 | 🔧 架构核心 |
| `plugins/plugin_sdk/normalizers/__init__.py` | plugin_sdk normalizers 子包导出 | 🔧 架构核心 |
| `plugins/plugin_sdk/normalizers/event_mapper.py` | 事件映射 normalizer | 🔧 架构核心 |
| `plugins/plugin_sdk/normalizers/input_normalizer.py` | 输入 normalizer | 🔧 架构核心 |

**核心设计模式**：
- **Inbox 模式**：统一编排入口（设备事件、指令结果、超时、人工操作）
- **幂等性控制**：白皮书 6.3.1 节（厂商 ID 优先 + hash 备选），Phase 1 起统一为 `WES-{OPERATION_KIND}-{HASH}` 命名
- **Outbox 模式**：统一调度出口（设备指令、外部回调、状态记录）

#### 🔧 Runtime 编排层 (src/app/runtime/orchestration/) — Phase 1 + Phase 2 launch PR

> Runtime 编排层是 Phase 1 SPEC（`feature/workline-phase-1-spec`）落地的核心抽象：9 个 runtime/orchestration 实体 + BC-02 RuntimeSnapshot 合同 + H4 反注入边界 + H5 幂等键规范。设计原则：实体只承载状态，业务语义在 workline 层 Service 维护，跨域副作用通过 IdempotencyKey 串联。**Phase 2 launch PR**新增 RuntimeReconciliationFacade 作为 device/callback 域对账能力唯一入口（详见 [`runtime-ownership-map.md`](./runtime-ownership-map.md) 与 [`adr/0001-phase2-runtime-ownership.md`](./adr/0001-phase2-runtime-ownership.md)）。

| 文件 | 用途 | 分类 |
|------|------|------|
| `execution_session.py` | ExecutionSession 实体（按 `trace_id`/`session_id`/`business_key` 唯一） | 🔧 架构核心 |
| `execution_correlation.py` | ExecutionCorrelation 实体（一次性 correlation 跨实体锚点；含历史回填） | 🔧 架构核心 |
| `execution_work_item.py` | ExecutionWorkItem 实体（work item 状态机） | 🔧 架构核心 |
| `runtime_inbox.py` | RuntimeInbox 实体（持久化入口契约；H4 边界守门） | 🔧 架构核心 |
| `runtime_timeline.py` | RuntimeTimeline 实体（事件溯源） | 🔧 架构核心 |
| `runtime_hold.py` | RuntimeHold 实体（Manual / Safety E-Stop / Material Conflict 等 hold 状态） | 🔧 架构核心 |
| `runtime_intent_log.py` | RuntimeIntentLog 实体（plugin 产出 RuntimeIntent 的 ledger） | 🔧 架构核心 |
| `idempotency_key.py` | IdempotencyKey 实体（`WES-{OPERATION_KIND}-{HASH}` 唯一约束） | 🔧 架构核心 |
| `conveyor_queue_membership.py` | ConveyorQueueMembership 实体（含 `CheckConstraint` 限定 `membership_status` 取值） | 🔧 架构核心 |
| `repositories/idempotency_key_repository.py` | IdempotencyKey Repository:upsert 语义封装 (`claim_if_absent` + `get_by_identity`) | 🔧 架构核心 |
| `repositories/__init__.py` | Repository 导出 | 🔧 架构核心 |
| `services/idempotency_guard.py` | IdempotencyGuard:outbound effect 幂等闸门（`ClaimResult.NEW/MATCH` + `IdempotencyConflict`） | 🔧 架构核心 |
| `services/runtime_snapshot_assembler.py` | RuntimeSnapshotAssembler：按 BC-02 合同把 session + timeline + inbox + hold + intent log 拼装成 RuntimeSnapshot 输出 | 🔧 架构核心 |
| `services/runtime_reconciliation_service.py` | **Phase 2 launch PR 新增** RuntimeReconciliationFacade:device/callback 域对账能力唯一入口；当前委托 workline 单例（Phase 2 burn-down 阶段替换为本地实现） | 🔧 架构核心 |
| `services/__init__.py` | 服务层导出（`RuntimeReconciliationFacade` / `IdempotencyGuard` / `RuntimeSnapshotAssembler`） | 🔧 架构核心 |
| `exceptions.py` | Workline Runtime 桥接异常（`PluginNotFoundError` / `LockAcquireError`），C5b 从 `src.workline_runtime.exceptions` 镜像为桥接副本 | 🔧 架构核心 |
| `enums.py` | **Phase 2 burn-down 阶段 2 (C5a)** wlr `enums.py` 平级镜像:FailureDomain / DecisionType 等运行时契约枚举 | 🔧 架构核心 |
| `device_ordering.py` | **Phase 2 burn-down 阶段 2 (C5a)** wlr `device_ordering` 平级镜像:基于 source device + topology + role 的命令目标排序 | 🔧 架构核心 |
| `effect_result.py` | **Phase 2 burn-down 阶段 2 (C5a)** wlr `effect_result` 平级镜像:RuntimeIntent effect 落地结果模型 | 🔧 架构核心 |
| `material_target_resolver.py` | **Phase 2 burn-down 阶段 2 (C5a)** wlr `material_target_resolver` 平级镜像:物料目标解析器 | 🔧 架构核心 |
| `runtime_intent.py` | **Phase 2 burn-down 阶段 2 (C5a)** wlr `runtime_intent` 平级镜像:RuntimeIntent dataclass + 校验 | 🔧 架构核心 |
| `runtime_intent_effects.py` | **Phase 2 burn-down 阶段 2 (C5a)** wlr `runtime_intent_effects` 平级镜像:RuntimeIntent effect 落地器（保留 SMT handoff 业务接入点） | 🔧 架构核心 |
| `timeline_generator.py` | **Phase 2 burn-down 阶段 2 (C5a)** wlr `timeline_generator` 平级镜像:RuntimeTimeline 生成器 | 🔧 架构核心 |
| `business_identity_bridge.py` | **Phase 2 burn-down 阶段 2 (C5a)** wlr `business_identity` 桥接门面:Runtime business identity helpers（re-export + 自引用 wlr.utils → src.app.workline.utils） | 🔧 架构核心 |
| `events_bridge.py` | **Phase 2 burn-down 阶段 2 (C5a)** wlr `runtime_events` 桥接门面:平台保留事件 / 平台控制事件 / 平台安全事件 / 生产事件判定 | 🔧 架构核心 |
| `lock_bridge.py` | **Phase 2 burn-down 阶段 2 (C5a)** wlr `lock` 桥接门面:Redis 分布式锁 + PostgreSQL advisory 降级 | 🔧 架构核心 |
| `orchestrator_bridge.py` | **Phase 2 burn-down 阶段 2 (C5a)** wlr `orchestrator` 桥接门面:OrchestratorService 编排器核心服务（两阶段锁合并 / NullPlugin 非 opt-in 抛错） | 🔧 架构核心 |
| `resource_wait_evidence_bridge.py` | **Phase 2 burn-down 阶段 2 (C5a)** wlr `resource_wait_evidence` 桥接门面:RESOURCE_WAIT evidence helper | 🔧 架构核心 |
| `sandbox_catalog_bridge.py` | **Phase 2 burn-down 阶段 2 (C5a)** wlr `sandbox_catalog` 桥接门面:SANDBOX / MOCK 确定性样例 catalog | 🔧 架构核心 |
| `topology_bridge.py` | **Phase 2 burn-down 阶段 2 (C5a)** wlr `topology` 桥接门面:WORKLINE 运行时拓扑视图（自引用 wlr.device_ordering / wlr.plugin_manifest 已重定向到本目录 + domain 镜像） | 🔧 架构核心 |
| `consumers/` | **Phase 2 burn-down 阶段 2 (C1/C2)** wlr 单点入口子包:wlr 唯一允许的非 wlr/non-migration production consumer 入口 | 🔧 架构核心 |
| `consumers/__init__.py` | consumers 子包导出（`RuntimeInboxConsumer`） | 🔧 架构核心 |
| `consumers/runtime_inbox_consumer.py` | **Phase 2 burn-down 阶段 2 (C1)** RuntimeInboxConsumer:RuntimeInbox 单点入口门面,委托 `src.app.workline.services.inbox_batch_processor` 实现（wlr 内部既有实现,lazy import 阶段 3 前的过渡）；不实现状态机 / idempotency / RuntimeHold 推进（阶段 3 业务迁移） | 🔧 架构核心 |
| `consumers/diagnostics_bridge.py` | **Phase 2 burn-down 阶段 2 (C2)** wlr `diagnostics` 聚合 re-export:16 公开符号聚合暴露给 runtime/orchestration 域（只聚合 `__all__`,不重新 export 子模块路径） | 🔧 架构核心 |
| `__init__.py` | 模块导出（9 entity） | 🔧 架构核心 |

#### 🔧 Runtime 能力面 (src/app/runtime/) — Phase 1 新增

Runtime 顶层 capability / normalizer registry：业务能力注入（query/effect）与入站 normalizer（callback/event/result）的注册表 SSOT；与 `src/app/runtime/orchestration/` 实体层严格分层，由 import-linter `capability-isolation` contract 守护边界。

| 文件 | 用途 | 分类 |
|------|------|------|
| `src/app/runtime/capability_port_registry.py` | CapabilityPortRegistry：runtime capability 注入只暴露 query/effect port contract，R-I3b 静态扫描拒绝 wms_integration / device service、HTTP client、DTO、provider exception、service locator、WmsEventPort、DeviceEventPort、RuntimeInbox consumer（Phase 1 Packet A + Packet D） | 🔧 架构核心 |
| `src/app/runtime/inbound_normalizer_registry.py` | InboundNormalizerRegistry (Phase 1 Packet D)：与 CapabilityPortRegistry 严格分离的入站 normalizer 注册表；singleton per-port；非业务 capability 允许路径（仅 `src/app/runtime/orchestration/consumers` 通过 RuntimeCapabilityContext.get_inbound_normalizer 访问） | 🔧 架构核心 |

**关键约束**：
- **H4 反注入边界**：callback / event / result 三个入口接受 payload 时，**仅允许**白名单顶层字段（`callback_type` / `data` / `trace_id` / `event_id` / `causation_id` / `source_system` / `source_version` / `occurred_at` / `request_id` / `timestamp` / `signature`）；业务追溯字段（如 `provider_code` / `source_event_id`）必须放入 `data` 内。外部回调 (`/callback/external`) 顶层白名单额外覆盖 WMS/RCS 协议业务元数据（`dispatch_key` / `status` / `exchange_*` / `rack_*` / `operation_key` / `operation_type` / `position_code` / `source_position_code` / `target_position_code` / `target_position_role` / `task_type` / `workline_code` / `bin_mounts` / `material` / `actions` / `sequence_no` / `source` / `station` / `target` / `active_bin_rack` / `error_code` / `error_message`）与 AGV 执行回执（`command_code` / `result` / `finish_time` / `device_code` / `task_status` / `reason_code` / `reason_message`）。H4 的真正安全屏障是子层 `_FORBIDDEN_PARAM_KEYS` 递归扫描（阻断 `plc_address` / `coordinate` 等设备控制字段），顶层白名单扩展不削弱 H4 安全语义。
- **H5 幂等键命名**：`WES-{OPERATION_KIND}-{HASH}`，唯一约束落在 `idempotency_keys` 表。

**相关文档**：
- 运行时语义 SSOT：`docs/business/workline_business_data_event_flow_spec.md` v0.1
- 架构设计：`docs/business/workline_plugin_architecture_design.md` v3.2
- SMT 分拣入库工作流：`docs/business/smt_sorter_inbound_workflow_guide.md`
- SMT handoff/manifest 闭环合同：`docs/superpowers/specs/2026-06-16-smt-sorting-inbound-manifest-flow-spec.md`
- SMT handoff/manifest 实施记录：`docs/superpowers/plans/2026-06-16-smt-sorting-inbound-manifest-flow.md`
- 历史 SMT 粗分机资料：`docs/archive/legacy-smt-classifier/`

#### 🔧 作业线运行时 (src/workline_runtime/) — Phase 2 launch PR 标记:wlr allowlist 严格型,Phase 2 T3 整目录删除

> 插件化编排核心框架，提供装饰器驱动的声明式插件开发。**Phase 2 launch PR 后**:`src.workline_runtime` 在生产代码中仅允许以下入口直接 import — wlr 自身 + `src/app/runtime/orchestration/consumers/` + `tests/` + `migrations/`;其余 `src/` 任何 production code 都不允许 import wlr(由 `scripts/architecture-guardrails.sh` `R-WLR` 规则强制,28 处历史跨域 import 已纳入 `scripts/architecture-guardrails.allowlist` 严格型条目,legacy_entry_id 格式 `legacy:<path>:<file>#R-WLR`)。**Phase 2 T3** 整目录删除,届时本索引表同步移除。

| 文件 | 用途 | 分类 |
|------|------|------|
| `plugin_base.py` | 插件基类 + 装饰器 + Builder（核心框架；含标准化命令结果公共 helper，如 envelope / failure 解析） | 🔧 架构核心 |
| `payloads.py` | 共享 Payload 定义（Pydantic 模型；`SixInOne` 已迁至 `contracts/` 统一维护） | 🔄 常用功能 |
| `contracts/` | 运行时统一合同模型（`SixInOne` SSOT、标准设备错误码） | 🔧 架构核心 |
| `null_plugin.py` | 空实现插件（测试回退） | 🎯 示例代码 |
| `trace_context.py` | 轻量 TRACE 传播上下文（request_id / trace_id / session / command / outbox 绑定） | 🔧 架构核心 |
| `plugin_context.py` | 插件上下文（依赖注入、运行时快照、标准化输入、诊断上下文、TraceContext） | 🔧 架构核心 |
| `types.py` | 插件运行时类型（CommandIntent, WaitIntent 等） | 🔧 架构核心 |
| `device_target_resolver.py` | 基于 source device + topology + role 解析命令目标设备 | 🔧 架构核心 |
| `orchestrator.py` | 编排器服务（READ 阶段编排、插件执行、错误标准化） | 🔧 架构核心 |
| `enums.py` | 运行时枚举（FailureCode, DecisionType 等） | 🔄 常用功能 |
| `plugin_sdk/` | 插件 SDK（标准化输入、运行时配置、分类器） | 🔧 架构核心 |
| `diagnostics/` | 统一诊断模型、错误码、软件/硬件问题分类 | 🔧 架构核心 |
| `runtime_intent_effects.py` | RuntimeIntent effect 落地器；SMT 分拣入库在 source-pick/terminal success 后通过 handoff service 写 ledger | 🔧 架构核心 |

#### 🧩 作业线插件实现 (src/workline_plugins/)

| 文件 | 用途 | 分类 |
|------|------|------|
| `rough_sorter/plugin.py` | 粗分机工作线插件：按真实物理流程产出 RuntimeIntent，覆盖扫码、测量、WMS 校验、搬运、入箱、货架补给和 NG 闭环 | 🔄 常用功能 |
| `rough_sorter/contract.py` | 粗分机插件合同：插件 key、事件/命令/phase/角色常量、命令 payload builder、业务键解析和结果分类 | 🔄 常用功能 |
| `rough_sorter/context.py` | 粗分机 Session context 快照模型 | 🔄 常用功能 |
| `smt_sorting_inbound/plugin.py` | SMT 分拣入库插件 manifest 与 handler 入口；manifest 只声明 source 单层货架位和 target 五层货架位 | 🔧 架构核心 |
| `smt_sorting_inbound/context.py` | SMT 分拣入库 typed context，含 `source_pick_request`、扫码平台状态和当前物料快照 helper | 🔧 架构核心 |
| `smt_sorting_inbound/flow_service.py` | SMT 分拣入库 RuntimeIntent 业务流，产生命令、context/resource intents 和 terminal ledger marker | 🔧 架构核心 |
| `smt_sorting_inbound/constants.py` | SMT 分拣入库插件 key、合同版本、角色、命令、事件和 phase 常量 | 🔄 常用功能 |

**插件开发文档**：
- **插件开发指南**：`docs/plugin_development_guide.md` 📖 必读文档
- **插件模板说明**：`docs/templates/workline_plugin/README.md` 📖 必读文档
- **RuntimeIntent 架构设计**：`docs/business/workline_plugin_architecture_design.md` 📖 必读文档
- **Runtime 工作流指南**：`docs/business/workline_runtime_workflow_guide.md` 📖 必读文档
- **旧 PluginResult 资料归档**：`docs/archive/legacy-plugin-result/README.md` 📚 历史对照

**核心特性**：
- **装饰器驱动**：`@on_event()`, `@on_command()` 类型化路由
- **Pydantic 自动验证**：Payload 自动解析和类型安全
- **RuntimeIntent 输出**：插件只声明上下文更新、命令、等待、业务 NG、完成或阻断意图
- **运行时拥有副作用**：拓扑解析、Session 生命周期、命令/outbox、等待状态和终态写入集中在 Runtime
- **无插件状态机**：不再使用 per-plugin `state_machine.py`、`transitions`、`PluginResultBuilder` 或 `plugin_state`

#### 🔔 回调模块 (src/app/callback/)

外部系统回调处理

| 目录 | 文件 | 用途 | 分类 |
|------|------|------|------|
| `models/` | `callback_log.py` | 回调入口日志模型（ingress audit；记录 request_id、ingress_outcome、failure_stage，不复制 workflow trace 事实） | 🔧 架构核心 |
| | `event.py` | 回调事件模型 | 🔧 架构核心 |
| `repositories/` | `callback_log_repository.py` | 回调日志仓库 | 🔧 架构核心 |
| `services/` | `callback_service.py` | 回调处理服务 | 🔧 架构核心 |
| `v1/` | `callback.py` | 回调 API 路由（入口校验、early return logging、request_id 入口锚点） | 🔧 架构核心 |

#### 🔌 WMS 能力面 ports (src/app/wms_integration/ports/) — Phase 1 新增

7 个 WMS 目标 port Protocol + typed data classes（Phase 1 CEO-001 完成 7/7）：
- 3 query port: MasterData / InventoryQuery / ReconciliationQuery
- 1 effect port: InventoryTransaction
- 1 effect port: Fulfillment
- 1 event normalizer port: Event（含 InboundEventPort 基协议 + WmsEventPort 4 normalizer）
- 1 document port: Document

| 目录 | 文件 | 用途 | 分类 |
|------|------|------|------|
| `ports/` | `master_data.py` | WmsMasterDataPort Protocol + 6 typed data classes（Phase 1 Packet B） | 🔧 架构核心 |
| | `inventory_query.py` | WmsInventoryQueryPort Protocol + 5 typed data classes（Phase 1 Packet B） | 🔧 架构核心 |
| | `inventory_transaction.py` | WmsInventoryTransactionPort Protocol + 3 typed data classes（Phase 1 Packet B） | 🔧 架构核心 |
| | `document.py` | WmsDocumentPort Protocol + 6 typed data classes（Phase 1 Packet D） | 🔧 架构核心 |
| | `fulfillment.py` | WmsFulfillmentPort Protocol + 2 typed data classes（Phase 1 Packet D） | 🔧 架构核心 |
| | `event.py` | InboundEventPort 基协议 + WmsEventPort Protocol + 5 typed data classes（Phase 1 Packet D） | 🔧 架构核心 |
| | `reconciliation_query.py` | WmsReconciliationQueryPort Protocol + 1 typed data class（Phase 1 Packet D） | 🔧 架构核心 |

#### 🔗 WMS 对接辅助域 (src/app/wms_integration/)

WMS Anti-Corruption Layer，统一同步 WMS 调用、异步 WMS/RCS 派发合同、回调标准化、短时查询缓存、DB-backed 熔断、脱敏证据留痕和调用方错误合同。该域不提供公开 `/api/v1/wms/...` 代理接口，也不接管库存主账、SystemOutbox 派发或 RuntimeHold 创建。

| 目录 | 文件 | 用途 | 分类 |
|------|------|------|------|
| `models/` | `evidence.py` | WMS 调用证据模型：脱敏快照、canonical hash、trace/correlation 字段 | 🔧 架构核心 |
| | `circuit_breaker.py` | WMS 熔断状态模型：operation 级共享失败计数、OPEN/HALF_OPEN/CLOSED 状态 | 🔧 架构核心 |
| | `ports.py` | WMS typed ports 请求/响应合同模型 | 🔧 架构核心 |
| `repositories/` | `evidence_repository.py` | WMS evidence Repository | 🔧 架构核心 |
| | `circuit_breaker_repository.py` | WMS circuit breaker state Repository | 🔧 架构核心 |
| `services/` | `http_client.py` | 同步 WMS HTTP client，暴露 typed exception hierarchy | 🔧 架构核心 |
| | `typed_ports.py` | 业务域可调用的 WMS typed ports 门面 | 🔧 架构核心 |
| | `evidence_service.py` | WMS evidence 脱敏、hash 和记录服务 | 🔧 架构核心 |
| | `circuit_breaker_service.py` | DB-backed WMS 熔断状态转换服务 | 🔧 架构核心 |
| | `callback_normalizer.py` | WMS/RCS 回调最小包络校验和字段标准化 | 🔧 架构核心 |
| | `transport_contract.py` | rack/handling WMS/RCS 外部派发 payload 合同辅助 | 🔧 架构核心 |
| | `cache.py` | WMS read-only 查询短 TTL 缓存封装 | 🔄 常用功能 |
| | `endpoint_config.py` | WMS endpoint operation path、timeout 和 operation name 配置 | 🔧 架构核心 |
| | `redaction.py` | WMS request/response 脱敏规则 | 🔧 架构核心 |
| | `exceptions.py` | WMS typed errors：timeout、5xx、business reject、circuit-open | 🔧 架构核心 |

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
| `benchmark/` | 性能基准测试 | 📚 参考资料 |
| `load/` | 负载测试（Locust） | 📚 参考资料 |
| `resilience/` | 弹性测试（Redis 重连、降级） | 📚参考资料 |
| `e2e/` | E2E 测试（流水线料盘搬运流程） | 🔄 常用功能 |
| `workline_runtime/` | 作业线运行时测试（纯逻辑测试） | 🔧 架构核心 |
| `wms_integration/` | WMS 对接辅助域测试（client、typed ports、evidence、breaker、cache、callback normalizer、caller contract） | 🔧 架构核心 |
| `architecture/` | 架构守卫测试（import-linter 合同 + Phase 2 burn-down 阶段 2 mirror 测试） | 🔧 架构核心 |
| `runtime/orchestration/` | Runtime orchestration 测试（RuntimeInboxConsumer 单点入口测试） | 🔧 架构核心 |
| `contracts/workline/` | Phase 2 launch PR behavior contract gap TDD 同步测试 | 🔧 架构核心 |
| `workline_plugins/` | 作业线插件测试（rough_sorter / smt_sorting_inbound / barcode_decision 等） | 🔧 架构核心 |
| `integration/workline_runtime/` | 作业线运行时 PostgreSQL 集成测试 | 🔧 架构核心 |

**Phase 2 burn-down 阶段 2 mirror/guardrail 测试文件**（`tests/architecture/`）:

| 文件 | 用途 | 分类 |
|------|------|------|
| `architecture/test_wlr_import_guardrail.py` | R-WLR wlr production import 守卫:wlr 在 `src.workline_runtime.*` 生产代码中仅允许 wlr 自身 + `src/app/runtime/orchestration/consumers/` + `tests/` + `migrations/` 入口直接 import | 🔧 架构核心 |
| `architecture/test_orchestration_bridges_mirror.py` | **Phase 2 burn-down 阶段 2 (C5a)** orchestration bridges 镜像一致性测试:runtime/orchestration 下的 7 个 bridge 文件（business_identity / events / lock / orchestrator / resource_wait_evidence / sandbox_catalog / topology）与 wlr 源文件签名一致 | 🔧 架构核心 |
| `architecture/test_plugin_mirrors_mirror.py` | **Phase 2 burn-down 阶段 2 (C5b)** plugin mirrors 镜像一致性测试:plugin_base / plugin_context / null_plugin / plugin_next / plugin_sdk 等镜像与 wlr 源文件签名一致 | 🔧 架构核心 |
| `architecture/test_workline_compat_mirror.py` | **Phase 2 burn-down 阶段 2 (C2)** workline compat 镜像一致性测试:`utils.py` / `trace_context.py` / `diagnostic_support.py` / `outbox_dispatch_support.py` / `runtime_services.py` 镜像与 wlr 源文件签名一致 | 🔧 架构核心 |
| `architecture/test_workline_domain_mirror.py` | **Phase 2 burn-down 阶段 2 (C3)** workline domain 镜像一致性测试:`ng_reason` / `material_identity` / `plugin_manifest` / `domain/contracts/` / `domain/models/` / `domain/services/` 镜像与 wlr 源文件签名一致 | 🔧 架构核心 |
| `architecture/test_workline_plugins_mirror.py` | **Phase 2 burn-down 阶段 2 (C5b)** workline plugins 镜像一致性测试:`plugins/` 子包（plugin_base / plugin_context / session_resolver / null_plugin / plugin_next / run_mode / plugin_sdk/）镜像与 wlr 源文件签名一致 | 🔧 架构核心 |

**Runtime orchestration 测试文件**（`tests/runtime/orchestration/`）:

| 文件 | 用途 | 分类 |
|------|------|------|
| `runtime/orchestration/test_runtime_inbox_consumer.py` | **Phase 2 burn-down 阶段 2 (C1)** RuntimeInboxConsumer 单点入口测试:wlr 唯一允许的非 wlr/non-migration production consumer 入口（consume / consume_sync / list_consumed_ids / inbound normalizer routing） | 🔧 架构核心 |

**Workline Runtime 测试文件**：

| 文件 | 用途 | 分类 |
|------|------|------|
| `workline_runtime/test_enums.py` | 枚举类单元测试（InboxKind, Status 等） | 🔧 架构核心 |
| `workline_runtime/test_inbox_service.py` | Inbox Service 幂等键计算测试 | 🔧 架构核心 |
| `workline_runtime/test_smt_sorting_inbound_plugin.py` | SMT 分拣入库插件 manifest、source-pick、target/ng terminal intent 合同测试 | 🔧 架构核心 |
| `workline_runtime/test_smt_sorting_inbound_context.py` | SMT 分拣入库 typed context 和 `source_pick_request` JSON-safe 测试 | 🔧 架构核心 |
| `workline_runtime/test_smt_inbound_handoff_claim.py` | SMT handoff 两阶段 claim、target WorkLine 串行保护和 release fact claim 入口测试 | 🔧 架构核心 |
| `workline_runtime/test_smt_inbound_handoff_recovery.py` | SMT handoff due scan、post-claim recovery、source-pick ledger 和 READY claim fallback 测试 | 🔧 架构核心 |
| `workline_runtime/test_smt_inbound_handoff_celery.py` | SMT handoff Celery recovery task 注册、参数和 summary 合同测试 | 🔧 架构核心 |
| `workline_runtime/test_runtime_intent_effects.py` | RuntimeIntent effects 回归测试，覆盖 SMT source-pick、target/ng terminal ledger 和 no-double-claim | 🔧 架构核心 |

**Phase 2 behavior contract 测试文件**（`tests/contracts/workline/`,launch PR commit `8602c33b` 落地 8 个 TDD 同步 contract，burn-down 安全网）：

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

**E2E 测试文件**：

| 文件 | 用途 | 分类 |
|------|------|------|
| `e2e/__init__.py` | E2E 测试模块导出 | 🔧 架构核心 |
| `e2e/test_conveyor_robot_arm.py` | 流水线料盘搬运 E2E 测试（使用 ECS Mock 事件 API） | 🔄 常用功能 |
| `integration/workline_runtime/test_smt_inbound_handoff_e2e.py` | SMT handoff release-to-terminal、多 item 串行和 terminal replay E2E 回归 | 🔧 架构核心 |
| `integration/workline_runtime/test_smt_inbound_handoff_claim_postgres.py` | PostgreSQL READY claim / SKIP LOCKED 并发保护集成测试 | 🔧 架构核心 |
| `integration/workline_runtime/test_smt_inbound_handoff_recovery_postgres.py` | PostgreSQL post-claim recovery 和索引执行计划保护测试 | 🔧 架构核心 |

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
- `POST /api/v1/mock/event` - 手动上报设备事件，替代旧 `/api/v1/sensor/trigger`
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

**Phase 1 SPEC 相关迁移**（`feature/workline-phase-1-spec`）：

| 文件 | 用途 | 分类 |
|------|------|------|
| `versions/20260626_1200_0e9de1e6c7e3_phase1_device_fk_ring_dissolve.py` | Phase 0→1 FK ring dissolve：动态发现 pg_constraint 名称后 drop device ↔ workline_sessions 循环外键，保留字段用于业务追溯 | 🔧 架构核心 |
| `versions/20260626_1719_f04718a3f04f_add_remaining_runtime_orchestration_.py` | 新增 9 个 runtime/orchestration 实体表（execution_sessions / execution_correlations / execution_work_items / runtime_inboxes / runtime_timelines / runtime_holds / runtime_intent_logs / idempotency_keys / conveyor_queue_memberships），含 `CheckConstraint` 限定 `membership_status` 取值；ExecutionCorrelation 历史回填默认列 | 🔧 架构核心 |

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
├───Jenkinsfile               # CI/CD 配置
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
    │   ├───admin/            # 后台管理
    │   ├───api_auth/         # API 认证
    │   ├───auth/             # 用户认证
    │   ├───sys/              # 系统管理
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

### 7.2 文档维护

**同步工具**: Serena MCP

**更新触发条件**:
1. 新增/删除核心模块
2. 架构重大调整
3. 新增关键文件/目录

---

**文档结束**

*本文档由 Claude Code + Serena MCP 生成和维护*
