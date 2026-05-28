<!-- /autoplan restore point: /Users/kaizhou/.gstack/projects/kaizhoumasha-wes_backend/develop-autoplan-restore-20260427-103032.md -->

# WORKLINE 记录与诊断体系改进计划

## 目标

把当前 WORKLINE 记录体系从“事后可追日志”升级为“现场可诊断账本”。

目标用户包括：

- 现场实施工程师：看到异常后 1 分钟内判断卡在入口、编排、插件、派发、设备还是 WMS。
- 后端工程师：能用一条 trace 查询复盘完整因果链，不靠 grep 日志拼图。
- 设备供应商：能基于 `command_code` 做幂等去重，并在 result 中原样回传链路字段。
- 仓库运营人员：能追溯 SMT 料卷、批次、料架、库位、MSD/高值标识和设备动作证据。
- 测试与调试人员：能在 SANDBOX 下走同一套 Session、Outbox、Callback 链路，不触碰真实设备。

记忆点：任何 accepted event 都必须能归因到一条 trace；任何失败都必须能生成一张可操作的诊断卡。

## 当前事实

当前系统已经有正确的主干：

- `callback_logs`：FastAPI callback 入口原始证据，已有 `request_id`、`trace_id`、`ingress_outcome`、`failure_stage`。
- `workline_inbox`：编排唯一入口，承载设备事件、命令结果、外部 HTTP、超时、人工操作、重放请求。
- `workline_sessions`：业务状态机实例，已有 `run_mode`、`business_key`、`context_json`、`contract_version`、等待和失败字段。
- `workline_timelines`：状态迁移、决策、派发准备、等待、失败等业务时间线。
- `device_commands`：设备命令控制流，已有 `command_code`、`trace_id`、`plugin_key`、`contract_version`、ACK 和结果字段。
- `system_outbox`：副作用派发队列，承载设备命令、外部 HTTP 和内部信号。

当前协议也已经确立两层包络：

- `callback/event` 顶层只放 `device_code`、`event_type`、`timestamp`、`data`。
- `callback/result` 顶层只放 `command_code`、`device_code`、`result`、`finish_time`、`data`、`error_detail`。
- WES 下发 command 顶层只放协议字段，业务字段放 `params`。
- SANDBOX 不向 payload 增加 `sandbox` 标志，依靠 `run_mode=SIMULATION` 改变派发出口。

主要缺口：

- `trace_id` 语义承担了 trace，但命名和约束不够硬。
- 缺 `event_id` / `causation_id`，无法稳定表达事件身份和直接原因。
- 诊断模型已在 `src/workline_runtime/diagnostics/` 存在，但诊断不是持久事实。
- 派发只有 outbox 当前状态，缺逐次 attempt 账本。
- SANDBOX 只有 dispatcher 分流，缺调试工作台 API 和人工操作审计。
- SSE 是 Redis Pub/Sub 轻通知，不能当事实源。
- SMT/WMS 业务证据多在 payload/timeline 中，缺稳定结构化归档。

## 第一性原理

软硬件集成系统的记录体系不是“日志系统”，而是物理世界事实、软件决策和副作用的共同账本。

每个进入系统的 event 必须回答：

1. 它是谁：`event_id`。
2. 它属于哪条链：`trace_id`。
3. 它由谁直接触发：`causation_id`。
4. 它代表哪个业务对象：`business_key`。
5. 它是否进入状态机：`session_id`。
6. 它造成了什么副作用：`command_code` / `outbox_id`。
7. 它现在卡在哪里：`blocking_point`。
8. 它能否重放、重试或人工接管：`recoverability`。

因此，链路键必须分层，不能让一个字段承担所有含义。

## 目标链路键

### 核心字段

| 字段 | 含义 | 约束 |
| --- | --- | --- |
| `event_id` | 归一化事件唯一 ID | 每条 inbox 非空唯一 |
| `trace_id` | 端到端因果链 ID | accepted event 非空 |
| `causation_id` | 直接原因 ID | 可空；result/replay/manual 必填 |
| `parent_event_id` | 上游事件 ID | 可空；用于重放和树形追踪 |
| `business_key` | SMT/WMS 业务对象键 | 可空；能解析则必须写入 |
| `session_id` | Workline 状态机实例 | 路由成功后写入 |
| `command_code` | 设备控制流主键 | 设备命令和 result 必填 |
| `dispatch_key` | outbox 派发幂等键 | outbox 非空唯一 |

### 命名决策

未发布系统不需要保留模糊命名。WORKLINE 运行域只使用 `trace_id` 表达端到端链路。

本分支采用破坏式收口方式完整交付当前计划项：

- 表字段、模型、服务签名、API 响应、测试和 mock 只保留 `trace_id`。
- Runtime API、TraceContext、DiagnosticContext 使用 `trace_id`。
- callback 入站只接受 `trace_id`，不再读取旧命名。
- 迁移负责删除遗留列，不做旧字段回填或双写。

## 数据模型改进

### 1. callback_logs

新增字段：

- `event_id`
- `trace_id`
- `payload_hash`
- `normalized_payload_json`
- `protocol_version`
- `source_message_id`
- `related_inbox_id`
- `related_command_id`
- `diagnostic_id`

规则：

- 入口拒绝的请求也必须有 `trace_id` 和诊断记录。
- 原始 body 保留为证据，normalized payload 用于诊断和重放。
- `request_id` 只代表来源侧请求，不再承担业务链路含义。

### 2. workline_inbox

新增或调整字段：

- `event_id` 非空唯一。
- `trace_id` 非空。
- `causation_id`
- `parent_event_id`
- `business_key`
- `canonical_event_type`
- `payload_hash`
- `normalized_payload_json`
- `schema_version`
- `run_mode`
- `replay_of_event_id`

约束：

- `idempotency_key` 加唯一约束。
- `event_id` 加唯一约束。
- `trace_id, received_at` 建组合索引。
- `status, next_retry_at` 建组合索引，服务重试和 dead letter 处理。

### 3. workline_sessions

新增或调整字段：

- `trace_id` 非空。
- `root_event_id`
- `last_event_id`
- `blocking_point`
- `blocking_since`
- `first_failure_diagnostic_id`
- `last_diagnostic_id`

规则：

- 一个 session 属于一条 trace。
- 一个 trace 可以有多个 session，例如人工拆分、补偿、并行设备动作。
- `run_mode` 是 session 快照，不允许运行中被 WorkLine 配置漂移影响。

### 4. workline_timelines

新增字段：

- `event_id`
- `trace_id`
- `causation_id`
- `diagnostic_id`
- `business_key`

约束：

- `session_id, seq_no` 唯一。
- `trace_id, occurred_at` 索引。
- `failure_domain, status, occurred_at` 索引。

规则：

- Timeline 不只是审计日志，它是业务解释层。
- 业务 NG 写 `DECISION_MADE` 或 `BUSINESS_DECISION`，不要写系统 failure。
- 系统异常必须关联 diagnostic。

### 5. device_commands

调整：

- `session_id` 从字符串改为整数外键。
- 新增 `trace_id`。
- 新增 `event_id`，表示创建该命令的事件。
- 新增 `causation_id`，通常等于触发命令的 event/timeline decision。
- 新增 `protocol_version`。
- 新增 `issued_at`、`expires_at`，替代模糊 `timestamp` 语义。

规则：

- `command_code` 继续是设备侧幂等主键。
- `trace_id` 只做链路归因，不替代 `command_code`。
- `ACK_RECEIVED`、`COMPLETED` 和 outbox `SENT` 语义必须分清：
  - outbox `SENT`：WES 已把请求发给设备服务。
  - command `ACK_RECEIVED`：设备服务接受任务。
  - command `COMPLETED`：设备物理动作完成并 callback/result。

### 6. system_outbox

新增字段：

- `trace_id`
- `event_id`
- `causation_id`
- `run_mode`
- `protocol_version`
- `payload_hash`
- `first_attempt_at`
- `last_attempt_id`
- `diagnostic_id`

约束：

- `dispatch_key` 唯一。
- `trace_id, created_at` 索引。
- `status, next_retry_at` 索引。
- `run_mode, status, created_at` 索引，服务 SANDBOX pending 列表。

### 7. 新增 workline_diagnostics

诊断必须成为一等事实。

建议字段：

- `diagnostic_id`
- `occurred_at`
- `trace_id`
- `event_id`
- `causation_id`
- `session_id`
- `inbox_id`
- `outbox_id`
- `command_id`
- `workline_id`
- `device_id`
- `device_code`
- `plugin_key`
- `contract_version`
- `run_mode`
- `error_domain`
- `error_code`
- `severity`
- `recoverability`
- `problem_class`
- `failure_stage`
- `message`
- `operator_action`
- `next_steps_json`
- `evidence_json`

规则：

- 所有失败路径必须创建 diagnostic。
- 一条 diagnostic 可以被 callback、inbox、timeline、command 或 outbox 引用。
- 诊断卡片应能直接回答：问题在哪、谁负责、下一步做什么、能否自动恢复。

### 8. 新增 workline_dispatch_attempts

Outbox 当前状态不足以诊断网络、设备、供应商接口问题。每次派发必须有 attempt。

建议字段：

- `id`
- `outbox_id`
- `attempt_no`
- `trace_id`
- `run_mode`
- `channel`：`LIVE` / `SANDBOX`
- `target_type`
- `target_code`
- `request_url`
- `request_headers_json`
- `request_payload_hash`
- `started_at`
- `finished_at`
- `latency_ms`
- `response_status`
- `response_body_hash`
- `response_body_sample`
- `exception_type`
- `exception_message`
- `operator_id`
- `diagnostic_id`

规则：

- LIVE 和 SANDBOX 都写 attempt。
- SANDBOX 人工处理也必须写 attempt，记录 operator、处理时间、结果模板。
- 不在 attempt 中保存无限大响应体，只保留 hash 和有限 sample。

## 设备指令顶层包络

当前顶层为：

```json
{
  "device_code": "ARM01",
  "command_code": "CMD-...",
  "task_type": "MEASUREMENT_REEL",
  "priority": 5,
  "timeout": 300000,
  "timestamp": 1777286400000,
  "params": {}
}
```

建议升级为：

```json
{
  "protocol_version": "wes-device-command.v1",
  "trace_id": "TRC-20260427-000001",
  "causation_id": "EVT-20260427-000088",
  "command_code": "CMD-20260427-MEASUREMENT_REEL-8F3A91C2",
  "device_code": "ARM01",
  "command_type": "MEASUREMENT_REEL",
  "priority": 5,
  "issued_at": 1777286400000,
  "expires_at": 1777286700000,
  "timeout_ms": 300000,
  "contract_version": "smt_classifier.v1",
  "callback_url": "http://wes/api/v1/callback/result",
  "params": {}
}
```

规则：

- `params` 继续承载业务命令字段。
- 不把 `session_id`、`inbox_id`、`outbox_id`、`command_id` 暴露给设备。
- 不向 payload 写 `sandbox`。
- 设备 result 应原样回传 `command_code`，建议同时回传 `trace_id` 和 `causation_id`。
- `task_type` 与 `command_type` 不应长期并存。建议破坏性改为 `command_type`。

## FastAPI 改进

新增诊断 API：

- `GET /api/v1/workline/traces/{trace_id}`
- `GET /api/v1/workline/traces/{trace_id}/blocking-point`
- `GET /api/v1/workline/traces/{trace_id}/diagnostics`
- `GET /api/v1/workline/events/{event_id}`
- `GET /api/v1/workline/commands/{command_code}/trace`

新增 SANDBOX API：

- `GET /api/v1/workline/sandbox/outboxes`
- `GET /api/v1/workline/sandbox/outboxes/{outbox_id}`
- `POST /api/v1/workline/sandbox/outboxes/{outbox_id}/callback-template`
- `POST /api/v1/workline/sandbox/outboxes/{outbox_id}/complete`
- `POST /api/v1/workline/inbox/{inbox_id}/replay`
- `POST /api/v1/workline/sessions/{session_id}/manual-hold`
- `POST /api/v1/workline/sessions/{session_id}/resume`
- `POST /api/v1/workline/sessions/{session_id}/cancel`

API 规则：

- API 层不直接查数据库，遵守现有 API -> Service -> Repository 分层。
- replay 不修改旧 inbox，只创建新 inbox，并设置 `replay_of_event_id`。
- manual 操作也通过 inbox 进入编排，不绕过状态机。

## SSE 改进

SSE 只做通知，不做事实源。

事件类型建议：

- `workline.trace.updated`
- `workline.event.accepted`
- `workline.diagnostic.raised`
- `workline.inbox.dead_lettered`
- `workline.outbox.failed`
- `workline.sandbox.outbox.pending`
- `device.command.acked`
- `device.command.completed`
- `device.status.changed`

payload 只包含：

```json
{
  "trace_id": "TRC-...",
  "event_id": "EVT-...",
  "session_id": 123,
  "command_code": "CMD-...",
  "diagnostic_id": "DIA-...",
  "version": 7
}
```

客户端收到 SSE 后通过 FastAPI 拉详情。这样 Redis Pub/Sub 丢消息不会破坏事实一致性。

## SANDBOX 工作台

SANDBOX 的目标不是 dry-run，而是在不触碰真实设备的情况下跑完整 WORKLINE 链路。

必须具备：

- 按 `run_mode=SIMULATION` 查询 pending outbox。
- 展示真实 payload，不注入 sandbox 字段。
- 自动生成 `callback/result` 模板。
- 支持填写 result/data/error_detail 后回灌 WES。
- 每次人工处理写 `workline_dispatch_attempts`。
- 记录 operator、处理原因、处理时间、结果。
- 支持从某个 event replay，但 replay 创建新 event，不篡改旧事实。

验收口径：

- 同一个事件在 LIVE 和 SANDBOX 下除派发出口外，Session、Timeline、Command、Outbox 结构一致。
- SANDBOX 回传 result 后能推动同一个 session 继续执行。
- 前端只看 SANDBOX API 即可完成调试，不需要手工 curl 和查库。

## SMT/WMS 业务证据

SMT 仓储标准要求的核心是：物料、批次、位置、设备动作和 WMS 结果可复核。

建议新增 `workline_business_facts`，或先用标准化 timeline payload 过渡。

建议字段：

- `trace_id`
- `event_id`
- `session_id`
- `business_key`
- `fact_type`
- `hhpn`
- `mfrpn`
- `qty`
- `date_code`
- `lot_code`
- `pkg_id`
- `material_code`
- `rack_code`
- `bin_code`
- `location_code`
- `zone_code`
- `msd_level`
- `is_high_value`
- `is_pcb`
- `is_irregular`
- `wms_request_id`
- `wms_response_snapshot`
- `decision`
- `evidence_json`

规则：

- WMS 是库存真相源，WES 记录 WMS 查询和决策证据，不自行成为库存主数据。
- 业务 NG 和系统异常严格分离。
- SixInOne 不完整是业务数据质量问题，应有明确 `DATA_QUALITY` 诊断。
- 料卷入库、分拣、上架、出库、异常剔除都应能用 `business_key` 和 `trace_id` 双入口查询。

## 运维与治理目标

上面未提及但必须达成：

### 审计

- 所有 manual/sandbox/replay 操作记录用户、原因、前后状态。
- 高风险操作必须可追溯到用户和 trace。

### 告警

- dead letter、outbox 连续失败、设备无 ACK、session 等待超时必须产生 diagnostic 和 SSE。
- 告警规则基于 diagnostic，不基于文本日志。

### 数据保留

- 原始 payload 保留周期应可配置。
- 大响应体只保留 hash 和 sample。
- SMT 物料、供应商、批次信息按业务要求保留，但避免无限期保存调试噪声。

### 协议治理

- 每个 command/event/result 包络有 `protocol_version`。
- 每个 `params` / `data` 有 `contract_version` 或 schema version。
- 供应商 mock 和验收测试必须覆盖包络 allowlist。

### 并发与幂等

- 幂等依靠数据库唯一约束，不只靠服务层查询。
- 同一设备并发派发必须有锁或队列策略。
- timeline seq 必须由数据库约束兜底。

### 性能

- Trace 查询需要覆盖索引，避免按 payload JSON 扫描。
- 热路径只写必要事实，大型诊断详情放 evidence/sample。
- SSE 发布必须 after commit，避免前端看见未提交状态。

## PR 拆分计划

### PR 0：术语和链路键收敛

目标：把 `trace_id` 收敛为 `trace_id`，引入 `event_id` 和 `causation_id`。

改动：

- 更新 TraceContext、DiagnosticContext、NormalizedEvent/Result/External。
- 表模型增加 `trace_id`、`event_id`、`causation_id`。
- callback/event accepted 时生成 `event_id` 和 `trace_id`。
- callback/result 从 `command_code` 继承 `trace_id`，并生成 result `event_id`。
- timer/manual/replay 必须带 `causation_id`。

验收：

- 任一 accepted event 都能通过 `trace_id` 查询完整链路。
- 路由失败事件也有 `trace_id` 和 callback log。

### PR 1：数据库约束和类型修正

目标：让诊断依赖的事实不可重复、不可乱序。

改动：

- `workline_inbox.event_id` unique。
- `workline_inbox.idempotency_key` unique。
- `workline_timelines(session_id, seq_no)` unique。
- `system_outbox.dispatch_key` unique。
- `device_commands.session_id` 改整数 FK。
- 补充 trace 相关索引。

验收：

- 并发重复 callback 只能产生一个有效 inbox。
- timeline 并发写入不会出现同一 session 重复 seq。

### PR 2：持久诊断表

目标：把现有 runtime diagnostics 从日志升级为事实。

改动：

- 新增 `workline_diagnostics` 模型、Repository、Service。
- `_log_diagnostic` 改为写诊断表并返回 `diagnostic_id`。
- callback/inbox/outbox/command/timeline 失败路径关联 `diagnostic_id`。
- Trace API 返回诊断卡片和 first failure。

验收：

- CALLBACK_SCHEMA_INVALID、PLUGIN_EXECUTION_FAILED、OUTBOX_DISPATCH_FAILED、DEVICE_TIMEOUT 均有持久诊断。
- 诊断卡能给出 failure stage、责任域、恢复建议和证据。

### PR 3：派发尝试账本

目标：能解释每次派发到底发生了什么。

改动：

- 新增 `workline_dispatch_attempts`。
- LIVE 设备命令、外部 HTTP、SANDBOX 派发均写 attempt。
- outbox 记录 `first_attempt_at`、`last_attempt_id`。
- 失败 attempt 自动创建 diagnostic。

验收：

- 网络异常、HTTP 4xx/5xx、设备 busy、SANDBOX 人工完成都有 attempt 记录。
- trace 页面能看到 attempts 时间线。

### PR 4：设备命令包络升级

目标：下发指令携带清晰链路和协议版本。

改动：

- `_DEVICE_COMMAND_RESERVED_FIELDS` 更新为新包络字段。
- `task_type` 破坏性改名为 `command_type`。
- `timestamp/timeout` 改为 `issued_at/expires_at/timeout_ms`。
- 下发 payload 增加 `protocol_version`、`trace_id`、`causation_id`、`contract_version`、`callback_url`。
- callback/result 允许设备回传 `trace_id` 和 `causation_id`，但仍以 `command_code` 为第一归属键。

验收：

- 设备 payload 顶层无业务字段。
- SANDBOX payload 与 LIVE payload 一致。
- result 缺 trace 字段时可由 command_code 恢复 trace。

### PR 5：SANDBOX 工作台 API

目标：让调试人员不查库、不手写 payload，也能推进完整 Session。

改动：

- 新增 sandbox outbox 查询、详情、callback template、complete API。
- 新增 replay API。
- 新增 manual hold/resume/cancel API。
- 所有人工操作写 audit 和 dispatch attempt。

验收：

- 一个 SMT happy path 可在 SANDBOX 下全流程跑通。
- 每一步人工处理都有 operator 和原因。

### PR 6：Trace / Blocking Point API 与 SSE

目标：前端和现场排障直接看到阻塞点。

改动：

- 实现 blocking point 服务。
- Trace API 聚合 callback、inbox、session、timeline、diagnostic、command、outbox、attempt。
- SSE 只发送 ID 级通知。
- Runtime overview 增加 first failure、blocking point、diagnostic severity。

验收：

- 任一失败 trace 能返回唯一 first failure。
- SSE 丢失不影响刷新后看到最终事实。

### PR 7：SMT/WMS 业务事实

目标：满足 SMT 仓储追溯和 WMS 证据要求。

改动：

- 定义 `workline_business_facts` 或标准 timeline fact payload。
- SixInOne 解析结果、WMS 查询结果、库位/料架/料箱决策结构化。
- 业务 NG、数据质量问题、系统异常三类分开。

验收：

- 可按 PkgID、LotCode、business_key、trace_id 查询完整分拣证据。
- WMS 响应快照和 WES 决策可对账。

## 测试矩阵

必须覆盖：

- callback/event 合法 payload 创建 event_id 和 trace_id。
- callback/event 非法顶层字段写 callback log 和 diagnostic。
- callback/result 通过 command_code 继承 trace。
- 重复 request/idempotency key 只产生一个 inbox。
- 插件业务 NG 不产生系统 failure。
- 插件异常产生 diagnostic。
- outbox LIVE 成功、HTTP 失败、网络异常、重试耗尽。
- SANDBOX outbox pending、template、manual complete、result 回灌。
- replay 创建新 event，不修改原 inbox。
- SSE after commit 发布 trace updated。
- SMT SixInOne 不完整产生 DATA_QUALITY 诊断。
- device command payload 不包含内部 DB id 和业务顶层字段。

建议测试位置：

- `tests/workline_runtime/`
- `tests/workline_plugins/smt_classifier/`
- `tests/api/workline/`
- `tests/e2e/`
- `tests/resilience/`

## 非目标

本计划不做：

- 通用流程 DSL。
- 可视化流程编排器。
- 替代 WMS 主数据或库存真相源。
- 把 SSE 变成持久事件流。
- 给设备暴露内部数据库 ID。
- 为旧字段做兼容读写、回填或双写。

## 完成定义

完成后，系统应满足：

1. 任意 accepted event 都有 `event_id` 和 `trace_id`。
2. 任意 command result 都能通过 `command_code` 回到同一条 trace。
3. 任意失败都有持久 diagnostic。
4. 任意 outbox 派发都有 attempt。
5. SANDBOX 不改 payload，但可完整推进 Session。
6. SSE 只负责通知，FastAPI 负责事实查询。
7. SMT 关键业务证据可按业务键和 trace 双入口追溯。
8. 现场排障不需要查数据库即可判断阻塞点和下一步动作。

---

## GSTACK 评审报告

生成时间：2026-04-27 11:10 CST

> 终审覆盖说明：以下评审报告保留为决策背景。用户在实施前终审明确要求“系统未发布，不需要兼容，保持代码清爽”，因此所有“兼容窗口、旧字段双写、旧命名保留”的建议均已作废；最终执行口径以本文“命名决策”和“轻量分支实施策略”为准。

### 阶段 1：CEO / 产品策略评审（历史记录，兼容建议已作废）

#### 阶段 0：上下文

- 基准分支：`develop`。
- 当前分支：`develop`。
- PR 上下文：当前 `develop` 无打开中的 PR；GitHub 仓库默认分支为 `develop`。
- 评审范围：本计划文档。
- UI 范围：无。计划没有新增页面、组件、表单、布局或可视化设计。
- DX 范围：有。计划新增 API、协议、SANDBOX、replay/manual 操作、诊断和调试面。
- 设计文档：未在 `~/.gstack/projects/kaizhoumasha-wes_backend` 找到独立设计文档。
- 恢复点：`/Users/kaizhou/.gstack/projects/kaizhoumasha-wes_backend/develop-autoplan-restore-20260427-103032.md`。

#### 0A. 前提挑战

| 前提 | 评审 | 决策 |
| --- | --- | --- |
| 正确问题是“可追踪、可操作的诊断”，不是继续加日志。 | 成立。当前已有 `callback_logs`、`workline_inbox`、`workline_sessions`、`workline_timelines`、`device_commands`、`system_outbox` 和运行时诊断卡，但事实还不够持久、也不够操作化。 | 接受。 |
| accepted event 必须有 `event_id`、`trace_id` 和可恢复性判断。 | 成立，但被拒绝或失败的 ingress 也需要稳定 trace 或诊断根。 | 接受，并要求补入口失败测试。 |
| 系统未发布，所以可以直接统一到 `trace_id`。 | 终审后成立。当前分支同步清理代码、测试、mock、迁移和新增文档，不保留旧命名。 | 用户终审：破坏式收口。 |
| 供应商会回传 `trace_id` / `causation_id`。 | 假设偏弱。`command_code` 是唯一可靠的供应商侧恢复键。 | 接受为可选合规信号，不能作为恢复前提。 |
| 持久诊断本身就足以改善现场诊断。 | 不完整。诊断必须有 owner、blocking point、operator action、recoverability、去重和 evidence 形状。 | 自动决策：先定义诊断卡契约，再落表。 |
| SMT/WMS 业务证据可以等到 PR7。 | 风险高。现场排查通常需要物料、批次、库位、WMS 响应和业务决策证据。 | 用户挑战 2：最小业务证据应前置，或至少异常优先采集。 |

前提门：用户在 2026-04-27 回复 `继续`，视为通过。

#### 0B. 现有代码复用

| 子问题 | 现有代码 | 复用结论 |
| --- | --- | --- |
| Callback 入站证据 | `src/app/callback/models/callback_log.py`、`src/app/callback/v1/callback.py`、`callback_log_service` | 扩展现有 callback log，不建平行 ingress 表。 |
| Ingress 队列和幂等 | `src/app/workline/models/inbox.py`、`inbox_service.py`、`inbox_repository.py` | 在这里补 `event_id`、`trace_id`、`causation_id`、唯一约束和标准化 payload。 |
| Session 状态和等待/失败快照 | `src/app/workline/models/session.py`、`session_resolver.py` | 扩展现有 session，保留 `run_mode` 快照语义。 |
| Timeline 解释层 | `src/app/workline/models/timeline.py`、`timeline_generator.py` | 复用 timeline generator，插件仍不能直接构造 timeline。 |
| 指令生命周期 | `src/app/device/models/command.py`、`device_command_service.py` | 保留 `command_code` 作为供应商可见恢复键，谨慎迁移 `session_id` 类型。 |
| Outbox 派发 | `src/app/workline/models/outbox.py`、`outbox_repository.py`、`src/celery_app/tasks/workline.py` | 新增 attempts 表；outbox 继续作为当前状态投影。 |
| 运行时诊断 | `src/workline_runtime/diagnostics/`、callback/workline 任务中的 `_log_diagnostic` | 持久化现有诊断卡契约，不另造第二套 shape。 |
| Trace 查询 | `trace_query_service.py`、`src/app/workline/v1/trace.py` | 扩展现有 read model。当前已聚合 callback、inbox、session、command、outbox、timeline。 |
| Runtime overview | `runtime_query_service.py`、`runtime.py` | 诊断存在后再投影 blocking point 和 first failure。 |
| SANDBOX pending outbox | `SystemOutboxRepository.get_sandbox_pending_messages` | 复用并硬化，补 API/service 层和 attempt 记录。 |
| 设备 payload envelope | `_DEVICE_COMMAND_RESERVED_FIELDS`、`_normalize_vendor_command_payload`、`_build_outbox_payload` | 这是协议热路径，本分支直接收敛到新合同并以测试锁定。 |

#### 0C. 理想状态差距

```text
当前状态
  callback log + inbox + session + timeline + command + outbox 已存在，
  但 trace_id 语义过载，诊断仍主要是运行时/日志事实。

本计划
  引入 trace/event/causation 身份、持久诊断、dispatch attempts、
  sandbox/manual/replay API，以及 trace/blocking-point 查询。

12 个月理想状态
  现场工程师打开一条 trace 就能看到：
  first failure、current blocking point、owner、recoverability、
  operator action、设备/WMS 证据、attempts、replay/manual 选项。
```

建议把首个可验证交付改为一个黄金诊断闭环：让已知故障在 60 秒内定位到阻塞点和下一步动作，然后再扩展完整账本。

#### 0C-bis. 实现方案对比

| 方案 | 摘要 | 工作量 | 风险 | 优点 | 缺点 | 复用 | 完整度 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A. 最小 Trace 补丁 | 在现有表上加 `trace_id` alias 和 blocking-point 查询。 | M | 中 | diff 小，能快速提高可见性。 | 诊断不持久，没有 attempt 账本。 | TraceQueryService、诊断 builder。 | 5/10 |
| B. 原始账本计划 | 基本按 PR0-PR7 执行。 | XL | 高 | 长期模型最完整。 | 先做 schema/protocol，再证明诊断价值；供应商变更风险高。 | 全部 WORKLINE 表。 | 8/10 |
| C. 事故优先账本 | 先做顶级事故诊断闭环，直接收敛 trace 身份，再补 attempt/business facts。 | L | 中 | 先证明 MTTR 改善，同时避免旧命名继续扩散。 | 需要重排计划并明确 top incidents。 | 现有表、diagnostics builder、trace API、outbox。 | 9/10 |

推荐：方案 C。

#### 0D. 模式判断

模式：选择性扩展。

达到目标的最小可行集合：

1. 定义诊断卡契约：owner、blocking point、recoverability、operator action、next steps、evidence。
2. 加 trace/event/causation 字段，并删除旧命名读写路径。
3. 持久化 top failure：schema invalid、session resolve failed、plugin exception、outbox dispatch failed、device timeout。
4. 新增 blocking-point 和 trace diagnostics API。
5. 为 outbox failure、sandbox/manual completion 增加 dispatch attempts。
6. 覆盖 accepted、rejected、duplicate、replay、result、timeout 流程测试。

选择性扩展项：

| 候选项 | 决策 | 理由 |
| --- | --- | --- |
| 术语迁移前增加事故优先诊断闭环 | 用户挑战 | 两个外部声音都建议调整 PR 顺序。 |
| `trace_id` 命名收口 | 用户终审 | 系统未发布，直接破坏式清理。 |
| 供应商认证套件 | 用户挑战 | 供应商合规风险比内部字段更关键。 |
| OpenTelemetry span 输出 | 品味项 | 可作为补充，关系型账本仍是事实源。 |
| exception-first `workline_business_facts` | 品味/用户挑战 | 业务证据需要前置，但不能复制成影子 WMS。 |

#### CEO 双声部

| 声部 | 核心观点 |
| --- | --- |
| Codex CEO | 当前计划偏架构导向，缺少 baseline MTTR/top incidents；破坏性命名变更风险高；供应商 trace echo 假设弱；PR0 应先证明诊断价值。 |
| 子代理 CEO | 应把目标表述为“1 分钟可操作诊断卡”，而不是数据模型迁移；黄金诊断闭环应前置；`command_code` 保持硬恢复键；业务证据应更早出现。 |

CEO 共识表：

| 维度 | 共识 |
| --- | --- |
| 正确问题 | 是：诊断价值比日志堆叠重要。 |
| 第一交付 | 不应是大规模迁移；应是可运行诊断闭环。 |
| 字段命名 | 只保留 `trace_id`。 |
| 恢复键 | `command_code` 是硬恢复键。 |
| 诊断契约 | 必须先定义，再持久化。 |
| 业务证据 | 需要前置或异常优先，而不是全部等到 PR7。 |

#### 1. 架构评审

```text
callback/event,result,external
  -> CallbackOrchestrationService
    -> CallbackLogService
    -> WorklineInboxService
    -> WorklineDiagnosticService

process_inbox_batch
  -> SessionResolver
  -> plugin runtime
  -> TimelineGenerator
  -> DeviceCommandService
  -> OrchestratorWriteBackService
    -> SystemOutboxRepository

Trace APIs
  -> TraceQueryService
    -> callback_log + inbox + session + command + outbox + attempts + timeline + diagnostics

Sandbox/Manual/Replay APIs
  -> Workline 操作服务
    -> 状态前置条件 + audit + diagnostic + timeline
```

| 架构问题 | 严重度 | 决策 |
| --- | --- | --- |
| PR0 过于迁移优先。 | 高 | 改成事故优先诊断闭环。 |
| trace/event/causation 可能扩散到多个层。 | 中 | 通过 `TraceContext` 和 service 边界集中。 |
| `workline_diagnostics` 可能变成泛化日志表。 | 高 | 诊断卡必须有固定契约和代码注册表。 |
| SANDBOX API 容易绕过状态机。 | 高 | 通过 inbox/编排进入，不直接改 session。 |

#### 2. 错误与救援登记表

| 路径 | 失败模式 | 期望救援 | 缺口 |
| --- | --- | --- | --- |
| callback/event | schema invalid | callback log + diagnostic root | 需要 rejected ingress 诊断测试。 |
| callback/result | trace 缺失 | 通过 `command_code` 恢复 trace | 当前计划需强调先查 command。 |
| inbox create | 并发重复 | DB unique + conflict handling | 不能只靠服务层查询。 |
| session resolve | 找不到 session | diagnostic + blocking point | 需要持久化和 API 输出。 |
| plugin execution | 插件异常 | diagnostic + evidence | 当前只有日志/运行时卡。 |
| timeline append | seq 冲突 | lock/retry | unique 约束不够。 |
| outbox dispatch | 网络/HTTP 失败 | attempt + diagnostic + retry/dead letter | 需要 attempt 租约语义。 |
| sandbox complete | 过期/重复完成 | 409 + audit + diagnostic | 需要状态机。 |
| replay | 改写旧 inbox | 创建新 event | 需测试保证不可变。 |
| manual operation | 越权或无原因 | 权限 + reason + audit | 需 RBAC 和审计。 |

#### 3. 安全与威胁模型

| 威胁 | 风险 | 缓解 |
| --- | --- | --- |
| Trace API 泄露设备/WMS/物料证据 | 高 | 权限、对象范围、脱敏、保留周期。 |
| SANDBOX/manual/replay 被滥用 | 高 | 独立权限、reason、idempotency、audit。 |
| 原始 headers/body 无限保存 | 中 | hash + sample + redaction。 |
| 供应商 payload 混入内部 ID | 中 | 不向设备暴露 session/inbox/outbox/db id。 |
| SSE 被误用为事实源 | 中 | 仅通知，事实从 FastAPI 拉取。 |
| 诊断卡暴露敏感业务字段 | 中 | evidence key 白名单和按角色展示。 |

#### 4. 数据流与边界情况

重点边界：

- rejected callback 也需要 trace/diagnostic root。
- `callback/result` 必须先用 `command_code` 恢复归属，再判断 device mismatch。
- 一条 trace 多 session 时，TraceQueryService 需要真正的多 session read model。
- dispatch attempt 需要覆盖 worker crash 前后窗口。
- manual/replay/sandbox 必须具备 idempotency、状态前置条件和审计。

#### 5. 代码质量评审

- 复用 `TraceContext`、`DiagnosticContext`、`DiagnosticCard`，不要新造第二套诊断结构。
- 新增 service 必须从 `__init__.py` 导出。
- API 层只调用 service，不直接 SQL 或 repository。
- Timeline 仍由 generator 统一构造，插件不能直接写 timeline。
- 业务证据应限制在必要字段和异常证据，不要变成 WMS 主数据副本。

#### 6. 测试评审

必须覆盖：

```text
callback/event: accepted, rejected, duplicate, missing trace
callback/result: known command_code, unknown command_code, mismatched device_code, duplicate result
runtime: session resolved, session not found, plugin validation failure, plugin exception
outbox: success, timeout, HTTP error, retry exhaustion, crash windows
sandbox/manual/replay: success, stale, double complete, permission denied, idempotency conflict
trace APIs: detail, blocking point, diagnostics list, redaction, object scope, query performance
migration: nullable-first, backfill, unique constraints, old/new worker overlap
```

关键测试缺口：诊断卡 snapshot、并发 duplicate callback、timeline seq race、migration/backfill、dispatch lease crash window、RBAC/object scope、redaction、trace query 性能、供应商协议兼容。

#### 7. 性能评审

| 风险 | 应对 |
| --- | --- |
| Trace detail 变成宽 fan-out 查询 | 为 `trace_id`、`event_id`、`session_id`、`command_code`、时间字段建索引。 |
| JSON payload 搜索变慢 | 将 PkgID/LotCode/MfrPN 等可搜索证据提升为列或索引字段。 |
| attempts 表增长快 | 设计保留周期、分区或归档。 |
| SSE before commit | 只在事实提交后发通知。 |
| 热路径诊断过重 | 只记录进程内已有证据，避免 ingress 同步查 WMS。 |

#### 8. 可观测性与调试

- 诊断 code 注册表：owner、severity、recoverability、operator_action、evidence keys、docs link。
- 指标：diagnostic count、blocking point 时长、outbox attempt latency、ACK latency、replay/manual 次数。
- Runbook：每个诊断 code 至少有下一步动作模板。

#### 9. 部署与发布

安全顺序：

```text
1. 增加 nullable 字段和诊断表。
2. dual-write trace/command 命名 alias。
3. backfill 并验证新字段。
4. 读取优先用 canonical 字段，fallback 到 legacy 字段。
5. 添加 DB unique，并在 service 中处理冲突。
6. 先交付诊断卡垂直切片和测试。
7. 增加 attempt lease 和 sandbox/manual/replay 操作。
8. 验证后再加 not-null/FK 约束。
9. 供应商、mock、前端合约全部通过后再移除 legacy alias。
```

部署门槛：至少一个 rolling deploy 窗口内，新旧 worker 必须兼容。

#### 10. 长期方向

- 保留 `command_code` 作为供应商稳定恢复键。
- 关系型账本是事实源；OpenTelemetry 只能作为补充。
- WMS 仍是库存真相源，WES 只存业务证据。
- 协议破坏性修改可逆性低，必须延后到兼容窗口结束后。

#### 11. 设计/UX 评审

跳过。没有 UI 范围。API 响应信息架构和未来 trace 页展示问题放到 DX 评审处理。

#### CEO 阶段不在范围内

| 项 | 理由 |
| --- | --- |
| 通用 workflow DSL | 当前目标是诊断，不是重写编排引擎。 |
| 可视化流程设计器 | 不需要它来证明诊断卡价值。 |
| 替代 WMS 主数据 | WMS 保持库存真相源。 |
| SSE durable event stream | SSE 只做通知。 |
| 向设备暴露内部 DB ID | 保持设备协议稳定。 |
| 完整 OpenTelemetry 平台 | 可选补充，不是主方案。 |

#### CEO 完成摘要

| 项 | 结果 |
| --- | --- |
| 模式 | 选择性扩展 |
| 主要结论 | 计划方向正确，但 PR 顺序必须改成诊断价值优先。 |
| 关键挑战 | 兼容窗口、`command_code` 恢复、诊断契约、最小业务证据、供应商认证。 |
| 双声部 | Codex + 子代理已运行 |
| 共识 | 5/6 确认，1 个排序分歧 |

阶段 1 完成，进入阶段 2。

### 阶段 2：设计评审

跳过。未检测到 UI 范围。

证据：

- 计划未新增前端组件、页面、表单、按钮、弹窗、导航或 dashboard。
- 唯一的用户体验问题是 trace、diagnostics、blocking point、sandbox API 的响应信息架构。
- 该问题在 DX 评审中处理。

设计双声部：跳过。

阶段 2 完成，进入阶段 3。

### 阶段 3：工程评审

生成时间：2026-04-27 10:55 CST

#### 工程范围挑战

计划技术方向基本成立，但第一实现切片应调整。当前代码已经有 callback log、inbox、session、command、outbox、timeline、运行时诊断和 trace 查询。最高价值的工程动作不是先做大规模 schema 迁移，而是在兼容安全的前提下，交付一个事故优先垂直切片，证明操作员能在 1 分钟内看到阻塞点和下一步动作。

必须调整的顺序：

```text
1. 保持 `trace_id`、`task_type`、`timestamp`、`timeout` 合约可用。
2. 增加 canonical alias 和 dual-read/dual-write。
3. callback/result 先用 `command_code` 恢复，再判断设备上下文是否错误。
4. 在现有事实上持久化一张诊断卡契约。
5. 诊断闭环证明后，再做状态机、attempt 和 schema hardening。
```

#### 工程双声部

| 声部 | 最强发现 | 决策 |
| --- | --- | --- |
| Codex Eng | 破坏性改名太早；`command_code` 恢复必须先于 device-context rejection；TraceQueryService 已存在；多 session trace 与现有单 session 查询冲突；唯一约束和 dispatch 需要分阶段。 | 接受，转为必改项。 |
| 子代理 Eng | 增加单一 `WorklineDiagnosticService`；DB 幂等 + `IntegrityError` 处理；timeline `max(seq_no)+1` 需要锁/重试；outbox attempts 需要 lease token；trace/sandbox API 需要独立权限和对象范围。 | 接受，作为编码前约束。 |

共识：只有在补齐兼容、并发、授权、诊断优先交付后，才批准按修订计划实施。不要按当前 PR0-PR7 原顺序直接实现。

#### 1. 架构评审

推荐架构：

```text
Callback API
  -> CallbackOrchestrationService
    -> command_code-first recovery
    -> CallbackLogService
    -> WorklineInboxService
    -> WorklineDiagnosticService
      -> WorklineDiagnosticRepository

Celery WORKLINE task
  -> SessionResolver
  -> plugin runtime
  -> TimelineGenerator
  -> WorklineDiagnosticService
  -> DeviceCommandService
  -> OrchestratorWriteBackService
    -> SystemOutboxRepository

Trace APIs
  -> TraceQueryService
    -> callback_log + inbox + sessions + commands + outbox + attempts + timeline + diagnostics

Sandbox / manual / replay APIs
  -> Workline operation services
    -> state precondition checks
    -> audit + diagnostic + timeline entries
```

| 严重度 | 问题 | 证据 | 必改项 |
| --- | --- | --- | --- |
| 高 | PR0/PR4 暗含破坏性改名。 | callback allowlist、Celery payload、测试、mock、硬件文档仍使用旧字段。 | 增加兼容窗口：canonical `trace_id` + legacy `trace_id`，并明确 `device_trace_id`/`ack_trace_id`。 |
| 高 | result 恢复顺序不适合现场排查。 | 现有入口可能先校验设备上下文再查 command。 | 有 `command_code` 时先查 command，再诊断设备不匹配。 |
| 高 | 多 session trace 语义不足。 | repository 返回单 session；TraceQueryService 以单 session 为中心。 | 定义多 session trace read model。 |
| 高 | 持久诊断事务语义不足。 | 当前诊断主要是 runtime/log card。 | 增加统一 service，集中 code registry、redaction、dedupe、事务策略。 |
| 高 | 幂等并发不安全。 | `idempotency_key`、`dispatch_key` 当前是索引，不是完整冲突处理。 | nullable-first unique rollout + DB conflict/upsert。 |
| 中 | timeline 顺序有竞争。 | 当前 `max(seq_no)+1` 会竞态。 | 增加 row/advisory lock 或 retry-on-conflict。 |
| 中 | `device_commands.session_id` 不是简单类型修改。 | 当前存 string、查 string。 | dual-column/backfill/validation 后再加 FK。 |
| 中 | SMT 证据过晚。 | 当前 `business_key` 是 PkgID hash，现场需要可见字段。 | 首个诊断切片中加入最小业务证据，并加脱敏/保留规则。 |

#### 2. 代码质量评审

| 领域 | 现有模式 | 工程建议 |
| --- | --- | --- |
| API | `v1/` 路由调用 service。 | 新 API 不得直接查 DB 或 repository。 |
| Service | 编排和 runtime service 承载业务逻辑。 | 新增 `WorklineDiagnosticService`、`WorklineDispatchAttemptService`、必要 operation service。 |
| Repository | Base repository 模式。 | 新增 trace read model 查询 helper。 |
| Diagnostics | `DiagnosticContext`、`DiagnosticEvent`、`DiagnosticCard`、`diagnostic_builder`。 | 持久化这套契约。 |
| Trace context | `TraceContext` 已能投影 callback/inbox/session/timeline。 | 扩展 canonical `trace_id`，保留 `trace_id` alias。 |
| Timeline | generator 负责构造。 | 插件仍不能直接写 timeline。 |
| Protocol payload | Celery helper 标准化供应商命令 payload。 | 先在这些 helper 中做兼容迁移。 |

不可妥协规则：

- API 层只调用 service。
- 诊断写入只有一个 service 入口。
- 每个 diagnostic code 都要有 owner、severity、recoverability、operator_action、evidence keys、redaction policy。
- 设备可见协议字段必须有 SMT 硬件文档合约测试。
- old worker 与 new worker 必须能在发布窗口重叠运行。

#### 3. 测试覆盖评审

```text
callback/event
  -> accepted event
  -> schema invalid event
  -> concurrent duplicate event
  -> missing trace but recoverable payload

callback/result
  -> known command_code
  -> unknown command_code
  -> mismatched device_code
  -> duplicate ACK/result
  -> vendor omits trace fields

WORKLINE processing
  -> session resolved
  -> session not found
  -> plugin validation failure
  -> plugin exception
  -> command created
  -> outbox created
  -> timeline sequence conflict retry

outbox dispatch
  -> success
  -> network timeout
  -> HTTP error
  -> retry exhaustion
  -> crash after attempt created
  -> crash after external send before finalize

sandbox/manual/replay
  -> sandbox complete success
  -> stale complete
  -> double complete
  -> wrong result template
  -> manual hold/resume/cancel authorization
  -> replay creates new event and preserves old event

trace/diagnostics APIs
  -> trace detail
  -> blocking point
  -> diagnostics list
  -> object-scope authorization
  -> redaction
  -> payload query index behavior
```

必须补的测试缺口：诊断卡 snapshot、并发 duplicate callback、timeline seq race、migration/backfill、dispatch lease crash windows、RBAC/object scope、redaction、trace query performance、vendor protocol compatibility。

#### 4. 性能评审

| 风险 | 设计响应 |
| --- | --- |
| Trace detail 查询过宽 | 为 trace/event/session/command/time 增加索引和有界 join。 |
| JSON payload 搜索慢 | 可搜索证据提升为列或生成索引字段。 |
| attempts 增长快 | 增加保留/分区/归档策略。 |
| 通知早于提交 | after commit 后再通知，客户端回拉事实。 |
| 诊断写入拖慢 callback | 卡片构造保持有界，不在 ingress 同步做昂贵查找。 |

#### 5. 安全与授权评审

必改项：

- trace detail、diagnostics、sandbox complete、replay、manual hold/resume/cancel、raw evidence 需要独立权限。
- 返回 trace 数据前按 line、device、warehouse 或租户等价边界做对象范围校验。
- headers、token、raw body、WMS response、device payload sample 必须按策略脱敏。
- sandbox/manual/replay 操作必须记录 operator、reason、idempotency key、before/after state、trace。
- replay 不允许改写历史 inbox。

#### 6. 错误路径评审

| 路径 | 失败模式 | 当前计划 | 必改项 |
| --- | --- | --- | --- |
| rejected callback | 没有 inbox/session。 | 部分覆盖。 | 从 callback log 建 synthetic trace/diagnostic root。 |
| result callback | 错误设备上下文掩盖可恢复 command。 | 缺口。 | 先按 `command_code` 恢复。 |
| inbox duplicate | 并发重复接受。 | 缺口。 | DB unique + conflict handling。 |
| session resolve | 找不到 session。 | 部分覆盖。 | 持久诊断，带 owner/action/evidence。 |
| plugin runtime | 异常丢失业务上下文。 | 部分覆盖。 | 持久诊断并带最小业务事实。 |
| timeline append | 并发 ACK 重复 seq。 | 缺口。 | lock/retry sequence allocation。 |
| outbox dispatch | worker crash 导致 attempt 丢失。 | 缺口。 | attempt lease token + finalize 语义。 |
| sandbox complete | 过期/重复人工完成。 | 缺口。 | 状态机和冲突响应。 |
| replay/manual | 越权或非幂等。 | 缺口。 | 权限、reason、idempotency、audit。 |

#### 7. 部署评审

安全发布顺序同 CEO 阶段结论：nullable-first、dual-write、backfill、read fallback、unique/conflict handling、诊断垂直切片、attempt/sandbox/manual/replay、最后才 enforce not-null/FK 和移除 legacy alias。

#### 工程阶段已有能力

| 能力 | 现有 artifact | 复用决策 |
| --- | --- | --- |
| 通用后端启动 | `README.md` | 保留，并增加 WORKLINE diagnostics 入口。 |
| Callback 证据 | callback logs/services/routes | 扩展，不建平行路径。 |
| Runtime 队列 | workline inbox/services | 在这里加身份、唯一性和 replay causation。 |
| 业务 session | sessions + resolver | 增加诊断指针和 trace alias。 |
| Timeline | timeline + generator | 继续由 generator 统一生成。 |
| 指令恢复键 | `device_commands.command_code` | 保持供应商可见硬键。 |
| Dispatch 当前状态 | outbox | 新增 attempt ledger。 |
| Runtime diagnostics | `src/workline_runtime/diagnostics/` | 持久化并文档化。 |
| Trace read model | `TraceQueryService` | 扩展，不重建。 |
| 插件开发文档 | `docs/plugin_development_guide.md`、templates | 扩展诊断 expectations 和 fixtures。 |

#### 工程阶段不在范围内

| 项 | 理由 |
| --- | --- |
| 替代 WMS 真相源 | WES 只存证据，不存主数据。 |
| 要求供应商回传 trace 才能恢复 | `command_code` 才是硬恢复键。 |
| 完整 OpenTelemetry 迁移 | 可选补充。 |
| 通用 event-sourcing 重写 | 现有表已表达领域事实。 |
| 前端 trace UI | 本计划无 UI 范围。 |
| 第一版破坏旧硬件协议字段 | 与当前文档和代码冲突。 |

#### 工程完成摘要

| 维度 | 评分 | 决策 |
| --- | --- | --- |
| 架构 | 原计划 6/10，修订后 8/10 | 方向成立，顺序不安全。 |
| 测试覆盖 | 4/10 | 需补并发、迁移、RBAC、脱敏、snapshot。 |
| 性能 | 5/10 | 需补索引、查询、保留周期。 |
| 安全 | 4/10 | 需补权限、对象范围、脱敏、审计。 |
| 错误路径 | 5/10 | 需补恢复顺序和状态机细节。 |
| 部署风险 | 原计划 4/10，兼容后 7/10 | 兼容窗口是硬门槛。 |

阶段 3 完成，进入阶段 3.5。

#### 工程共识表

| 评审轴 | Codex Eng | 子代理 Eng | 共识 |
| --- | --- | --- | --- |
| 第一切片 | 诊断卡先于 schema 清理 | 诊断 service 先于分散写入 | 事故优先诊断闭环。 |
| 兼容性 | 破坏性改名太早 | 需要 dual-read/write | 发布期保留旧合约。 |
| 恢复键 | `command_code` 先查 | `command_code` 是稳定键 | trace echo 不能作为恢复前提。 |
| 并发 | unique rollout 和 seq race 需细化 | DB 幂等、锁/重试、attempt lease | 先做事务/并发设计。 |
| 安全 | API 暴露敏感证据 | 独立 RBAC/object scope/redaction | 授权和证据策略是硬门槛。 |
| 测试 | 缺并发、迁移、RBAC | 同时缺状态机和 crash window | 原测试矩阵不足。 |

### 阶段 3.5：开发体验评审

生成时间：2026-04-27 11:05 CST

#### 开发者产品分类

主类型：API/Service + 内部平台文档。

开发者可见面：

- `callback/event`、`callback/result`、`callback/external` payload 合约。
- trace、diagnostics、blocking-point、events、command trace API。
- sandbox outbox/template/complete、replay、manual operation API。
- 插件作者 contract、fixtures、sandbox happy path。
- `trace_id`/`trace_id` 与 `task_type`/`command_type` 迁移指南。

模式：DX POLISH。它是已有内部平台增强，但首次验证路径必须清晰。

#### 开发者画像

| 字段 | 内容 |
| --- | --- |
| 谁 | 现场集成工程师 + 后端/平台工程师，在 dev/test 中验证 WORKLINE 诊断后再联调硬件或支持现场。 |
| 场景 | 拿到 vendor/device payload，需要回答：WES 是否接受、属于哪条 trace、卡在哪里、谁负责修、能否安全 replay/manual complete。 |
| 容忍度 | 已运行开发环境中 5 分钟内得到第一张可操作诊断卡；现场已知 `trace_id` 或 `command_code` 时 60 秒内定位。 |
| 期望 | 可复制命令、seeded fixtures、稳定 request/response 示例、标准诊断不查库不 grep 日志、旧供应商 payload 兼容说明清晰。 |
| 次要用户 | WORKLINE 插件作者，需要 fixtures、contract tests 和诊断 expectations。 |

#### 开发者共情叙事

我打开 `README.md`，看到的是通用后端启动路径：Docker、依赖、migration、API server。文档没有把我引向 WORKLINE 诊断场景。我找到已归档的 `docs/archive/legacy-plugin-result/plugin_validation_quickstart.md`，里面有 callback curl，但第一步需要连 Postgres，后续也靠 SQL 查 session 状态。到了 result callback 示例，它使用 `command_type` 且省略 `command_code`，而集成白皮书要求 `command_code`，命令 payload 又仍写 `task_type`。我继续看 sandbox happy path，它正确描述了 sandbox 应覆盖事件输入、命令派发、人工 callback 和 session 推进，但没有实际 API 响应或一条命令证明路径。这个计划承诺 trace、blocking point、diagnostics、sandbox、replay、manual API，方向正确；但作为第一次接入的人，我仍无法在不知道数据库表和日志位置的情况下，证明“这次失败归 DEVICE，下一步该 manual complete”。

#### 竞品 / 参考 DX 基准

| 参考 | 当前公开模式 | 对本计划的启发 |
| --- | --- | --- |
| [Stripe quickstarts](https://docs.stripe.com/quickstarts) | 按语言/框架提供端到端 quickstart 和交互示例。 | WES 需要可复制诊断流程和期望输出。 |
| [Stripe API reference](https://docs.stripe.com/api?lang=curl) | 可预测 API、test mode、request id、结构化 JSON、curl-first 文档。 | callback 和 trace 示例应可运行且结构化。 |
| [Vercel CLI deploy](https://vercel.com/docs/cli/deploy) | 一个命令输出有用的 deployment URL。 | WES smoke 命令应打印 `trace_id` 和下一步诊断 API。 |
| [Docker get started](https://docs.docker.com/get-started/) | 清晰 getting started 和 hands-on lab。 | fresh clone 可以更长，但第一条 proof path 必须明确。 |
| WES 目标 | 一个 fixture 产出 trace、diagnostic、blocking point、next action。 | 运行中开发环境目标小于 5 分钟。 |

当前 TTHW 估计：

- 已运行开发环境：超过 10 分钟，因为证明路径分散在多份文档、SQL、日志和未定义的 trace-card 输出之间。
- fresh clone：30-60 分钟，因为需要 Docker、env、依赖、migration、seed、worker、callback flow。

目标 TTHW：

- 已运行开发环境：小于 5 分钟得到第一张可操作 trace card。
- fresh clone：小于 15 分钟跑通 seeded diagnostic demo。
- 现场支持：已知 `trace_id` 或 `command_code` 后 60 秒内看到 blocking point 和 operator action。

#### 魔法时刻定义

```text
发送一个 fixture -> 收到 trace_id -> 调 blocking-point API ->
看到 owner、recoverability、operator_action、evidence、replay/manual links
```

交付载体：

- `docs/workline_diagnostics_quickstart.md`。
- happy path、schema failure、plugin failure、outbox failure、sandbox complete、replay/manual conflict fixtures。
- 一个 smoke script 或 Postman/Newman collection，输出 `trace_id`、`diagnostic_id` 和下一步 API URL。
- 期望 JSON snapshot 入库并在 CI 校验。

#### 9 阶段开发者旅程

| 阶段 | 开发者动作 | 当前摩擦 | 必改项 |
| --- | --- | --- | --- |
| 发现 | 打开 README 或 docs index。 | README 只有通用后端启动。 | README 链接 WORKLINE diagnostics quickstart。 |
| 设置 | 启动本地 stack。 | 有 Docker/uv/migration，但没有诊断 seed path。 | 增加 SIMULATION WorkLine + fixtures seed/demo 命令。 |
| 配置 | 开启 WorkLine/run mode。 | 依赖 SQL 和隐含测试线 ID。 | 用 API 或脚本配置，quickstart 不手写 SQL。 |
| 发送事件 | POST callback/event fixture。 | curl 示例未连接 trace proof。 | ACK 返回/关联 `request_id`、`trace_id`、`event_id`、`diagnostic_id`。 |
| 观察 Trace | 调 trace/blocking-point API。 | 只有 endpoint 名，没有 response contract。 | 加 request/response 示例和 snapshot tests。 |
| 调试失败 | 阅读诊断卡。 | 诊断字段只是概念，不是错误契约。 | 加 code registry：problem、cause、fix、recoverability、evidence、doc link。 |
| SANDBOX 完成 | 处理 pending sandbox outbox。 | 仍像手工构造 payload。 | 加 pending -> template -> complete API 示例和冲突响应。 |
| Replay/Manual | replay inbox 或 hold/resume/cancel session。 | endpoint 名存在，但无前置条件和幂等示例。 | 文档化状态机、权限、审计、幂等和 409 响应。 |
| 升级 | 迁移协议/字段名。 | 破坏性改名易混乱。 | 加兼容/弃用指南和移除条件。 |

#### 第一次接入困惑报告

| 时间 | 困惑 |
| --- | --- |
| T+0:00 | 打开 README，只能启动后端，找不到 WORKLINE 诊断场景。 |
| T+3:00 | 找到 plugin validation，第一步实际验证就要手写 SQL。 |
| T+8:00 | 发送 event 后，没有文档化 trace response 或下一条命令。 |
| T+12:00 | 发送 result 示例时发现 quickstart 用 `command_type`，白皮书要求 `command_code`，命令 payload 还用 `task_type`。 |
| T+18:00 | sandbox 文档描述了概念，但没有可执行 endpoint 示例。 |
| T+25:00 | 计划架构可信，但仍不知道成功诊断卡 JSON 应该长什么样。 |

#### 8 维 DX 评分

| 维度 | 分数 | 证据 | 达到 8+/10 的要求 |
| --- | ---: | --- | --- |
| Getting Started | 4/10 | 没有 WORKLINE diagnostic hello-world；README 是通用后端启动。 | quickstart、seed/demo 命令、smoke script、期望输出。 |
| API/CLI/SDK | 6/10 | endpoint 列表强，但缺 request/response/error contract。 | 每个新操作都有 OpenAPI 示例和 curl。 |
| Error Messages | 6/10 | 诊断意图强，但没有精确 JSON。 | diagnostic code registry，含 problem/cause/fix/doc link。 |
| Documentation | 5/10 | 插件指南有用，但文档分散且命名冲突。 | docs index、quickstart、兼容指南、更新示例。 |
| Upgrade Path | 4/10 | 破坏性 rename 缺开发者迁移文档。 | dual-read/write 指南、warning、移除条件、contract tests。 |
| Dev Environment | 5/10 | Docker/uv 有，但 proof path 需要 DB/log。 | seeded simulation 环境和 no-SQL quickstart。 |
| Internal Support | 4/10 | 每个 diagnostic code 缺 owner/channel/runbook。 | 增加 owner/runbook/escalation 表。 |
| DX Measurement | 3/10 | 没有 TTHW、quickstart pass rate、现场诊断指标。 | 追踪 TTHW、smoke success、diagnostic-card usability、MTTR。 |

整体 DX：原计划 4.6/10；补齐必改项后可到 8/10。

#### DX 双声部

| 声部 | 最强发现 | 决策 |
| --- | --- | --- |
| Codex DX | 没有 copy-paste 诊断路径；术语冲突；sandbox/manual/replay 缺操作示例；文档分散；无 DX measurement。 | 接受，增加 diagnostic hello-world 和 response contract。 |
| 子代理 DX | adoption-critical 用户是现场集成工程师；现有 proof path 仍依赖 SQL/log；插件指南好但 quickstart 弱；TTHW 目标小于 5 分钟。 | 接受，no-SQL diagnostic proof 作为硬门槛。 |

#### DX 共识表

| 评审轴 | Codex DX | 子代理 DX | 共识 |
| --- | --- | --- | --- |
| 画像 | 现场集成 + 后端工程师 | 现场集成工程师优先，后端负责实现 | 固定画像。 |
| TTHW | 运行环境 >10 分钟，fresh clone 30-60 分钟 | 20-45 分钟首次路径 | 当前 TTHW 未定义且过高。 |
| 目标 | 运行环境 <5 分钟，fresh clone <15 分钟 | <5 分钟 | 使用 <5 分钟运行环境和 <60 秒现场诊断。 |
| 魔法时刻 | fixture -> trace -> blocking point card | 一个命令/curl 返回诊断卡 | 本分支必须交付 diagnostic hello-world。 |
| 顶级阻塞 | 无 response contract/snapshot | 无 no-SQL/no-log proof path | 加 quickstart、examples、snapshots、CI check。 |
| 迁移 | 命名冲突困扰集成方 | 需要兼容/弃用指南 | 加 migration guide 和 contract tests。 |

#### DX 实施清单

```text
[ ] 新增 docs/workline_diagnostics_quickstart.md。
[ ] 从 README.md 链接该 quickstart。
[ ] 增加 seeded SIMULATION WorkLine/demo fixture setup。
[ ] 增加一个 smoke script 或 Postman/Newman collection。
[ ] 第一次运行输出 trace_id、diagnostic_id、blocking point、next API。
[ ] 为 callback、trace、diagnostics、sandbox、replay、manual API 增加 OpenAPI 示例。
[ ] 增加诊断卡期望 JSON snapshot。
[ ] 增加 diagnostic code registry：owner、cause、fix、recoverability、evidence、docs link。
[ ] 增加 trace_id/trace_id 与 task_type/command_type 兼容指南。
[ ] 增加 vendor/device certification fixtures 和 contract tests。
[ ] CI 校验 quickstart smoke 仍可运行。
[ ] 运行中开发环境 TTHW <5 分钟。
[ ] 已知 trace_id 或 command_code 的现场诊断 <60 秒。
```

#### DX 阶段不在范围内

| 项 | 理由 |
| --- | --- |
| 公共 SaaS onboarding | WES 是内部后端/平台，不是公共 API 产品。 |
| 托管浏览器 playground | 可后续做；当前 fixtures + scripts 足够。 |
| 完整 SDK 生成 | 内部 API 先用 curl/OpenAPI/Postman 示例即可。 |
| 重写插件指南 | 现有指南有价值，应扩展诊断 expectations。 |
| 前端 sandbox workbench UI | 先定义 API，本计划无 UI 范围。 |

阶段 3.5 完成，进入阶段 4。

### 跨阶段主题

| 主题 | 被哪些阶段发现 | 信号 | 必须响应 |
| --- | --- | --- | --- |
| 事故优先诊断证明 | CEO、Eng、DX | 所有声音都反对 schema-first 执行顺序。 | broad migration 前先加 diagnostic hello-world。 |
| 兼容优先发布 | CEO、Eng、DX | 旧命名仍存在于代码、文档、mock、供应商合约。 | dual-read/write 和 migration guide 先于移除。 |
| `command_code` 是恢复键 | CEO、Eng | 供应商 trace echo 有用但不可靠。 | 先按 `command_code` 恢复，再诊断上下文错误。 |
| 诊断需要硬契约 | CEO、Eng、DX | 持久行本身不能改善现场诊断。 | 定义 card schema、code registry、owner/action/evidence/docs。 |
| 标准诊断不查 SQL/日志 | CEO、DX | 当前证明路径仍依赖 DB/log。 | quickstart 和 API 必须证明 trace/debug 路径。 |
| 授权与证据治理 | Eng、CEO | trace/sandbox/manual 暴露敏感运行状态。 | RBAC、对象范围、审计、脱敏、保留周期。 |
| 供应商认证 | CEO、DX | supplier conformance 是根风险。 | 在依赖 trace echo 前加可执行兼容 fixtures。 |

## 决策审计轨迹

| # | 阶段 | 决策 | 分类 | 原则 | 理由 | 拒绝方案 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | CEO | 使用选择性扩展模式 | 机械 | P1/P2 | 这是既有 WORKLINE 系统增强，适合严谨评审和有限扩展。 | 只保持原范围 |
| 2 | CEO | 推荐事故优先账本方案 | 用户挑战 | P1/P6 | 两个外部声音都认为应先证明诊断价值。 | 原 PR0-first 顺序 |
| 3 | CEO | 为 `trace_id`/`trace_id` 和 `task_type`/`command_type` 增加兼容窗口 | 用户挑战 | P3/P5 | 代码、测试、文档仍使用旧名。 | 立即破坏性改名 |
| 4 | CEO | 把 `command_code` 作为硬恢复键，trace echo 仅作可选信号 | 机械 | P5 | 供应商可能不回传 trace，`command_code` 已能定位 command。 | 要求供应商 trace echo 才能恢复 |
| 5 | CEO | 先定义诊断契约，再持久化 | 机械 | P1/P5 | 诊断必须有 owner、blocking point、recoverability、action、evidence。 | 先建泛化诊断表 |
| 6 | CEO | 最小 SMT/WMS 证据前置或异常优先 | 品味/用户挑战 | P1/P3 | 现场诊断需要业务证据，但不能复制成影子 WMS。 | 全部等到 PR7 |
| 7 | CEO | 考虑供应商认证套件，并前置或并行 SANDBOX API | 用户挑战 | P2/P6 | 供应商不合规是高杠杆风险。 | 只做内部 sandbox |
| 8 | CEO | SSE 只做通知 | 机械 | P5 | Redis Pub/Sub 不能作为事实源。 | SSE 作为 durable source |
| 9 | CEO | 为 attempts 和业务证据增加 retention/redaction | 机械 | P1/P3 | 防止证据表泄露或无限增长。 | 保存无限 headers/body |
| 10 | CEO | 增加 top failure 诊断卡 snapshot 测试 | 机械 | P1 | 测试必须验证可操作输出。 | 只测 happy-path 行写入 |
| 11 | Eng | 兼容 alias 是前置条件 | 用户挑战 | P3/P5 | 旧字段仍在代码、测试、mock、硬件文档中。 | 立即切到 `trace_id`/`command_type` |
| 12 | Eng | callback/result 先按 `command_code` 恢复，再拒绝设备上下文 | 机械 | P1/P5 | `command_code` 是 trace 缺失时的稳定恢复键。 | 先拒绝再查 command |
| 13 | Eng | 增加统一 `WorklineDiagnosticService` | 机械 | P5 | 集中 code registry、redaction、事务、去重、证据 shape。 | 分散写 diagnostic |
| 14 | Eng | 使用 DB 幂等 + 冲突处理 | 机械 | P5 | 服务层 check-then-create 会并发竞态。 | 只加索引 |
| 15 | Eng | 定义 timeline sequence allocation | 机械 | P5 | `max(seq_no)+1` 加 unique 只会把竞态变异常。 | 只靠 unique |
| 16 | Eng | 增加 dispatch attempt lease 语义 | 机械 | P5 | 外部调用前后都有 crash window。 | attempt 无 ownership token |
| 17 | Eng | trace/sandbox/manual API 增加独立 RBAC 和对象范围 | 机械 | P1/P3 | API 暴露证据并能改变运行状态。 | 复用宽泛权限 |
| 18 | Eng | 增加 evidence redaction/retention | 机械 | P1/P3 | headers、body、WMS 响应、物料标识需要受控暴露。 | 默认暴露 raw evidence |
| 19 | Eng | 为操作 API 增加明确状态机 | 机械 | P5 | sandbox complete、replay、manual 需要前置条件和幂等。 | ad hoc 改状态 |
| 20 | Eng | 实施前补迁移、并发、性能测试 | 机械 | P1/P5 | 这些是主要生产风险。 | 只测 happy path |
| 21 | DX | 增加 WORKLINE diagnostic hello-world | 用户挑战 | P1/P2 | 计划承诺 1 分钟诊断，但没有可复制 proof path。 | schema-first 作为首个可见交付 |
| 22 | DX | 标准诊断不依赖 SQL/日志 | 机械 | P1/P2 | 现场工程师应使用 trace/diagnostic API。 | 保留 SQL/log 为主路径 |
| 23 | DX | 增加精确 response contract 和 OpenAPI 示例 | 机械 | P1/P2 | endpoint 名不足以指导集成和测试。 | 只有 endpoint 列表 |
| 24 | DX | 增加可执行 fixtures 或 Postman/Newman collection | 机械 | P2/P5 | 诊断承诺必须可运行、可 CI 验证。 | 只有手工 curl |
| 25 | DX | 增加 diagnostic code registry 文档 | 机械 | P1/P3 | 每个 code 需要 owner、cause、fix、recoverability、evidence、docs link。 | free-text diagnostic message |
| 26 | DX | README 链接 WORKLINE diagnostics quickstart | 机械 | P2 | 第一次接入目前只能看到通用后端设置。 | 隐藏在 docs 中 |
| 27 | DX | 增加协议命名兼容/弃用指南 | 机械 | P3/P5 | 集成方面对 `task_type`/`command_type`、`trace_id`/`trace_id` 冲突。 | 直接 rename 无迁移文档 |
| 28 | DX | 增加 vendor/device certification harness | 用户挑战 | P2/P6 | 供应商合规是高杠杆采用风险。 | 依赖非正式供应商文档 |
| 29 | DX | 增加 DX 指标和 CI quickstart check | 机械 | P1/P2 | TTHW 和现场诊断目标需要可测 gate。 | 不度量 DX 意图 |
| 30 | 跨阶段 | 只有解决关注点后才视为修订计划可批准 | 机械 | P5/P6 | 所有阶段都认可方向，但反对当前执行顺序。 | 原 PR 顺序不变直接批准 |

## 实施前工程终审

生成时间：2026-04-27 11:28 CST
触发方式：`$plan-eng-review`
结论：**可以进入实施，但只能按本终审锁定的顺序实施；原 PR0-PR7 顺序不得直接开工。**

### 终审判定

所有未决问题在本节关闭为明确决策。实施阶段不得再把这些点留成“边做边定”。

| 项 | 终审决策 |
| --- | --- |
| 是否先做 `trace_id` 大迁移 | 否。先做诊断闭环，旧字段兼容保留。 |
| `command_code` 与供应商 trace echo 谁是恢复锚点 | `command_code` 是硬恢复锚点；供应商 `trace_id` / `causation_id` 只是增强证据。 |
| 诊断是否允许分散写入 | 否。统一走 `WorklineDiagnosticService`。 |
| 幂等是否继续靠服务层查重 | 否。必须有 DB unique / `ON CONFLICT` / `IntegrityError` 处理。 |
| timeline `seq_no` 如何处理竞态 | 每个 session 用事务级 advisory lock 分配，外加唯一约束兜底。 |
| dispatch attempt 是否只是日志 | 否。它是状态账本，必须有 lease token 和 finalize 语义。 |
| SANDBOX/manual/replay 是否能绕过状态机 | 否。全部通过 service 前置条件、audit、diagnostic、timeline。 |
| 标准诊断是否允许查库/grep 日志 | 否。标准路径必须通过 trace/diagnostics/blocking-point API。 |
| SMT/WMS 业务证据是否一次性做全表 | 否。本分支只做诊断 evidence 中的最小异常证据；查询型 `workline_business_facts` 后置。 |
| 是否需要单独设计文档 | 不阻塞。本计划和本终审就是实施 source of truth。 |

### 锁定执行顺序

```text
1. 诊断 hello-world 垂直切片
    - 不做破坏性 rename。
    - 使用现有 callback_logs / inbox / session / command / outbox / timeline。
    - 交付一条 fixture -> trace_id -> blocking-point -> diagnostic card 的 no-SQL 路径。

2. 兼容身份层
    - 新增 trace_id / event_id / causation_id 字段或投影。
    - 保留 trace_id 读写兼容。
    - callback/result 先用 command_code 恢复，再诊断 device mismatch。

3. 持久诊断与幂等硬化
    - 新增 WorklineDiagnosticService / Repository / model。
    - idempotency_key、dispatch_key、event_id 使用 DB 约束和冲突处理。
    - 诊断 code registry 固化 owner、cause、fix、recoverability、evidence。

4. Timeline 与 trace read model 硬化
    - timeline seq_no 分配加 transaction-level advisory lock。
    - TraceQueryService 从单 session pivot 改为 sessions 列表 read model。
    - device_commands.session_id 采用 dual-column/backfill 迁移。

5. Dispatch attempt 账本
    - 新增 workline_dispatch_attempts。
    - dispatch lease: create attempt -> mark dispatching -> external call -> finalize。
    - crash window 必须有测试。

6. SANDBOX / replay / manual API
    - pending -> template -> complete。
    - replay 创建新 event，不改旧 inbox。
    - manual 操作写 audit、diagnostic、timeline。

7. 文档、认证与观测
    - WORKLINE diagnostics quickstart。
    - vendor/device certification fixtures。
    - TTHW、诊断耗时、attempt latency、dead letter 指标。
```

### Step 0：范围挑战

#### 已有代码能复用什么

| 子问题 | 已有能力 | 终审处理 |
| --- | --- | --- |
| 入站原始证据 | `callback_logs`、callback routes/services | 复用，扩展字段和诊断关联。 |
| 编排入口 | `workline_inbox`、`InboxService` | 复用，补事件身份和 DB 幂等。 |
| 业务状态 | `workline_sessions`、`SessionResolver` | 复用，补 diagnostic 指针和 trace alias。 |
| 指令恢复 | `device_commands.command_code` | 作为 result 恢复硬锚点。 |
| 当前 outbox 状态 | `system_outbox` | 保留 current-state projection，新增 attempts 解释历史。 |
| runtime 诊断卡 | `src/workline_runtime/diagnostics/` | 作为持久诊断卡契约基础。 |
| trace 查询 | `TraceQueryService` | 扩展为多 session read model，不重建宽表。 |
| sandbox 语义 | plugin guide、sandbox happy path、run_mode | API 化，不在 payload 注入 sandbox 字段。 |

#### 最小变更集合

最小可开工集合是基础诊断闭环。没有基础诊断闭环，不允许进入 attempts、sandbox/manual/replay 的实现。

```text
基础诊断闭环必须交付：
callback accepted/rejected evidence
  -> trace identity
  -> diagnostic card
  -> blocking-point API
  -> no-SQL quickstart
  -> DB 幂等与关键并发测试
```

#### 复杂度判定

计划会触达超过 8 个文件，并引入超过 2 个 service/model。复杂度触发，但不是过度设计，因为目标横跨 callback、workline runtime、device command、outbox、trace API、migration 和 docs。范围控制方式不是砍掉完整性，而是用本分支完整交付当前计划项。

### 1. 架构终审

```text
                 +---------------------------+
                 | callback/event,result     |
                 +-------------+-------------+
                               |
                               v
                 +---------------------------+
                 | CallbackOrchestration     |
                 | - command_code first      |
                 | - trace/event identity    |
                 +-------------+-------------+
                               |
      +------------------------+-------------------------+
      |                        |                         |
      v                        v                         v
CallbackLogService     WorklineInboxService     WorklineDiagnosticService
      |                        |                         |
      v                        v                         v
callback_logs          workline_inbox            workline_diagnostics
                               |
                               v
                 +---------------------------+
                 | Celery WORKLINE runtime   |
                 | session/plugin/timeline   |
                 +-------------+-------------+
                               |
                  +------------+-------------+
                  |                          |
                  v                          v
            DeviceCommand              SystemOutbox
                  |                          |
                  +------------+-------------+
                               v
                    DispatchAttemptService
```

#### 架构问题与决策

| # | 问题 | 严重度 / 置信度 | 证据 | 终审决策 |
| --- | --- | --- | --- | --- |
| A1 | 原正文 PR0 仍是术语/链路键迁移优先。 | 最高 / 9 | 本文原 `PR 0：术语和链路键收敛`；代码仍广泛使用 `trace_id`、`task_type`。 | 原 PR0 降级为身份兼容；新增诊断 hello-world。 |
| A2 | `callback/result` 当前先解析 device context，再查 command。 | P1 / 9 | `callback.py` 中 device context resolve 发生在 command lookup 之前。 | 改为 `command_code` first recovery，再做 device mismatch diagnostic。 |
| A3 | TraceQueryService 当前是单 session pivot。 | P1 / 8 | `TraceQueryResult.session` 是单对象，`get_by_trace_id` 返回单 session。 | 改为 `sessions: list` read model；首个实现可先返回单元素列表。 |
| A4 | timeline ACK 分支仍用 `max(seq_no)+1`。 | P1 / 9 | `_append_command_acked_timeline()` 中直接查 max。 | 引入 per-session transaction advisory lock + unique constraint。 |
| A5 | dispatch attempt 没有 lease/finalize 边界。 | P1 / 8 | outbox dispatch 当前 success/fail 直接 mark 状态。 | attempt 是账本：创建、认领、外部调用、finalize 分事务定义。 |
| A6 | trace/sandbox/manual API 暴露高敏证据和状态变更。 | P1 / 8 | 计划新增操作 API，但原正文未给 permission matrix。 | API 实施前必须先落权限矩阵、对象范围和 redaction。 |

采用的外部基础能力：PostgreSQL 官方支持 `CREATE INDEX CONCURRENTLY`、unique index、`INSERT ... ON CONFLICT` 和 advisory locks。终审采用这些 Layer 1 能力，不自研分布式锁或幂等框架。

### 2. 代码质量终审

| # | 问题 | 严重度 / 置信度 | 终审决策 |
| --- | --- | --- | --- |
| C1 | 诊断写入如果分散到 callback、worker、dispatcher，会形成重复逻辑。 | P1 / 8 | 只允许 `WorklineDiagnosticService.raise_*()` 或等价单入口。 |
| C2 | API 层容易为了 trace/sandbox 查询直接 SQL。 | P1 / 8 | 新 API 只调用 service；repository 只在 service 内部使用。 |
| C3 | `task_type` / `command_type` 双命名若无 adapter 会污染全链路。 | P1 / 8 | 只在协议 adapter 层双读双写；领域内部用 canonical 字段。 |
| C4 | 业务证据表如果首个切片全做，会变成影子 WMS。 | P2 / 7 | 本分支只进 diagnostic evidence；查询型 `workline_business_facts` 后置。 |
| C5 | `device_commands.session_id` 直接改整数 FK 会破坏现有查询。 | P1 / 9 | 新增 int FK 列双写/backfill；旧 string 字段保留到切换完成。 |

实施文件约束：

- 新增 service 必须导出到对应 `__init__.py`。
- 所有时间戳存库使用 `timezone.now_for_db()`，API 输出用 aware ISO。
- 不删除现有有价值注释；涉及流程图的服务建议增加 ASCII 数据流注释。

### 3. 测试终审

测试框架：pytest。现有配置来自 `pyproject.toml`，命令使用 `uv run pytest tests/`。

#### 覆盖图

```text
CODE PATHS                                                   TEST STATUS

[+] callback/event
  ├── accepted event -> callback_log + inbox                 [GAP] [→E2E]
  ├── rejected schema -> callback_log + diagnostic root      [GAP] [→E2E]
  ├── duplicate concurrent event -> one inbox                [GAP] [→E2E]
  └── missing trace -> generated trace/event id              [GAP]

[+] callback/result
  ├── known command_code -> recover trace/session            [GAP] [→E2E]
  ├── unknown command_code -> diagnostic                     [GAP]
  ├── mismatched device_code -> diagnostic, not blind reject [GAP]
  ├── duplicate ACK/result                                  [GAP]
  └── vendor omits trace fields                              [GAP]

[+] WorklineDiagnosticService
  ├── create card with owner/recoverability/action/evidence  [GAP]
  ├── redaction policy                                       [GAP]
  ├── dedupe same failure                                    [GAP]
  └── rollback behavior                                      [GAP]

[+] inbox idempotency
  ├── insert success                                         [已有部分]
  ├── concurrent same idempotency_key                        [GAP] [→E2E]
  └── IntegrityError / ON CONFLICT returns duplicate outcome [GAP]

[+] timeline append
  ├── normal sequence allocation                             [已有部分]
  ├── callback ACK branch                                    [GAP]
  └── concurrent seq race with retry/lock                    [GAP] [→E2E]

[+] dispatch attempt
  ├── live success                                           [GAP]
  ├── HTTP/network failure                                   [已有部分 outbox failure，不含 attempt ledger]
  ├── crash after attempt created                            [GAP] [→E2E]
  ├── crash after vendor call before finalize                [GAP] [→E2E]
  └── retry exhausted / dead letter                          [GAP]

[+] trace APIs
  ├── detail by trace_id                                     [GAP] [→E2E]
  ├── blocking point                                         [GAP] [→E2E]
  ├── diagnostics list                                       [GAP]
  ├── command_code -> trace                                  [GAP]
  ├── multi-session trace                                    [GAP]
  └── RBAC/object scope/redaction                            [GAP] [→E2E]

[+] sandbox/manual/replay
  ├── pending outbox list                                    [GAP]
  ├── callback template                                      [GAP]
  ├── complete success                                       [GAP] [→E2E]
  ├── stale/double complete -> 409                           [GAP]
  ├── replay creates new inbox/event                         [GAP]
  └── manual hold/resume/cancel with audit                   [GAP] [→E2E]

COVERAGE: 现有测试覆盖 runtime/plugin/outbox 的部分 happy/error path；
          本计划新增行为在实施前视为 0% 覆盖，必须随代码同步补齐。
```

#### 必须新增的测试文件建议

| 测试文件 | 类型 | 必测内容 |
| --- | --- | --- |
| `tests/api/test_workline_diagnostics_api.py` | API/E2E | trace detail、blocking point、diagnostics list、redaction、权限。 |
| `tests/api/test_callback_trace_identity.py` | API/E2E | accepted/rejected/duplicate callback 的 trace/event/diagnostic。 |
| `tests/api/test_callback_result_recovery.py` | API/E2E | `command_code` first recovery、unknown command、device mismatch。 |
| `tests/workline_runtime/test_workline_diagnostic_service.py` | unit/service | 诊断卡契约、dedupe、redaction、rollback 策略。 |
| `tests/workline_runtime/test_inbox_idempotency_concurrency.py` | integration | DB unique + conflict handling。 |
| `tests/workline_runtime/test_timeline_sequence_allocator.py` | integration | advisory lock / retry / unique 约束。 |
| `tests/workline_runtime/test_dispatch_attempt_service.py` | service/integration | attempt lease、success/failure/finalize、crash window。 |
| `tests/api/test_workline_sandbox_operations.py` | API/E2E | pending/template/complete/stale/double complete。 |
| `tests/api/test_workline_manual_replay_operations.py` | API/E2E | replay/manual 权限、审计、幂等、状态前置条件。 |
| `tests/integration/workline_runtime/test_workline_diagnostics_quickstart.py` | E2E | quickstart smoke，fixture -> trace -> diagnostic card。 |

### 4. 性能终审

| # | 风险 | 严重度 / 置信度 | 终审决策 |
| --- | --- | --- | --- |
| P1 | trace detail 扇出查询在生产量下变慢。 | P1 / 8 | 所有 trace/list API 必须有 query budget 测试和索引说明。 |
| P2 | JSON evidence 搜索会退化成 payload scan。 | P1 / 8 | PkgID/LotCode/MfrPN 等可搜索字段升列或生成列，不做任意 JSON scan。 |
| P3 | attempts 表增长快。 | P2 / 7 | P4 实施时同步定义 retention 或分区策略。 |
| P4 | `CREATE INDEX CONCURRENTLY` 失败会留下 invalid index。 | P1 / 8 | migration runbook 必须包含失败检测和 drop/retry 步骤。 |
| P5 | advisory lock 用错会造成阻塞或锁泄漏。 | P1 / 8 | 只使用 transaction-level lock；禁止 session-level lock。 |

### 失败模式登记表

| 新路径 | 生产失败方式 | 是否已有测试 | 是否有错误处理 | 用户体验 | 终审状态 |
| --- | --- | --- | --- | --- | --- |
| callback/event accepted | DB unique 冲突 | 否 | 部分 | 可能重复或 500 | 必补 |
| callback/event rejected | 无 trace 根 | 否 | 部分 | 只能看错误，无诊断 | 必补 |
| callback/result known command | device context 先失败 | 否 | 部分 | 明明有 command 却查不到链路 | 必改 |
| callback/result unknown command | vendor 回错 command_code | 否 | 部分 | 应看到 UNKNOWN_COMMAND 诊断 | 必补 |
| diagnostic persist | 业务事务 rollback | 否 | 未定义 | 失败可能消失 | 必定事务策略 |
| timeline append | 并发 ACK | 否 | 无 | seq 冲突或 timeline 丢失 | 必补 |
| dispatch attempt | worker crash | 否 | 无 | 不知道是否发给设备 | 必补 |
| sandbox complete | 双击完成 | 否 | 未定义 | 可能推进两次 | 必补 |
| replay | 改写历史 inbox | 否 | 计划禁止 | 证据污染 | 必补 |
| trace API | 越权看其他线体 | 否 | 未定义 | 敏感证据泄露 | 必补 |

关键缺口数：10。实施前允许存在缺口，实施完成前不允许验收通过。

### NOT in scope（终审修订后）

| 项 | 理由 |
| --- | --- |
| 公共前端 trace 工作台 | 本轮先锁定 API 和事实模型，无 UI 范围。 |
| 完整 OpenTelemetry 平台 | 可后续补 span；本轮事实源是关系型账本。 |
| 全量 `workline_business_facts` 查询表 | 本分支只做诊断 evidence，避免影子 WMS。 |
| `task_type` / `command_type` 进一步术语清理 | 不属于本轮 trace 诊断收口。 |
| 通用 event-sourcing 重写 | 现有 WORKLINE 表足够表达目标。 |
| 生产监控 dashboard | 已在 `TODOS.md` 作为监控告警项，不阻塞基础诊断闭环。 |

### 轻量分支实施策略（最终执行口径）

| Step | 涉及模块 | 依赖 |
| --- | --- | --- |
| 诊断 hello-world + quickstart | docs、tests/integration、callback、trace API | 无 |
| trace/event 身份收口 | callback、workline models、migrations、trace context | 诊断闭环契约 |
| 持久诊断服务 | workline services/repositories/models、diagnostics runtime | 诊断闭环契约 |
| 幂等硬化 | workline inbox/outbox repositories、migrations | identity 收口 |
| timeline allocator | workline timeline、callback orchestration、runtime writer | identity 收口 |
| trace read model | workline trace service/API | identity 收口、diagnostics |
| dispatch attempts | workline outbox、celery dispatcher、migrations | diagnostics |
| sandbox/manual/replay | workline API/services、audit、permissions | diagnostics、dispatch attempts |
| docs/certification/metrics | docs、tests、scripts | 可并行，但要跟随最终 API |

并行 lanes：

```text
先顺序完成：诊断闭环契约和 quickstart smoke。

随后可并行：
Lane A: trace/event 身份收口 -> trace read model
Lane B: 持久诊断服务 -> diagnostic code registry docs
Lane C: 幂等硬化 -> timeline allocator
Lane D: vendor certification fixtures / quickstart docs

等待 A+B+C 合并后：
Lane E: dispatch attempts
Lane F: sandbox/manual/replay

冲突提示：A/B/C 都会触碰 `src/app/workline/`，需要清晰 ownership；
同一文件级别冲突时顺序合并，不强行平行。
```

### 实施前未决问题清零表

| 未决问题 | 终审答案 |
| --- | --- |
| 首个诊断闭环到底做什么？ | 做 no-SQL 诊断 hello-world 垂直切片，并在本分支继续完成当前计划项。 |
| 原 PR0 怎么处理？ | 直接做 trace/event 身份收口；不保留旧命名兼容。 |
| result 恢复以谁为准？ | `command_code`。 |
| 诊断表事务怎么定？ | `WorklineDiagnosticService` 定义策略：业务失败需要随主事实提交；主事务回滚场景必须写 callback-level 诊断根。 |
| 幂等怎么定？ | DB unique + `ON CONFLICT` / `IntegrityError`，服务层查重只能作为优化。 |
| timeline 并发怎么定？ | transaction-level advisory lock per session + unique 约束。 |
| attempt crash window 怎么定？ | attempt lease token + finalize；每个 crash window 有测试。 |
| multi-session trace 怎么定？ | read model 返回 `sessions: list`；首个实现可单元素，后续支持多 session。 |
| session_id string 迁移怎么定？ | dual-column/backfill，禁止直接改类型。 |
| 业务证据怎么定？ | 进 diagnostic evidence，完整 facts 表后置。 |
| 权限怎么定？ | trace、diagnostics、sandbox、manual、replay 分权限，且做对象范围校验。 |
| TODO 是否阻塞？ | 现有监控告警 TODO 不阻塞基础诊断闭环；本轮不新增 TODO，延后项已写入 NOT in scope。 |

### 终审完成摘要

| 项 | 结果 |
| --- | --- |
| Step 0 范围挑战 | 原范围不砍，本分支完整完成当前计划项；基础诊断闭环是开工门槛。 |
| 架构评审 | 6 个问题，均已决。 |
| 代码质量评审 | 5 个问题，均已决。 |
| 测试评审 | 覆盖图已产出，10 类关键缺口。 |
| 性能评审 | 5 个问题，均已决。 |
| NOT in scope | 已写。 |
| What already exists | 已写。 |
| TODOS.md | 不新增；现有 P2 监控 TODO 不阻塞。 |
| 失败模式 | 10 个关键缺口，实施完成前必须全部覆盖。 |
| Outside voice | 使用前序 autoplan 的 Codex/子代理结论；本轮不再重复生成。 |
| 并行化 | 6 条 lane，诊断闭环先顺序，A-D 可并行，E-F 后置。 |
| Lake Score | 终审选择完整方案：测试、并发、迁移、错误路径不降级。 |

最终门槛：实现完成后，只有当 API/服务/迁移/测试/文档全部满足本节约束，才能认为该计划进入可交付状态。
