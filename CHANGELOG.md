# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- (Future changes will be listed here)

## [0.4.4.0] - 2026-06-02

### Added

- 新增 SMT 分拣入库 WorkLine 插件、上下文合同和 P0 编排流，覆盖源格取盘、工作料盘扫码、目标料格放盘、本地 NG 与会话闭环。
- 新增 SMT 料格分配纯策略、活动货架快照恢复和料格深度 Numeric 迁移，支持按料盘厚度进行稳定料格分配。
- 新增 SMT 分拣入库插件、资源投影、料格分配、NG 回流和 P0 集成回归测试。

### Changed

- WorkLine 插件注册、配置校验、运行时 intent effects 和写回服务接入 SMT 分拣入库业务合同。
- 资源投影和 SMT 货架/料箱调度服务改为复用共享料格分配策略，并保留 Decimal 深度证据。

### Fixed

- 修复 SMT command-result 被误归类为设备事件源能力导致激活被阻断的问题。
- 修复非 HTTP 设备协议参与 START 状态探活时生成 `tcp://`、`mqtt://` 等 httpx 不支持 URL 的问题。
- 修复设备状态预检对配置化 `status_path`、特殊字符 `device_code` 和结构化 DEVICE_BUSY 诊断的兼容性。

## [0.4.3.0] - 2026-06-02

### Added

- 新增 WorkLine `STOPPED` 运行态和 START 准入服务，现场恢复后需要平台 START 事件完成 ECS 状态探测，所有必需设备 AUTO/IDLE 后才释放派发。
- 新增 WorkLine START 准入投影字段、数据库迁移、运行态查询返回字段和 `WORKLINE_START_REQUESTED` 平台控制事件。
- 新增命令派发前实时 ECS 设备状态预检、设备忙停放、STOPPED outbox 停放和 dev mock START 调试事件支持。
- 新增 START 准入、callback 入站、runtime hold 释放、outbox 派发、事件映射和 mock 端点的回归测试。

### Changed

- Callback event 入站区分平台控制事件、平台安全事件和生产事件，STOPPED/RECONCILING/ESTOPPED 期间拒收生产事件。
- Runtime Hold 和 ESTOP 恢复后不再直接回到 READY，而是转入 STOPPED 等待现场 START；已阻断 outbox 会停放到 workline 维度等待 START 释放。
- WorkLine 配置校验补充命令目标设备能力、通信配置和 runtime event mapping 保留事件校验。

### Fixed

- 修复 runtime event mapping 可把生产事件映射为 START 并绕过生产事件安全门禁的问题。
- 修复 START 探测期间并发安全状态变化可能被 stale ORM 实例覆盖为 READY 的问题。
- 修复 STOPPED 等待 START 窗口中新增 outbox 被误标记为永久失败的问题。
- 修复设备状态预检 URL 未编码 `device_code`、设备忙态和同命令 ACK 超时重试被当作普通派发失败消耗重试的问题。

## [0.4.2.0] - 2026-05-28

### Added

- Workline Inbox 支持原子 claim、处理器 token fencing、stale PROCESSING 回收和按设备/Session bucket 的并发处理保护，worker 可在保持同一冲突域串行的前提下提高吞吐。
- 新增 Workline Unit of Work、SessionLifecycleService、DeviceCommandGateway、OutboxDispatchService、OrchestratorWriteBackService 和任务队列网关，运行态提交、设备指令、outbox 派发和写回职责从 Celery 入口拆出。
- SMT 扫码链路接入 WMS 库存 typed port，并补充本地 WMS mock、E2E 配置、服务定位和调用证据测试。
- 新增 Workline inbox 热队列部分索引、handling 满箱交换 completion policy 补齐迁移，以及可信代理来源配置。

### Changed

- Callback、Runtime Hold、Workline 操作和 Celery 任务入口改为复用服务层与 UoW 边界，避免 API/Celery 入口直接承载业务编排细节。
- RuntimeIntent effect、诊断上下文、outbox delivery、系统 outbox engine、rack/handling 完成策略和货架投影枚举归一化到共享服务/工具函数。
- Workline 文档、SMT classifier runtime flow、物料流 runtime 和 outbox 派发指南同步到新的职责拆分与运行态合同。

### Fixed

- 修复 Inbox 并发消费中重复 claim、stale worker 终态覆盖、bucket 失败回滚范围和 Redis SSE 降级等可靠性问题。
- 修复 Runtime Hold 释放后失败原因丢失、重复释放幂等、对账 ACK 误清理终端会话以及运行态写回边界问题。
- 修复 PostgreSQL advisory lock SQL 参数化、可信代理客户端 IP 解析、rack 投影枚举归一化、handling completion policy 历史数据补齐和 SMT 参数类型收窄。

### Removed

- 移除旧 `ProcessInboxMessages`、`OutboxDispatcher` 和 `process_inbox_messages` 内部合同，测试和运行入口统一指向新的服务层。

## [0.4.1.0] - 2026-05-27

### Added

- 新增 WMS 对接辅助域，提供库存查询、预留释放、入库确认、出库确认等内部 typed ports。
- 新增 WMS 调用证据、脱敏快照、canonical hash 和 DB-backed 熔断状态，支持跨 API/Celery 实例共享依赖故障状态。
- 新增 WMS/RCS 回调标准化、运输合同构造、短时查询缓存和调用方接入 checklist。

### Changed

- Rack、Handling 和 callback 运输/回调链路改为复用 WMS integration 合同与 normalizer，保持现有入口和业务分发不变。
- Redis 缓存助手补充 WMS 查询降级语义，坏缓存或 Redis 不可用时回源 WMS。

### Fixed

- WMS typed ports 兼容 `data` 返回 list 的响应结构，避免批量查询数组被当作普通对象吞掉。

## [0.4.0.0] - 2026-05-12

### Added

- 新增作业线物料流 Runtime，插件可通过统一 RuntimeIntent 驱动物料到达、目的地解析、出站派发和业务身份追踪。
- 新增 Runtime Hold、运行态对账、安全事件和 NG 退料能力，现场可阻断、诊断、释放和修复异常运行链路。
- 新增 callback ingress service 与命名响应模型，回调入口统一校验最小包络、记录诊断并返回明确的接收/拒绝结果。
- 新增运行态指标、告警、投影、trace response builder 和修复脚本，支持从 callback、inbox、outbox、session 到设备动作的完整调查。
- 新增 Celery worker 进程级健康检查，开发热重载场景下不再依赖 remote-control ping 判断容器健康。

### Changed

- 内置插件迁移到 RuntimeIntent 模型，移除旧状态机与插件结果链路，把运行状态所有权收敛到 Runtime 层。
- callback API 不再因为 Celery 控制面抖动快速失败；回调先提交入库，再依赖即时任务、Beat 或重试继续处理。
- WorkLine、Device、Outbox、Session、Inbox、Trace 和 Safety 服务围绕运行态阻断、对账恢复和物料身份重新组织。
- 文档、插件模板和开发指南同步到新的物料流 Runtime、Runtime Hold 和插件无状态开发模式。

### Fixed

- 修复 callback 已提交但即时触发 Celery 失败时会影响调用方的问题，改为记录警告并走 Beat/重试兜底。
- 修复 worker 健康检查可能把 `worker_healthcheck.py` 自身误判为 Celery worker 的问题。
- 修复运行态对账、超时扫描、沙箱派发、NG 退料和插件契约中的多处回归，并补充 API、服务和 Runtime 单元测试。

### Removed

- 移除旧插件状态字段、旧设备目标解析器、状态机模板和旧插件结果链路，避免运行态存在第二套事实来源。

## [0.3.0.0] - 2026-04-27

### Added

- 新增 WORKLINE 诊断账本能力，现场可通过 trace、blocking point、诊断卡、dispatch attempt 复盘 callback、inbox、session、command、outbox 和 timeline 链路。
- 新增 workline diagnostics 与 dispatch attempts 持久化模型、迁移、Repository、Service 和 trace read model 聚合响应。
- 新增 WORKLINE trace、sandbox pending、replay 和 manual operation API，并补充快速开始文档与架构索引入口。
- 新增 timeline seq_no 事务级 advisory lock 分配、dispatch attempt lease/finalize 语义和 no-SQL 诊断快速路径测试。

### Changed

- callback/event、callback/result、callback/external 统一返回 trace/event/causation identity，并把 callback result 的恢复锚点收口到 `command_code`。
- Runtime、插件上下文、SMT classifier、mock 和 E2E fixture 统一投影 trace 信息，Trace API 返回 sessions、dispatch attempts 和诊断上下文。
- Replay 现在创建新的 replay event，并把原 event 作为 causation/evidence 保留，不改写历史 inbox。

### Fixed

- 修复重复 callback/event 使用顶层 `event_id` 时仍可能产生新副作用的问题，并在 DB unique 并发冲突后回读原 inbox 返回 duplicate outcome。
- 修复重复事件 ACK 未回填原业务 trace 的问题，调用方不再拿到只有重复日志的新 trace。
- 修复 replay/manual 传入不存在资源时返回全局 500 的问题，改为明确资源不存在响应。

## [0.2.0.0] - 2026-04-25

### Added

- 新增 WorkLine 运行模式治理，支持 `AUTO` / `MANUAL` / `SIMULATION`，并限制沙箱模拟模式只能在 dev/test 环境启用。
- 新增 WorkLine 插件 manifest、拓扑校验、插件状态投影和运行时上下文快照能力。
- 新增 SMT classifier 的 typed context、状态机、诊断结果和更完整的命令/回调契约测试。
- 新增 inbound tote QC 第二插件 spike，覆盖 `WEIGH_TOTE` / `DIVERT_TOTE` 命令、手工回调和异常路径。
- 新增中文插件开发指南、插件模板、沙箱 happy path 和模板资产回归测试。
- 新增设备运行态治理字段和服务逻辑，维护 `IDLE` / `RUNNING` / `ERROR` / `OFFLINE` / `MAINTENANCE` 以及当前指令、工作线和 Session 占用信息。
- 新增系统事件流服务和 `/sys/events/stream` SSE 入口，用于推送设备状态、指令和工作线运行事件。

### Changed

- 调整 WorkLine outbox 派发逻辑，`SIMULATION` 会进入沙箱出口并保留真实 payload 供调试。
- 将设备指令 `task_type` 从中心枚举约束改为可扩展字符串，允许插件定义自己的设备任务类型。
- 将需要真实 WES、Celery、种子数据和本地 mock 服务的 SMT mock 集成测试改为显式 live gate。
- 调整真实设备 outbox 派发治理，同一设备上一条硬件任务未完成前不会派发下一条；`PENDING` 仅表示 WES 队列，不再视为设备占用。

### Fixed

- 修复插件模板和开发指南中已不存在的 `ClassificationResult` 示例，避免新插件从错误契约起步。
- 移除运行时契约层中重复的 Session/Inbox 状态枚举定义，统一引用模型层枚举。
- 修复本地 SMT mock 集成测试会通过系统代理误判服务状态的问题。
- 修复 TimescaleDB 未在 Postgres 启动时预加载导致迁移 DDL 被服务器中断的问题。
- 修复 SMT mock 链路中插件任务类型被旧映射改写为 `PROCESS` / `PICK_AND_PLACE` 的问题。
- 修复指令结果回调后设备运行态不会按硬件成功/失败释放或转故障的问题。
- 修复失败 outbox 可能遗留活跃设备指令和 `RUNNING` 设备占用的问题，并通过迁移修复历史数据。

## [0.1.0.0] - 2026-03-23

### Added

**Core Framework**
- FastAPI + SQLModel + SQLAlchemy 2.0 分层架构（API → Service → Repository）
- ModelFactory：自动生成 Create/Update Schema，支持乐观锁更新
- Mixin 系统：AuditMixin, OptimisticLockMixin, SoftDeleteMixin, DataTableMixin, EnterpriseMixin
- Hook 系统：Repository 层业务逻辑扩展点
-雪花 ID 生成器，支持分布式环境
- 全局异常处理系统，统一错误响应格式
- 可配置日志系统，请求上下文管理

**认证与授权**
- JWT 认证系统，支持令牌刷新和黑名单
- 会话管理和多设备登录控制
- RBAC 权限模型（用户-角色-权限-菜单）
- 动态权限缓存失效机制
- API 应用签名认证（HMAC-SHA256）
- API 应用有效期管理和权限类型细分
- 权限与菜单自动同步机制

**管理员模块**
- 用户管理：CRUD、密码重置、软删除
- 角色管理：动态权限分配
- 权限管理：树形结构支持、菜单和按钮权限
- 菜单管理：前后端统一注入机制

**设备管理模块**
- 设备 CRUD 操作，支持乐观锁并发控制
- 设备指令和事件回调功能
- 回调日志记录（callback_logs 表）
- 统一外部 device_code / 内部 device_id 契约

**作业线模块**
- WorklineInbox 收件箱功能
- 作业线插件化编排与全链路追踪设计
- E2E 测试数据初始化脚本

**数据层**
- Alembic 数据库迁移工具集成，支持多 Schema 配置
- 软删除与唯一约束冲突解决方案（部分唯一索引）
- 审计日志：操作耗时统计、后台任务模式
- 智能关系加载策略，优化查询性能
- 统一 ENUM 类型规范（VARCHAR + CHECK 约束，禁用 PostgreSQL 原生 ENUM）

**缓存系统**
- 统一缓存配置（Redis）
- 缓存装饰器，支持函数级缓存控制
- 权限缓存自动失效

**开发工具**
- Ruff 代码规范和格式化配置
- basedpyright 类型检查
- VSCode 工作区设置优化
- pytest 测试框架，254+ 测试用例

**部署与运维**
- Docker Compose 多环境配置
- GitLab CI/CD Pipeline（已迁移至 Jenkins）
- Rocky Linux 环境搭建脚本
- 健康检查端点
- CORS 跨域配置，支持多格式解析

**文档**
- CLAUDE.md：开发指南和架构规则
- 软件需求规格说明书 (SRS)
- P9 WES 第三方设备接入白皮书
- CI/CD 环境搭建文档

### Changed

**架构重构**
- 修复 Service 层分层违规，确保 API → Service → Repository 严格调用链
- 统一 ENUM 类型并新增 workline 模型
- 将 get_all 方法替换为 get_list 方法，优化查询逻辑
- 重构 mixins 模块结构
- 收敛乐观锁更新模型
- 迁移至基于 pyright 的类型检查

**接口优化**
- 统一 API 响应格式为标准响应模式
- 收敛菜单树接口并精简快速回归集
- 为 BaseAPI 添加 response_model 标准化响应格式
- 对齐认证菜单接口并修复菜单查询空结果

**代码质量**
- RUFF 格式化和检查修复
- 消除 basedpyright 类型检查问题
- 移除未使用的导入，优化代码结构
- 修复 Mixin 继承 MRO 错误

**依赖管理**
- 重构 Celery 应用结构
- 从 GitLab CI 迁移到 Jenkins Pipeline

### Fixed

- 修复应用 ID 为 None 时权限查询异常
- 修复用户可选字段清空更新失效
- 修复乐观锁并发更新检测
- 修复软删除模型查询问题
- 修复 FastAPI 依赖注入类型问题
- 统一数据脚本入口并修复迁移约束命名
