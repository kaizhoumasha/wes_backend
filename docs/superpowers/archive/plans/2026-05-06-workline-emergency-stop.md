<!-- /autoplan restore point: /Users/kaizhou/.gstack/projects/kaizhoumasha-wes_backend/workline-sandbox-runtime-flow-autoplan-restore-20260506-143551.md -->
<!-- /autoplan restore point: /Users/kaizhou/.gstack/projects/kaizhoumasha-wes_backend/workline-sandbox-runtime-flow-autoplan-restore-20260506-135145.md -->

# WorkLine 软件侧急停冻结与事故审计计划

> 本计划只处理 WES 软件侧对工作线/设备上报急停事务的冻结、阻断、审计和恢复授权；不控制 PLC 或设备物理急停。

## 目标

将 `ESTOP_PRESSED` 从插件业务事件流中移出，作为平台保留的 WorkLine 级安全事件处理。

急停发生后，WES 必须：

- 在 `SessionResolver` 和插件编排前短路处理 `ESTOP_PRESSED`。
- 阻止该 WorkLine 派生新的 session、outbox、command 和外部副作用。
- 将该 WorkLine 上未完成的 session/outbox/command 统一终止并失败/取消。
- 记录 `WorklineSafetyIncident` 事故流水和 drain 证据。
- 只允许有权限的认证用户在结构化恢复检查后 clear-estop。
- clear-estop 只恢复新流程接收能力，不复活旧 session/outbox/command。

## 非目标

- 不控制物理急停、PLC、设备硬件停机或复位。
- 不承诺远端设备已停止执行已经下发的命令；WES 只记录本地终止和证据。
- 不实现 SafetyZone、跨线共享设备影响范围或运营看板。
- 不在本计划中实现 timeout 默认 failure；该项拆为独立 PR/计划。
- 不保留插件级 `ESTOP_PRESSED` handler 兼容逻辑。
- 不新增单独开工 event、`READY_TO_START` 状态或第二套 start checklist。
- 不新增第二张 `estop_log` 表；v1 使用 `WorklineSafetyIncident` 作为急停事故流水和恢复审计表。

## 架构决策

### D1. ESTOP 是平台保留事件

`ESTOP_PRESSED` 不属于插件事件、单个设备 session 事件或业务命令结果。插件不得在以下位置声明或处理该事件：

- `supported_events`
- `event_source_roles`
- `@on_event("ESTOP_PRESSED")`

Manifest、decorator、插件 registry/启动期都应硬拒绝该声明，错误信息必须包含：

```text
ESTOP_PRESSED 是平台保留安全事件
```

标准错误格式：

| Field | Value |
|---|---|
| `error_code` | `RESERVED_RUNTIME_EVENT_DECLARED` |
| `plugin_key` | 触发错误的插件 |
| `event` | `ESTOP_PRESSED` |
| `declaration_surface` | `supported_events` / `event_source_roles` / `@on_event` / `registry` |
| `file_hint` | 能定位时给出模块/handler 名称 |
| `migration_hint` | 删除该声明；如需观察急停，请查询 incident/runtime API，不要写插件 handler |

### D2. 急停入口必须在 session 解析前

`ProcessInboxMessages._process_batch()` 在 malformed gate 后、`_load_related_entities()` 前识别 `ESTOP_PRESSED`。

该分支直接调用 `WorkLineSafetyService.handle_estop()`，不得触发：

- `SessionResolver.resolve_or_create()`
- plugin `on_device_event()`
- orchestrator business flow

### D3. WorkLine 是 v1 软件冻结边界

v1 以 `WorkLine` 为软件冻结边界：

- 通过 inbox/command/device 解析 `workline_id`。
- 解析失败时记录 unresolved ESTOP，并产生高优先级诊断/运行态事件。
- SafetyZone 和跨线共享设备影响范围进入 TODO，不阻塞本计划。

### D4. 未完成工作全部终止

用户已确认急停时该工作线未完成工作全部终止：

- open session -> `FAILED`，failure code 使用 `WORKLINE_ESTOPPED`。
- pending/dispatching/sent/acked outbox -> 本地终止，不再重试。
- pending/sent/ack command -> 本地取消/失败，不宣称远端设备已物理停止。
- 相关设备进入需要人工处理的错误/安全状态，并清空当前命令引用。

事故证据必须区分：

- WES 本地已终止。
- 远端设备状态未知或待人工确认。

真实状态集合必须使用当前模型枚举：

- session open 状态：`NEW`、`RUNNING`、`WAITING_DEVICE_RESULT`、`WAITING_EXTERNAL`、`MANUAL_HOLD`。
- outbox 本地终止状态：`NEW`、`DISPATCHING`、`SENT`、`ACKED`。
- command 本地终止状态：`PENDING`、`SENT`、`ACK_RECEIVED`。
- inbox 旧工作阻断状态：`NEW`、`PROCESSING`、`RETRY` 中属于 ESTOPPED WorkLine 的非 ESTOP 消息不得继续推进业务。

### D5. clear-estop 是权限动作

clear-estop 必须：

- 走 API 层 -> Service 层 -> Repository 层，不允许 API 直接访问数据库。
- 使用认证上下文中的用户作为 `cleared_by`，不接受请求体自报 operator。
- 要求独立权限 `biz:workline:clear-estop`，不得复用 `biz:workline:update`。
- 要求结构化恢复检查记录。
- 在无 active incident、工作线不存在、恢复检查不合格时失败。

clear-estop 只将 WorkLine 恢复为可接收新流程，不恢复旧 session/outbox/command。

### D6. active incident 必须幂等

每条 WorkLine 同时最多一条 ACTIVE incident。服务层锁 WorkLine，并通过数据库唯一约束或等效机制防止并发重复 ACTIVE。

数据库约束：

- `UNIQUE(workline_id) WHERE status = 'ACTIVE' AND workline_id IS NOT NULL`
- `source_inbox_id` 唯一，避免同一 ESTOP inbox 重复生成 unresolved incident。

unresolved ESTOP 不进入“无限 ACTIVE”语义，应记录为 unresolved/diagnostic 事实，并让 inbox 有明确终态，避免无限重试。

### D7. timeout 默认 failure 拆出

timeout 默认 failure 是 runtime 等待治理，不属于本计划。相关文档、测试、代码改动不放入本 PR。

### D8. ESTOP scope resolver 独立于 SessionResolver

新增专用 ESTOP scope 解析逻辑，只允许通过以下路径解析 WorkLine：

1. `inbox.workline_id`
2. `inbox.command_id` -> command `workline_id`
3. `inbox.device_id` 或 payload `device_code` -> device `work_line_id`

该 resolver 不得调用 `SessionResolver.resolve_or_create()`，不得创建 session。

### D9. freeze 与 drain 分两阶段

急停处理分为两个幂等阶段：

1. 小事务：锁 WorkLine、upsert active incident、设置 `ESTOPPED` 并提交。
2. drain 阶段：批量终止 session/outbox/command/device，记录 `drain_status`、counts、errors 和远端未知证据。

即使 drain 部分失败，WorkLine 也必须已经冻结。后续重试 drain 必须幂等。

### D10. dispatcher claim 必须防竞态

dispatcher 在真正外部 I/O 前必须重新确认 WorkLine safety projection。主实现固定为 repository 原子 claim：

- 新增 repository claim 方法，只 claim `READY` WorkLine 的 outbox。
- claim 条件必须排除 `ESTOPPED` WorkLine。
- dispatcher 不允许绕过该 claim 方法直接 `mark_as_dispatching()`。
- 外部 I/O 前执行二次 safety check。

发现 `ESTOPPED` 后直接本地终止，不进入普通 `mark_as_failed()` retry 逻辑，不发 HTTP 请求。

### D11. 迟到 command callback 不得复活旧工作

ESTOP 后设备可能继续返回迟到 callback。callback ingress 必须：

- 检测 command/session/workline 是否已因 ESTOP terminal。
- 记录迟到远端结果证据。
- 不覆盖 command/outbox/session 的 ESTOP terminal 状态。
- 不创建会推进业务的 command-result inbox。

### D12. incident evidence 需要脱敏和大小限制

`trigger_payload_json` 和 `evidence_json` 进入事故审计前必须脱敏：

- token、secret、password、authorization、cookie、内部 URL 参数等敏感字段不得原样返回。
- 列表 API 只返回摘要，详情 API 需要独立权限。
- JSON 字段需要大小限制，避免大 payload 放大事故表。
- `trigger_payload_json` 最大 16KB，`evidence_json` 最大 64KB；超限截断并标记 `truncated=true`。
- 脱敏规则应复用同一个 helper，并测试嵌套 token、authorization header、URL query secret 和大 payload。

### D13. v1 恢复流程保持简单

v1 不引入单独“开工”事件，不增加 `READY_TO_START` 状态。

恢复流程固定为：

1. 操作员手工按下现场急停按钮。
2. 设备/PLC 或上游系统向 WES 上报 `ESTOP_PRESSED`。
3. WES 冻结 WorkLine，终止未完成工作，阻断新工作。
4. 操作员现场排查并复位急停按钮。
5. 有权限的认证用户调用 `clear-estop`，提交最小恢复 checklist。
6. WES 将 WorkLine 从 `ESTOPPED` 恢复为 `READY`。
7. 后续正常业务事件自然开工。

如果设备/PLC 额外上报 `ESTOP_RELEASED` / `SAFETY_RESET_CONFIRMED`，v1 只把它作为 active incident 的 evidence 记录，不自动恢复 `READY`，不创建 session，不进入插件。

### D14. 急停日志表策略

v1 需要单独记录急停事故，但不需要再拆第二张“急停日志表”。

`WorklineSafetyIncident` 就是本计划的急停事故流水和恢复审计表，承担：

- 急停触发记录。
- WorkLine 冻结状态证据。
- drain counts / drain errors。
- 远端未知命令证据。
- 复位/恢复 checklist。
- clear-estop 操作者和时间。
- unresolved ESTOP 排查信息。

后续如果出现“一次 incident 下需要多条细粒度事件时间线”的需求，再增加 `WorklineSafetyIncidentEvent` 子表；v1 不做。

## API Contract

### Endpoints

| Method | Path | Permission | Purpose |
|---|---|---|---|
| `GET` | `/worklines/{workline_id}/safety/incidents/active` | `biz:workline:detail` | 查询指定 WorkLine 当前 active incident 摘要 |
| `GET` | `/workline-safety-incidents` | `biz:workline:detail` | 查询 incident 列表，默认返回脱敏摘要 |
| `GET` | `/workline-safety-incidents/{incident_id}` | `biz:workline:detail` + detail policy | 查询 incident 详情，返回脱敏 payload/evidence |
| `POST` | `/worklines/{workline_id}/clear-estop` | `biz:workline:clear-estop` | 结构化恢复检查后解除软件侧冻结 |

### ClearEstopRequest

| Field | Required | Description |
|---|---|---|
| `clear_reason` | yes | 恢复原因 |
| `checks` | yes | 恢复检查项数组，每项包含 `key`、`passed`、`evidence` |
| `device_confirmations` | yes | 设备检查数组，每项包含 `device_id/device_code`、`status`、`current_command_id`、`confirmed_safe` |
| `operator_note` | no | 操作员补充说明 |
| `confirmed_at` | yes | 现场确认时间，API response 使用 aware ISO，DB 存储使用 `timezone.now_for_db()` |

最小检查项：

- `physical_estop_released_confirmed`
- `devices_inspected`
- `remote_unknown_commands_acknowledged`
- `drain_completed_or_reviewed`
- `line_clear_confirmed`

`cleared_by` 来自认证上下文，不在请求体中接收。

### Response Fields

clear-estop 成功响应至少包含：

| Field | Meaning |
|---|---|
| `incident_id` | 被解除的 incident |
| `workline_id` | 工作线 ID |
| `runtime_status` | `READY` |
| `cleared_by` | 认证用户 |
| `cleared_at` | 恢复时间 |
| `drain_status` | drain 最终状态 |
| `old_work_terminal` | 旧 session/outbox/command 是否保持 terminal |

incident 列表只返回：

- `incident_id`
- `status`
- `workline_id`
- `event_type`
- `drain_status`
- `created_at`
- `summary_counts`
- `truncated`

incident 详情返回脱敏 payload/evidence，并包含 `redacted_fields`。

### Error Response Examples

插件保留事件声明错误：

```json
{
  "error_code": "RESERVED_RUNTIME_EVENT_DECLARED",
  "plugin_key": "smt_classifier",
  "event": "ESTOP_PRESSED",
  "declaration_surface": "@on_event",
  "file_hint": "SmtClassifierPlugin.handle_estop",
  "migration_hint": "删除该声明；如需观察急停，请查询 incident/runtime API，不要写插件 handler"
}
```

clear-estop 恢复检查失败：

```json
{
  "code": "4001",
  "message": "急停恢复检查不通过",
  "data": {
    "reason_code": "ESTOP_RECOVERY_REJECTED",
    "failed_checks": ["devices_not_safe", "unresolved_remote_commands"],
    "required_actions": ["确认所有设备状态不是 ERROR/OFFLINE", "确认远端未知命令已人工复核"]
  }
}
```

unresolved ESTOP 详情摘要：

```json
{
  "incident_id": 123,
  "status": "UNRESOLVED",
  "reason_code": "ESTOP_WORKLINE_UNRESOLVED",
  "source_inbox_id": 456,
  "device_code": "ARM01",
  "command_id": null,
  "resolution_inputs_tried": ["inbox.workline_id", "command.workline_id", "device.work_line_id"],
  "missing_identifiers": ["workline_id"],
  "next_action": "修复设备到工作线映射后人工关联或重新处理该事件",
  "can_clear": false,
  "escalation_level": "HIGH"
}
```

## 错误码归属矩阵

| Code | Location | API ResponseCode | Recoverable | Recommended action |
|---|---|---|---|---|
| `WORKLINE_ESTOPPED` | WorkLine runtime status / session failure reason | n/a | yes, via clear-estop | 查看 active incident 并完成恢复检查 |
| `WORK_ACCEPTANCE_BLOCKED_BY_ESTOP` | 新 session/command/outbox 创建拒绝 | `BusinessErrorCode.INVALID_STATE` | yes | 等待 clear-estop 后重试新流程 |
| `DISPATCH_BLOCKED_BY_WORKLINE_ESTOP` | dispatcher 阻断 | n/a diagnostic/outbox last_error | yes | 不重试该 outbox，查看 incident |
| `CANCELLED_BY_ESTOP` | 旧 session/outbox/command drain | n/a terminal reason | no | 旧工作保持 terminal，重新创建新流程 |
| `ESTOP_WORKLINE_UNRESOLVED` | diagnostic / unresolved incident | `BusinessErrorCode.INVALID_STATE` for manual actions | yes, via mapping repair | 修复 device/command/workline 映射 |
| `ESTOP_RECOVERY_REJECTED` | clear-estop API | `BusinessErrorCode.INVALID_STATE` | yes | 按 `required_actions` 完成恢复检查 |
| `RESERVED_RUNTIME_EVENT_DECLARED` | plugin manifest/decorator/registry | startup/test error | yes | 删除插件 ESTOP 声明 |

## 状态与数据约定

### WorkLine 投影字段

只在 `WorkLine` 表模型增加软件侧安全投影字段，不放入 `WorkLineBase`：

| Field | Purpose |
|---|---|
| `runtime_status` | `READY` / `ESTOPPED`，表示是否允许新工作派生 |
| `active_safety_incident_id` | 当前 active incident |
| `stopped_at` | 本次冻结时间 |
| `stopped_reason` | 冻结原因 |
| `resumed_at` | 最近恢复时间 |

### Incident 字段

`WorklineSafetyIncident` 记录急停事故流水：

| Field | Purpose |
|---|---|
| `workline_id` | 归属工作线，可为空以记录 unresolved ESTOP |
| `status` | `ACTIVE` / `CLEARED` / `UNRESOLVED` |
| `event_type` | 默认 `ESTOP_PRESSED` |
| `source_inbox_id` | 来源 inbox |
| `source_device_id` | 来源设备 |
| `source_command_id` | 来源命令 |
| `trigger_payload_json` | 原始触发 payload |
| `evidence_json` | drain 结果、远端未知命令、诊断证据 |
| `drain_status` | `PENDING` / `PARTIAL` / `COMPLETED` / `FAILED` |
| `drain_error_json` | drain 局部失败证据 |
| `cleared_by` | 认证用户 |
| `clear_reason` | 恢复原因 |
| `recovery_check_json` | 结构化恢复检查 |
| `release_evidence_json` | 可选，设备/PLC 上报复位事件时记录证据；不驱动 READY |
| `resolution_inputs_tried` | unresolved ESTOP 尝试过的解析输入 |
| `missing_identifiers` | unresolved ESTOP 缺失的关键标识 |
| `next_action` | unresolved ESTOP 推荐操作 |

### 错误码

| Code | Meaning |
|---|---|
| `WORKLINE_ESTOPPED` | 工作线急停冻结，禁止新工作 |
| `CANCELLED_BY_ESTOP` | 已因急停终止本地未完成工作 |
| `BLOCKED_BY_WORKLINE_ESTOP` | 派发出口被急停状态阻断 |
| `ESTOP_WORKLINE_UNRESOLVED` | 急停无法解析工作线 |
| `ESTOP_RECOVERY_REJECTED` | 恢复检查不通过 |

## 文件职责

### Create

- `src/workline_runtime/runtime_events.py`：平台保留 runtime event 常量与校验 helper。
- `src/app/workline/models/safety.py`：runtime status、incident status、incident table 和 API schema。
- `src/app/workline/repositories/safety_incident_repository.py`：active incident 查询、锁定、创建、恢复。
- `src/app/workline/services/safety_service.py`：急停入口、工作线锁、drain、clear-estop、派生守卫。
- `src/app/workline/services/safety_resolver.py`：ESTOP scope resolver，只解析 WorkLine/Device/Command，不创建 session。
- `src/app/workline/v1/safety.py`：incident 查询和 clear-estop API。
- `migrations/versions/20260506_1500_d1f2a3b4c5d6_add_workline_safety_incidents.py`：schema migration。
- `tests/workline_runtime/test_reserved_runtime_events.py`
- `tests/workline_runtime/test_workline_safety_models.py`
- `tests/workline_runtime/test_workline_safety_service.py`
- `tests/workline_runtime/test_workline_estop_pre_session.py`
- `tests/workline_runtime/test_outbox_dispatcher_safety.py`
- `tests/workline_runtime/test_workline_safety_concurrency.py`
- `tests/workline_runtime/test_workline_safety_late_callback.py`
- `tests/api/workline/test_workline_safety_api.py`
- `tests/integration/workline/test_workline_safety_incident_flow.py`

### Modify

- `src/app/workline/models/workline.py`：添加 WorkLine 安全投影字段。
- `src/app/workline/models/runtime.py`：运行态响应增加 safety fields。
- `src/app/workline/models/__init__.py`：导出 safety models。
- `src/app/workline/repositories/workline_repository.py`：增加 `get_for_update()` 和安全投影更新 helper。
- `src/app/workline/repositories/session_repository.py`：增加工作线 open session 查询/失败 helper。
- `src/app/workline/repositories/outbox_repository.py`：增加工作线安全取消 helper；急停阻断不作为普通 retry。
- `src/app/device/repositories/command_repository.py`：增加工作线 command 安全取消 helper。
- `src/app/device/services/device_service.py`：增加工作线设备进入安全错误态 helper。
- `src/app/workline/services/runtime_query_service.py`：查询 runtime safety projection。
- `src/app/workline/services/__init__.py`：导出 `WorkLineSafetyService` 和实例。
- `src/app/workline/repositories/__init__.py`：导出 safety incident repository。
- `src/app/workline/v1/__init__.py`：注册 safety router。
- `src/celery_app/tasks/workline.py`：ESTOP pre-session gate、orchestrator effect guard、dispatcher safety result。
- `src/app/callback/services/callback_orchestration_service.py`：迟到 command callback 识别和证据记录，禁止覆盖 ESTOP terminal 状态。
- `src/app/device/services/device_command_service.py`：已因 ESTOP terminal 的 command 不被普通 callback 覆盖。
- `src/workline_runtime/session_resolver.py`：移除 `ESTOP_PRESSED` 设备级 session 归属逻辑。
- `src/workline_runtime/plugin_base.py`：`@on_event` 和 subclass handler 注册硬拒绝保留事件。
- `src/workline_runtime/plugin_manifest.py`：manifest 中 `supported_events` / `event_source_roles` 硬拒绝保留事件。
- `src/workline_runtime/null_plugin.py`：删除 `ESTOP_PRESSED` handler。
- `src/workline_plugins/smt_classifier/plugin.py`：删除插件急停处理。
- `src/workline_plugin_registry.py`：registry 加载时再次校验保留事件。
- `docs/plugin_development_guide.md`：说明 reserved events 和软件侧急停边界。
- `docs/workline_safety_incident_sop.md`：运维事故 SOP、unresolved 处置、clear-estop 检查说明。
- `docs/templates/workline_plugin/plugin.py.tmpl`：模板删除急停 handler。
- `docs/templates/workline_plugin/README.md`：模板说明平台安全事件。
- `docs/workline_flow_diagram.md`：急停从插件事件流移到 WorkLine 软件侧急停冻结流。

## 实施任务

### Task 1. Reserved Runtime Event Contract

目标：

- 建立 `RESERVED_RUNTIME_EVENTS = {"ESTOP_PRESSED"}`。
- Manifest、decorator、subclass route table、registry 启动期都拒绝插件声明 ESTOP。
- 错误信息面向插件开发者，明确删除位置。
- 提供迁移扫描命令和 before/after 指南。

验收：

- `ESTOP_PRESSED` 不能出现在 plugin `supported_events`。
- `ESTOP_PRESSED` 不能出现在 `event_source_roles`。
- `@on_event("ESTOP_PRESSED")` 在类定义/注册阶段失败。
- registry 加载旧插件时失败发生在启动期或测试期，而不是现场运行期。
- 错误包含 `plugin_key`、`declaration_surface`、`file_hint`、`migration_hint`。
- 文档提供扫描命令：

```bash
rtk rg -n "ESTOP_PRESSED|@on_event\\(\"ESTOP_PRESSED\"\\)" src/workline_plugins src/workline_runtime docs/templates
```

验证：

```bash
rtk uv run pytest tests/workline_runtime/test_reserved_runtime_events.py -q
```

### Task 2. Safety Models and Migration

目标：

- 增加 WorkLine 安全投影字段。
- 增加 `WorklineSafetyIncident` 表和 schema。
- active incident 并发幂等：每条 WorkLine 同时最多一条 ACTIVE incident。
- unresolved ESTOP 可记录，但必须带诊断证据和唯一 source inbox。
- incident 支持 drain status 和局部失败证据。

验收：

- migration 可升级。
- 现有 WorkLine backfill `runtime_status=READY`。
- 新字段默认值、nullable 策略、索引名、partial unique、downgrade 行为明确。
- 权限 `biz:workline:clear-estop` 进入权限同步/菜单/角色流程，默认不授予普通 update 用户。
- `WorkLineBase` 不暴露内部安全投影字段。
- active incident 唯一性有数据库层保护。
- unresolved ESTOP 不会因 `workline_id IS NULL` 绕过幂等。
- enum/check 约束与模型枚举一致。

验证：

```bash
rtk uv run pytest tests/workline_runtime/test_workline_safety_models.py -q
rtk ./scripts/migrate.sh upgrade
```

### Task 3. Safety Service Core

目标：

- `WorkLineSafetyService.handle_estop()` 负责调用 safety resolver、锁定 WorkLine、创建/复用 incident、设置冻结投影。
- drain 独立为可重试幂等阶段，记录 `drain_status`、counts、errors。
- `WorkLineSafetyService.assert_accepting_work()` 为新 session/outbox/command/external effect 提供统一 guard。
- `WorkLineSafetyService.clear_estop()` 负责权限后的恢复逻辑，不复活旧工作。

验收：

- 急停后 open session 变为 terminal failure。
- 未派发/派发中/已发送 outbox 不再重试。
- command 本地取消/失败，证据中记录远端状态未知。
- 设备进入需要人工处理的状态并清空当前命令引用。
- 重复 ESTOP 复用 active incident 或追加证据，不产生多个 ACTIVE。
- freeze 小事务成功后，即使 drain 失败，WorkLine 仍保持 `ESTOPPED`。

验证：

```bash
rtk uv run pytest tests/workline_runtime/test_workline_safety_service.py -q
```

### Task 4. True Pre-Session ESTOP Inbox Gate

目标：

- 在 `_load_related_entities()` 前处理 `ESTOP_PRESSED`。
- 移除 `SessionResolver` 中 ESTOP 的设备级归属逻辑。
- ESTOP inbox 处理成功后不产生 session。
- ESTOPPED WorkLine 上的非 ESTOP inbox 在 session 创建前被阻断。
- unresolved ESTOP inbox 有明确终态和诊断，不无限重试。

验收：

- monkeypatch `SessionResolver.resolve_or_create()` 抛错时，ESTOP 测试仍通过。
- `inbox.session_id` 保持为空。
- WorkLine runtime 投影变为 `ESTOPPED`。
- unresolved ESTOP 产生 `ESTOP_WORKLINE_UNRESOLVED` 诊断。

验证：

```bash
rtk uv run pytest tests/workline_runtime/test_workline_estop_pre_session.py -q
```

### Task 5. Safety Guards for Orchestrator and Outbox Dispatch

目标：

- 在 `_apply_orchestrator_effects()` 创建 external decision/command 前检查 WorkLine 是否 accepting work。
- 在 `OutboxDispatcher` 派发前检查 WorkLine 是否 accepting work。
- 急停阻断不计入普通 dispatch retry。
- dispatcher claim 与 WorkLine safety check 必须防并发竞态。
- dispatcher 只通过 repository 原子 claim 方法领取 outbox。

验收：

- ESTOPPED WorkLine 上的新 command/outbox 创建被拒绝。
- ESTOPPED WorkLine 上 pending outbox 被本地终止，`last_error = BLOCKED_BY_WORKLINE_ESTOP`。
- dispatcher 不向设备或外部 HTTP 发请求。
- 运行态 SSE/诊断能看到阻断原因。
- ESTOP 与 dispatcher 抢同一 outbox 时，HTTP mock 未被调用。
- 测试覆盖 claim 条件和二次 safety check。

验证：

```bash
rtk uv run pytest tests/workline_runtime/test_outbox_dispatcher_safety.py -q
```

### Task 6. Recovery API and Runtime Projection

目标：

- 增加 incident 查询 API。
- 增加 clear-estop API。
- runtime projection 返回 `runtime_status`、active incident、停止原因、恢复时间等字段。
- API contract 按本计划固定路径、权限、请求/响应字段和错误响应。

验收：

- API 层只调用 Service，不直接访问 DB 或 Repository。
- clear-estop 使用认证用户作为 `cleared_by`。
- clear-estop 使用独立权限 `biz:workline:clear-estop`。
- 无权限返回 403。
- 无 active incident 返回明确错误。
- 恢复检查缺失或设备仍不安全时拒绝 clear。
- clear-estop 后只允许新流程，不改变旧 session/outbox/command terminal 状态。
- incident 列表 API 只返回脱敏摘要，详情 API 受权限保护。
- `ESTOP_RECOVERY_REJECTED` 返回 `failed_checks` 和 `required_actions`。
- unresolved incident 返回 `resolution_inputs_tried`、`missing_identifiers`、`next_action`、`can_clear=false`。

验证：

```bash
rtk uv run pytest tests/api/workline/test_workline_safety_api.py -q
rtk grep -r "from sqlalchemy import select" src/app/*/v1/
rtk grep -r "db.execute(" src/app/*/v1/
```

### Task 7. Plugin and Documentation Cleanup

目标：

- 删除 `null_plugin` 和 `smt_classifier` 中的 ESTOP handler/manifest 声明。
- 更新插件开发文档和模板，说明 `ESTOP_PRESSED` 是平台保留安全事件。
- 明确 WES 只处理软件侧冻结，不控制物理急停。
- 增加插件迁移指南和最小 hello world。

验收：

- 模板中不再包含 `@on_event("ESTOP_PRESSED")`。
- SMT 插件不注册 ESTOP handler。
- 文档说明插件不得声明 ESTOP。
- 文档不再把 ESTOP 描述为插件 session 恢复事件。
- 文档包含 before/after 片段：删除 manifest/decorator 声明，改查 runtime/incident API。
- 最小 hello world 包含复制模板、单测命令、sandbox seed/fixture、event curl、result curl、通过标准。

验证：

```bash
rtk uv run pytest tests/workline_plugins/test_plugin_template_assets.py tests/integration/workline_plugins/test_smt_classifier_plugin_events.py -q
```

### Task 8. End-to-End Safety Incident Flow

目标：

- 覆盖多设备、多 session、多 outbox/command 的完整急停流。
- 覆盖急停后 dispatcher 阻断。
- 覆盖 clear-estop 后只允许新流程。
- 覆盖迟到 command callback 不复活旧工作。

验收：

- `ProcessInboxMessages._process_batch()` 能处理 ESTOP 并冻结 WorkLine。
- 未完成 session/outbox/command 全部终止。
- 新 outbox 在 ESTOPPED 状态下不会派发。
- clear-estop 后 WorkLine 恢复 `READY`，旧工作仍保持 terminal。
- ESTOP 后迟到 callback 只记录证据，不覆盖 terminal 状态。

验证：

```bash
rtk uv run pytest tests/integration/workline/test_workline_safety_incident_flow.py -q
```

### Task 9. Full Verification

验证：

```bash
rtk uv run pytest \
  tests/workline_runtime/test_reserved_runtime_events.py \
  tests/workline_runtime/test_workline_safety_models.py \
  tests/workline_runtime/test_workline_safety_service.py \
  tests/workline_runtime/test_workline_estop_pre_session.py \
  tests/workline_runtime/test_outbox_dispatcher_safety.py \
  tests/workline_runtime/test_workline_safety_concurrency.py \
  tests/workline_runtime/test_workline_safety_late_callback.py \
  tests/api/workline/test_workline_safety_api.py \
  tests/workline_plugins/test_plugin_template_assets.py \
  tests/integration/workline_plugins/test_smt_classifier_plugin_events.py \
  tests/integration/workline/test_workline_safety_incident_flow.py \
  -q

rtk uv run ruff format src/app/workline src/app/device src/workline_runtime src/workline_plugins tests/workline_runtime tests/api/workline tests/integration/workline tests/workline_plugins
rtk uv run ruff check src/app/workline src/app/device src/workline_runtime src/workline_plugins tests/workline_runtime tests/api/workline tests/integration/workline tests/workline_plugins
rtk ./scripts/migrate.sh upgrade
rtk grep -r "from sqlalchemy import select" src/app/*/v1/
rtk grep -r "db.execute(" src/app/*/v1/
```

## 验收标准

功能验收：

- ESTOP 不进入 SessionResolver/plugin/orchestrator 正常业务流。
- ESTOP 能在无 session 的 inbox 上成功处理。
- ESTOP 后 WorkLine 不再接收新流程、不再派发新外部副作用。
- ESTOP 后未完成工作全部 terminal。
- clear-estop 不复活旧工作。
- 重复 ESTOP 幂等。
- clear-estop 有 RBAC、认证用户、结构化恢复检查。

架构验收：

- API 层不直接访问数据库或 Repository。
- Service 负责业务编排。
- Repository 负责 DB 查询和批量更新。
- 新 service/repository/model 均在 `__init__.py` 导出。
- `EnterpriseMixin` 不重复继承 `AuditMixin` 或 `OptimisticLockMixin`。
- 时间写入数据库使用 `timezone.now_for_db()`。

测试验收：

- 覆盖成功路径、失败路径、并发/重复 ESTOP、权限失败、恢复拒绝、dispatcher 阻断。
- focused tests、ruff、migration smoke、architecture guard 全部通过。

## 风险与缓解

| Risk | Severity | Mitigation |
|---|---|---|
| WES 软件冻结被误解成物理安全控制 | Critical | 文档和 API 描述明确不控制 PLC/物理急停 |
| SENT/ACKED 远端命令状态被误报为已物理取消 | Critical | evidence 记录远端未知，不宣称设备已停止 |
| 并发 ESTOP 产生多个 ACTIVE incident | High | WorkLine 锁 + DB 唯一约束 + 并发测试 |
| clear-estop 权限过低 | High | RBAC + 认证用户 + 结构化 checklist |
| WorkLine 边界覆盖不了共享设备 | Medium | 进入 TODO，后续 SafetyZone 设计 |
| 文档/模板仍鼓励插件处理 ESTOP | Medium | registry hard reject + 文档/模板测试 |

## Deferred to TODOS.md

- SafetyZone / shared-device topology impact model。
- PLC/physical emergency stop integration。
- 急停运营看板、告警、SOP、MTTR 指标。
- timeout 默认 failure 独立 PR。

---

## GSTACK REVIEW REPORT

### /autoplan Phase 0 Intake

Captured: 2026-05-06 14:35 Asia/Shanghai

Restore point:

- `/Users/kaizhou/.gstack/projects/kaizhoumasha-wes_backend/workline-sandbox-runtime-flow-autoplan-restore-20260506-143551.md`

Base branch:

- `develop`

Scope detection:

- UI scope: no。计划没有新增页面、表单、布局或交互组件，只有 runtime/API 投影与文档。
- DX scope: yes。计划修改插件契约、模板、开发文档、API、错误信息和测试入口。

### Phase 1 CEO Review - Strategy & Scope

Status: `APPROVED_AFTER_PREMISE_GATE`

用户已确认：

1. 只接受工作线/设备侧上报急停事务，不控制物理急停。
2. 急停时 WorkLine 上未完成 session 全部终止并失败/取消。
3. timeout 默认 failure 从本计划拆出，单独 PR/计划处理。
4. 允许把计划重写为决策、边界、验收、风险和验证命令。

#### CEO DUAL VOICES - CONSENSUS TABLE

| Dimension | Claude Subagent | Codex | Consensus |
|---|---|---|---|
| Premises valid? | 部分有效，需要收敛软件/物理边界 | 部分有效，需要改名和确认终止语义 | Resolved by user |
| Right problem to solve? | 是，但应叫软件冻结/审计 | 是，但要确认终止 vs 暂停 | Resolved by user |
| Scope calibration correct? | timeout 应拆出 | timeout 应拆出 | Confirmed |
| Alternatives explored? | 原计划不足 | 原计划不足 | Addressed in rewrite |
| 6-month trajectory sound? | 需补远端未知、clear 权限 | 需补边界和状态语义 | Addressed in rewrite |

#### Decision Audit Trail

| # | Phase | Decision | Classification | Principle | Rationale | Rejected |
|---|---|---|---|---|---|---|
| 1 | CEO | 保留 ESTOP 平台化、pre-session gate、插件 hard reject | Mechanical | P1 completeness | 现有 session/plugin 路径不适合安全事件 | 保留插件级 ESTOP handler |
| 2 | CEO | 目标收敛为软件侧 WorkLine inhibit + incident audit | User confirmed | P5 explicit | 用户确认不控制物理急停 | 暗示物理安全闭环 |
| 3 | CEO | 急停时未完成工作全部终止并失败/取消 | User confirmed | User context | 用户明确选择终止语义 | 暂停旧流程等待恢复 |
| 4 | CEO | timeout 默认 failure 拆出独立 PR | User confirmed | P3 pragmatic | 用户确认拆出 | 与 ESTOP 同批实现 |
| 5 | CEO | active incident 增加唯一性/并发测试 | Mechanical | P1 completeness | 安全事故幂等不能只靠锁已有行 | 只用 `SELECT FOR UPDATE` |
| 6 | CEO | clear-estop 使用认证 actor、RBAC、结构化 checklist | Mechanical | P1 completeness | 恢复生产是安全敏感动作 | 请求体自报 operator |
| 7 | CEO | SENT/ACKED command 不宣称远端已物理取消 | Mechanical | P5 explicit | 本地状态不能伪造设备事实 | 统一宣称远端取消成功 |
| 8 | CEO | SafetyZone/PLC/运营看板进入 TODO，不阻塞 v1 | Taste | P3 pragmatic | 真实长期缺口，但超出当前计划 | 当前 PR 扩成完整安全平台 |
| 9 | CEO | 计划文档重写为决策/验收文档 | User confirmed | P5 explicit | 用户允许，且符合仓库规则 | 继续保留完整代码脚本 |

#### Phase 1 Completion Summary

| Area | Verdict | Notes |
|---|---|---|
| Strategy | Pass after user confirmation | 软件侧冻结边界、终止语义、timeout 拆出均已确认。 |
| Scope | Pass with deferred items | SafetyZone/PLC/运营看板进入 TODO。 |
| Architecture | Pass with changes | 入口、写副作用出口、派发出口三道 guard。 |
| Security | Pass with changes | clear-estop 必须 RBAC + 认证用户 + checklist。 |
| Data semantics | Pass with changes | 本地终止不宣称远端物理停止。 |
| Documentation | Pass after rewrite | 原完整代码脚本已重写为计划文档。 |

### Phase 3 Eng Review

Status: `COMPLETED_WITH_CHANGES`

Test plan artifact:

- `/Users/kaizhou/.gstack/projects/kaizhoumasha-wes_backend/workline-sandbox-runtime-flow-test-plan-20260506-145819.md`

#### ENG DUAL VOICES - CONSENSUS TABLE

| Dimension | Claude Subagent | Codex | Consensus |
|---|---|---|---|
| Architecture sound? | 方向符合分层，但 ESTOP resolver 必须独立 | 方向对，但 pre-session 解析边界矛盾 | CONFIRMED concern, fixed in D8 |
| Test coverage sufficient? | 缺并发、unresolved、unsafe clear、late callback | 缺 dispatcher race、late callback、freeze/drain rollback | CONFIRMED concern, test plan expanded |
| Performance risks addressed? | bulk drain 和 dispatcher N+1 需约束 | 大事务 drain 会拖慢急停 | CONFIRMED concern, fixed in D9 |
| Security threats covered? | clear 权限粒度和审计链不足 | evidence 泄露和权限过泛 | CONFIRMED concern, fixed in D5/D12 |
| Error paths handled? | unresolved 和重复 ESTOP 不够严密 | delayed callback 会覆盖 terminal | CONFIRMED concern, fixed in D6/D11 |
| Deployment risk manageable? | hard reject 前需清插件/模板 | hard reject 会打破当前 SMT/null plugin | CONFIRMED concern, migration order added |

Consensus result:

- Confirmed concerns: 6/6
- Disagreements: 0
- User challenges: 0

#### Architecture ASCII Diagram

```text
Callback API / Device Event
        |
        v
WorklineInbox NEW
        |
        v
ProcessInboxMessages._process_batch
        |
        +-- ESTOP_PRESSED?
        |       |
        |       v
        |   WorkLineSafetyResolver.resolve_from_inbox
        |       |  (inbox -> command -> device, never SessionResolver)
        |       v
        |   WorkLineSafetyService.freeze_estop
        |       |  small transaction: lock WorkLine, upsert incident, ESTOPPED
        |       v
        |   WorkLineSafetyService.drain_estop
        |       |  idempotent bulk terminalization
        |       v
        |   Incident evidence + runtime SSE + diagnostics
        |
        +-- non-ESTOP and WorkLine ESTOPPED?
        |       |
        |       v
        |   BLOCKED_BY_WORKLINE_ESTOP before session creation
        |
        +-- normal path
                |
                v
        SessionResolver -> Orchestrator -> _apply_orchestrator_effects
                                            |
                                            v
                                      assert_accepting_work
                                            |
                                            v
                                      outbox / command / external effects

OutboxDispatcher
        |
        v
claim outbox with WorkLine safety guard
        |
        +-- ESTOPPED -> local terminal, no retry, no HTTP
        |
        +-- READY -> second check -> device/external/sandbox dispatch

Command Callback
        |
        v
CallbackOrchestrationService
        |
        +-- command/session/workline terminal by ESTOP
        |       -> record late remote result evidence only
        |
        +-- normal callback
                -> update command + create command-result inbox
```

#### Section 1. Architecture

Findings applied:

- Added `safety_resolver.py` so ESTOP scope resolution does not call `SessionResolver` or create session.
- Added explicit non-ESTOP inbox blocking for ESTOPPED WorkLine before session creation.
- Added fixed dispatcher claim/safety check requirement.
- Added callback ingress guard for late remote results.

Layering decision:

- API `v1/safety.py` only handles request validation, auth/permission dependency, and response schema.
- `WorkLineSafetyService` owns business transitions and calls repositories/services.
- Repositories own bulk DB operations and locks.
- Celery runtime may call service methods but must not embed safety business rules inline beyond routing.

#### Section 2. Code Quality

Findings applied:

- The plan now names real enum constants instead of informal status words.
- The plan avoids full class/function/test code and keeps file responsibilities and acceptance criteria.
- Hidden complexity is isolated into named components: `WorkLineSafetyResolver`, two-stage freeze/drain, dispatcher claim guard, late callback guard.

#### Section 3. Test Review

Test diagram:

| Data / code path | Test type | Required coverage |
|---|---|---|
| manifest/decorator/registry hard reject | unit | ESTOP rejected in all declaration surfaces |
| ESTOP scope resolver | unit | inbox, command, device paths; no SessionResolver call |
| unresolved ESTOP | unit/integration | diagnostic, unique source inbox, no infinite retry |
| freeze small transaction | service | WorkLine remains ESTOPPED even if drain fails |
| drain bulk terminalization | service | session/outbox/command/device status and evidence |
| duplicate concurrent ESTOP | concurrency | one ACTIVE incident |
| dispatcher race | concurrency | no HTTP call after ESTOP wins race |
| orchestrator write guard | unit | command/external effect blocked before creation |
| late command callback | service | terminal state not overwritten |
| clear-estop API | API | 401/403, dedicated permission, unsafe device rejected |
| incident query | API | summary redaction, detail permission |
| plugin cleanup | unit/integration | SMT/null/template no ESTOP handler before hard reject |
| E2E | integration | ESTOP -> freeze/drain -> block dispatch -> clear -> old work terminal |

#### Section 4. Performance

Requirements added:

- Freeze is a small transaction and commits before drain.
- Drain uses bulk repository updates where possible.
- Dispatcher avoids N+1 WorkLine lookups by resolving WorkLine safety projection during claim or via join/preload.
- Incident indexes include active partial unique and source inbox uniqueness.

#### Failure Modes Registry - Eng Additions

| ID | Failure Mode | Severity | Mitigation |
|---|---|---|---|
| ENG-FM-001 | ESTOP resolver accidentally creates session | Critical | Dedicated safety resolver and monkeypatch test |
| ENG-FM-002 | Dispatcher crosses safety check then sends HTTP during ESTOP race | Critical | Claim guard, second check, barrier test |
| ENG-FM-003 | Late command callback overwrites ESTOP terminal state | Critical | Callback terminal guard and evidence-only path |
| ENG-FM-004 | Drain failure rolls back WorkLine freeze | High | Two-phase freeze/drain |
| ENG-FM-005 | Unresolved ESTOP duplicates because `workline_id` is NULL | High | source inbox uniqueness and unresolved status |
| ENG-FM-006 | hard reject breaks startup before cleanup | Medium | cleanup plugin/template before enabling registry hard reject |

#### Phase 3 Completion Summary

| Area | Verdict | Notes |
|---|---|---|
| Architecture | Pass with required changes | Changes have been folded into D8-D12 and tasks. |
| Tests | Pass with expanded plan | Test plan artifact written. |
| Performance | Pass with constraints | Two-phase freeze/drain avoids rollback and long lock. |
| Security | Pass with changes | Dedicated permission, auth actor, evidence redaction. |
| Deployment | Pass with sequencing | Cleanup before hard reject. |

### Phase 3.5 DX Review

Status: `COMPLETED_WITH_CHANGES`

#### Developer Journey Map

| Stage | Persona | Current plan support | Required improvement |
|---|---|---|---|
| Discover reserved ESTOP rule | Plugin developer | Rule exists | Standard error format and migration guide added |
| Remove legacy handler | Plugin developer | Task 7 cleanup | before/after and scan command added |
| Verify plugin still loads | Plugin developer | tests listed | registry hard reject test and template test required |
| Trigger ESTOP in dev | Backend implementer | E2E listed | API/error examples added |
| Debug unresolved ESTOP | Ops/integration | diagnostic mentioned | runbook fields and `next_action` added |
| Clear ESTOP | Ops user/API consumer | permission mentioned | API contract and recovery schema added |
| Inspect incident | Ops/API consumer | incident API mentioned | list/detail field contract and redaction added |
| Handle late callback | Backend implementer | Eng added guard | test path added |
| Ship safely | Maintainer | full verification listed | migration/permission seed checks added |

TTHW assessment:

- Plugin developer after changes: target 10-15 min to understand and remove ESTOP declarations, 30 min to run migration checks.
- Backend implementer after changes: 2-4 days for full path, because concurrency and callback guards remain non-trivial.
- Ops/integration developer after changes: 30-60 min to understand clear-estop and unresolved incident actions.

#### DX DUAL VOICES - CONSENSUS TABLE

| Dimension | Claude Subagent | Codex | Consensus |
|---|---|---|---|
| Getting started < 5 min? | No; plugin migration needs clearer path | No; plugin hello world too heavy | CONFIRMED concern |
| API naming guessable? | Not enough routes/request fields | API contract missing | CONFIRMED concern, fixed |
| Error messages actionable? | hard reject too vague | error code ownership unclear | CONFIRMED concern, fixed |
| Docs findable & complete? | Missing unresolved runbook and examples | Missing role-specific docs | CONFIRMED concern, fixed |
| Upgrade path safe? | Need migration scan before hard reject | Need scan/before-after/CI check | CONFIRMED concern, fixed |
| Dev environment friction-free? | Needs minimal hello world | Current flow too heavy | CONFIRMED concern, added to Task 7 |

#### DX Scorecard

| Dimension | Before | After plan updates | Notes |
|---|---:|---:|---|
| Getting started | 5/10 | 7/10 | Hello world requirement added, implementation still needed. |
| API/permission ergonomics | 4/10 | 8/10 | Paths, permissions, request/response fields fixed. |
| Error actionability | 5/10 | 8/10 | Error ownership matrix and examples added. |
| Documentation | 6/10 | 8/10 | Migration guide, SOP, API contract required. |
| Upgrade/migration | 4/10 | 8/10 | scan command and before/after required. |
| Dev tooling | 5/10 | 7/10 | pytest commands exist; hello world still a deliverable. |
| Observability/debugging | 6/10 | 8/10 | unresolved fields and next action added. |
| Feedback loops | 5/10 | 7/10 | verification commands clear, no live docs automation yet. |

Overall DX: 7.6/10 after plan updates.

#### DX Implementation Checklist

- Add standard `RESERVED_RUNTIME_EVENT_DECLARED` error payload.
- Add API contract docs to `docs/plugin_development_guide.md` or dedicated backend API section.
- Add `docs/workline_safety_incident_sop.md`.
- Add plugin migration before/after and scan command.
- Add minimum recovery checklist schema and rejection response.
- Add incident list/detail redaction contract and tests.
- Add unresolved incident runbook and response fields.
- Add minimal plugin hello world path.

#### Phase 3.5 Completion Summary

| Area | Verdict | Notes |
|---|---|---|
| API DX | Pass with changes | API Contract section added. |
| Error DX | Pass with changes | Ownership matrix and examples added. |
| Plugin migration | Pass with changes | scan command, standard error, before/after requirement added. |
| Ops runbook | Pass with changes | unresolved and clear-estop response contracts added. |
| Docs | Pass with required deliverables | new SOP and hello world are explicit tasks. |

### Cross-Phase Themes

| Theme | Phases | Resolution |
|---|---|---|
| 软件冻结边界必须明确，不控制物理急停 | CEO, Eng, DX | Title/goal/non-goal/API docs now state software-side only. |
| 不允许安全事件进入 plugin/session 普通流 | CEO, Eng | ESTOP pre-session gate and safety resolver added. |
| 并发和外部副作用是主要风险 | Eng, DX | atomic dispatcher claim, two-stage freeze/drain, concurrency tests added. |
| 恢复动作必须可审计、可拒绝、可排查 | CEO, Eng, DX | dedicated permission, auth actor, checklist schema, rejected response added. |
| 开发者需要迁移路径而不是只看到 hard reject | Eng, DX | error payload, scan command, before/after migration guide added. |

### Final Approval Gate

Status: `APPROVED_BY_AUTOPLAN_RECOMMENDATION`

Decisions made:

- Auto/mechanical decisions: 16
- User-confirmed decisions: 4
- Taste decisions surfaced: 1
- Remaining user challenges: 0

Taste decision:

- SafetyZone/PLC/运营看板是否进入当前 PR。Recommendation: defer。理由：它是真实长期方向，但会把当前软件侧急停冻结扩成跨硬件/运营平台，超出本计划。

Auto-decided scope:

- Keep ESTOP platform-reserved.
- Split timeout default failure out.
- Add safety resolver.
- Add two-stage freeze/drain.
- Add dispatcher atomic claim.
- Add late callback guard.
- Add clear-estop dedicated permission.
- Add API contract and error ownership matrix.
- Add migration/runbook/plugin DX deliverables.

Deferred to TODOS.md:

- SafetyZone / shared-device topology impact model。
- PLC/physical emergency stop integration。
- 急停运营看板、告警、SOP、MTTR 指标。
- timeout 默认 failure 独立 PR。

Recommended next step:

- 使用本计划进入实现阶段；实现时从 Task 1/2/4/5 的 safety boundary tests 开始，再实现 service/API。
