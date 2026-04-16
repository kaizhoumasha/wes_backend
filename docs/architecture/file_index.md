# P9 WES Backend 项目文件索引

**最后更新**: 2026年4月16日
**同步状态**: ⚠️ 已完成高优先级文档入口修正；其余内容请以实际仓库结构为准

---

## 版本更新日志

| 日期 | 版本 | 变更内容 |
|------|------|----------|
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

#### 🎯 示例模块 (src/app/demo/)

开发参考示例

| 目录 | 文件 | 用途 | 分类 |
|------|------|------|------|
| `models/` | `demo_product.py` | 示例产品模型 | 🎯 示例代码 |
| | `demo_product_list.py` | 示例列表模型 | 🎯 示例代码 |
| `repositories/` | `demo_product_repository.py` | 示例仓库 | 🎯 示例代码 |
| `services/` | `demo_product_service.py` | 示例服务 | 🎯 示例代码 |
| `v1/` | `demo_product.py` | 示例路由 | 🎯 示例代码 |

#### 🔧 作业线模块 (src/app/workline/)

作业线运行时系统，遵循白皮书 v3.1 架构设计（插件化、状态机、幂等性）

| 目录 | 文件 | 用途 | 分类 |
|------|------|------|------|
| `models/` | `inbox.py` | WorklineInbox/Outbox 收发件箱模型 | 🔧 架构核心 |
| | `session.py` | WorklineSession 会话模型 | 🔧 架构核心 |
| | `timeline.py` | WorklineTimeline 时间轴模型 | 🔧 架构核心 |
| | `workline.py` | WorkLine 模型（插件容器、运行时配置、诊断归属） | 🔧 架构核心 |
| `repositories/` | `inbox_repository.py` | Inbox Repository（幂等键计算） | 🔧 架构核心 |
| | `__init__.py` | Repository 导出（inbox_repository） | 🔧 架构核心 |
| `services/` | `inbox_service.py` | Inbox Service（创建 Inbox 消息） | 🔧 架构核心 |
| | `__init__.py` | Service 导出（inbox_service） | 🔧 架构核心 |

**核心设计模式**：
- **Inbox 模式**：统一编排入口（设备事件、指令结果、超时、人工操作）
- **幂等性控制**：白皮书 6.3.1 节（厂商 ID 优先 + hash 备选）
- **Outbox 模式**：统一调度出口（设备指令、外部回调、状态记录）

**相关文档**：
- 运行时语义 SSOT：`docs/business/workline_business_data_event_flow_spec.md` v0.1
- 架构设计：`docs/business/workline_plugin_architecture_design.md` v3.2
- SMT 粗分机完整数据流：`docs/business/workline_smt_classifier_runtime_flow.md`
- SMT 粗分机硬件偏差分析：`docs/business/workline_smt_classifier_hardware_gap_analysis.md`

#### 🔧 作业线运行时 (src/workline_runtime/)

插件化编排核心框架，提供装饰器驱动的声明式插件开发

| 文件 | 用途 | 分类 |
|------|------|------|
| `plugin_base.py` | 插件基类 + 装饰器 + Builder（核心框架） | 🔧 架构核心 |
| `payloads.py` | 共享 Payload 定义（Pydantic 模型） | 🔄 常用功能 |
| `null_plugin.py` | 空实现插件（测试回退） | 🎯 示例代码 |
| `plugin_context.py` | 插件上下文（依赖注入、运行时快照、标准化输入、诊断上下文） | 🔧 架构核心 |
| `types.py` | 插件运行时类型（CommandIntent, WaitIntent 等） | 🔧 架构核心 |
| `orchestrator.py` | 编排器服务（锁、事务、派发） | 🔧 架构核心 |
| `enums.py` | 运行时枚举（FailureCode, DecisionType 等） | 🔄 常用功能 |
| `plugin_sdk/` | 插件 SDK（标准化输入、运行时配置、分类器） | 🔧 架构核心 |
| `diagnostics/` | 统一诊断模型、错误码、角色化投影 | 🔧 架构核心 |

**插件开发文档**：
- **插件开发指南**：`docs/plugin_development_guide.md` 📖 必读文档
- **性能对比报告**：`docs/plugin_performance_comparison.md` 📚 参考资料
- **系统 vs 插件能力**：`docs/system_vs_plugin_capabilities.md` 📚 参考资料
- **工作线流程图**：`docs/workline_flow_diagram.md` 📚 参考资料
- **Transition 流程详解**：`docs/transition_flow_guide.md` 📚 参考资料
- **快速验证指南**：`docs/plugin_validation_quickstart.md` 📚 参考资料

**核心特性**：
- **装饰器驱动**：`@on_event()`, `@on_command()`, `@step()` 自动路由
- **Pydantic 自动验证**：Payload 自动解析和类型安全
- **状态机集成**：声明式状态迁移，自动校验
- **链式响应构建**：`PluginResultBuilder` 简化结果构建
- **代码减少 70%**：1915 行 → ~500 行（SmtClassifierPlugin 示例）

#### 🔔 回调模块 (src/app/callback/)

外部系统回调处理

| 目录 | 文件 | 用途 | 分类 |
|------|------|------|------|
| `models/` | `callback_log.py` | 回调日志模型 | 🔧 架构核心 |
| | `event.py` | 回调事件模型 | 🔧 架构核心 |
| `repositories/` | `callback_log_repository.py` | 回调日志仓库 | 🔧 架构核心 |
| `services/` | `callback_service.py` | 回调处理服务 | 🔧 架构核心 |
| `v1/` | `callback.py` | 回调 API 路由 | 🔧 架构核心 |

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

**Workline Runtime 测试文件**：

| 文件 | 用途 | 分类 |
|------|------|------|
| `workline_runtime/test_enums.py` | 枚举类单元测试（InboxKind, Status 等） | 🔧 架构核心 |
| `workline_runtime/test_inbox_service.py` | Inbox Service 幂等键计算测试 | 🔧 架构核心 |

**E2E 测试文件**：

| 文件 | 用途 | 分类 |
|------|------|------|
| `e2e/__init__.py` | E2E 测试模块导出 | 🔧 架构核心 |
| `e2e/test_conveyor_robot_arm.py` | 流水线料盘搬运 E2E 测试（使用摄像头传感器 API） | 🔄 常用功能 |

#### 🎭 Mock 设备服务

| 目录/文件 | 用途 | 分类 |
|-----------|------|------|
| `mock/` | E2E 测试 Mock 设备服务 | 🔧 架构核心 |
| `mock/Dockerfile` | Mock 服务 Docker 镜像构建 | 🔧 架构核心 |
| `mock/__init__.py` | Mock 服务模块导出 | 🔧 架构核心 |
| `mock/camera_mock_server.py` | 摄像头 Mock 服务（含传感器模拟 API） | 🔧 架构核心 |
| `mock/robot_arm_mock_server.py` | 机械臂 Mock 服务（接收指令、回调结果） | 🔧 架构核心 |
| `mock/README.md` | Mock 服务使用文档（含传感器 API 说明） | 📖 必读文档 |

**Mock 服务 API 端点**：

**摄像头 Mock (端口 8003)**：
- `GET /api/v1/device/status` - 设备状态查询
- `POST /api/v1/sensor/trigger` - 手动触发传感器检测物料到达
- `POST /api/v1/sensor/auto/start` - 启动自动触发
- `POST /api/v1/sensor/auto/stop` - 停止自动触发
- `GET /api/v1/sensor/status` - 获取传感器状态
- `GET /api/v1/sensor/events` - 获取事件历史记录

**机械臂 Mock (端口 8004)**：
- `GET /api/v1/device/status` - 设备状态查询
- `POST /api/v1/device/command` - 接收设备指令
- `POST /api/v1/device/cancel` - 取消执行指令

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
| `api_authentication_design.md` | API 认证设计文档 | 📚 参考资料 |
| `api_authentication_summary.md` | API 认证功能摘要 | 📚 参考资料 |
| `third_party_integration_whitepaper.md` | 第三方集成指南 | 📚 参考资料 |
| `workline_smt_classifier_runtime_flow.md` | SMT 粗分机插件与 Mock 设备端到端数据流说明 | 📚 参考资料 |
| `workline_smt_classifier_hardware_gap_analysis.md` | SMT 粗分机当前实现与真实硬件协议偏差分析 | 📚 参考资料 |
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
    │   ├───demo/             # 示例模块
    │   └───sys/              # 系统管理
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
