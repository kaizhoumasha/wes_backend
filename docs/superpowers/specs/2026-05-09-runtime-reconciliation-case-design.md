# Runtime Reconciliation Case 与 NG 物料处置设计

## 背景

当前 Sandbox 已能展示失败命令和运行时对账状态，但仍有三个问题：

1. 拓扑设备节点上的计数语义不清，已终态命令可能被用户理解为仍有待处理任务。
2. `FAILED` 命令只显示状态，不足以解释失败原因，也缺少进入人工对账流程的清晰入口。
3. 对账只覆盖工作线、设备、指令状态，缺少实体物料处置指导，尤其无法表达 NG 物料后续批量返工。

本设计将 runtime reconciliation 从 Sandbox 附属操作提升为独立 Case 工作流。Sandbox、拓扑和顶部卡片只提供摘要和入口；完整处理在独立 Case 页面完成，并为后续 PDA 嵌入保留边界。

## 目标

- 拓扑主视图的数字只表示设备当前未完成任务。
- `FAILED` 命令不计入未完成任务，但设备和页面必须显示异常/对账标记。
- 提供独立 `RuntimeReconciliationCase` 页面，支持桌面端和 PDA 端访问。
- 对账必须覆盖实体物料处置，第一版支持 `CONTINUE` 与 `RETURN_TO_NG`。
- `RETURN_TO_NG` 创建按物料/原 session 追踪的 NG 记录，后续支持批量返入口重做。
- NG 原因以插件定义为主，系统内置兜底原因为辅。

## 非目标

- 第一版不自动伪造入口 EVENT。
- 第一版不做完整工单系统、角色派工、NG 周转箱追踪。
- 第一版不扩展 session 终态，`RETURN_TO_NG` 对应当前 session `FAILED`。
- 第一版不在拓扑或 Sandbox 列表内直接执行解除隔离。

## 领域模型

### RuntimeReconciliationCase

代表一次运行时对账事件。来源是 `WorklineSession.reconciliation_state=PENDING`，但前端和 PDA 面向 Case 操作，而不是直接面向 session。

Case 聚合以下事实：

- workline、session、plugin、trace
- reconciliation reason/source
- source inbox/outbox/command/device
- diagnostic 与 timeline 证据
- 当前 checklist、operator note、resolution
- material disposition 与 NG reason

### FailedCommandEvidence

失败命令是 Case 的证据，不是人工操作本体。它应展示：

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

### NgReturnItem

当物料处置为 `RETURN_TO_NG` 时创建一条 NG 记录，粒度为单物料/原 session。后续多个 NG item 可组成返入口批次，由入口设备发送新的真实 EVENT 开启新 session，并关联原 NG 记录。

第一版字段：

- source workline/session/command/event
- material identity，如 `PkgID`、`HHPN`、`LotCode`
- `disposition=RETURN_TO_NG`
- `ng_reason_source`
- `ng_reason_code`
- `ng_reason_label`
- operator note
- created_from_reconciliation_case_id
- `status=WAITING_REWORK`

## NG 原因目录

NG 原因由插件定义为主，系统内置兜底原因。

插件契约增加 `ng_reason_catalog`。例如 SMT 粗分机插件可声明：

- `SCAN_ABNORMAL`：扫码异常
- `SIZE_ABNORMAL`：尺寸异常
- `MEASUREMENT_MISMATCH`：测量不匹配

系统内置兜底原因：

- `UNKNOWN_PHYSICAL_STATE`：设备动作状态未知
- `OPERATOR_JUDGED_NG`：现场人工判定 NG
- `RUNTIME_RECOVERY_NG`：运行时异常恢复导致转 NG

Case 页面规则：

- 选择 `RETURN_TO_NG` 时，`ng_reason_code` 必填。
- 优先展示插件原因，系统兜底原因单独分组展示。
- 若对账原因为 ACK 超时，默认预选 `UNKNOWN_PHYSICAL_STATE`。
- 系统可根据失败证据预填 NG 原因，但现场人员可以改成更准确的插件原因。

## 页面职责

### 拓扑主视图

拓扑设备节点显示两个概念：

- `open_task_count_by_device`：设备当前未完成任务数，只包含 `PENDING`、`SENT`、`ACK_RECEIVED`、`BLOCKED_RESOURCE`。
- `open_issue_count_by_device`：设备当前待处理异常数，来自 pending reconciliation、failed command、active diagnostic。

数字徽标只显示未完成任务。异常用红色标记表达。点击设备后，设备面板按三组展示：

- 未完成任务
- 异常/对账 Case
- 历史命令

### Sandbox 命令列表

- `COMPLETED`、`FAILED` 命令可作为历史显示，但不算待操作。
- `FAILED` 行显示失败摘要与“查看对账 Case”入口。
- 不在命令行内直接执行解除隔离。

### 顶部运行时对账卡片

顶部卡片只保留高优先级摘要：

- 当前 Case
- 停线原因
- 影响设备/命令
- 物料处置是否已记录
- 入口按钮：进入对账 Case

完整 checklist 和 resolve 表单迁移到 Case 页面。

### Runtime Reconciliation Case 页面

独立路由：

```text
/runtime/reconciliations/:caseId
```

第一版新增持久化 `RuntimeReconciliationCase` 表。对已有仅写入 session reconciliation 字段的数据，后端 resolver 在首次读取时创建 Case 记录，并返回同一路由。

页面包含五块：

1. 现场摘要：线体、设备、命令、session、物料标识、发生时间、风险等级。
2. 失败原因：人话解释与技术证据。
3. 现场检查清单：根据 reconciliation reason 动态变化。
4. 物料处置：`CONTINUE` 或 `RETURN_TO_NG`，后者必须选择 NG 原因。
5. 结论与恢复：提交 resolution，创建 NG item 或继续流程，并释放 WorkLine 隔离。

## 提交流程与防呆

### CONTINUE

- 必须完成 checklist。
- 必须填写 operator note。
- 允许选择 session 结论：`COMPLETED`、`FAILED`、`CANCELLED`。
- 不创建 NG item。

### RETURN_TO_NG

- 必须完成 checklist。
- 必须选择 `ng_reason_code`。
- 必须填写 operator note。
- session 结论固定为 `FAILED`。
- 创建 `NgReturnItem(status=WAITING_REWORK)`。
- 释放 WorkLine 隔离。
- 不自动发入口 EVENT。

### 防呆规则

- 若存在迟到 callback evidence，页面必须突出提示，并要求重新确认物料是否仍应进入 NG。
- 提交前后端重新校验 Case/session 版本，避免 command 状态已变化但页面仍提交旧结论。
- 若 WorkLine 已被其他人解除隔离，提交失败并刷新 Case。
- 所有 resolve 都写入 operator id、confirmed time、checks、material disposition、NG reason、operator note。

## 后端接口

第一版接口以 Case 为边界：

- `GET /api/v1/workline/reconciliations/{case_id}`
- `POST /api/v1/workline/reconciliations/{case_id}/resolve`
- `GET /api/v1/workline/reconciliations/ng-reasons?plugin_key=&contract_version=`
- `GET /api/v1/workline/ng-return-items`

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
  "case_version": 3
}
```

## 测试策略

后端测试：

- 拓扑计数不把 `COMPLETED`、`FAILED` 命令计入 `open_task_count_by_device`。
- pending reconciliation 能产生 `open_issue_count_by_device`。
- `RETURN_TO_NG` resolve 固定 session 为 `FAILED` 并创建 `NgReturnItem`。
- `CONTINUE` resolve 不创建 NG item。
- 缺失 `ng_reason_code` 时拒绝 `RETURN_TO_NG`。
- Case version 过期时拒绝提交。

前端测试：

- 拓扑数字与异常标记分离展示。
- `FAILED` 命令行显示失败摘要和 Case 入口，不显示 ACK/Result 操作。
- 顶部对账卡片只显示摘要和入口。
- Case 页面在 `RETURN_TO_NG` 时要求选择 NG 原因。
- 迟到 callback evidence 出现时阻止无确认提交。

集成验证：

- 构造 ACK 超时命令，确认拓扑不再把该命令计入未完成任务，但显示异常标记。
- 进入 Case，选择 `RETURN_TO_NG`，提交后 WorkLine 解除隔离，NG item 可在队列中查询。
- NG item 后续批量返入口时由入口真实 EVENT 创建新 session，并保留关联关系。
