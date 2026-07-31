# TODOS

> 2026-07-31 清理说明：`docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md`
> 是 WES 执行架构唯一目标基线。系统尚未发布，不保留旧版本兼容、旧数据迁移、双路径或旧架构后续事项。
> active TODO 只记录与最终架构一致、已有真实触发条件且尚未排期的独立工作；已完成事项由 Git 历史记录，
> 不继续保留在本文件。

## Infrastructure

### TimescaleDB audit_logs hypertable 落地

**What:** 在最终数据库模型中将 `wes_sys.audit_logs` 按 `opera_time` 建为 TimescaleDB hypertable，并补齐审计日志的时间分区、索引和保留策略。

**Why:** `audit_logs` 是持续追加的历史审计事实表，主要按时间、用户、操作类型、对象类型和状态检索。当前 TimescaleDB 已启用但没有任何 hypertable，系统承担扩展成本却未使用核心能力；先从低耦合审计日志表试点，风险最低。

**Context:** 2026-06-11 TimescaleDB 审计确认：`wes_db` 中 `timescaledb_information.hypertables = 0`，`audit_logs` 无外键引用，现有索引包括 `opera_time`、`trace_id`、`username`、`(object_type, opera_time)`、`(action, opera_time)`、`(status, opera_time)`。TimescaleDB 要求 hypertable 上的 `PRIMARY KEY` / `UNIQUE` 索引必须包含分区列，因此不能直接对当前 `PRIMARY KEY(id)` 表执行 `create_hypertable`。

**Scope:**
- 架构收敛完成前实施时直接纳入最终 Alembic 基线；不得为现有开发/测试数据编写转换迁移
- 将 `wes_sys.audit_logs` 按 `opera_time` 创建 hypertable，建议初始 `chunk_time_interval = 7 days`
- 建模时避免 `pk_audit_logs` 和 `ix_wes_sys_audit_logs_id` 的唯一约束冲突：不使用 `PRIMARY KEY(id)` / `UNIQUE(id)`，保留普通 `id` 索引用于按 ID 查询
- 保留现有时间检索索引：`opera_time`、`trace_id`、`username`、`(object_type, opera_time)`、`(action, opera_time)`、`(status, opera_time)`
- 评估并补充 `username + opera_time DESC` 复合索引，优化审计后台按用户和时间范围检索
- 明确 `id` 唯一性取舍：数据库不再单独强制 `id` 唯一，依赖现有自增或雪花 ID 生成；如需数据库强唯一，必须重构为包含 `opera_time` 的复合主键
- 在生产保留周期明确后添加 retention policy，例如 `add_retention_policy('wes_sys.audit_logs', drop_after => INTERVAL '365 days')`
- 增加空库建库测试，确认 `audit_logs` 出现在 `timescaledb_information.hypertables`，并确认目标审计查询可用

**Depends on:** 最终数据库 metadata 和基线生成时点已确定，TimescaleDB worker 配置已落地，并确认生产审计日志 retention 周期。

**Effort:** S-M (human: 0.5-1 day / CC: ~30-60 min)

**Priority:** P1

---

### API 容器横向扩容拓扑

**What:** 移除当前 `docker-compose.yml` 中 API 服务的固定 `container_name` 与 host port 绑定，通过 Nginx / service discovery 支持多 API 容器副本。

**Why:** 本轮计划将 `API_REPLICAS` 修正为 1 并锁定单容器 4 Uvicorn worker 拓扑，这是当前真实状态；未来流量增长需要横向扩容，但当前网络命名与端口映射阻碍了多副本。

**Context:** 生产当前为单 API 容器，4 Uvicorn worker；连接预算公式 `1×4×5 + 4×4×1 = 36` 中的 `1` 就是 API 容器副本数。扩容需要同步更新预算、Nginx 上游与 compose service 定义。

**Scope:**
- 移除 API service 的 `container_name` 和 host `ports` 映射
- 增加 Nginx / Traefik / 内部负载均衡 service
- 更新连接预算公式，使 `API_REPLICAS` 成为可配置变量
- 验证多副本启动后 Celery Worker、Beat 和前端仍能正确访问 API

**Depends on:** 当前单容器拓扑稳定，连接预算护栏生效。

**Effort:** M (human: ~1 day / CC: ~30 min)

**Priority:** P3

---

## WorkLine

### WorkLine 角色与拓扑设备绑定向导

**What:** 为 WorkLine 配置台补充按标准设备角色、实际拓扑、现场容量和故障隔离范围组织的设备绑定向导。

**Why:** 最终架构只配置现场无法推导的设备实例、Endpoint、位置容量、角色绑定和物理拓扑；前端需要一个低噪声入口帮助运维补齐这些真实配置。

**Context:** 后端以 `WorkLine` 静态身份、标准角色约定和显式配置校验为真源，不使用 WorkLine/Vendor Manifest、动态能力 Catalog 或运行时发现。

**Scope:**
- 按工作线模式和标准角色展示待绑定设备、位置容量及拓扑缺口
- 支持从未绑定设备或当前 WorkLine 设备中选择/调整角色
- 活动 `LineRunEpoch` 存在时只读展示，提示清线并结束当前 Epoch 后调整
- 复用后端显式配置校验的 blocker、warning 和修复入口，不在前端复制业务规则

**Depends on:** 最终 WorkLine、设备角色、位置拓扑和配置校验合同稳定。

**Effort:** M

**Priority:** P2

---

## Operations

### 统一运营看板、告警与 Runbook

**What:** 在最终执行对象、设备和 WMS 集成指标稳定后，建设统一运营看板、告警阈值和现场 Runbook。

**Why:** `InboundEvidence`、`DeviceCommand`、`TransportTask`、`WmsConfirmation`、硬件故障和依赖暂停需要在同一运营面呈现，避免每条工作线重复建设看板和告警口径。

**Context:** Celery Worker 单异步运行时改造会新增按 role/PID/run-id 结构化的 PostgreSQL `application_name`、连接预算门禁和 pool timeout 配置；这些信号应并入同一运营面，而不是再建一套数据库专用看板。

北向 WMS 已定义同步调用、breaker、超时和业务拒绝指标口径。本 TODO 不重建领域口径，只在最终信号稳定后
聚合到跨执行对象、Device、WMS 和 Database 的统一运营面。

**Scope:**
- InboundEvidence 接收/处理延迟、幂等冲突和失败数
- DeviceCommand ACK age、CALLBACK age、dispatch deadline、设备 ERROR/OFFLINE/MAINTENANCE
- TransportTask 成员进度、批次完成延迟和失败数
- WmsConfirmation 待确认数量、最老年龄、重试次数和依赖恢复时间
- 硬件故障、依赖暂停、人工清线和迟到 CALLBACK 证据
- 聚合 WMS 同步调用、breaker、timeout/5xx、429/Retry-After 和业务拒绝信号
- Database pool checkout wait/timeout、按 `application_name` 的连接预算占用、`idle in transaction` 数量
- 数据库告警与 Runbook：例如 `idle in transaction > 0` 持续 2 分钟、pool timeout 或预算占用接近上限
- 现场 Runbook：WMS/RCS 拒绝、入站处理失败、command evidence 缺失、WMS 确认积压、设备状态不一致和人工清线

**Depends on:** 最终执行对象的 observability 指标、Celery 单异步运行时、连接预算与结构化 `application_name` 落地，并产生真实或接近真实的试运行数据。

**Effort:** M-L

**Priority:** P2

---

### 分拣机/粗分机供应商联调操作手册

**What:** 在最终 DeviceCommand、CALLBACK、InboundEvidence、WMS 同步能力和入库流程稳定后，编写分拣机/粗分机供应商联调手册。

**Why:** 顶层设计只定义 WES/ECS/WMS 边界和业务合同；供应商联调还需要可执行的 payload 样例、回调样例、异常码、测试步骤和恢复流程。

**Context:** 手册应在最终合同稳定后从真实接口与回调样例生成，不引用旧 Runtime、WorkLineInbox、自动 replay 或兼容 Payload。

**Scope:**
- 设备角色、动作 payload、callback result/event 样例
- 正常入库、NG、满箱/换架、设备失败、WMS/RCS 拒绝五类联调场景
- command_code / event_id / trace_id / idempotency_key 使用约定
- ECS 只 ACK Event_Push、WES 通过 Receive Command 下发后续动作的联调步骤
- 进程重启后的证据核对、人工清线和新 LineRunEpoch 恢复步骤

**Depends on:** 最终 DeviceCommand、external callback auth、WmsCapabilities 和入库业务合同稳定。

**Effort:** M

**Priority:** P2

---

## Reliability

### 全仓 Redis fail-open/fail-closed/fallback 审计

**What:** 审计整个仓库所有 Redis 调用点，明确每个调用是 fail-open、fail-closed 还是带 fallback，并补齐缺失的降级或错误处理。

**Why:** Celery Worker 和入站异步处理只覆盖了部分 Redis 降级边界；其余模块（缓存、锁、SSE、IP 定位等）可能仍存在无明确语义的 Redis 失败路径，需要在独立后续项中统一。

**Context:** `src/database/redis_client.py` 已提供原子初始化和降级模式，但尚未覆盖全仓调用点。本 TODO 应输出一份调用点清单与每点的失败语义。

**Scope:**
- 列出所有 `RedisManager` / `redis_client` 调用点
- 标注 fail-open（继续服务）/ fail-closed（报错拒绝）/ fallback（PostgreSQL advisory lock 等）
- 对未标注或语义不一致的调用点补齐处理与测试

**Depends on:** 最终 Worker 与入站处理路径稳定，Redis 原子初始化稳定。

**Effort:** M (human: ~1 day / CC: ~30 min)

**Priority:** P3
