# TODOS

> 2026-07-13 清理说明：WorkLine/plugin 与 RuntimeInbox 目标态重构已成为设计真源。被
> `docs/architecture/workline-and-plugin-restructuring.md` 吸收的旧 WorkLine、plugin、
> queue、WMS ACL、reconciliation、monitoring、benchmark 和 DeviceCommand 待办不再在
> active TODO 中重复维护。RuntimeInbox 数据库、Service/replay、reset、heavy benchmark 与严格 CI
> 已完成，不重复建项；active TODO 只保留仍需后续排期的运营 UI、告警、Runbook 和其他独立事项。

## Infrastructure

### TimescaleDB audit_logs hypertable 落地

**What:** 将 `wes_sys.audit_logs` 按 `opera_time` 转为 TimescaleDB hypertable，并补齐审计日志的时间分区、索引和保留策略。

**Why:** `audit_logs` 是持续追加的历史审计事实表，主要按时间、用户、操作类型、对象类型和状态检索。当前 TimescaleDB 已启用但没有任何 hypertable，系统承担扩展成本却未使用核心能力；先从低耦合审计日志表试点，风险最低。

**Context:** 2026-06-11 TimescaleDB 审计确认：`wes_db` 中 `timescaledb_information.hypertables = 0`，`audit_logs` 无外键引用，现有索引包括 `opera_time`、`trace_id`、`username`、`(object_type, opera_time)`、`(action, opera_time)`、`(status, opera_time)`。TimescaleDB 要求 hypertable 上的 `PRIMARY KEY` / `UNIQUE` 索引必须包含分区列，因此不能直接对当前 `PRIMARY KEY(id)` 表执行 `create_hypertable`。

**Scope:**
- 新增 Alembic 迁移，将 `wes_sys.audit_logs` 按 `opera_time` 创建 hypertable，建议初始 `chunk_time_interval = 7 days`
- 迁移前处理 `pk_audit_logs` 和 `ix_wes_sys_audit_logs_id` 的唯一约束冲突：删除 `PRIMARY KEY(id)` / `UNIQUE(id)`，保留普通 `id` 索引用于现有按 ID 查询
- 保留现有时间检索索引：`opera_time`、`trace_id`、`username`、`(object_type, opera_time)`、`(action, opera_time)`、`(status, opera_time)`
- 评估并补充 `username + opera_time DESC` 复合索引，优化审计后台按用户和时间范围检索
- 明确 `id` 唯一性取舍：数据库不再单独强制 `id` 唯一，依赖现有自增或雪花 ID 生成；如需数据库强唯一，必须重构为包含 `opera_time` 的复合主键
- 在生产保留周期明确后添加 retention policy，例如 `add_retention_policy('wes_sys.audit_logs', drop_after => INTERVAL '365 days')`
- 增加验证脚本或迁移测试，确认 `audit_logs` 出现在 `timescaledb_information.hypertables`，并确认原有审计查询仍可用

**Depends on:** TimescaleDB worker 配置已落地；实施前需确认目标环境 Alembic 当前版本可正常升级，并确认生产审计日志 retention 周期。

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

### WorkLine manifest 角色优先设备绑定向导

**What:** 为 WorkLine 配置台补充按 manifest 设备角色、required/optional 标记、能力缺口和 SafetyZone 影响范围组织的设备绑定向导。

**Why:** 目标态后端会把设备角色、能力、共享设备影响范围和启停门禁固化在 manifest/validator 中；前端仍需要一个低噪声入口帮助运维按缺口补齐配置。

**Context:** 后端目标态合同已统一到 manifest、SafetyZone 和 shared-device validator；该事项只承接尚未实现的运营配置入口，不重复定义后端规则。

**Scope:**
- 按 manifest required roles 展示待绑定设备、能力要求和 SafetyZone 归属
- 支持从未绑定设备或当前 WorkLine 设备中选择/调整角色
- active / DRAINING / VALIDATING WorkLine 只读展示，提示停线或 drain 后调整
- 复用后端 manifest validator / configuration status 的 blocker、warning 和修复入口

**Depends on:** WorkLine manifest schema、SafetyZone/shared-device validator 和配置页基础合同稳定。

**Effort:** M

**Priority:** P2

---

### WorkLine inventory 批量运行引用摘要

**What:** 当任一环境需要盘点超过 100 条 WorkLine 时，实现按 `workline_id` 批量汇总未完成 Session、Command、Outbox、Inbox 和 RuntimeHold 的查询 port。

**Why:** Foundation inventory 复用的单条摘要最坏约 9 SQL/WorkLine；直接调大安全上限会放大 N+1、延长 `REPEATABLE READ` 快照事务，并增加生产数据库连接和 MVCC 清理压力。

**Pros:** 支持大规模环境，同时让停用门禁、inventory 和后续 cutover preflight 继续共享同一套 active/terminal 状态语义。

**Cons:** 需要扩展 `RuntimeInboxQueryPort`、RuntimeHold repository 和 WorkLineRepository，触及 HIGH 风险 RuntimeInbox 查询路径，必须先完成 GitNexus impact analysis。

**Context:** `docs/superpowers/archive/plans/2026-07-15-workline-active-inventory-foundation.md` 将逐条查询策略的安全上限固定为 100，并设置 60 秒总超时。触发后应设计按 WorkLine 分组的 bulk count/sample 查询，让单条与批量 API 共享状态常量和测试矩阵；禁止 inventory repository 直接复制 RuntimeInbox/Outbox 等状态集合，也禁止仅通过配置调大上限。

**Effort:** L (human: ~3-5 days / CC: ~6-10 hours)

**Priority:** P3

**Depends on:** 任一真实环境出现超过 100 条未删除 WorkLine，或性能数据证明逐条策略无法满足 60 秒总超时。

---

## Operations

### 统一运营看板、告警与 Runbook

**What:** 在目标态 runtime、reconciliation、device 和 wms_integration 指标稳定后，建设统一运营看板、告警阈值和现场 Runbook。

**Why:** 旧 TODO 中的 SMT Handoff 看板、RuntimeHold 看板、急停看板、WMS breaker 告警和粗分机监控本质上是同一套运营观测能力。按目标态应合并设计，避免每条业务线重复建看板和告警口径。

**Context:** Celery Worker 单异步运行时改造会新增按 role/PID/run-id 结构化的 PostgreSQL `application_name`、连接预算门禁和 pool timeout 配置；这些信号应并入同一运营面，而不是再建一套数据库专用看板。

2026-07-25 边界更新：北向 WMS capability 已交付 operation/profile SLO 目录、目标指标口径与 Runbook，但
`wms_effect.*` 尚未注册为生产信号，真实采集与告警映射仍由
`docs/operations/wms-northbound-acceptance-and-cutover.md` 的 Task 9 门禁约束。本 TODO 不重复定义该领域口径，
只在真实映射稳定后把现成信号聚合到跨 Runtime、Device、Database 的统一运营面。

**Scope:**
- RuntimeInbox backlog、dead-letter、RESOURCE_WAIT、Outbox BLOCKED_RESOURCE
- DeviceCommand ACK age、dispatch deadline、ECS status probe 失败、设备 ERROR/OFFLINE/MAINTENANCE
- Reconciliation active 数、MTTR、reason、late callback、manual resolve
- 聚合北向能力已定义的 WMS submit/status、breaker、timeout/5xx、429/Retry-After、UNKNOWN/open
  reconciliation 与 callback hint 信号；不在本 TODO 重建领域口径或 Runbook
- Safety incident / ESTOP evidence / shared-device 影响范围
- Database pool checkout wait/timeout、按 `application_name` 的连接预算占用、`idle in transaction` 数量
- 数据库告警与 Runbook：例如 `idle in transaction > 0` 持续 2 分钟、pool timeout 或预算占用接近上限
- 现场 Runbook：WMS/RCS 拒绝、Inbox dead-letter、command evidence 缺失、对账 evidence 缺失、设备状态不一致

**Depends on:** 目标态 observability 指标、Celery 单异步运行时、连接预算与结构化 `application_name` 落地，并产生真实或接近真实的试运行数据。

**Effort:** M-L

**Priority:** P2

---

### 分拣机/粗分机供应商联调操作手册

**What:** 在目标态 DeviceCommand、callback、RuntimeInbox、WMS fulfillment 和入库能力重建稳定后，编写分拣机/粗分机供应商联调手册。

**Why:** 顶层设计只定义 WES/ECS/WMS 边界和业务合同；供应商联调还需要可执行的 payload 样例、回调样例、异常码、测试步骤和恢复流程。

**Context:** 手册应在目标态合同稳定后从真实接口与回调样例生成，避免继续维护已退役 WorkLineInbox 流程的供应商说明。

**Scope:**
- 设备角色、动作 payload、callback result/event 样例
- 正常入库、NG、满箱/换架、设备失败、WMS/RCS 拒绝五类联调场景
- command_code / event_id / trace_id / idempotency_key 使用约定
- ECS 只 ACK Event_Push、WES 通过 Receive Command 下发后续动作的联调步骤

**Depends on:** 目标态 device command contract、external callback auth、入库能力重建和 WMS fulfillment contract 稳定。

**Effort:** M

**Priority:** P2

---

## Reliability

### 全仓 Redis fail-open/fail-closed/fallback 审计

**What:** 审计整个仓库所有 Redis 调用点，明确每个调用是 fail-open、fail-closed 还是带 fallback，并补齐缺失的降级或错误处理。

**Why:** Celery Worker 单运行时改造只锁定了 Worker 与 RuntimeInbox 的 Redis 降级边界；其余模块（缓存、锁、SSE、IP 定位等）可能仍存在无明确语义的 Redis 失败路径，需要在独立后续项中统一。

**Context:** `src/database/redis_client.py` 已提供原子初始化和降级模式，但尚未覆盖全仓调用点。本 TODO 应输出一份调用点清单与每点的失败语义。

**Scope:**
- 列出所有 `RedisManager` / `redis_client` 调用点
- 标注 fail-open（继续服务）/ fail-closed（报错拒绝）/ fallback（PostgreSQL advisory lock 等）
- 对未标注或语义不一致的调用点补齐处理与测试

**Depends on:** Worker 单运行时改造完成，Redis 原子初始化稳定。

**Effort:** M (human: ~1 day / CC: ~30 min)

**Priority:** P3

## Completed

### 修复 test_start_admission_service 预存失败

**What:** `tests/workline_runtime/test_start_admission_service.py` 19 个测试在 develop base 即失败（`START_ADMISSION_CONFIGURATION_INVALID` 等断言过时），与料盘根域分支无关。

**Why:** /ship 第五轮验收发现，pre-existing 失败需单独排查，避免污染后续 PR 的测试信号。

**Context:** 2026-06-22 在 `feature/material-unit-root-domain` 分支 `/ship` 时确认 develop base `ee1f3b67` 同样失败，非本分支回归。

**Scope:**
- 排查 `start_admission_service` 启动准入逻辑与测试 fixture 的 contract version / 配置漂移
- 修复或同步测试断言

**Effort:** S

**Priority:** P0

**Completed:** v0.8.0.0 (2026-06-22) — fixture `rough_sorter.v1` → `v2` (合同版本同步迁移)，随 `df97828` 提交。

### Runtime scene 结构化运行资源证据契约

**What:** 为 Rack、Bin、PKG、Slot、Part SN、Magazine 等现场资源证据提供稳定结构化运行字段。

**Why:** `/runtime/monitor` 现场态势图需要显示执行证据，但 WES 不是 WMS 库存事实源。没有结构化契约时，前端只能从 `context_json`、`payload_json`、`event_payload` 猜测资源含义，容易把插件专用 JSON 推断误展示成库存真相。

**Context:** `docs/superpowers/archive/specs/2026-06-05-runtime-workline-scene-monitor-design.md` 工程评审最初接受为后续项；2026-06-08 前端 eng review 后，用户选择本 PR 直接落地逐项 `RuntimeResourceEvidenceItem[]`，不再推迟到 P3。v1 仍明确禁止前端 raw JSON resource badge inference。

**Scope:**
- 定义资源证据字段的来源、命名、生命周期和权限边界
- 区分执行证据、WMS 回调证据和库存授权/库存真相
- 在 runtime detail 增加稳定资源证据 view
- 补 contract/schema tests，防止前端回到 raw JSON 推断

**Depends on:** 与 `docs/superpowers/archive/plans/2026-06-06-wes-single-layer-rack-orchestration-boundary-plan.md` 的 runtime detail 合同同步实施。

**Effort:** M (human: ~1-2 days / CC: ~2-3 hours)

**Priority:** P1

**Completed:** v0.4.8.0 (2026-06-08)

---

### 第一个真实 WMS 同步 caller integration + end-to-end RuntimeHold/diagnostic validation

**What:** 选择第一个真实 WMS 同步调用方接入 `wms_integration` typed ports，并做端到端 RuntimeHold/diagnostic validation。

**Why:** 当前 caller contract 通过 fake caller 保护 timeout/5xx/circuit-open 的处理边界；仍需要真实业务调用方验证 evidence_key 传播、RuntimeHold 或诊断创建、用户可见错误和恢复路径。

**Context:** `docs/superpowers/archive/plans/2026-05-26-wms-integration-domain.md` 的 Deferred / TODO Decisions 已接受该后续项，`docs/integration/wms_caller_checklist.md` 是接入检查清单。

**Scope:**
- 选定首个真实 WMS 同步查询或写入场景
- 调用方只依赖 typed ports，不直接 import WMS HTTP client
- timeout/5xx/circuit-open 按 checklist 创建 RuntimeHold 或诊断
- 验证 evidence_key、trace_id、request_id 在业务错误和运维视图中可追踪
- 增加端到端或集成测试覆盖成功、WMS business reject、WMS unavailable 和 breaker open

**Depends on:** 首个业务接入场景确认，RuntimeHold/diagnostic 入口可用。

**Effort:** M-L

**Priority:** P1

**Completed:** v0.4.1.0 (2026-05-27)

### Runtime reconciliation 系统级处理

**What:** 按 `docs/superpowers/archive/plans/2026-05-08-workline-timeout-system-handling.md` 实现系统级 runtime reconciliation，移除插件 `on_timeout()` 默认 failure 路径。

**Why:** execution Callback timeout 和 dispatch ACK exhausted 都是物理状态未知/通信接受状态未知，不能由插件默认 failure 处理，也不能自动重发物理命令。

**Context:** 已由 `2026-05-08-workline-timeout-system-handling.md` 取代原“Timeout 默认 failure 独立实现”方向。

**Scope:**
- 删除 `@on_timeout()` / `WorklinePlugin.on_timeout()` 兼容路径
- `WorklineRuntimeReconciliationService`
- ACK 后激活 execution deadline
- dispatch ACK exhausted reconciliation
- `BLOCKED_RESOURCE` parked outbox release
- focused runtime reconciliation tests

**Depends on:** WorkLine 软件侧急停冻结计划完成或独立排期。

**Effort:** M

**Priority:** P2

**Completed:** v0.4.0.0 (2026-05-12)
