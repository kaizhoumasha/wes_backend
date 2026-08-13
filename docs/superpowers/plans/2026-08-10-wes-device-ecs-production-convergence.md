# WES Phase 7 DeviceCommand/ECS 通用能力生产收敛实施计划

> **For agentic workers:** 实施时逐任务执行并在每个 Commit 后独立复审；代码行为必须按 TDD 先建立失败证据，纯文档步骤不得新增测试代码。
>
> 状态：Approved
>
> **批准基线：** `develop@7f17533aa8d51622867bc637055ee76ff6efeb8a`。实施前必须确认下列文档内容摘要仍一致；摘要变化时只复审受影响边界，不得静默沿用本计划。

| 输入 | SHA-256 | 生命周期 |
| --- | --- | --- |
| `docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md` | `e019c2c87c9272a9ced39bd59bafff99f9853688f678d3b2423867d40fa266fe` | Approved 架构真源 |
| `docs/architecture/device-command-contract.md` | `d05383d3104b2e4c55b676c4d4b78607eb8a95993964ab721f3af23ddab38fb5` | Approved 核心合同 |
| `docs/integration/third_party_integration_whitepaper.md` | `87112745ecc032fcfe7772d9dc8c01ca72d577e8852914982adcd4f2b8911680` | Approved 统一 wire |
| `docs/contracts/wms-outbound-picking-task-integration-requirements.md` | `8d1632cdb3d3f1864543fd456d1b859b3cfc2ec28b86acee0b96d1425cf68675` | ReviewRequired 下游适用性输入 |
| `docs/contracts/wms-inbound-putaway-integration-requirements.md` | `9d4a53cf9a67faabd2b8156e0498b0130a17a7d2ca30f5f9f48debbeeb44e9a4` | ReviewRequired 下游适用性输入 |

**Goal:** 在零业务插件核心上交付唯一、可安装的 `DeviceCommand` 可靠生命周期和统一 ECS wire，直接替换旧 DeviceCommand/RuntimeIntentLog/SystemOutbox 设备分支，为后续入库与出库插件提供稳定应用端口。

**Architecture:** 一个 WES 部署只配置一个局域网 ECS/网关 Base URL，并为全体设备复用一个进程级长期 `OutboundHttpTransport`。`DeviceCommand`、设备状态观察和设备 evidence 各自持久化；命令在新鲜 `AUTO + IDLE`、活动 `LineRunEpoch` 与合同绑定一致时才发送，ACK 只表示接纳，CALLBACK 才能形成物理终态。Phase 7 不建立业务插件、供应商 Adapter registry、通用 Intent/Effect/SystemOutbox 或兼容路径。

**Tech Stack:** Python 3.13、FastAPI、SQLModel/SQLAlchemy、PostgreSQL、Celery、Phase 2 `OutboundHttpTransport`、Pytest、Alembic。

## Global Constraints

- API → Service → Repository → Database；API 不直接访问 Repository 或数据库。
- `TransportTask` 与 `DeviceCommand` 是平行可靠对象，不共享状态机、表、Repository、重试策略或测试。
- WES 只发送逻辑位置和已批准设备附录字段；ECS/PLC 拥有物理动作、硬件互锁、防撞和安全。
- 当前纯局域网只允许 HTTP、固定路径和无应用层认证；不保留 Token、HMAC、Nonce、可配置路径或供应商私有 DTO。
- 未发布系统直接替换旧实现；不做数据迁移、回填、downgrade、兼容字段、双写、双读、re-export 或 fallback。
- Phase 7 核心测试不得导入 PickingTask、入库 GRN、粗分、SMT、供应商名称或业务 fixture。
- `docs/hardware/` 完整保留且不修改。
- 代码与测试任务遵循 TDD；文档同步只做格式、链接、状态和差异检查。

---

## 1. 阶段定位与下游适用性

### 1.1 Phase 7 唯一职责

Phase 7 只拥有以下公共能力：

- `DeviceCommand` 稳定 identity、不可变 payload digest、deadline、claim/lease 和状态机；
- 每个 `device_code` 最多一个未闭合命令，`RECONCILING` 同样占用设备；
- 活动 `LineRunEpoch` 及该 Epoch 下不可变的 device/contract 绑定；
- 新鲜设备状态观察、统一 ECS outbound Adapter、同步 ACK 和异步 CALLBACK；
- 结果/事件 evidence 的持久化后 ACK、部署级 `source_event_id` 幂等、冲突与迟到 fencing；
- 三个有界 worker、唯一 Composition Root、固定 WES callback route 和只读诊断查询；
- 后续插件使用的显式命令、结果和事件应用端口。

Phase 7 不拥有业务对象、发送时机和 CALLBACK 后续 Decision。零业务插件、零设备绑定是合法安装态：系统装配 Adapter、callback route 和 worker，但不会创建命令；未绑定设备返回 `DEVICE_NOT_FOUND`，不访问 ECS。

### 1.2 出库与入库共同需要的能力

两份业务合同只作为下游适用性校验，不进入核心测试：

| 下游需要 | Phase 7 提供 | 下游插件继续拥有 |
| --- | --- | --- |
| 一盘物料的一次 PICK/PUT/SCAN 物理动作 | 稳定 `command_code`、逻辑参数快照、唯一物理终态 | `MaterialExecution`、目标选择、发送时机和下一步 Decision |
| 两个机械臂并行 | 唯一约束按 `device_code` 隔离，不同设备可并行 | 扫码台交接顺序；硬件防撞仍由 ECS/PLC 负责 |
| 料盘离开来源后的不可逆闭环 | 已 ACK 或 delivery unknown 的命令禁止换 identity 重放；结果不明进入对账 | 取消只停止后续 PICK，当前盘闭合到目标、NG 或人工处置 |
| 完整扫码/测量事件 | 不可变 event evidence、Epoch fencing 和 typed event port | 六合一码、GRN、物料资格、目标和 NG 业务解释 |
| 出库/入库位置事实 | 命令结果与 `execution_ref`、`command_code` 关联 | `PositionProjection`、WMS Fact 和库存权威更新 |

PickingTask、GRN、目标 Bin/Cell/SLOT、容量、Transport、NG 业务分类和任务完成不进入 `DeviceCommand` 表或公共 DTO。业务合同后续修改只有在改变固定 device wire 或上述公共不变量时才重开 Phase 7；纯业务 operation 变化只影响 Phase 8/9 插件。

## 2. 固定 wire 的方向

四个固定路径不得混淆调用方向：

| 方向 | 方法和路径 | WES 责任 |
| --- | --- | --- |
| WES → ECS/网关 | `POST /api/v1/device/command` | `EcsAdapter.submit_command()` 调用供应商统一端点；WES 不注册同名入站 route |
| WES → ECS/网关 | `GET /api/v1/device/status?device_code=...` | `EcsAdapter.fetch_status()` 获取不可缓存的新鲜状态；WES 不注册同名入站 route |
| ECS/网关 → WES | `POST /api/v1/callback/result` | WES 固定 result route，先持久化 evidence 再 ACK |
| ECS/网关 → WES | `POST /api/v1/callback/event` | WES 固定 event route，先持久化 evidence 再 ACK |

ECS Base URL 是部署级单值配置。多供应商必须由 ECS 或局域网网关收敛到同一 uniform wire；WES 不建立每供应商 Client、路径映射或 Adapter registry。

## 3. 最终文件结构

### 3.1 新建文件

| 文件 | 单一职责 |
| --- | --- |
| `src/app/workline/models/line_run_epoch.py` | `LineRunEpoch` 与不可变 `LineRunEpochDeviceBinding` 模型 |
| `src/app/workline/repositories/line_run_epoch_repository.py` | 活动 Epoch、device binding 的锁定读取与唯一约束访问 |
| `src/app/workline/services/line_run_epoch_service.py` | 创建/关闭 Epoch、冻结设备合同绑定的应用服务 |
| `src/app/device/contracts.py` | `DeviceCommandRequest/Handle/Outcome`、`DeviceEvent` 和显式应用端口类型 |
| `src/app/device/models/evidence.py` | 状态观察、accepted evidence 和 conflict evidence 模型 |
| `src/app/device/repositories/evidence_repository.py` | evidence 幂等插入、claim、冲突和 applied 写回 |
| `src/app/device/services/device_dispatch_service.py` | 状态准入、命令发送、ACK/unknown 分类和 fenced writeback |
| `src/app/device/services/device_evidence_service.py` | result/event ingress、evidence apply、Epoch fencing 和 outcome/event 读取 |
| `src/app/device/ecs_adapter.py` | 固定 command/status wire 与 Phase 2 Transport 的唯一 Adapter |
| `src/app/device/composition.py` | 一个 ECS Client、Service/Repository 和生命周期的显式装配 |
| `src/app/device/v1/ecs_callback.py` | WES 两个固定 callback route；只调用 Service |
| `src/celery_app/tasks/device_command.py` | dispatch、evidence apply、reconcile 三个有界任务入口 |
| `tests/runtime/device_command/` | 与业务/供应商无关的命令生命周期 FAST 测试 |
| `tests/workline/test_line_run_epoch.py` | Epoch 与绑定的纯领域测试 |
| `tests/contracts/device/test_uniform_ecs_wire.py` | 四个固定路径、公共包络和错误语义 |
| `tests/api/test_device_ecs_callbacks.py` | callback route facade、状态码与 OpenAPI |
| `tests/integration/device_command/` | PostgreSQL 唯一约束、事务、claim/lease 和 evidence 幂等 |
| `tests/e2e/device_command/test_device_command_production_wiring.py` | broker → ECS fake → callback → evidence worker → PostgreSQL 生产闭环 |
| `tests/support/ecs_uniform_wire.py` | 仅测试使用的 uniform-wire ECS fake，不含供应商 DTO |
| `tests/deployment/test_device_command_startup.py` | ECS 配置、唯一 Composition Root、worker/queue 和 fail-closed 启动 |
| `tests/architecture/test_device_command_legacy_absence.py` | 旧 gateway/outbox/config/identity/私有认证缺席门禁 |

### 3.2 保留并修改的最终 owner

| 文件 | 最终责任 |
| --- | --- |
| `src/app/device/models/command.py` | 直接重建最终 `DeviceCommand`，不保留旧 Schema/Enum/关系 |
| `src/app/device/models/device.py` | 只保留设备静态身份、WorkLine 拓扑和启用状态 |
| `src/app/device/repositories/command_repository.py` | 命令创建、claim、唯一活动查询、fenced writeback 和对账扫描 |
| `src/app/device/services/device_command_service.py` | 创建命令、应用终态、读取 typed outcome；不做 HTTP 和 commit |
| `src/app/device/v1/device.py` | 设备静态主数据 CRUD；删除本地运行态/维护状态写接口 |
| `src/app/callback/v1/callback.py` | 仅保留非设备 callback owner；result/event route 原子移交到 `device/v1/ecs_callback.py` |
| `src/app/sys/models/outbox.py` | 保留 WMS/其他非设备 Outbox；删除 `DEVICE_COMMAND` 分支和索引 |
| `src/app/sys/repositories/outbox_repository.py` | 保留非设备 claim；删除设备 head-of-line 和活动状态分支 |
| `src/app/sys/services/outbox_engine.py` | 保留 WMS/其他出站；删除设备 dispatch 分支和 Gateway import |
| `src/register.py` | 装配/关闭唯一 DeviceCommand runtime，并注册固定 callback router |
| `src/celery_app/config.py` | `device-command` 静态队列和三个 Beat 扫描提示 |
| `src/celery_app/app.py` | 导入三个新任务；不再通过 workline/sys task 承载设备命令 |
| `docs/architecture/heavy-test-impact.toml` | 新生产路径、迁移、支撑资产到 DeviceCommand HEAVY 的精确映射 |

## 4. 最终数据模型与不变量

### 4.1 `LineRunEpoch`

`LineRunEpoch` 字段冻结为：`id`、`epoch_code`、`workline_id`、`topology_digest`、`configuration_digest`、`status=ACTIVE|CLOSED`、`started_at`、`closed_at`、审计和乐观锁字段。

- `epoch_code` 全局唯一；同一 `workline_id` 最多一个 `ACTIVE` Epoch，使用 PostgreSQL partial unique index 保证。
- `LineRunEpochDeviceBinding` 字段固定为：`id`、`line_run_epoch_id`、`device_id`、`device_code`、`contract_key`、`contract_version`、`status_max_age_ms`、`command_timeout_ms`。
- 同一 Epoch 内 `device_code` 唯一；binding 创建后不可更新。设备合同、拓扑或配置变化必须关闭旧 Epoch 并创建新 Epoch。
- Phase 7 零绑定态不创建占位 binding；Phase 8/9 安装真实附录时显式创建。

### 4.2 `DeviceCommand`

字段冻结为：

- identity/correlation：`command_code`、`device_code`、`line_run_epoch_id`、`device_binding_id`、`execution_ref_type`、`execution_ref_id`、`trace_id`；
- immutable request：`contract_key`、`contract_version`、`task_type`、`params`、`payload_digest`、`deadline_at`；
- lifecycle：`status`、`attempt_count`、`next_attempt_at`、`claim_token`、`claimed_at`、`claim_expires_at`；
- result/reconciliation：`ack_received_at`、`completed_at`、`result_evidence_id`、`failure_code`、`reconciliation_reason`；
- handoff：`outcome_published_at`；
- 通用审计和乐观锁字段。

状态闭集：

| 状态 | 含义 | 后继 |
| --- | --- | --- |
| `PENDING` | 已持久化，尚未发送或 ECS 明确未接纳后等待原 identity 重提 | `DISPATCHING`、`FAILED`、`TIMED_OUT` |
| `DISPATCHING` | 已领取，状态探测/发送可能进行中 | `PENDING`、`ACKNOWLEDGED`、`FAILED`、`RECONCILING` |
| `ACKNOWLEDGED` | ECS 明确接纳，物理动作未终态 | `SUCCEEDED`、`FAILED`、`RECONCILING` |
| `RECONCILING` | delivery unknown、ACK 后超期、identity/证据矛盾或 Epoch 风险 | 匹配权威 evidence 后到 `SUCCEEDED|FAILED`；否则人工闭合 |
| `SUCCEEDED` | 匹配 CALLBACK 证明物理成功 | 无 |
| `FAILED` | 匹配 CALLBACK 证明物理失败，或请求明确未接纳且合同性失败 | 无 |
| `TIMED_OUT` | 能证明请求未离开 WES 且 deadline 已过 | 无 |

同一 `device_code` 在 `PENDING|DISPATCHING|ACKNOWLEDGED|RECONCILING` 中最多一条记录。禁止 `CANCELLED`、通用 priority 队列和换 identity 自动重放。

### 4.3 状态与 evidence

- `DeviceStatusObservation`：`id`、`device_code`、`command_code`、`contract_key/version`、`mode`、`status`、`current_command_code`、`device_timestamp`、`received_at`、`payload_digest`、`raw_payload`。观察不可更新，不作为物理终态。
- `DeviceEvidence`：`id`、`kind=RESULT|EVENT`、全局唯一 `source_event_id`、`device_code`、可空 `command_code`、`contract_key/version`、可空 `line_run_epoch_id`、`payload_digest`、`raw_payload`、`received_at`、`apply_status=PENDING|APPLIED|IGNORED|RECONCILING`、`processed_at`、`published_at`。
- `DeviceEvidenceConflict`：`id`、`source_event_id`、`first_evidence_id`、`conflicting_digest`、`raw_payload`、`reason_code`、`received_at`。只记录冲突，不推进命令或插件。
- 结果回调引用未知命令时返回 `404 / COMMAND_NOT_FOUND`，不建立 accepted `DeviceEvidence`；只在 conflict/diagnostic owner 中保存拒绝证据。

## 5. 应用端口与事务边界

### 5.1 显式应用端口

接口名和职责冻结如下，具体 Python 实现遵循项目类型风格，不在计划中粘贴完整代码：

- `DeviceCommandPort.create_command(request: DeviceCommandRequest) -> DeviceCommandHandle`
- `DeviceCommandPort.get_outcome(command_code: str) -> DeviceCommandOutcome | None`
- `DeviceCommandPort.mark_outcome_published(command_code: str, expected_version: int) -> None`
- `DeviceEventPort.list_pending_events(line_run_epoch_id: int, limit: int) -> list[DeviceEvent]`
- `DeviceEventPort.mark_event_published(evidence_id: int, expected_version: int) -> None`

`DeviceCommandRequest` 只含 `device_code`、`line_run_epoch_id`、`execution_ref_type/id`、`contract_key/version`、`task_type`、`params`、`deadline_at`、`trace_id`。它不含 WMS operation、PickingTask、GRN、目标选择、供应商路径、认证、priority 或 retry 配置。

Phase 7 不注册 outcome/event 的业务 consumer，也不创建 no-op publisher。Phase 8/9 插件通过显式 Composition Root 调用上述端口并在自身事务/worker 中推进业务对象。

### 5.2 事务顺序

1. **创建命令：** 锁定活动 Epoch binding → 校验合同身份 → 规范化附录 payload → 检查设备未闭合命令 → 插入 `PENDING` 命令并提交。事务内不做 HTTP。
2. **领取派发：** Repository 使用 `FOR UPDATE SKIP LOCKED` 领取到 `DISPATCHING`，写入 claim token/lease 后提交。
3. **状态准入与发送：** 事务外调用状态端点；事务内追加状态观察并重新核验 claim/Epoch；满足新鲜 `AUTO+IDLE+current_command_code=null` 后事务外发送命令。
4. **派发写回：** `200` 到 `ACKNOWLEDGED`；`429` 且明确未接纳时原 identity 有界回到 `PENDING`；明确合同拒绝到 `FAILED`；网络、超时和 `5xx` 到 `RECONCILING`。所有写回必须匹配 claim token 与版本。
5. **callback ingress：** 解码前检查 `256 KiB` → 校验公共包络 → 锁定 identity → 原子插入 evidence 或判定 duplicate/conflict → commit 后 ACK。
6. **evidence apply：** `SKIP LOCKED` 领取 evidence → 锁定命令/Epoch → 校验 identity、digest、合同和唯一终态 → 应用结果并标记 evidence。事件无业务 consumer 时只完成公共校验和持久化。
7. **对账：** PENDING 在可证明未离开 WES 时可 `TIMED_OUT`；DISPATCHING delivery unknown、ACK 后超期、冲突或 Epoch 不可信统一进入 `RECONCILING`，不自动释放设备槽。

## 6. Worker 与唯一 Composition Root

固定任务和队列：

| Celery task | 队列 | Beat | 批量边界 |
| --- | --- | --- | --- |
| `src.celery_app.tasks.device_command.dispatch_device_commands_batch` | `device-command` | 10 秒兜底 | `limit=100` |
| `src.celery_app.tasks.device_command.process_device_evidence_batch` | `device-command` | 10 秒兜底 | `limit=100` |
| `src.celery_app.tasks.device_command.reconcile_device_commands_batch` | `device-command` | 30 秒兜底 | `limit=100` |

Beat 只唤醒数据库扫描，消息过期由下一轮替代。不得按命令发送 Celery payload 快照，也不得让 Celery 重试替代 `DeviceCommand` 状态机。

`build_device_command_runtime()` 只创建一个长期 ECS Client、一个 Adapter 和所需 Service/Repository；FastAPI lifespan 和 Celery worker 各自拥有并关闭自己的 event-loop-local runtime。配置仅允许：

- `ECS_BASE_URL`：必须是无 userinfo/query/fragment 的局域网 `http://` URL；
- `ECS_CONNECT_TIMEOUT_SECONDS`、`ECS_READ_TIMEOUT_SECONDS`：有界基础传输预算；
- `DEVICE_COMMAND_QUEUE=device-command`：固定队列名，不做动态路由。

零设备绑定时 runtime 仍可启动，但任何创建命令请求在外部 I/O 前返回 `DEVICE_NOT_FOUND`。

## 7. 当前生产 owner 逐文件处置矩阵

处置只针对设备命令闭包；同一文件的 WMS、Transport、通用审计或 RuntimeInbox 职责必须保留。

| 当前文件 | 处置 | 最终 successor / 保留理由 |
| --- | --- | --- |
| `src/app/device/models/command.py` | DELETE → REBUILD | 同路径最终 `DeviceCommand`；删除旧 TaskType、priority、timeout、CANCELLED、兼容 callback 字段 |
| `src/app/device/models/device.py` | RETAIN + TRIM | 静态身份/拓扑保留；通信、认证、可配置路径和可变运行态转交 Epoch binding/status evidence |
| `src/app/device/models/__init__.py` | MODIFY | 只导出最终模型和 DTO |
| `src/app/device/repositories/command_repository.py` | DELETE → REBUILD | 同路径 final claim/fencing/unique owner |
| `src/app/device/repositories/__init__.py` | MODIFY | 导出 final command/evidence Repository |
| `src/app/device/services/device_command_service.py` | DELETE → REBUILD | 同路径 final application service；不复用 RuntimeIntentLog/SystemOutbox 语义 |
| `src/app/device/services/device_service.py` | RETAIN + TRIM | 只保留静态主数据；删除 current command/heartbeat/runtime mutation |
| `src/app/device/services/runtime_state_policy.py` | DELETE → NONE | 状态权威改为 ECS status observation，不再本地改写 Device 行 |
| `src/app/device/services/__init__.py` | MODIFY | 导出 final services |
| `src/app/device/v1/device.py` | RETAIN + TRIM | 静态 Device CRUD；删除 enter/exit maintenance、clear fault runtime route |
| `src/app/device/v1/__init__.py` | MODIFY | 合并静态 device router 与固定 ECS callback router |
| `src/app/device/__init__.py` | MODIFY | 暴露唯一组合 router 和应用端口 |
| `src/app/callback/v1/callback.py` | RETAIN + HANDOFF | 删除 result/event route；保留 `/external` 非设备入口 |
| `src/app/callback/models/event.py` | DELETE → successor | 移至 `src/app/device/models/evidence.py` 的统一 event DTO |
| `src/app/callback/models/ingress_response.py` | RETAIN + TRIM | 删除 device result/event response；保留 external response |
| `src/app/callback/models/__init__.py` | MODIFY | 删除已移交 device exports |
| `src/app/callback/services/callback_ingress_service.py` | RETAIN + TRIM | 删除 device result/event 分支；保留 WMS/external ingress |
| `src/app/callback/services/callback_orchestration_service.py` | RETAIN + TRIM | 删除 DeviceCommand callback orchestration；保留非设备职责 |
| `src/app/callback/services/__init__.py` | MODIFY | 删除 device ingress export |
| `src/app/runtime/orchestration/services/device_command_gateway.py` | DELETE → successor | `src/app/device/ecs_adapter.py` + dispatch service |
| `src/app/runtime/orchestration/services/device_command_lease.py` | DELETE → successor | claim/lease 字段和 command repository |
| `src/app/runtime/orchestration/device_runtime_projection.py` | DELETE → successor | `DeviceStatusObservation`；不保留可变镜像投影 |
| `src/app/runtime/orchestration/services/device_runtime_projection_writer_service.py` | DELETE → successor | evidence repository/service |
| `src/app/runtime/system_capabilities/device/device_command_write/__init__.py` | DELETE → NONE | 真实插件直接消费 `DeviceCommandPort` |
| `src/app/runtime/system_capabilities/device/device_command_write/definition.py` | DELETE → NONE | 不保留 capability registry 包装 |
| `src/app/runtime/system_capabilities/device/device_command_write/handler.py` | DELETE → NONE | 不保留旧 handler |
| `src/app/runtime/orchestration/runtime_intent_effects.py` | RETAIN + TRIM | 删除 DEVICE_COMMAND effect 分支，其他 intent/effect owner 保留 |
| `src/app/runtime/orchestration/enums.py` | RETAIN + TRIM | 删除 `DEVICE_COMMAND` effect enum 分支 |
| `src/app/runtime/orchestration/consumers/callback_runtime_inbox_writer.py` | RETAIN + TRIM | device result/event 不再写 RuntimeInbox；WMS 分支保留 |
| `src/app/runtime/orchestration/services/inbox/outbox_dispatch_service.py` | RETAIN + TRIM | 删除 device Gateway bridge，非设备 outbox 保留 |
| `src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_service.py` | RETAIN + TRIM | 删除 device command 生产/推进，通用 inbox 不变量保留 |
| `src/app/runtime/orchestration/services/runtime_inbox/runtime_inbox_context_loader.py` | RETAIN + TRIM | 删除 DeviceCommand 上下文加载 |
| `src/app/runtime/orchestration/services/session/session_resolver.py` | RETAIN + TRIM | 删除旧 command/session 归属，其他 session 解析保留 |
| `src/app/runtime/orchestration/services/reconciliation/runtime_reconciliation_service_impl.py` | RETAIN + TRIM | 删除 device callback/timeout 分支；Phase 7 reconciliation successor |
| `src/app/runtime/orchestration/services/hold/runtime_hold_query_service.py` | RETAIN + TRIM | 删除 DeviceCommand 旧状态读取，通用 hold 保留 |
| `src/app/runtime/orchestration/services/hold/runtime_hold_release_service.py` | RETAIN + TRIM | 删除旧 device replay/cancel，通用 hold 保留 |
| `src/app/runtime/orchestration/services/trace/trace_query_service.py` | RETAIN + TRIM | 改读 typed outcome/evidence，非设备 trace 保留 |
| `src/app/workline/services/write_back_service.py` | RETAIN + TRIM | 删除 DeviceCommandGateway 调用；未来插件显式调用 Port |
| `src/app/workline/services/safety_service.py` | RETAIN + TRIM | 删除旧 current_command_id 判断；只保留 WorkLine 安全边界 |
| `src/app/workline/repositories/workline_repository.py` | RETAIN + TRIM | 删除 Device 行运行态 join；静态 WorkLine owner 保留 |
| `src/app/sys/models/outbox.py` | RETAIN + TRIM | 删除 DEVICE_COMMAND enum/索引；WMS/其他 Outbox 不动 |
| `src/app/sys/repositories/outbox_repository.py` | RETAIN + TRIM | 删除设备 head-of-line/active 状态查询；其他 lane 不动 |
| `src/app/sys/services/outbox_engine.py` | RETAIN + TRIM | 删除 device dispatch；WMS/其他 dispatch 不动 |
| `src/celery_app/outbox_dispatch_composition.py` | RETAIN + TRIM | 不再构建设备 Gateway；其他 Outbox scope 保留 |
| `src/celery_app/tasks/sys.py` | RETAIN | 不承载新 DeviceCommand；其他 Outbox task 保留 |
| `src/celery_app/tasks/workline.py` | RETAIN + TRIM | 删除 command timeout 和 heartbeat 扫描；新 task successor |
| `src/celery_app/tasks/runtime_inbox.py` | RETAIN + TRIM | 删除 DeviceCommand mapper/import；RuntimeInbox task 保留 |
| `src/celery_app/config.py` | MODIFY | 删除旧 device heartbeat/通用 outbox 设备路由，加入三个固定任务 |
| `src/register.py` | MODIFY | 唯一 runtime lifecycle 和 callback router 装配 |
| `migrations/env.py` | MODIFY | 导入最终 Epoch/DeviceCommand/evidence metadata |

旧 Alembic revisions 保持历史文件不改；Phase 7 只新增一条 generator 生成的直接 schema cutover revision。历史 revision chain 在 Phase 11 统一重建。

## 8. 测试 owner 与旧测试处置

### 8.1 最终测试 owner

| 验收面 | 唯一 owner |
| --- | --- |
| 状态机、identity、payload digest、deadline、单设备互斥、claim/lease、ACK/CALLBACK、unknown | `tests/runtime/device_command/` |
| Epoch 和不可变 device/contract binding | `tests/workline/test_line_run_epoch.py` |
| 固定路径、公共包络、状态准入、错误和 Client 生命周期 | `tests/contracts/device/test_uniform_ecs_wire.py` |
| callback route、body 上限、HTTP/响应 DTO/OpenAPI | `tests/api/test_device_ecs_callbacks.py` |
| PostgreSQL partial unique、事务、并发 claim、evidence idempotency | `tests/integration/device_command/` |
| broker/worker/ECS fake/callback/PostgreSQL 闭环 | `tests/e2e/device_command/test_device_command_production_wiring.py` |
| 供应商附录和真实 ECS 行为 | 供应商一致性验收，不进入本仓库核心测试 |
| 入库/出库何时创建命令及结果后如何推进 | Phase 8/9 插件测试，不进入核心 `tests/` |

### 8.2 当前测试逐文件处置

目标测试必须先建立并通过，再删除旧 owner：

| 当前测试 | 处置 |
| --- | --- |
| `tests/contracts/device/test_device_command_ecs_contract.py` | REWRITE → `test_uniform_ecs_wire.py` |
| `tests/contracts/device/test_device_command_idempotency_contract.py` | REWRITE → runtime + integration final owners |
| `tests/contracts/device/test_device_command_service_contract.py` | REWRITE → `tests/runtime/device_command/` |
| `tests/device/test_device_service_runtime_state.py` | DELETE → final status observation/Epoch tests；静态 Device CRUD 另有既有 owner |
| `tests/architecture/test_device_command_boundary_guardrail.py` | REWRITE → final boundary + absence rules |
| `tests/api/test_callback_result_api.py` | SPLIT：device 场景迁入 `test_device_ecs_callbacks.py`，非设备 owner 保留 |
| `tests/api/test_callback_event_api.py` | SPLIT：device 场景迁入 `test_device_ecs_callbacks.py`，WMS/external 场景保留 |
| `tests/api/test_callback_idempotency.py` | SPLIT：device 幂等迁入 final evidence owner |
| `tests/api/test_qa_callback_audit_status_regression.py` | REWRITE device route 断言，非设备断言保留 |
| `tests/api/test_workline_runtime_sse.py` | DELETE device command 旧 SSE 场景 → NONE；不以监控事件证明命令核心 |
| `tests/runtime/orchestration/test_device_command_gateway.py` | DELETE → uniform adapter/dispatch service tests |
| `tests/runtime/orchestration/test_device_command_result_observability.py` | DELETE → typed outcome/evidence 诊断测试 |
| `tests/runtime/orchestration/test_command_result_correlation_authority.py` | DELETE → `execution_ref` 和 result evidence owner |
| `tests/runtime/orchestration/test_runtime_recovery_policies.py` | SPLIT：DeviceCommand lease 场景迁入 final repository；其他 runtime owner 保留 |
| `tests/runtime/orchestration/test_runtime_inbox_timeout_scanner.py` | DELETE device timeout 分支 → final reconcile worker |
| `tests/runtime/orchestration/test_runtime_inbox_timer_reconciliation_flow.py` | DELETE device 分支 → final reconciliation integration |
| `tests/callback/test_callback_runtime_inbox_authority.py` | SPLIT：device callback 不再进入 RuntimeInbox；非设备 owner 保留 |
| `tests/integration/test_command_result_correlation_authority.py` | DELETE → final evidence transaction owner |
| `tests/integration/test_device_runtime_projection_writer_service.py` | DELETE → status observation integration |
| `tests/integration/test_system_outbox_dispatch_concurrency.py` | DELETE DEVICE_COMMAND 参数 → final command claim integration；其他 lane 保留 |
| `tests/integration/test_system_outbox_repository.py` | DELETE DEVICE_COMMAND 场景；其他 lane 保留 |
| `tests/migrations/test_phase1_device_fk_ring_dissolve.py` | DELETE → NONE；旧 schema 历史不进入目标测试 |
| `tests/workline_runtime/system_capabilities/test_device_command_authoritative_precondition.py` | DELETE → final create-command/Epoch tests |
| `tests/workline_runtime/system_capabilities/test_system_capability_effect_service.py` | DELETE DEVICE_COMMAND 场景；其他 capability owner 保留 |
| `tests/workline_runtime/test_dispatch_attempt_lease_fencing.py` | DELETE device 场景 → final command claim integration |
| `tests/workline_runtime/test_runtime_reconciliation_idempotency.py` | DELETE device 场景 → final evidence/reconcile tests |
| `tests/workline_runtime/test_system_outbox_resource_wait_contract.py` | DELETE DEVICE_COMMAND 场景；其他 Outbox owner 保留 |
| `tests/workline_runtime/test_workline_runtime_status_projection_service.py` | DELETE current_command_id 场景 → NONE |
| `tests/resilience/test_runtime_scenario_replay.py` | DELETE device replay 场景 → delivery unknown 禁止重放测试；其他场景保留 |
| `tests/support/sqlmodel_metadata.py` | MODIFY → 导入 final Epoch/command/evidence 模型 |
| `tests/integration/conftest.py` | MODIFY → final metadata/isolated DB fixture；不得使默认 FAST 依赖 DB |

删除测试的 Commit/PR 必须逐项标注本表 successor；标记 `NONE` 的测试不得通过保留旧 shim 维持。

## 9. Schema cutover

先运行 `uv run alembic revision -m "rebuild device command ecs lifecycle"` 生成 revision，再编辑生成文件；不得手写 revision ID。

升级只做目标 schema 直接替换：

1. 删除旧 `device_commands` 表及其约束/索引，按第 4 节重建；不复制旧数据。
2. 删除旧 `device_runtime_projections` 表。
3. 从 `devices` 删除 `vendor_type`、`capabilities_json`、`host`、`port`、`protocol`、`auth_token`、`timeout`、`callback_path`、`device_status`、`current_command_id`、`last_heartbeat_at`、`error_code`、`maintenance_mode`、`max_concurrent_tasks`、`idempotency_ttl`。
4. 新建 `line_run_epochs`、`line_run_epoch_device_bindings`、`device_commands`、`device_status_observations`、`device_evidences`、`device_evidence_conflicts`。
5. 删除 SystemOutbox 的 DEVICE_COMMAND partial indexes/check 分支和相关 resource-wait 元数据路径；不删除 SystemOutbox 表或 WMS lane。
6. 不提供 downgrade 还原、数据回填、桥接表或兼容视图。

在临时空 PostgreSQL 数据库执行 `uv run alembic upgrade head`，再比对 SQLModel metadata 与真实 schema。已有开发/测试数据允许直接清空。

## 10. 实施任务与 Commit 边界

### Task 1：TDD 建立 Epoch、最终模型与数据库约束

**Files:** 第 3.1 节 WorkLine/model 文件、`device/models/{command,evidence}.py`、对应 Repository 和 runtime/integration tests。

- [ ] 建立失败测试：同 WorkLine 单 ACTIVE Epoch、同 Epoch 单 device binding、binding 不可改写。
- [ ] 建立失败测试：同 `device_code` 在四个未闭合状态只能有一个命令；不同 device 可并行。
- [ ] 建立失败测试：command identity 相同 payload 幂等、不同 payload 冲突；`RECONCILING` 不释放设备槽。
- [ ] 运行目标测试确认失败原因来自目标模型/约束尚不存在。
- [ ] 实现最小模型、Repository 和 Service；API、Celery 和旧 owner 保持不动。
- [ ] 运行 `uv run pytest tests/workline/test_line_run_epoch.py tests/runtime/device_command tests/integration/device_command -q`，确认通过。
- [ ] Commit：`feat(device): 建立命令与运行代际可靠模型`。

### Task 2：TDD 建立统一 ECS Adapter 与状态准入

**Files:** `src/app/device/{contracts,ecs_adapter}.py`、dispatch service、uniform wire tests。

- [ ] 建立 command/status 固定路径、闭集包络、256 KiB 响应、无认证、一个长期 Client 测试。
- [ ] 建立新鲜 `AUTO+IDLE+current_command_code=null`、合同/Epoch 匹配和 stale/unknown 拒绝测试。
- [ ] 建立 HTTP 分类测试：200 ACK、429 明确未接纳、4xx 合同失败、network/timeout/5xx delivery unknown。
- [ ] 运行测试确认旧可配置路径、Token 和 Gateway 行为不满足目标合同。
- [ ] 实现 Adapter 和 dispatch service；不加入供应商 DTO、自动 HTTP 重试或 endpoint registry。
- [ ] 运行 `uv run pytest tests/contracts/device/test_uniform_ecs_wire.py tests/runtime/device_command -q`。
- [ ] Commit：`feat(device): 交付统一 ECS 派发与状态准入`。

### Task 3：TDD 建立 callback evidence 与 Epoch fencing

**Files:** evidence model/repository/service、`device/v1/ecs_callback.py`、API/contract/integration tests。

- [ ] 建立解码前 body 上限、公共包络、unknown command、duplicate/conflict、部署级 source identity 测试。
- [ ] 建立 evidence commit 后 ACK、旧 Epoch/零 Epoch、结果唯一终态和迟到结果测试。
- [ ] 建立 `execution_ref` 只关联不解释业务、result/event typed port 不导入业务包的测试。
- [ ] 实现两个固定 WES route 和 evidence apply；从旧 callback ingress 移交 device 分支。
- [ ] 运行 `uv run pytest tests/api/test_device_ecs_callbacks.py tests/contracts/device tests/runtime/device_command tests/integration/device_command -q`。
- [ ] Commit：`feat(device): 建立设备证据与回调闭环`。

### Task 4：TDD 建立三个 worker 和生产 Composition Root

**Files:** `device/composition.py`、`tasks/device_command.py`、Celery config/app、register、deployment/e2e tests。

- [ ] 建立 runtime 单 Client、FastAPI/Celery lifecycle、固定 queue 和三个任务的失败测试。
- [ ] 建立真实 broker → uniform ECS fake → callback → evidence worker → PostgreSQL 闭环测试。
- [ ] 建立零设备绑定启动成功、命令 fail closed 且无 ECS 请求测试。
- [ ] 实现 runtime/worker/route 装配；Beat 仅扫描，worker payload 不携带命令快照。
- [ ] 运行 deployment、E2E 和受影响 integration。
- [ ] Commit：`feat(device): 接入设备命令生产运行时`。

### Task 5：原子删除旧 DeviceCommand 执行闭包与切换 schema

**Files:** 第 7、8、9 节列出的所有旧 owner、生成的 Alembic revision、absence tests、HEAVY mapping。

- [ ] 确认 Task 1–4 目标测试全部通过。
- [ ] 生成并编辑单一 schema cutover revision，在临时空库升级验证。
- [ ] 按第 7 节删除 Gateway、SystemCapability、RuntimeIntentLog/SystemOutbox 设备分支、旧 callback 和旧配置。
- [ ] 按第 8 节迁移或删除旧测试；不得恢复 shim、alias、re-export 或兼容字段。
- [ ] 更新 HEAVY mapping，使所有新生产文件、迁移和测试支撑资产有精确 owner。
- [ ] 运行缺席扫描、模型/schema 比对和目标 FAST/integration。
- [ ] Commit：`refactor(device): 原子切换 DeviceCommand ECS 闭环`。

### Task 6：验证入库/出库下游适用性且不引入业务耦合

**Files:** application port tests、architecture guardrail、device contract docs；不修改两份 ReviewRequired 业务合同正文。

- [ ] 用纯领域 fixture 验证不同 `execution_ref_type/id` 可关联不同后续业务对象，但核心不按其值分支。
- [ ] 验证不同 `device_code` 可并行、同一 `device_code` 串行，支撑双机械臂而不建立扫码台软件锁。
- [ ] 验证 params 可承载附录批准的逻辑位置，但拒绝 PLC 点位、坐标、速度和硬件控制字段。
- [ ] 验证核心源码/测试不导入 PickingTask、入库/出库 operation、GRN、业务 NG 或供应商 fixture。
- [ ] Commit：`test(device): 验证业务无关设备应用端口`。

### Task 7：最终质量、运行态和文档验收

- [ ] 运行第 11 节全部命令，任何 skip 都不计通过。
- [ ] 验证最终镜像仅安装统一 Device/ECS runtime，不含 mock、旧 Gateway 或 no-op consumer。
- [ ] 更新 SRS、file index、runbook、CHANGELOG 和 superpowers 生命周期索引；纯文档不新增 pytest。
- [ ] GitNexus `detect_changes` 确认变更只覆盖 Phase 7 owner；HIGH/CRITICAL 新风险必须重新评审。
- [ ] Commit：`docs(device): 完成 Phase 7 验收收口`。

## 11. 精确验证命令

### 11.1 FAST 与架构

```bash
uv run pytest tests/workline/test_line_run_epoch.py tests/runtime/device_command tests/contracts/device tests/api/test_device_ecs_callbacks.py -q
uv run pytest tests/architecture/test_suite_topology_guardrail.py tests/architecture/test_core_plugin_test_ownership_guardrail.py tests/architecture/test_device_command_boundary_guardrail.py tests/architecture/test_device_command_legacy_absence.py -q
uv run pytest --collect-only -q -o addopts='' | tail -5
```

### 11.2 PostgreSQL integration

使用独立临时 PostgreSQL 数据库，不得复用业务库：

```bash
ALEMBIC_DATABASE_URL="$DEVICE_COMMAND_TEST_DATABASE_URL" uv run alembic upgrade head
RUN_WORKLINE_INTEGRATION=1 INTEGRATION_DATABASE_URL="$DEVICE_COMMAND_TEST_DATABASE_URL" uv run pytest tests/integration/device_command -q
```

环境变量必须指向经安全检查确认的临时数据库；测试结束删除临时数据库，不修改其他 worktree 的 `.env`。

### 11.3 E2E 与运行态

```bash
RUN_WORKLINE_INTEGRATION=1 INTEGRATION_DATABASE_URL="$DEVICE_COMMAND_TEST_DATABASE_URL" uv run pytest tests/e2e/device_command/test_device_command_production_wiring.py -q
uv run pytest tests/deployment/test_device_command_startup.py tests/deployment/test_celery_task_runtime_contract.py -q
```

E2E 必须实际经过 broker、`device-command` worker、uniform ECS fake、FastAPI callback、evidence worker 和 PostgreSQL；直接调用 Service 或空批次 smoke 不合格。

### 11.4 HEAVY 与质量

```bash
uv run scripts/select_heavy_tests.py --scope unstaged
uv run pytest tests/scripts -q
./scripts/git-quality-gate.sh --profile quality
uv run ruff format --check .
uv run ruff check .
uv run bandit -r src/
```

按 selector 输出显式运行全部受影响 HEAVY。集成环境缺失导致的 skip 是未验收，不是通过。

### 11.5 旧 owner 缺席门禁

```bash
rg -n "DeviceCommandGateway|device_command_write|SystemOutboxDispatchType\.DEVICE_COMMAND|SystemOutboxTargetType\.DEVICE" src tests
rg -n "current_command_id|callback_path|auth_token|DeviceProtocol|vendor_type" src/app/device
rg -n "RuntimeIntentLog|SystemOutbox|httpx\.AsyncClient" src/app/device
rg -n "PICKING|GRN|ROUGH_SORT|SMT|FANUC|KEYENCE" tests/runtime/device_command tests/contracts/device
```

前三条必须零结果；第四条必须零业务/供应商耦合结果。允许的合同文档字面量不计生产引用。

## 12. 提交顺序与失败处理

固定顺序：模型/约束 → Adapter/准入 → evidence/callback → worker/runtime → 原子 schema/旧 owner 切换 → 下游适用性 → 验收文档。不得把旧 owner 删除提前到目标测试通过之前，也不得把兼容层作为中间 Commit。

失败处理：

- 任一 identity、Epoch、物理终态或 delivery unknown 场景不明确时停止代码实施，先修订 Approved 合同或本计划；
- 新增文件超过本计划清单时先解释唯一责任，禁止临时 helper 扩散；
- 发现 SystemOutbox/RuntimeInbox 非设备职责被影响时撤回该删除，缩小到 DEVICE_COMMAND 分支；
- 发现业务合同要求核心解释业务字段时拒绝扩张 Phase 7，转交 Phase 8/9 插件计划；
- 发现供应商差异时转交设备合同附录或 ECS/网关，不新增 WES 私有 Adapter。

## 13. Phase 7 退出门禁

1. 生产只存在一个 `DeviceCommand` 聚合、一个统一 `EcsAdapter`、一个 Composition Root 和两个固定 WES callback handler。
2. 每个 `device_code` 最多一个未闭合命令；状态/Epoch/合同不可信时失败关闭。
3. ACK 不推进物理终态；只有匹配 CALLBACK 或经审计的人工对账可闭合。
4. duplicate 返回首次结果；conflict、unknown、迟到和 delivery unknown 不自动重放或推进。
5. 零 Epoch 事件可诊断但不调用插件；旧 Epoch 证据不绑定当前 Epoch。
6. 旧 DeviceCommand Gateway、RuntimeIntentLog/SystemOutbox 设备分支、可配置路径、旧 identity/状态/认证和裸 Client 零引用。
7. 核心、供应商一致性和业务插件测试所有权不重叠。
8. FAST、QUALITY、精确 HEAVY、PostgreSQL、broker E2E、运行态 smoke 和缺席门禁全部通过且无 skip。
9. 零业务插件、零设备绑定可启动；未绑定设备不访问 ECS。
10. Phase 8/9 可仅通过显式应用端口关联各自执行对象，Phase 7 无入库/出库业务分支。

## 14. 批准结论

本计划已冻结当前引用图、逐文件 successor/`NONE`、最终模型与事务、三个 worker、唯一生产装配、schema cutover、测试 owner、精确验证命令和 Commit 边界。Phase 6 已完成，统一设备合同与 wire 已批准；Phase 7 可以从批准基线开始代码实施。

实现仍必须逐任务用 TDD 证明，不能把本文 `Approved` 误读为代码已完成、设备已联调或业务插件已交付。
