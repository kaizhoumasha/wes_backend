# P9 WES Backend 项目文件索引

> 本索引只描述当前工作区的文件职责；历史版本变更由 Git 历史和项目外归档保留，不在当前索引重复维护。

**最后更新**: 2026年8月3日
**同步状态**: ✅ 当前架构真源、实施入口、有效合同与 implementation baseline 已分层；被取代设计不再位于项目内

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
| `docs/architecture/SRS.md` | 产品范围、参与方职责以及功能与非功能需求真源 | 📖 必读文档 |
| `docs/architecture/adr/2026-05-13-wes-wms-rcs-resource-boundary.md` | WES/WMS/RCS 运行时资源、库存权责和回调入口 ADR | 📖 必读文档 |
| `docs/architecture/adr/2026-05-26-wms-integration-domain.md` | WMS 对接辅助域 ADR：反腐层边界、证据留痕、熔断和调用方合同 | 📖 必读文档 |
| `docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md` | WES 最小执行架构最终真源 | 📖 必读文档 |
| `docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md` | 九阶段架构收敛总控 | 📖 必读文档 |
| `docs/plugin_development_guide.md` | 最小插件 SPI、封闭 Decision 和独立插件包交付指南 | 📖 必读文档 |
| `docs/superpowers/README.md` | 文档生命周期、保留判定与项目外归档索引 | 📖 必读文档 |
| `docs/contracts/observability-contract.md` | Runtime 稳定观测合同：callback / RuntimeInbox / intent / device command / WMS breaker 的 span、metric、log event 和 attribute 口径 | 📖 必读文档 |
| `docs/contracts/wms-northbound-interaction-contract.md` | WMS 北向 35 项 Operation 冻结合同：19 QUERY、9 项同步 EFFECT、7 项 ACK/status EFFECT | 📖 必读文档 |
| `docs/business/wms_full_factory_operation_blueprint.md` | WMS Gateway 边界、粗分/分拣业务冻结和接入验收要点 | 📖 必读文档 |
| `docs/superpowers/plans/2026-08-03-wes-wms-thin-access-convergence.md` | WMS 薄接入、目标 Mock、类型化 Client、QUERY 切换和验收边界 | 📖 必读文档 |
| `docs/contracts/runtime-toggle-governance.md` | Runtime toggle 治理合同：owner、expiry、scope、default、rollback、test_matrix 与安全边界 | 📖 必读文档 |
| `docs/integration/wms_caller_checklist.md` | WMS 同步调用方接入 checklist：RuntimeHold/诊断、错误处理和证据传播要求 | 📖 必读文档 |
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

> 📐 **WES 最小执行架构当前真源**：
>
> - 顶层 SPEC：[`docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md`](../superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md)
> - 九阶段总控：[`docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md`](../superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md)
> - 历史资料统一通过 [`docs/superpowers/README.md`](../superpowers/README.md) 查询，不属于当前架构、合同或实施真源。
>
> 当前实现与顶层 SPEC 不一致时，以顶层 SPEC 和经批准的阶段详细计划为准。

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
- **幂等性控制**：当前实现优先使用来源系统稳定 ID，并以 payload digest 识别同键冲突；最终规则以顶层 SPEC 为准
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

Runtime 顶层 capability / normalizer registry：收敛前 `implementation_baseline` 的业务能力注入与入站 normalizer 注册点；与 `src/app/runtime/orchestration/` 实体层的现有分层由 import-linter `capability-isolation` contract 守护。该注册体系不是目标架构真源。

| 文件 | 用途 | 分类 |
|------|------|------|
| `src/app/runtime/capability_port_registry.py` | CapabilityPortRegistry：runtime capability 注入只暴露 query/effect port contract；静态扫描拒绝 provider implementation、HTTP client、DTO、service locator 与入站 normalizer | 🔧 架构核心 |
| `src/app/runtime/inbound_normalizer_registry.py` | InboundNormalizerRegistry：与 CapabilityPortRegistry 严格分离的入站 normalizer 注册表；singleton per-port；仅正式 consumer 通过 RuntimeCapabilityContext 访问 | 🔧 架构核心 |

**关键约束**：

- **H4 反注入边界**：callback / event / result 三个入口接受 payload 时，**仅允许**白名单顶层字段（`callback_type` / `data` / `trace_id` / `event_id` / `causation_id` / `source_system` / `source_version` / `occurred_at` / `request_id` / `timestamp` / `signature`）；业务追溯字段（如 `provider_code` / `source_event_id`）必须放入 `data` 内。外部回调 (`/callback/external`) 顶层白名单额外覆盖 WMS/RCS 协议业务元数据（`dispatch_key` / `status` / `exchange_*` / `rack_*` / `operation_key` / `operation_type` / `position_code` / `source_position_code` / `target_position_code` / `target_position_role` / `task_type` / `workline_code` / `bin_mounts` / `material` / `actions` / `sequence_no` / `source` / `station` / `target` / `active_bin_rack` / `error_code` / `error_message`）与 AGV 执行回执（`command_code` / `result` / `finish_time` / `device_code` / `task_status` / `reason_code` / `reason_message`）。H4 的真正安全屏障是子层 `_FORBIDDEN_PARAM_KEYS` 递归扫描（阻断 `plc_address` / `coordinate` 等设备控制字段），顶层白名单扩展不削弱 H4 安全语义。
- **H5 幂等键命名**：`WES-{OPERATION_KIND}-{HASH}`，唯一约束落在 `idempotency_keys` 表。

**相关文档**：

- 目标架构真源：`docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md`
- 直接替换总控：`docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md`
- 插件交付指南：`docs/plugin_development_guide.md`
- 历史资料统一索引：`docs/superpowers/README.md`

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
- **最小执行架构**：`docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md` 📖 必读文档
- **历史设计索引**：`docs/superpowers/README.md` 📚 统一归档入口

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

19 项 QUERY 统一由 `WmsQueryExecutionPort` 执行；各领域模块只声明 operation-specific typed request/result。
EFFECT 与事件 normalizer 继续使用各自 typed port。

| 目录 | 文件 | 用途 | 分类 |
|------|------|------|------|
| `ports/` | `query_execution.py` | 19 项 registry QUERY 的唯一泛型执行 Port | 🔧 架构核心 |
| | `effect_preparation.py` | 16 项 registry EFFECT 的唯一事务内准备 Port | 🔧 架构核心 |
| | `master_data_operations.py` | 主数据 QUERY operation-specific request/result 与 Definition | 🔧 架构核心 |
| | `inventory_operations.py` | 库存 QUERY operation-specific request/result 与 Definition | 🔧 架构核心 |
| | `document_operations.py` | Q08–Q13/Q19 operation-specific request/result 与 Definition | 🔧 架构核心 |
| | `fulfillment_operations.py` | E07–E16 operation-specific request/result、ACK 与批次收敛合同 | 🔧 架构核心 |
| | `event.py` | InboundEventPort 基协议 + WmsEventPort Protocol + 5 typed data classes | 🔧 架构核心 |
| | `reconciliation_operations.py` | 对账 QUERY operation-specific request/result 与 Definition | 🔧 架构核心 |

#### 🔗 WMS 对接辅助域 (src/app/wms_integration/)

WMS Gateway 子系统：静态 registry 冻结 19 项 QUERY、9 项同步 EFFECT 和 7 项 ACK/status EFFECT；一个部署只允许一个 active Provider profile。该域负责 endpoint 编译、transport、evidence、状态归约与发布证明，不负责 WorkLine 编排、库存主账、RCS 调度或设备防呆，也不提供公开 `/api/v1/wms/...` 代理接口。

| 目录 | 文件 | 用途 | 分类 |
|------|------|------|------|
| 根目录 | `operation_contract.py` / `operation_registry.py` | 35 项 operation 的当前静态 registry（`implementation_baseline`）；目标薄端口将直接替换该注册体系 | 🔧 架构核心 |
| | `provider_profile.py` / `endpoint_compiler.py` / `provider_readiness.py` | 单 active Provider 的 profile 校验、参数化 endpoint 编译与启动准入 | 🔧 架构核心 |
| | `query_executor.py` / `query_runtime.py` / `query_projection.py` | QUERY 的预算、分页、GET/Q19 POST 投影、受限 transport 与 typed outcome | 🔧 架构核心 |
| | `effect_preparation_runtime.py` / `effect_runtime.py` / `effect_lane_runtime.py` | 同步数据 EFFECT 与异步 AGV/CTU 履约 EFFECT 的准备、结果归约和 lane 隔离 | 🔧 架构核心 |
| | `deployment_attestation.py` | 部署 profile、合同 digest 和运行角色的发布前证明 | 🔧 架构核心 |
| `models/` | `evidence.py` / `circuit_breaker.py` / `ports.py` | WMS 调用证据、熔断状态与共享 Port 模型 | 🔧 架构核心 |
| `ports/` | `*_operations.py` / `query_execution.py` / `effect_preparation.py` / `effect_status.py` | operation-specific typed Port 与统一 QUERY/EFFECT 执行合同 | 🔧 架构核心 |
| `repositories/` | `evidence_repository.py` / `circuit_breaker_repository.py` | 脱敏 evidence 与熔断状态持久化 | 🔧 架构核心 |
| `services/` | `http_transport.py` / `callback_normalizer.py` / `wms_event_normalizer.py` | 共享 HTTP transport、callback hint 与普通 WMS 事件标准化 | 🔧 架构核心 |
| `evidence/` | `envelope.py` / `catalog.py` | 外部事实 evidence envelope、hash 与允许字段目录 | 🔧 架构核心 |

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
| `integration/test_base_repository_crud.py` | BaseRepository 真实数据库 CRUD 核心 HEAVY 测试（显式运行） | 🔧 架构核心 |
| `database/test_base_repository_hooks.py` | Repository HookManager 与 BaseRepository wiring 轻量测试 | 🔧 架构核心 |
| `integration/test_base_repository_hooks.py` | BaseRepository create/update/delete Hook 数据库 CRUD 核心 HEAVY 测试（显式运行） | 🔧 架构核心 |
| `test_base_repository_error_handling.py` | Repository 错误处理测试 | 🔧 架构核心 |
| `integration/test_optimistic_lock.py` | 乐观锁核心 HEAVY / 数据库并发测试（显式运行） | 🔧 架构核心 |
| `integration/test_system_outbox_repository.py` | SystemOutbox 持久化、事务与 lease 恢复核心 HEAVY 测试（显式运行） | 🔧 架构核心 |
| `integration/test_runtime_intent_log_effect_repository.py` | RuntimeIntentLog effect ledger 持久化与幂等核心 HEAVY 测试（显式运行） | 🔧 架构核心 |
| `integration/test_callback_external_payload_limit.py` | Callback 真实 writer 的 RuntimeInbox payload bytes、零落库与 HTTP 413 核心 HEAVY 测试（显式运行） | 🔧 架构核心 |
| `integration/test_wms_event_runtime_inbox_idempotency.py` | 普通 WMS event 的 RuntimeInbox 数据库幂等、跨类型冲突与 correlation 核心 HEAVY 测试（显式运行） | 🔧 架构核心 |
| `integration/test_system_outbox_dispatch_concurrency.py` | SystemOutbox 公平桶、背压、lease fencing 与真实 Repository 核心 HEAVY 测试（显式运行） | 🔧 架构核心 |
| `integration/test_device_runtime_projection_writer_service.py` | DeviceRuntimeProjection writer/repository 持久 upsert、唯一冲突重读与 DeviceService 同事务同步核心 HEAVY 测试（显式运行） | 🔧 架构核心 |
| `integration/test_command_result_correlation_authority.py` | DeviceCommand 结果服从固定 ExecutionCorrelation 的数据库核心 HEAVY 测试（显式运行） | 🔧 架构核心 |
| `resilience/test_wms_circuit_breaker.py` | DB-backed WMS breaker 时序与恢复核心 HEAVY 测试（显式运行） | 🔧 架构核心 |
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
| `integration/` | 显式运行的核心集成 HEAVY 测试（Repository/Outbox 持久化、数据库领取、幂等冲突、多组件闭环） | 📚 参考资料 |
| `load/` | 显式运行的负载/基准测试（Locust + runtime benchmark gate 四场景） | 📚 参考资料 |
| `resilience/` | 显式运行的弹性/恢复 HEAVY 测试（breaker 时序、重试/死信、崩溃恢复、Redis 重连与降级） | 📚参考资料 |
| `e2e/` | E2E 测试（流水线料盘搬运流程） | 🔄 常用功能 |
| `workline_runtime/` | Runtime capability、投影、对账与 material-flow 纯逻辑回归 | 🔧 架构核心 |
| `wms_integration/` | WMS 对接辅助域轻量测试（typed QUERY transport、client、typed effects、evidence、callback normalizer、caller contract） | 🔧 架构核心 |
| `architecture/` | 架构守卫测试（import-linter 合同 + runtime public-surface / boundary / prohibited-import guardrail） | 🔧 架构核心 |
| `runtime/orchestration/` | Runtime orchestration 轻量单元/合同测试（不触发真实数据库领取与故障恢复） | 🔧 架构核心 |
| `contracts/` | 跨模块合同测试（workline behavior contract + runtime ops contract 文档存在性） | 🔧 架构核心 |
| `contracts/workline/` | Runtime boundary behavior contract 测试 | 🔧 架构核心 |
| `workline/` | WorkLine 配置域测试（manifest activation validator、plane read model） | 🔧 架构核心 |
| `integration/workline_capabilities/` | generated plugin、binding、System Capability 与 PostgreSQL 性能闭环 | 🔧 架构核心 |

**runtime boundary / guardrail 测试文件**（`tests/architecture/`）:

| 文件 | 用途 | 分类 |
|------|------|------|
| `architecture/test_runtime_status_owner_guardrail.py` | Runtime status ownership 守卫：运行态写入集中在 runtime/orchestration projection，WorkLine/material-flow 只通过 snapshot/readiness 读取 | 🔧 架构核心 |

**RuntimeInbox 核心测试文件**：

| 文件 | 用途 | 分类 |
|------|------|------|
| `integration/test_runtime_inbox_consumer_service.py` | RuntimeInboxService 数据库幂等接收、唯一冲突重读、payload conflict 409 和人工重放审计 HEAVY 测试 | 🔧 架构核心 |
| `integration/test_runtime_inbox_service_internal_events.py` | 内部事件、设备事件与命令结果的数据库持久化、幂等与关联校验 HEAVY 测试 | 🔧 架构核心 |
| `integration/test_callback_external_payload_limit.py` | Callback external/result/event 真实 writer 的 payload bytes、HTTP 413、日志降级与零落库 HEAVY 测试 | 🔧 架构核心 |
| `integration/test_wms_event_runtime_inbox_idempotency.py` | WMS source event 数据库幂等、跨事件类型冲突、correlation 与 ACK 持久化 HEAVY 测试 | 🔧 架构核心 |
| `integration/test_runtime_inbox_claim_repository.py` | canonical repository FIFO claim、lease、fencing、命名空间与 SLI snapshot HEAVY 测试 | 🔧 架构核心 |
| `integration/test_runtime_inbox_repository_consumers.py` | query/trace/reconciliation 等消费者读取 RuntimeInbox repository 的数据库合同 HEAVY 测试 | 🔧 架构核心 |
| `resilience/test_runtime_inbox_failure_state_machine.py` | RuntimeInbox 重试预算、退避、死信、lease fencing 与故障恢复 HEAVY 测试 | 🔧 架构核心 |
| `integration/test_runtime_inbox_processing_postgresql.py` | 真实 PostgreSQL producer → claim → 三阶段 processor → effects → terminal 闭环 | 🔧 架构核心 |
| `integration/test_runtime_inbox_migration_postgresql.py` | Revision A/B 与 A→parent→A 毫秒值保留回环 | 🔧 架构核心 |
| `resilience/test_runtime_inbox_crash_recovery_postgresql.py` | claim 后崩溃、write-back 后终态前崩溃的 lease/fencing/事务恢复 | 🔧 架构核心 |
| `load/test_runtime_inbox_claim_benchmark.py` | 1000 条混合 backlog、4 worker 真实 PostgreSQL claim 性能门禁 | 📚 参考资料 |
| `runtime/orchestration/test_idempotency_audit_contract.py` | IdempotencyGuard conflict audit payload 测试 | 🔧 架构核心 |
| `runtime/orchestration/test_runtime_recovery_policies.py` | RuntimeInbox backpressure 与 DeviceCommand lease 恢复策略测试 | 🔧 架构核心 |

**RuntimeInbox 严格验收与文档门禁**：

| 文件 | 用途 | 分类 |
|------|------|------|
| `tests/deployment/test_runtime_inbox_postgresql_acceptance_ci.py` | CI 隔离数据库、runner 顺序、失败非零、artifact/cleanup 与 commit 绑定合同 | 🔧 架构核心 |
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
| `workline/test_manifest_activation_validator.py` | WorkLine manifest 激活前 queue/device/capability 引用 blocker 测试 | 🔧 架构核心 |
| `workline/test_plane_read_model.py` | WorkLine plane scene/snapshot 读模型 schema 和 queue 节点派生测试 | 🔧 架构核心 |

**Workline Runtime 测试文件**（当前纯逻辑集合）：

| 文件 | 用途 | 分类 |
|------|------|------|
| `workline_runtime/test_runtime_inbox_projection_query_contract.py` | RuntimeInbox 投影查询、状态统计和 audit-only 边界 | 🔧 架构核心 |
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
| `contracts/workline/test_manual_replay_audit_contract.py` | BC-XX DEAD_LETTER 终态不可就地重置 + 重放新建 inbox + H5 审计 (actor + reason 必填) + causation_id 因果链 | 🔧 架构核心 |

**WMS 对接辅助域测试文件**：

| 文件 | 用途 | 分类 |
|------|------|------|
| `wms_integration/test_wms_client.py` | WMS HTTP client typed error、evidence_key 和熔断交互测试 | 🔧 架构核心 |
| `wms_integration/test_caller_contract.py` | 首个真实 caller 接入前的 RuntimeHold/diagnostic 合同保护测试 | 🔧 架构核心 |
| `wms_integration/test_evidence.py` | WMS evidence 脱敏、hash、关联 ID 和保存行为测试 | 🔧 架构核心 |
| `resilience/test_wms_circuit_breaker.py` | DB-backed WMS breaker 状态转换、并发 probe 与恢复时序 HEAVY 测试 | 🔧 架构核心 |
| `wms_integration/test_cache.py` | WMS read-only 短缓存、坏缓存清理和降级回源测试 | 🔄 常用功能 |
| `wms_integration/test_callback_normalizer.py` | WMS/RCS 回调包络校验和字段标准化测试 | 🔧 架构核心 |
| `wms_integration/test_transport_contract.py` | rack/handling WMS/RCS 派发 payload 合同防漂移测试 | 🔧 架构核心 |
| `wms_integration/test_fulfillment_state_machine.py` | Fulfillment 11 态内部 evidence 状态机、typed ACK/status/terminal result 收敛、终态保护和 CB-blocked replay 测试 | 🔧 架构核心 |
| `wms_integration/test_typed_evidence_envelope.py` | Typed EvidenceEnvelope / ExternalReference 字段和 extra forbid 合同测试 | 🔧 架构核心 |

**E2E 测试文件**：

| 文件 | 用途 | 分类 |
|------|------|------|
| `e2e/__init__.py` | E2E 测试模块导出 | 🔧 架构核心 |

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
| `architecture/SRS.md` | 产品范围、参与方职责以及功能与非功能需求真源 | 📖 必读文档 |
| `architecture/file_index.md` | 代码库动态索引（本文档） | 📖 必读文档 |
| `architecture/device-command-contract.md` | DeviceCommand 核心基础能力边界 | 📖 必读文档 |
| `permission-model.md` | RBAC 权限模型文档 | 📖 必读文档 |
| `CLAUDE.md` | Claude Code 开发指南 | 📖 必读文档 |
| `devops/database_migration.md` | 数据库迁移指南 | 🔄 常用功能 |
| `menu-api-usage.md` | 菜单 API 使用指南 | 📚 参考资料 |
| `REPOSITORY_GUIDE.md` | Repository 使用指南 | 📚 参考资料 |
| `integration/callback_event_validation_principles.md` | callback/event 前置校验边界说明 | 📖 必读文档 |
| `integration/wms_caller_checklist.md` | WMS 同步调用方接入 checklist：错误处理、RuntimeHold/诊断和 evidence_key 传播 | 📖 必读文档 |
| `integration/workline_device_error_code_standardization.md` | 现有设备诊断实现基线、厂商 Adapter 映射职责与最终收敛边界 | 🔄 实现基线 |
| `contracts/observability-contract.md` | Runtime / callback / device / WMS 稳定观测合同 | 📖 必读文档 |
| `contracts/runtime-toggle-governance.md` | Typed runtime toggle 治理和安全边界合同 | 📖 必读文档 |
| `superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md` | WES 最小执行架构最终真源 | 📖 必读文档 |
| `superpowers/README.md` | 当前文档生命周期与项目外归档索引 | 📖 必读文档 |
| `architecture/adr/2026-05-26-wms-integration-domain.md` | WMS 对接辅助域 ADR | 📖 必读文档 |
| `hardware/` | 硬件厂商原始协议与联调资料；作为独立厂商 Adapter 实施输入，不是 WES 核心架构真源 | 📚 外部输入 |
| `devops/JENKINS.md` | Jenkins 使用指南 | 📚 参考资料 |
| `devops/jenkins-setup-current-env.md` | Jenkins 环境配置 | 📚 参考资料 |
| `devops/jenkins-checklist.md` | Jenkins 部署检查清单 | 📚 参考资料 |

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

**Runtime 相关迁移**：当前 revision chain 只是 `implementation_baseline`；最终模型与单一初始基线以顶层 SPEC 和九阶段总控为准。

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
│   ├───devops/JENKINS.md
│   ├───REPOSITORY_GUIDE.md
│   ├───devops/database_migration.md
│   ├───architecture/SRS.md
│   ├───architecture/file_index.md  # 本文档
│   ├───integration/callback_event_validation_principles.md
│   ├───devops/jenkins-checklist.md
│   ├───devops/jenkins-setup-current-env.md
│   ├───menu-api-usage.md
│   └───permission-model.md
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

```text
1. 定义 Base (业务字段) → UserBase
2. 定义 Model (Base + Mixins) → User
3. ModelFactory 自动生成 Schema → UserCreate, UserUpdate
4. 定义 Repository (继承 BaseRepository)
5. 定义 Service (继承 BaseService)
6. 定义 API (继承 BaseAPI) → 零代码 CRUD
```

### 5.2 分层架构

```text
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

同步工具：Serena MCP

更新触发条件：

1. 新增/删除核心模块
2. 架构重大调整
3. 新增关键文件/目录

---

文档结束。

本文档由 Claude Code + Serena MCP 生成和维护。
