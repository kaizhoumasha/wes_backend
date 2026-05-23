<!-- /autoplan restore point: /Users/kaizhou/.gstack/projects/kaizhoumasha-wes_backend/workline-sandbox-runtime-flow-autoplan-restore-20260509-161045.md -->

> Legacy notes: 本规格中提到的旧插件 builder 仅用于说明当时输入来源；当前实现以 `RuntimeIntent` 为插件输出合同。

# Runtime Hold 与 NG 物料处置设计

## 背景

当前 Sandbox 已能展示失败命令和运行时对账状态，但仍有三个问题：

1. 拓扑设备节点上的计数语义不清，已终态命令可能被用户理解为仍有待处理任务。
2. `FAILED` 命令只显示状态，不足以解释失败原因，也缺少进入人工恢复流程的清晰入口。
3. 对账只覆盖工作线、设备、指令状态，缺少实体物料处置指导，尤其无法证明 NG 物料已经离开正常物理流。

本设计将 runtime reconciliation 收敛为统一的 Runtime Hold 异常恢复工作流。`RuntimeHold` 是权威事实源，覆盖 runtime reconciliation、设备执行未知、通信 ACK 耗尽以及后续可合并的安全冻结；Reconciliation 只是 Hold 的 `hold_type` 或视图标签，不成为第三套异常事实源。Sandbox、拓扑和顶部卡片只提供摘要和入口；完整处理在独立 Runtime Hold 页面完成，并为 PDA 扫码确认保留一等路径。

## 目标

- 拓扑主视图的数字只表示设备当前未完成设备命令。
- `FAILED` 命令不计入未完成任务，但设备和页面必须显示异常 Hold 标记。
- 提供独立 Runtime Hold 页面，支持桌面端证据复核和 PDA 端现场扫码确认；页面以 Hold 为权威状态边界。
- 对账必须覆盖实体物料处置，第一版支持 `CONTINUE` 与 `RETURN_TO_NG`。
- `RETURN_TO_NG` 创建按物料/原 session 追踪的 NG 记录，且释放 WorkLine 前必须记录服务端可校验的物理交接证据。
- NG 原因进入统一 reason taxonomy，插件原因通过映射加入，不新增孤立口径。
- 成功指标：停线恢复耗时下降、误转 NG 下降、人工二次确认时间下降、重复异常可按 Hold reason / NG reason / 设备聚合统计。

## 非目标

- 第一版不自动伪造入口 EVENT。
- 第一版不做完整工单系统、角色派工、NG 周转箱生命周期追踪。
- 第一版不扩展 session 终态，`RETURN_TO_NG` 对应当前 session `FAILED`。
- 第一版不在拓扑或 Sandbox 列表内直接执行解除隔离。
- 第一版不保留独立 `RuntimeReconciliationCase` 权威对象；可保留“Reconciliation”作为 Hold 类型标签和页面文案副标题。
- 第一版不做完整原生/离线 PDA 应用；但 Web 页面必须提供扫码优先、触控可用的 PDA 布局。

## 第一性原则

- 生产线能否恢复派发，只能由当前所有 blocking Hold 的状态决定，不能由某个 session 或事故服务直接把 `WorkLine.runtime_status` 写回 `READY`。
- 数据库记录不等于物理交接。`RETURN_TO_NG` release 必须包含位置/扫码/清线/迟到 callback 复核等现场证据。
- 物料唯一性不能来自展示字段。`PkgID`、`HHPN`、`LotCode` 只能是候选展示字段，幂等键必须由插件 `MaterialIdentityResolver` 解析。
- GET 不产生写入。所有 Runtime Hold 在进入异常状态的事务中创建，历史数据通过显式 repair job 补齐。
- 前端不推断释放资格。`GET hold` 返回服务端计算的 `release_eligibility`，POST 失败也返回同一结构。

## 领域模型

### RuntimeHold

实现级 ADR：第一版新增持久化表 `wes_biz.runtime_holds` 与模型 `RuntimeHold`，继承 `EnterpriseMixin`，作为所有运行时异常恢复的权威事实源。`WorklineSession.reconciliation_*`、`WorklineSafetyIncident` 和 `WorkLine.runtime_status` 在过渡期只能作为来源事实或投影，不再拥有 WorkLine release 语义。

核心字段：

- `id`
- `hold_type`: `RUNTIME_RECONCILIATION`、`SAFETY_ESTOP`、`MANUAL_HOLD`
- `status`: `OPEN`、`IN_PROGRESS`、`RESOLVED`、`VOIDED`、`REOPENED`
- `blocking`: 是否阻塞 WorkLine 派发
- `workline_id`
- `session_id`
- `trace_id`
- `plugin_key`
- `contract_version`
- `source_kind`
- `source_reason`
- `source_idempotency_key`
- `source_inbox_id`
- `source_outbox_id`
- `source_command_id`
- `source_device_id`
- `evidence_snapshot_json`
- `release_evidence_json`
- `material_disposition`
- `ng_reason_source`
- `ng_reason_code`
- `ng_reason_label`
- `resolved_at`
- `resolved_by`
- `voided_at`
- `voided_by`
- `reopened_from_hold_id`

约束与查询规则：

- `source_idempotency_key` 全局唯一，用于 timeout scanner、dispatch ACK exhausted、repair job 重复运行幂等。
- active Hold 定义为 `status in ('OPEN', 'IN_PROGRESS', 'REOPENED') and blocking=true`。
- 同一 `workline_id` 可同时存在多个 active Hold，但 WorkLine release 必须等所有 active blocking Hold 都解除。
- `RuntimeHold` 的 `version` 来自 `EnterpriseMixin` 乐观锁；POST resolve 必须携带 `hold_version`。
- GET resolver 不创建 Hold。历史 `WorklineSession.reconciliation_state=PENDING` 必须由 `scripts/data/repair_runtime_holds.py --dry-run/--apply --limit` 补齐。

### RuntimeHoldReleaseService

新增 `RuntimeHoldReleaseService`，作为唯一能把 WorkLine 写回可派发状态、并释放被停靠 Outbox 的服务。

释放规则：

```text
POST resolve RuntimeHold
  -> lock RuntimeHold
  -> lock WorkLine
  -> lock related WorklineSession / DeviceCommand / Outbox rows
  -> validate hold_version and evidence_revision
  -> write resolution facts / NgReturnItem if needed
  -> recompute active blocking holds for workline
  -> if none remain: WorkLine.runtime_status = READY and release blocked outbox
  -> if any remain: keep WorkLine in ESTOPPED or RECONCILING
```

现有 `WorklineRuntimeReconciliationService`、`WorklineSafetyService.clear_estop` 不再直接设置 `WorkLine.runtime_status = READY`。`workline_outbox` 的 blocked ownership 从 `blocked_by_reconciliation_session_id` 迁移到 `blocked_by_runtime_hold_id`；旧字段在迁移窗口内只读或 backfill 后删除。

### RuntimeReconciliation

Runtime Reconciliation 是 `RuntimeHold.hold_type=RUNTIME_RECONCILIATION` 的读模型和 UI 标签。它展示 session、command、outbox、diagnostic、timeline 证据，但不拥有独立状态机、版本号或释放语义。

### FailedCommandEvidence

失败命令是 Hold 的证据，不是人工操作本体。它应展示：

- command code/status/error detail
- outbox status/last error
- device code/status
- diagnostic card
- timeline 关键节点

### MaterialDisposition

对账时必须选择物料处置：

- `CONTINUE`：现场确认物料可以继续当前流程。
- `RETURN_TO_NG`：物料退出正常流程，进入 NG 暂存/待重做队列。

`RETURN_TO_NG` 是去向，不是原因，也不表示立刻回入口。

### MaterialIdentity

插件契约新增 `MaterialIdentityResolver`。输入来自 session、source event/result、command payload、现场扫码证据和插件 context；输出为结构化 `MaterialIdentity`：

- `idempotency_key`: 插件生成的物料幂等键。
- `business_key`: 插件业务键，可与 session ownership 对齐。
- `display`: 只读展示字段，如 `PkgID`、`HHPN`、`LotCode`。
- `raw_evidence_hash`: 参与解析的扫码/事件/结果证据摘要。
- `resolution_status`: `RESOLVED`、`AMBIGUOUS`、`MISSING`。

`RETURN_TO_NG` release 要求 `resolution_status=RESOLVED`。客户端不能提交最终 `material_identity`，只能提交扫描原文、NG 位置和现场检查；后端在同一事务中调用插件 resolver 并写入解析结果。

### PhysicalHandoffEvidence

`RETURN_TO_NG` 释放 WorkLine 前必须记录物理交接证据。第一版客户端提交：

- `ng_location_code`：NG 暂存位、NG 平台、容器或库位编码，必须通过后端主数据/白名单校验。
- `ng_location_scan`：PDA/扫码枪读取的位置原文。
- `material_scan_payload`：现场重新扫描到的物料原文。
- `line_clear_checked=true`：已确认设备/工位没有残留同一物料。
- `late_callback_reviewed=true`：若存在迟到 callback evidence，必须重新确认是否仍转 NG。
- `handoff_witness_id`：可选见证人，不作为确认人事实源。

服务端写入：

- `handoff_confirmed_by`：从 `require_auth` 的当前用户写入。
- `handoff_confirmed_at`：服务端当前时间。
- `material_identity`：由插件 `MaterialIdentityResolver` 解析。
- `evidence_hash`：用于并发与审计的证据摘要。

缺少这些证据时，后端拒绝 `RETURN_TO_NG` release；Hold 保持 open，WorkLine 不恢复派发。

### NgReturnItem

当物料处置为 `RETURN_TO_NG` 且物理交接证据通过校验时创建一条 NG 记录，粒度为单物料/原 session。后续多个 NG item 可组成返入口批次，由入口设备发送新的真实 EVENT 开启新 session，并关联原 NG 记录。

第一版字段：

- source workline/session/command/event
- material identity，由插件 `MaterialIdentity` 契约提供唯一键、展示字段和幂等键
- `disposition=RETURN_TO_NG`
- `ng_reason_source`
- `ng_reason_code`
- `ng_reason_label`
- physical handoff evidence
- operator note
- `created_from_runtime_hold_id`
- `status=WAITING_REWORK`

约束：

- `unique(created_from_runtime_hold_id, material_identity.idempotency_key)`。
- 相同 Hold 重复提交相同物料返回幂等成功或明确 `409`，不得创建第二条 NG item。
- 不同 Hold 解析出相同 `material_identity.idempotency_key` 时必须返回冲突模型，提示已有 NG item / active session。

## NG 原因目录

NG 原因由统一 reason taxonomy 管理，插件定义为主，系统内置兜底原因为辅。业务 NG、设备失败、运行时未知、人工判断必须映射到同一套可统计分类，避免插件私有 code 造成报表漂移。

新增 canonical taxonomy 数据结构：

- `canonical_code`
- `label`
- `source`: `PLUGIN`、`DEVICE_ERROR`、`RUNTIME`、`MANUAL`
- `plugin_key`
- `contract_version`
- `maps_from`
- `deprecated`

插件契约增加 `ng_reason_catalog`，但它必须复用或映射插件已有 `business_decision(reason_code, message, evidence, business_key)` 语义。例如 SMT 粗分机插件第一版直接映射现有 code：

- `SCAN_NG`
- `SCAN_NG_BY_RULE`
- `INSPECTION_SIZE_NG`
- `INSPECTION_THICKNESS_NG`
- `BARCODE_INVALID`
- `BARCODE_INCOMPLETE`

系统内置兜底原因：

- `UNKNOWN_PHYSICAL_STATE`：设备动作状态未知。
- `OPERATOR_JUDGED_NG`：现场人工判定 NG。
- `RUNTIME_RECOVERY_NG`：运行时异常恢复导致转 NG。

Runtime Hold 页面规则：

- 选择 `RETURN_TO_NG` 时，`ng_reason_code` 必填。
- 优先展示插件原因，系统兜底原因单独分组展示。
- 若对账原因为 ACK 超时，系统只提示“设备动作状态未知”，不默认把物料判定为 NG；选择 `RETURN_TO_NG` 时才建议 `UNKNOWN_PHYSICAL_STATE`，并要求物理交接证据。
- 系统可根据失败证据建议 NG 原因，但现场人员可以改成更准确的插件原因；所有建议和覆盖都进入 Hold 审计。
- 迁移前必须输出未映射 reason 报告，禁止静默落到 `UNKNOWN`。

## 页面职责

### 拓扑主视图

拓扑设备节点显示四个概念：

- `open_command_count_by_device`：设备当前未完成命令数，只包含 `DeviceCommand.PENDING`、`SENT`、`ACK_RECEIVED`。
- `blocked_outbox_count_by_device`：因 WorkLine/Hold 被停靠的 `WorklineOutbox.BLOCKED_RESOURCE` 数量。
- `open_task_count_by_device`：兼容旧字段时等于 open command，不包含 blocked outbox。
- `open_issue_count_by_device`：设备当前待处理异常数，只来自 open Runtime Hold / active incident。

数字徽标只显示未完成命令。异常用状态标记表达。点击设备后，设备面板按三组展示：

- 未完成命令
- 异常 Runtime Hold
- 历史命令

### Sandbox 命令列表

- `COMPLETED`、`FAILED` 命令可作为历史显示，但不算待操作。
- `FAILED` 行显示失败摘要与“查看 Runtime Hold”入口。
- 不在命令行内直接执行解除隔离。

### 顶部运行时对账卡片

顶部卡片是决策入口，不承载完整表单。必须显示：

- active Hold 数量
- 最严重 Hold / 当前 blocker
- 停线原因与影响设备/命令
- release requirements 已满足/缺失数量
- 物理交接证据是否已记录
- 主按钮：进入 Runtime Hold

### Runtime Hold 页面

独立前端路由：

```text
/runtime/holds/:holdId
```

页面使用 `Runtime Hold` 作为可见主名词；`Reconciliation` 仅作为类型 tag。页面第一屏先回答“能不能恢复线体”，再展示证据：

1. 固定顶部决策条：Hold 状态、release eligibility、主要 blocker、版本/证据是否最新、主操作按钮。
2. Release checklist：服务端返回的必填检查、缺失证据和合法 outcome。
3. 物料处置：`CONTINUE` 或 `RETURN_TO_NG`，后者扫码优先收集 NG 位置和物料证据。
4. 失败原因：人话解释与技术证据。
5. Timeline / diagnostic / command / outbox 证据。
6. 审计与 reopen/correction 历史。

桌面端主用于 supervisor 证据复核，PDA 端主用于现场扫码确认：

- 桌面端：双栏布局，左侧决策与表单，右侧证据和 timeline。
- PDA 端：单列、44px 以上触控目标、扫码输入优先、主要按钮固定底部。
- PDA `RETURN_TO_NG` 必须先扫 NG 位置，再扫物料；文本手输只作为权限控制下的 fallback 并进入审计。

必须覆盖 UI 状态：

- loading
- empty / Hold 不存在
- permission denied
- stale version / evidence changed
- Hold already resolved
- missing material identity
- ambiguous material identity
- reason catalog loading/error/empty
- late callback invalidates current decision
- network submit failure with retry

可访问性要求：

- 颜色不能是唯一状态信号。
- 所有表单项和错误有 label / described-by。
- 键盘可达，focus 状态明确。
- PDA 触控目标不小于 44px。
- 文案使用短句，不用长段解释替代状态结构。

## 提交流程与防呆

### CONTINUE

- 必须完成 checklist。
- 必须填写 operator note。
- 允许选择 session 结论：`COMPLETED`、`FAILED`、`CANCELLED`。
- 不创建 NG item。
- 必须通过合法组合矩阵校验：material disposition、session outcome、command outcome、WorkLine release 不得互相矛盾。

### RETURN_TO_NG

- 必须完成 checklist。
- 必须选择 `ng_reason_code`。
- 必须提交 `physical_handoff_evidence` 的位置与扫码原文。
- 必须填写 operator note。
- session 结论固定为 `FAILED`。
- 服务端解析 MaterialIdentity 并创建 `NgReturnItem(status=WAITING_REWORK)`。
- 仅在物理交接证据完整、Hold version 未过期、证据 revision 未变化、线体清空检查通过后释放 WorkLine 隔离。
- 不自动发入口 EVENT。

### 防呆规则

- 若存在迟到 callback evidence，页面必须突出提示，并要求重新确认物料是否仍应进入 NG。
- 提交前后端重新校验 Hold/session/command/outbox 版本，避免状态已变化但页面仍提交旧结论。
- 若 WorkLine 已被其他人解除隔离，提交返回 `409` 并带最新 Hold 决策模型。
- 所有 resolve 都写入 actor、server confirmed time、checks、material disposition、NG reason、operator note、physical handoff evidence。
- 支持 `OPEN`、`IN_PROGRESS`、`RESOLVED`、`VOIDED`、`REOPENED` 生命周期；误提交通过 correction/reopen 审计处理，不直接覆盖历史事实。

## 后端接口

第一版接口以 Runtime Hold 为边界：

- `GET /api/v1/workline/runtime-holds/{hold_id}`
- `POST /api/v1/workline/runtime-holds/{hold_id}/resolve`
- `GET /api/v1/workline/runtime-holds/ng-reasons?plugin_key=&contract_version=`
- `GET /api/v1/workline/ng-return-items?runtime_hold_id=&status=&material_idempotency_key=`

权限：

- `biz:workline:view-runtime-hold`
- `biz:workline:resolve-runtime-hold`
- `biz:workline:list-ng-return-item`

`GET RuntimeHoldDetailResponse` 必须包含：

- `hold`
- `source`
- `material_identity`
- `failed_command_evidence`
- `release_eligibility`
- `allowed_actions`
- `required_checks`
- `blockers`
- `reason_suggestions`
- `latest_evidence_hash`
- `hold_version`
- `refresh_url`

`release_eligibility` 示例：

```json
{
  "can_resolve": false,
  "can_release_workline": false,
  "legal_outcomes": ["RETURN_TO_NG", "CONTINUE"],
  "required_checks": ["device_reachable_checked", "physical_state_confirmed"],
  "missing_evidence": ["material_scan_payload", "ng_location_code"],
  "blockers": [
    {
      "code": "MISSING_PHYSICAL_HANDOFF",
      "message": "RETURN_TO_NG 必须先记录 NG 位置和物料扫码证据"
    }
  ],
  "hold_version": 3
}
```

提交 payload：

```json
{
  "resolution": "FAILED",
  "checks": {
    "device_reachable_checked": true,
    "command_code_checked": true,
    "physical_state_confirmed": true
  },
  "operator_note": "现场确认物料进入 NG 暂存",
  "material_disposition": "RETURN_TO_NG",
  "ng_reason": {
    "source": "RUNTIME",
    "code": "UNKNOWN_PHYSICAL_STATE",
    "label": "设备动作状态未知"
  },
  "physical_handoff_evidence": {
    "ng_location_code": "NG_PLATFORM_01",
    "ng_location_scan": "NG_PLATFORM_01",
    "material_scan_payload": "PKG-001|HHPN-001|LOT-001",
    "line_clear_checked": true,
    "late_callback_reviewed": true,
    "handoff_witness_id": "operator-002"
  },
  "hold_version": 3,
  "latest_evidence_hash": "sha256:..."
}
```

服务端响应中的 `physical_handoff_evidence` 才包含 `handoff_confirmed_by`、`handoff_confirmed_at`、`material_identity` 和 `evidence_hash`。

错误合同：

- stale version 使用 HTTP `409`，错误码 `RUNTIME_HOLD_VERSION_CONFLICT`。
- evidence changed 使用 HTTP `409`，错误码 `RUNTIME_HOLD_EVIDENCE_CHANGED`。
- 缺证据使用 HTTP `422`，错误码 `RUNTIME_HOLD_MISSING_RELEASE_EVIDENCE`。
- 已 resolved 使用 HTTP `409`，错误码 `RUNTIME_HOLD_ALREADY_RESOLVED`。
- reason 未映射使用 HTTP `422`，错误码 `RUNTIME_HOLD_REASON_UNMAPPED`。

`409` 响应必须返回最新决策模型：

```json
{
  "code": "RUNTIME_HOLD_VERSION_CONFLICT",
  "message": "Runtime Hold 已被更新，请刷新后重试",
  "data": {
    "current_hold_version": 4,
    "current_status": "IN_PROGRESS",
    "blockers": [],
    "release_eligibility": {
      "can_resolve": true,
      "can_release_workline": false
    },
    "refresh_url": "/api/v1/workline/runtime-holds/123"
  }
}
```

OpenAPI / 前端生成要求：

- 每个接口必须有 response model、权限 summary、成功示例和失败示例。
- operationId 由路径生成，目标前端类型名应稳定为 `workline_runtime_holds_by_hold_id_get`、`workline_runtime_holds_by_hold_id_resolve_post` 等 `runtime_holds` 口径。
- 后端 schema 改动后必须执行前端 `pnpm contract:verify`，并按现有生成脚本更新类型/验证器。

## 迁移与 repair runbook

- 使用 Alembic revision generator 创建迁移。
- 第一步 nullable-first 新增 `runtime_holds`、`ng_return_items`、`blocked_by_runtime_hold_id`。
- 第二步业务代码双写 RuntimeHold，并让 release 通过 `RuntimeHoldReleaseService`。
- 第三步运行 repair job 补齐历史 pending session reconciliation：

```bash
uv run python scripts/data/repair_runtime_holds.py --dry-run --limit 100
uv run python scripts/data/repair_runtime_holds.py --apply --limit 100
```

repair job 要求：

- dry-run 输出待创建 Hold 数、重复 source idempotency key、未映射 reason、缺 material identity 的数量。
- apply 幂等；重复运行不创建重复 Hold。
- 每批限制行数并按 `source_idempotency_key` 加锁或使用数据库唯一约束处理并发。
- 提供前后 invariant SQL：active reconciliation session 数量必须等于 active runtime hold 数量。
- 全量修复后再删除或废弃旧 `blocked_by_reconciliation_session_id` 写路径。

## Hello World 验证路径

目标 TTHW：已有本地环境下 5 分钟内完成一次 ACK timeout 到 `RETURN_TO_NG` 的闭环。

最小步骤：

1. `uv run uvicorn main:app --reload --host 0.0.0.0 --port 8001`
2. 使用 fixture 触发 ACK timeout / dispatch exhausted。
3. `GET /api/v1/workline/runtime-holds/{hold_id}` 查看 release eligibility。
4. 第一次 POST 缺少 physical handoff evidence，预期 `422`。
5. 第二次 POST 补齐 NG 位置和物料扫码，预期 resolve 成功。
6. `GET /api/v1/workline/ng-return-items?runtime_hold_id={hold_id}` 查询 NG item。

计划落地时必须补充一份 copy-paste 完整 quickstart，包括登录 token 获取、curl、预期响应和常见 `409`/`422` 修复说明。

## 测试策略

后端测试：

- 拓扑计数不把 `COMPLETED`、`FAILED` 命令计入 `open_command_count_by_device`。
- `BLOCKED_RESOURCE` 只计入 `blocked_outbox_count_by_device`，不进入 command count。
- pending reconciliation 能产生 `open_issue_count_by_device`。
- RuntimeHold 在进入异常状态的事务中创建，GET resolver 不创建 Hold。
- repair job dry-run/apply 幂等，重复运行不创建重复 Hold。
- `RETURN_TO_NG` resolve 固定 session 为 `FAILED` 并创建 `NgReturnItem`。
- 缺失 `physical_handoff_evidence` 时拒绝 `RETURN_TO_NG`，且不释放 WorkLine。
- 客户端伪造 `handoff_confirmed_by` / `handoff_confirmed_at` / `material_identity` 被忽略或拒绝。
- `CONTINUE` resolve 不创建 NG item。
- 缺失 `ng_reason_code` 时拒绝 `RETURN_TO_NG`。
- 未映射 reason 拒绝提交并输出可修复错误。
- Hold version 或 evidence hash 过期时返回 `409` 和最新决策模型。
- ESTOP + runtime reconciliation 同时存在时，解决其中一个 Hold 不会把 WorkLine 写回 READY。
- late callback 到达后旧页面提交失败。
- 双人重复提交同一 Hold 只创建一条 NG item。
- 物料 identity 解析失败/歧义时拒绝 release。
- `RuntimeHoldReleaseService` 是唯一释放 blocked outbox 的路径。

前端测试：

- 拓扑数字与异常标记分离展示。
- `FAILED` 命令行显示失败摘要和 Runtime Hold 入口，不显示 ACK/Result 操作。
- 顶部对账卡片只显示摘要、blocker、requirements 和入口。
- Runtime Hold 页面优先显示 release eligibility 与缺失证据。
- `RETURN_TO_NG` PDA 流程要求先扫 NG 位置再扫物料。
- 迟到 callback evidence 出现时阻止无确认提交。
- `409` stale version 展示刷新动作并使用返回的最新模型更新页面。
- reason catalog 空/失败时阻止提交，不落到任意字符串。
- 权限不足时只读证据，不显示 resolve CTA。

集成验证：

- 构造 ACK 超时命令，确认拓扑不再把该命令计入未完成命令，但显示 Runtime Hold 标记。
- 进入 Runtime Hold，选择 `RETURN_TO_NG`，缺少物理交接证据时提交失败且 WorkLine 保持隔离；补齐证据后提交成功、若无其他 active blocking Hold 则 WorkLine 解除隔离，NG item 可在队列中查询。
- 同时存在 safety hold 与 reconciliation hold 时，解决 runtime hold 后 WorkLine 仍保持 ESTOPPED。
- NG item 后续批量返入口时由入口真实 EVENT 创建新 session，并保留关联关系。

## GSTACK REVIEW REPORT

### /autoplan Phase 0 Intake

- Base branch: `develop`
- Plan file: `docs/superpowers/specs/2026-05-09-runtime-reconciliation-case-design.md`
- Restore point: `/Users/kaizhou/.gstack/projects/kaizhoumasha-wes_backend/workline-sandbox-runtime-flow-autoplan-restore-20260509-161045.md`
- UI scope: yes. The original plan defined topology, Sandbox list, top card and independent Case page behavior; after premise gate it is now a Runtime Hold page.
- DX scope: yes. The plan changes backend APIs, plugin contract, generated frontend contract and implementation-facing docs/tests.
- Design doc handling: the user-provided plan is the active design doc. No separate `~/.gstack` design artifact was found.
- Codex availability: available, `codex-cli 0.129.0`. CEO voice completed after transient websocket reconnect/certificate errors.
- GitNexus: configured but stale by 5 commits. `npx gitnexus analyze` failed locally with `Cannot destructure property 'package' of 'node.target' as it is null`; review used existing index plus direct source reading.

### Phase 1 CEO Review - Step 0

#### 0A. Premise Challenge

| Premise | Evaluation | Risk If Wrong | Recommendation |
|---|---|---|---|
| Runtime reconciliation should become an independent Case workflow. | Not yet proven. Both outside voices challenged this as a product-shape jump from "state explanation" to "small work-order system". | WES gains a third abnormal-state fact source beside `WorklineSafetyIncident` and session reconciliation. Operators see more screens, but the recovery decision may not improve. | Confirm whether the real target is "Case page" or "unified abnormal recovery control plane". |
| `RETURN_TO_NG` can release WorkLine isolation after creating `NgReturnItem`. | Unsafe as currently written. The plan excludes NG box/location tracking, but release is a production safety action. | A material may remain in the normal physical flow while the system records it as waiting rework and resumes downstream dispatch. | Require physical isolation evidence before release: NG location/container, operator confirmation, device clear or equivalent site proof. |
| Material identity can be captured from fields like `PkgID`, `HHPN`, `LotCode`. | Too weak. Existing code separates display identity from session ownership. `resolve_payload_display_identity()` is explicitly display-only, while session ownership uses plugin business key resolution and reuse rules. | NG records cannot be de-duplicated, batched, or reworked reliably. | Add a plugin-owned `MaterialIdentity` contract before `NgReturnItem` becomes authoritative. |
| Plugin-defined `ng_reason_catalog` is the right source of NG reasons. | Partially right, but incomplete. Plugins already emit `business_decision(reason_code, message, evidence, business_key)` for business NG. A new catalog can duplicate reason systems. | Reporting splits across business NG, runtime NG, manual NG and device failure taxonomies. | Create one reason taxonomy surface and derive plugin NG reasons from existing decision/failure semantics where possible. |
| First read can lazily create Case records from pending session reconciliation. | Wrong boundary. GET with write side effects breaks audit, versioning and concurrent desktop/PDA reads. | Multiple readers race, case creation time is not the incident time, and idempotency moves into a read path. | Create Case/Hold/Incident in the same transaction that enters `reconciliation_state=PENDING`; use repair job for legacy rows. |

#### 0B. Existing Code Leverage

| Sub-problem | Existing Code | Leverage Decision |
|---|---|---|
| WorkLine isolation state | `WorkLineRuntimeStatus.READY/RECONCILING/ESTOPPED` and `WorklineSafetyIncident` in `src/app/workline/models/safety.py` | Reuse as the abnormal-control foundation; avoid a parallel Case fact source unless it is a view/projection. |
| Runtime reconciliation entry/resolve | `WorklineRuntimeReconciliationService` in `src/app/workline/services/runtime_reconciliation_service.py` | Reuse as current system-level reconciliation coordinator; extend transaction boundaries rather than create Cases on read. |
| Trace/evidence aggregation | `trace_response_builder.py`, runtime trace models and diagnostics | Reuse for evidence cards and Case/Hold projection. Do not duplicate FailedCommandEvidence storage unless evidence needs immutable snapshots. |
| Device open command count | `_PENDING_COMMAND_STATUSES = {"PENDING", "SENT", "ACK_RECEIVED"}` in `runtime_query_service.py` | Rename semantics to `open_task_count`; include/exclude `BLOCKED_RESOURCE` from outbox separately instead of conflating command count and issue count. |
| Business NG reason facts | `PluginResultBuilder.business_decision()` and SMT plugin context fields | Reuse existing business decision/event taxonomy; avoid a second plugin-only `ng_reason_catalog` without mapping. |
| Material display identity | `resolve_payload_display_identity()` | Use only for display. It is not sufficient as NG item identity or idempotency key. |

#### 0C. Dream State Mapping

```text
CURRENT STATE
  Runtime reconciliation is session-centered. WorkLine can be RECONCILING,
  evidence lives in trace/diagnostic/timeline, and Sandbox/topology expose
  some failure facts but the operator has no complete recovery object.

ORIGINAL PLAN
  Added an independent RuntimeReconciliationCase page plus material disposition
  and NG item records. It improved operator visibility, but risked making Case,
  SafetyIncident and session reconciliation compete as sources of truth.

CURRENT PLAN AFTER PREMISE GATE
  Makes RuntimeHold the authority. Reconciliation is a hold type / page label,
  not a competing lifecycle. Material disposition, NG item creation and WorkLine
  release evidence attach to the Hold.

12-MONTH IDEAL
  WES has one abnormal recovery control plane. ESTOP, ACK timeout, callback
  timeout, execution unknown, manual hold and NG diversion share a common
  RuntimeHold/Incident lifecycle, immutable evidence, role-based actions,
  legal material/session/command outcome combinations and clear release rules.
```

Dream state delta: the updated plan now points at the 12-month ideal by making `RuntimeHold` the authority. Remaining delta is the deferred NG container/work-order/native PDA lifecycle and richer operations dashboards.

#### 0C-bis. Implementation Alternatives

| Approach | Summary | Effort | Risk | Pros | Cons | Reuses |
|---|---|---:|---|---|---|---|
| A. Minimal projection over existing session reconciliation | Keep session reconciliation as source of truth. Add route/view projection and move the form from top card into a page. Defer NG item persistence. | M | Medium | Fastest path; minimal data model churn; fixes operator entry point. | Does not solve material disposition rigor; still leaves ESTOP/reconciliation split. | `WorklineRuntimeReconciliationService`, trace builder, current top card. |
| B. Unified RuntimeHold/Incident control plane | Create/extend one authoritative abnormal recovery entity for ESTOP and reconciliation. Case page is a view over this entity. Material disposition and release evidence attach to the hold. | L | Medium | Best long-term trajectory; prevents duplicate fact sources; clean PDA/desktop concurrency and audit. | Larger plan rewrite; must define migration from current session reconciliation fields. | `WorklineSafetyIncident`, runtime reconciliation service, diagnostics, trace evidence. |
| C. Full work-order/NG rework workflow now | Build Case, NG item, role assignment, NG location/container, rework batching and return-entry linkage in one PR. | XL | High | Most complete operationally; avoids unsafe half-step around NG. | Too much blast radius; likely blocks quick topology/Sandbox fixes. | Existing session/trace plus new work-order and material modules. |

Recommendation: choose Approach B. It is the smallest design that makes the safety and traceability model coherent without boiling the ocean into a full work-order system.

#### 0D. Selective Expansion Scan

Mode selected by /autoplan: `SELECTIVE EXPANSION`.

Candidate expansions:

| Candidate | Decision | Rationale |
|---|---|---|
| Add measurable success criteria: MTTR, false NG rate, manual confirmation time, re-open/rework rate. | Add to plan. | In blast radius and cheap; otherwise the feature cannot be evaluated after shipping. |
| Add material handoff proof for `RETURN_TO_NG`. | Add to plan. | Safety-critical. Without it, release action is under-specified. |
| Add unified RuntimeHold/Incident ADR before schema work. | Accepted by user. | Both outside voices recommend changing the plan's stated structure; user confirmed unified Hold. |
| Full NG container/role/work-order system. | Defer. | Valuable, but larger than the current lake. Capture as TODO after premise gate if unified direction is approved. |
| PDA-specific implementation detail. | Defer. | Keep API/page boundaries PDA-ready, but avoid committing mobile UX before the control model is settled. |

#### 0E. Temporal Interrogation

| Time Horizon | Decision Needed Now | Why It Matters |
|---|---|---|
| Hour 1 foundations | Is the source of truth `RuntimeReconciliationCase`, session reconciliation, `WorklineSafetyIncident`, or a new unified hold/incident? | This choice determines tables, API routes, version checks and frontend navigation. |
| Hour 2-3 core logic | What legal combinations exist between material disposition, session outcome, command outcome and WorkLine release? | Without a matrix, `CONTINUE + FAILED` and `RETURN_TO_NG + release` can encode unsafe or contradictory facts. |
| Hour 4-5 integration | When is a Case/Hold created: timeout/dispatch transaction, read resolver, or repair job? | Read-side creation creates concurrency and audit problems. |
| Hour 6+ polish/tests | How does the UI explain "technical unknown" vs "material NG" to avoid overusing NG? | The wrong default reason pollutes operations data and can hide communication failures. |

#### 0F. Mode Confirmation

`SELECTIVE EXPANSION` is the right review posture. The baseline problem is real, and the user confirmed the two strategic structure changes before deeper Design/Engineering/DX phases.

### CEO Dual Voices

#### CLAUDE SUBAGENT (CEO - strategic independence)

Findings count: Critical 2, High 5, Medium 2.

Key findings:

- Critical: the plan frames an abnormal recovery control-plane problem as a standalone reconciliation Case page, creating three abnormal objects with `WorklineSafetyIncident` and session reconciliation.
- Critical: `RETURN_TO_NG` releases WorkLine without proving material has been physically isolated.
- High: material identity is too weak because display identity is not ownership/idempotency.
- High: `ng_reason_catalog` duplicates existing business decision/failure semantics.
- High: lazy Case creation on first read is the wrong transaction boundary.
- High: topology count, failure evidence and NG rework are separate product problems and should be staged.
- Medium: `open_issue_count_by_device` should count active unresolved holds/incidents, not historical failed commands/diagnostics.
- Medium: `CONTINUE` material disposition and session outcome need a legality matrix.

#### CODEX SAYS (CEO - strategy challenge)

Findings count: Critical 2, High 6, Medium 4.

Key findings:

- The plan has not validated that operators need a Case page rather than a lower-friction recovery decision surface.
- Goals are functional outputs, not business results: no MTTR, false NG, manual handling time or repeat-incident metrics.
- `CONTINUE` / `RETURN_TO_NG` is too coarse for real production recovery states.
- ACK timeout defaulting to `UNKNOWN_PHYSICAL_STATE` can confuse communication failure with material uncertainty.
- `NgReturnItem` without location/owner/SLA is half of an inventory/rework workflow.
- Release after form submission is unsafe without physical clear and downstream readiness evidence.
- Case lifecycle lacks in-progress/reopen/void/correction semantics.
- Plugin-private NG reasons need global taxonomy and version mapping.
- Permission and responsibility boundaries are missing.
- Future re-entry by real EVENT is structurally important and cannot be ignored by the first `NgReturnItem` shape.

#### CEO DUAL VOICES - CONSENSUS TABLE

```text
Dimension                            Claude      Codex       Consensus
---------------------------------------------------------------------------
1. Premises valid?                   No          No          DISAGREE WITH PLAN
2. Right problem to solve?           Reframe     Reframe     CONFIRMED
3. Scope calibration correct?        Too bundled Too bundled CONFIRMED
4. Alternatives sufficiently explored? No        No          CONFIRMED
5. Operational risks covered?        No          No          CONFIRMED
6. 6-month trajectory sound?         No          No          CONFIRMED
```

Consensus: 5/6 dimensions confirmed as concerns. The remaining dimension is not a model disagreement; both models disagree with the current premise.

### CEO User Challenges

Resolved by user confirmation on 2026-05-09.

#### Challenge 1 - Replace standalone `RuntimeReconciliationCase` source of truth with unified runtime hold/incident control plane

What the plan says: add a persistent `RuntimeReconciliationCase` table and route as the primary boundary for runtime reconciliation work.

What both models recommend: make Case a view/projection or typed subtype of one abnormal recovery fact source covering runtime reconciliation and safety holds. Candidate name: `RuntimeHold` or extended `WorklineSafetyIncident`.

Why: source-of-truth duplication is the largest long-term risk. The code already has `WorklineSafetyIncident` for safety events and `WorklineRuntimeReconciliationService` for session-based reconciliation; a new Case table as the authority creates inconsistent lifecycles, release semantics and audit trails.

What context the models may be missing: there may be product or naming requirements that force reconciliation to be operationally separate from safety incidents.

If the models are wrong: unifying too early could slow the quick operator-facing fix and make a narrow reconciliation workflow depend on broader safety incident design.

User decision: use unified Runtime Hold as authority. Reconciliation Case is a view/projection or typed subtype.

#### Challenge 2 - Do not allow `RETURN_TO_NG` to release WorkLine unless material handoff evidence is captured

What the plan says: `RETURN_TO_NG` creates `NgReturnItem(status=WAITING_REWORK)` and releases WorkLine isolation.

What both models recommend: require physical isolation proof before release. Minimum evidence: NG location/container or station, operator confirmation, material identity/idempotency, and a device/line clear check.

Why: release is a production safety action. A database record alone does not prove the material left the normal path.

What context the models may be missing: the site may have a physical SOP that guarantees the handoff outside WES.

If the models are wrong: adding handoff evidence can slow operators in v1, but the failure mode of skipping it is much worse: WES resumes while material state is still ambiguous.

User decision: require physical handoff evidence before WorkLine release.

### Decision Audit Trail

| # | Phase | Decision | Classification | Principle | Rationale | Rejected |
|---|---|---|---|---|---|---|
| 1 | Phase 0 | Treat supplied plan file as active design doc | Mechanical | P6 Bias toward action | The user invoked `/autoplan` with a concrete plan file; stopping for `/office-hours` would add process without new facts. | Running `/office-hours` first |
| 2 | Phase 0 | Run Design and DX phases later | Mechanical | P1 Completeness | Plan includes pages/UI flows and API/plugin contract work; both scopes are in blast radius. | Backend-only review |
| 3 | Phase 1 | Select `SELECTIVE EXPANSION` posture | Mechanical | P2 Boil lakes + P3 Pragmatic | The plan is an enhancement to an existing runtime system; review should hold baseline scope while surfacing high-value expansions. | Full expansion or scope reduction by default |
| 4 | Phase 1 | Surface unified hold/incident direction as User Challenge | User Challenge | Autoplan exception | Both models recommend changing the stated Case architecture, so the original direction stands unless user approves the change. | Silently rewriting the plan |
| 5 | Phase 1 | Surface physical handoff evidence for `RETURN_TO_NG` as User Challenge | User Challenge | Autoplan exception | Both models flag release without physical proof as a safety/feasibility risk, not taste. | Auto-accepting current release rule |
| 6 | Phase 1 | Pivot authoritative source to unified Runtime Hold | User-approved | P4 DRY + P5 Explicit over clever | User confirmed unified Hold; this removes duplicate abnormal-state authority and makes Case a view/projection. | Keeping `RuntimeReconciliationCase` as independent authority |
| 7 | Phase 1 | Require physical handoff evidence before `RETURN_TO_NG` release | User-approved | P1 Completeness | User confirmed evidence gate; release now requires NG location/container, operator confirmation, material identity and clear checks. | Releasing WorkLine with checklist-only confirmation |
| 8 | Phase 2 | Use `Runtime Hold` as visible UI noun and `Reconciliation` only as type tag | Auto-decided | P5 Explicit over clever | Mixed Case/Reconciliation/Hold names would confuse operators and generated API names. | Keeping `/runtime/reconciliations/:holdId` as primary route |
| 9 | Phase 2 | Make Runtime Hold page decision-first | Auto-decided | P1 Completeness | Operators first need to know whether the line can be released and what blocks it. | Evidence-first page with CTA buried below |
| 10 | Phase 2 | Require PDA scan-first `RETURN_TO_NG` flow | Auto-decided | P1 Completeness | Physical handoff evidence must be ergonomic at the station, not only on desktop. | Desktop-only manual text form |
| 11 | Phase 3 | Add `RuntimeHoldReleaseService` as sole WorkLine READY/outbox release writer | Auto-decided | P4 DRY + P5 Explicit over clever | Existing reconciliation and safety services can otherwise race and release a line while another Hold remains active. | Letting each service write `WorkLine.runtime_status=READY` |
| 12 | Phase 3 | Split command, blocked outbox and open issue counts | Auto-decided | P5 Explicit over clever | `BLOCKED_RESOURCE` is an Outbox status, not a DeviceCommand status. | Counting blocked outbox as open command |
| 13 | Phase 3 | Server computes `release_eligibility` on GET and POST errors | Auto-decided | P4 DRY | Frontend inference would drift from backend release rules. | Letting UI infer missing checks/evidence |
| 14 | Phase 3 | Server stamps handoff actor/time and resolves material identity | Auto-decided | P1 Completeness | Client-sent actor/time/identity is forgeable and duplicates existing auth/plugin facts. | Accepting `handoff_confirmed_by` or `material_identity` from client |
| 15 | Phase 3 | Use HTTP 409 with refreshed Hold model for version/evidence conflicts | Auto-decided | P1 Completeness | Stale desktop/PDA pages need a deterministic recovery path. | HTTP 200 business failure with only a message |
| 16 | Phase 3.5 | Add repair runbook with dry-run/apply/idempotency/invariant checks | Auto-decided | P1 Completeness | Migration safety is developer-facing and operationally risky. | Vague "repair job" note |
| 17 | Phase 3.5 | Require copy-paste Runtime Hold quickstart | Auto-decided | P5 Simpler over clever | First implementer needs a fast ACK-timeout-to-NG loop. | Endpoint list without runnable examples |
| 18 | Cross-phase | Defer full NG container/work-order/native PDA lifecycle to TODOS.md | Auto-decided | P3 Lake discipline | Valuable, but outside the first coherent Runtime Hold release. | Building full work-order system now |

### Phase 1 Premise Gate - Passed

User confirmed before continuing to Phase 2/3/3.5:

1. Runtime reconciliation pivots to a unified runtime hold/incident fact source. Case is a view/projection or typed subtype.
2. `RETURN_TO_NG` requires explicit physical handoff evidence before release.

### Phase 1 CEO Review - Sections 1-11

#### Section 1 - Architecture And Ownership

Finding: original Case authority would duplicate `WorklineSafetyIncident`, session reconciliation fields and WorkLine runtime status. Decision: rewrite to unified `RuntimeHold` authority and make release pass through `RuntimeHoldReleaseService`.

```text
Runtime event / timeout / safety event
  -> RuntimeHold(source_idempotency_key)
  -> RuntimeHoldDetail read model
  -> RuntimeHoldResolve command
  -> RuntimeHoldReleaseService
       -> WorkLine.runtime_status projection
       -> blocked Outbox release
       -> NgReturnItem when RETURN_TO_NG
```

#### Section 2 - Error & Rescue Registry

| Error | User impact | Rescue path | Plan change |
|---|---|---|---|
| Stale desktop/PDA page submits old version | Operator cannot know whether retry is safe | HTTP `409` with current Hold model and refresh URL | Added explicit conflict contract |
| Missing physical handoff evidence | WorkLine could resume while material remains in line | Hold stays open and `release_eligibility` names missing evidence | Added evidence gate |
| Material identity ambiguous | NG item cannot be deduped or reworked | Block release until resolver returns `RESOLVED` | Added plugin resolver contract |
| Reason code unmapped | Reporting splits across plugin/manual/runtime reasons | Reject with unmapped reason report | Added canonical taxonomy |
| Safety Hold and reconciliation Hold coexist | Clearing one condition releases line too early | Release service checks all active blocking holds | Added release arbitration |

#### Section 3 - Security And Permissions

Examined auth and operation route patterns. Existing operation APIs already receive `current_user_id` through `require_auth`; therefore client-submitted `handoff_confirmed_by`, `confirmed_at` and material identity are not trusted. Plan now requires server-stamped actor/time, permission-specific routes and read-only behavior for users without resolve permission.

#### Section 4 - Data And Interaction Edges

Finding: `GET` with lazy creation would corrupt audit boundaries. Decision: Hold creation must happen in the transaction that enters abnormal state, while legacy rows are handled by a dry-run/apply repair job. Late callback, double-submit, resolved Hold and reason-catalog failure are now explicit UI/API states.

#### Section 5 - Quality Bar

The plan now rejects DRY violations around counts, release logic and material identity. `BLOCKED_RESOURCE` is outbox state; plugin display identity is not identity; Reconciliation is a type tag rather than a second entity.

#### Section 6 - Test Coverage

CEO review required tests for false release, physical handoff absence, stale version and GET no-write behavior. These were added to the test strategy and expanded again in Phase 3.

#### Section 7 - Performance

The main performance risk is active Hold aggregation on runtime topology. The plan keeps counts source-specific and requires indexed active Hold queries by `workline_id`, `source_device_id`, `status`, `blocking` and `source_idempotency_key`.

#### Section 8 - Observability

Added success metrics and Hold reason/NG reason aggregation. Remaining operational dashboards are deferred to TODO because they depend on the first real Hold data.

#### Section 9 - Deployment

Added nullable-first migration, dual-write, repair job, invariant SQL and phased removal of old `blocked_by_reconciliation_session_id` write paths. Alembic revision must be generated by repository tooling, not hand-written.

#### Section 10 - Long-Term Trajectory

The updated plan now aligns with the 12-month ideal: one abnormal recovery control plane. Deferred scope is explicit: NG container/work-order lifecycle, native/offline PDA and operations dashboards.

#### Section 11 - Design

UI scope remains valid, but the plan now uses `Runtime Hold` as the visible noun and makes the page decision-first. Full design review follows in Phase 2.

#### NOT In Scope

- Full NG container / owner / SLA / work-order lifecycle.
- Native/offline PDA app.
- Batch rework execution workflow.
- Runtime Hold operations dashboard and alerting.
- Automatic synthetic entry event generation.

#### What Already Exists

| Need | Existing asset | Decision |
|---|---|---|
| WorkLine runtime status | `WorkLineRuntimeStatus.READY/RECONCILING/ESTOPPED` | Keep as projection, not authority |
| Safety incident lifecycle | `WorklineSafetyIncident` | Integrate through Hold release arbitration |
| Runtime reconciliation service | `WorklineRuntimeReconciliationService` | Reuse detection/resolve knowledge, remove direct release authority |
| Trace/evidence | trace response builder, diagnostics, timeline | Reuse for Hold evidence panels |
| Plugin decisions | `business_decision(reason_code, ...)` | Map into canonical reason taxonomy |
| Plugin business key | manifest resolver/session resolver | Extend with MaterialIdentity resolver |

#### Failure Modes Registry

| Failure mode | Severity | Status |
|---|---|---|
| Independent Case fact source diverges from safety/session facts | Critical | Fixed by unified Hold |
| WorkLine release while another Hold remains active | Critical | Fixed by release service |
| Return to NG without physical handoff proof | Critical | Fixed by evidence gate |
| Client-forged actor/time/material identity | High | Fixed by server stamping and resolver |
| GET creates rows | High | Fixed by repair job rule |
| Blocked outbox counted as command | Medium | Fixed by split counts |

#### Scope Expansion Decisions

| Expansion | Decision | Reason |
|---|---|---|
| Physical handoff proof | Added | Safety-critical and user-confirmed |
| MaterialIdentity plugin contract | Added | Needed for NG idempotency |
| Release arbitration service | Added | Needed to unify Hold authority |
| Full work-order lifecycle | Deferred | Larger than first coherent release |
| Native/offline PDA | Deferred | Keep Web PDA-ready first |

#### CEO Completion Summary

Phase 1 found two premise-level blockers. Both were resolved by user confirmation: unified Hold and mandatory physical handoff evidence. Current CEO score after rewrite: 8/10; remaining risk is execution scope, not strategic direction.

**Phase 1 complete.** Codex: 12 concerns. Claude subagent: 9 issues. Consensus: 5/6 confirmed concerns before user decision, 0 unresolved user challenges after confirmation. Passing to Phase 2.

### Phase 2 Design Review

Degradation note: CEO phase had a Claude subagent. For Phase 2 onward, no new subagents were launched because the host tool policy only allows subagents when the user explicitly asks for delegation. Design/Eng/DX dual-voice sections are therefore marked Codex-only degradation.

#### CODEX SAYS (design - UX challenge)

Findings:

- Critical: the page must answer “can we release the line now?” before showing evidence.
- High: missing states were not specified: loading, permission denied, stale version, resolved Hold, reason catalog failure, missing/ambiguous material identity and late callback invalidation.
- High: desktop and PDA need different shapes. Desktop is for supervisor review; PDA is for scan-first handoff confirmation.
- High: physical handoff UI must be scan-first and disable release until backend preview passes.
- Medium: accessibility requirements were absent.
- Medium: top card needs blocker/release requirements, not a generic Case link.
- Medium: reason taxonomy selector needs grouping, recommendation and override audit.
- Medium: naming must stop mixing Case/Reconciliation/Hold.

#### CLAUDE SUBAGENT (design - independent review)

Not run in this phase due host tool policy. Consensus cells with Claude are N/A, not confirmed.

#### Design Litmus Scorecard

| Dimension | Before | After plan update | Decision |
|---|---:|---:|---|
| Information hierarchy | 5 | 8 | Decision-first page |
| Interaction states | 3 | 8 | Added state matrix |
| Responsive/PDA strategy | 4 | 8 | Added desktop/PDA split |
| Accessibility | 3 | 7 | Added explicit baseline |
| Design system alignment | 6 | 7 | Uses existing runtime industrial UI |
| Specificity | 5 | 8 | Added concrete controls and routes |
| Error recovery | 4 | 8 | Added 409/422 UI behavior |

Design consensus: Codex-only. No taste decision remains; route/name consistency was auto-decided to `Runtime Hold`.

**Phase 2 complete.** Codex: 8 concerns. Claude subagent: not run. Consensus: N/A due degradation. Passing to Phase 3.

### Phase 3 Engineering Review

#### CODEX SAYS (eng - architecture challenge)

Findings:

- Critical: `RuntimeHold` authority needed concrete table, lifecycle, constraints and relation to current session/safety/workline fields.
- Critical: release arbitration was unsafe. Existing reconciliation and safety services can both write WorkLine `READY`.
- High: `BLOCKED_RESOURCE` is outbox state, not command state.
- High: API needed `allowed_actions`, `release_eligibility`, blockers, required checks, evidence hash and version.
- High: concurrency requires locks across hold/workline/session/command/outbox and 409 with refreshed model.
- High: physical handoff actor/time/material identity must be server-derived.
- High: MaterialIdentity and reason taxonomy require versioned plugin contracts.
- Medium: rollout needed dual-write, repair, generated frontend contract and old endpoint convergence.

#### CLAUDE SUBAGENT (eng - independent review)

Not run in this phase due host tool policy. Consensus cells with Claude are N/A, not confirmed.

#### Architecture Diagram

```text
Timeout scanner / dispatcher / safety API
  -> RuntimeHoldRepository.create_or_get(source_idempotency_key)
  -> RuntimeHoldService.get_detail()
       -> Trace evidence
       -> Plugin reason taxonomy
       -> MaterialIdentity preview
       -> ReleaseEligibilityBuilder

Frontend / PDA
  -> GET /api/v1/workline/runtime-holds/{hold_id}
  -> POST /api/v1/workline/runtime-holds/{hold_id}/resolve

RuntimeHoldResolveService
  -> validate version + evidence hash
  -> validate checks + material disposition
  -> MaterialIdentityResolver
  -> NgReturnItemRepository.create_idempotent()
  -> RuntimeHoldReleaseService
       -> lock WorkLine + active holds
       -> update WorkLine projection
       -> release outbox blocked_by_runtime_hold_id
```

#### Code Quality Findings

| Finding | Fix |
|---|---|
| Release logic duplicated between reconciliation and safety services | New `RuntimeHoldReleaseService` owns WorkLine READY and outbox release |
| Count names conflate command/outbox/issue | Split `open_command_count`, `blocked_outbox_count`, `open_issue_count` |
| Case naming leaks into route/API | Use `runtime-holds` and `/runtime/holds/:holdId` |
| Client payload duplicated trusted facts | Server stamps actor/time and resolves material identity |

#### Test Diagram

| Flow / code path | Required test | Status in plan |
|---|---|---|
| Timeout creates Hold | Backend integration | Added |
| GET Hold no write | Backend API/unit | Added |
| Split topology counts | Backend service + frontend unit | Added |
| RETURN_TO_NG missing evidence | Backend API + frontend unit | Added |
| RETURN_TO_NG valid evidence | Backend integration | Added |
| Material identity missing/ambiguous | Backend plugin contract | Added |
| Reason unmapped | Backend API | Added |
| Stale version/evidence hash | Backend API + frontend unit | Added |
| ESTOP and reconciliation coexist | Backend integration | Added |
| Double submit same Hold | Backend integration | Added |
| Late callback invalidates page | Backend integration + frontend unit | Added |
| Permission denied | Backend API + frontend unit | Added |
| Repair job dry-run/apply idempotency | Script tests | Added |
| OpenAPI/frontend contract sync | Contract verify | Added |

Test plan artifact: `/Users/kaizhou/.gstack/projects/kaizhoumasha-wes_backend/kaizhou-workline-sandbox-runtime-flow-test-plan-20260509-runtime-hold.md`

#### Performance Review

Main risks are active Hold aggregation and evidence-heavy GET. Plan requires indexed active Hold queries and evidence snapshots; full timeline can remain paged/collapsible if the detail response grows.

#### Engineering Failure Modes

| Failure mode | Severity | Mitigation |
|---|---|---|
| Release race between safety and reconciliation | Critical | Release service and row locks |
| Duplicate NG item | High | Unique constraint + idempotent create |
| Old blocked outbox not migrated | High | Dual-write + repair + invariant SQL |
| Plugin resolver raises or returns ambiguous identity | High | Block release and return actionable 422 |
| Frontend generated types drift | Medium | `pnpm contract:verify` gate |

#### Eng Completion Summary

Engineering score before update: 5/10. Engineering score after update: 8/10. Remaining risk is implementation size and migration discipline.

**Phase 3 complete.** Codex: 10 concerns. Claude subagent: not run. Consensus: N/A due degradation. Passing to Phase 3.5.

### Phase 3.5 DX Review

#### DX Scope Assessment

Product type: internal runtime platform with backend API, generated frontend contract, plugin SDK and operational repair scripts. Primary developer persona: WES backend/frontend engineer adding or operating a runtime Hold workflow.

Initial DX completeness: 4/10. Target after plan update: 7.5/10. TTHW before update: 30+ minutes and several guessed steps. Target TTHW: 5 minutes from existing local environment.

#### CODEX SAYS (DX - developer experience challenge)

Findings:

- Critical: `RuntimeHold` implementation boundary was too vague for a first implementer.
- Critical: stale version/error response DX was unusable; existing route style can return HTTP 200 business failures.
- High: endpoint list lacked response schemas, permissions, operation IDs and examples.
- High: `release_eligibility` needed to be a read model, not frontend logic.
- High: MaterialIdentity and reason taxonomy lacked plugin contract docs/templates/tests.
- High: migration/repair job lacked dry-run, idempotency, batching and invariant checks.
- Medium: topology count naming still risked confusing outbox and command states.
- Medium: hello-world path was missing.

#### CLAUDE SUBAGENT (DX - independent review)

Not run in this phase due host tool policy. Consensus cells with Claude are N/A, not confirmed.

#### Developer Journey Map

| Stage | Developer need | Plan requirement |
|---|---|---|
| Discover | Understand why Hold exists | Updated first-principles section |
| Model | Know tables/enums/constraints | RuntimeHold ADR section |
| Migrate | Add schema safely | Alembic + repair runbook |
| Contract | Know schemas/errors/permissions | API contract section |
| Plugin | Add identity/reason mapping | MaterialIdentity + taxonomy contract |
| Frontend | Generate/use stable types | operationId and contract verify |
| Test | Know what to cover | Test diagram + artifact |
| Debug | Recover from 409/422 | Error examples and release eligibility |
| Demo | Prove E2E quickly | Hello World path |

#### Developer Empathy Narrative

“I am implementing Runtime Hold for the first time. I need to know which table owns state, which service can release the line, what payloads I can trust, how to generate frontend types, and how to reproduce one ACK-timeout-to-NG flow without reverse engineering half the runtime system.”

#### DX Scorecard

| Dimension | Before | After plan update |
|---|---:|---:|
| Time to hello world | 3 | 7 |
| API naming | 5 | 8 |
| Error actionability | 3 | 8 |
| Docs findability | 4 | 7 |
| Migration safety | 3 | 8 |
| Plugin authoring | 4 | 7 |
| Frontend contract sync | 5 | 8 |
| Debuggability | 4 | 7 |

#### DX Implementation Checklist

- Define Pydantic schemas for GET/resolve/ng-reasons/ng-return-items.
- Add OpenAPI examples for success, missing evidence and 409 conflict.
- Add plugin template/docs for `MaterialIdentityResolver` and `ng_reason_catalog` mapping.
- Add `repair_runtime_holds.py --dry-run/--apply --limit` with invariant output.
- Add copy-paste Runtime Hold quickstart.
- Run backend focused tests and frontend `pnpm contract:verify` / `pnpm type:check`.

**Phase 3.5 complete.** DX overall: 4/10 -> 7.5/10. TTHW: 30+ min -> target 5 min. Codex: 10 concerns. Claude subagent: not run. Consensus: N/A due degradation. Passing to Phase 4.

### Cross-Phase Themes

**Theme: single authority** — flagged in Phase 1 and Phase 3. High-confidence signal. Fixed by unified Runtime Hold and release service.

**Theme: physical-world proof** — flagged in Phase 1, Phase 2 and Phase 3. High-confidence signal. Fixed by mandatory physical handoff evidence and PDA scan-first flow.

**Theme: contract specificity** — flagged in Phase 3 and Phase 3.5. High-confidence signal. Fixed by API schemas, 409/422 examples, plugin resolver and repair runbook requirements.

**Theme: naming consistency** — flagged in Phase 2 and Phase 3.5. Fixed by `Runtime Hold` UI noun and `runtime-holds` API path.

### Deferred To TODOS.md

- Full NG container / work-order / owner / SLA lifecycle.
- Native/offline PDA app and hardware scanner integration.
- Runtime Hold operations dashboard and alerts.

### Pre-Gate Verification

| Required output | Status |
|---|---|
| CEO premise challenge | Done |
| CEO dual voices | Done |
| Premise gate | Passed by user |
| Error & Rescue Registry | Done |
| Failure Modes Registry | Done |
| NOT in scope | Done |
| What already exists | Done |
| Dream state delta | Done |
| Design 7 dimensions | Done, Codex-only degradation noted |
| Eng architecture diagram | Done |
| Eng test diagram | Done |
| Test plan artifact | Written separately |
| DX journey map | Done |
| DX scorecard | Done |
| Cross-phase themes | Done |
| Decision Audit Trail | Done |

### /autoplan Final Gate

Plan summary: the plan now implements a unified `RuntimeHold` authority, requires physical handoff evidence before `RETURN_TO_NG` can release a WorkLine, and moves UI/API naming to Runtime Hold rather than Reconciliation Case.

User challenges: both original challenges were accepted by the user and incorporated. No unresolved user challenge remains.

Taste decisions: none surfaced as close calls after the user confirmed Hold + physical evidence. All remaining decisions were mechanical or safety/contract fixes.

Auto-decided: 18 decisions are logged in the Decision Audit Trail.

Review scores:

- CEO: 8/10 after premise fix.
- Design: 8/10 after decision-first UI, PDA flow and state matrix.
- Eng: 8/10 after Hold ADR, release service, counts split and migration/runbook.
- DX: 7.5/10 after API/error/plugin/migration/quickstart contract requirements.
