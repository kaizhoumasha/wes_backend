---
title: WMS / WES 人工出库拣料交互要求
status: Approved
created_at: 2026-09-02
updated_at: 2026-09-03
audience: WMS 与 WES 初级开发工程师、联调与测试人员
scope: Phase 12 人工出库拣料线（Line3）的 point2 任务准入、完成释放与应用结果；其余环节复用自动出库合同
related:
  - docs/contracts/wms-outbound-picking-task-integration-requirements.md
  - docs/contracts/transport-fulfillment-contract.md
  - docs/integration/wes-wms-interface-requirements.md
  - docs/superpowers/plans/2026-08-27-phase12-manual-bin-processing-guided-development.md
---

# WMS / WES 人工出库拣料交互要求

## 1. 文档定位

本文是 Phase 12 人工出库拣料线（现场编号 Line3）的联合评审基线，只定义**人工出库线相对自动出库线唯一的差异点**：
工作位（点2）任务由人工经 PDA 完成，而不是由机械臂执行 `PICK_AND_PUT`。

本文不是一份独立合同。人工出库线的任务下发、资源计算、货架搬运、料箱投料、扫码与位置事实、退料回库，
与
[`wms-outbound-picking-task-integration-requirements.md`](wms-outbound-picking-task-integration-requirements.md)（下称"出库合同"）
定义的自动出库场景完全一致。本文只增加人工线的 point2 任务准入、最终释放决定和应用结果报告三个 operation，不建立第二套
通用字段表达。

系统尚未发布。本文不提供旧接口、兼容字段或新旧路径并存。

## 2. 与出库合同的边界

### 2.1 复用出库合同的部分（零新增）

- 任务发布与队列：`outbound.picking_task.issued@v1`、`outbound.picking_task.queue_changed@v1`；
  人工任务使用同一个 PickingTask 实体和队列，发布时固定 `data.task_type=MANUAL`，不建立人工任务表或人工任务业务键；
- 任务准备与计划增量：`outbound.picking_task.prepare@v1`、`outbound.picking_task.plan_delta@v1`；
- 五层货架入站分批：`outbound.bin.inbound_batch@v1`；
- 退箱：`outbound.bin.return_batch@v1`，`RETURN_BUFFER` FIFO；
- 货架离场：`outbound.rack.departure_decide@v1`；
- 任务状态确认：`outbound.picking_task.completion_confirm@v1`；
- Transport 四个通用搬运方法（`move_rack` / `rotate_rack` / `move_bins` / `exchange_bins`）与其提交、回调合同；
- `BinExecution`、`PositionProjection` 等既有执行域对象与不变量；
- `WmsConfirmation` 的可靠派发、重试和结果证据；本文只把其中立关联扩为料盘或料箱二选一，不新增第二套 outbox。

上述接口的字段、条件必填、响应联合、错误码、幂等和重试语义完全以出库合同为准，本文不重复摘录，也不允许出现与出库合同
不一致的实现。

### 2.2 人工出库线独有的部分（本文新增）

- 工作位（点2）任务由人工经 PDA 完成，PDA 是 WMS 侧功能，不在 WES 集成范围内（详见第 4 节）；
- point2 扫描实际 Bin 后，WES 向 WMS 请求是否存在人工任务的新 operation：
  `outbound.manual_bin.work_admission_decide@v1`（详见第 5 节）；
- WMS 在原子持久化 PDA 子任务和业务结果后，向 WES 上报 Bin 级最终释放决定的新 operation：
  `outbound.manual_bin.work_completed@v1`（详见第 5 节）；
- WES 把完成决定的异步应用结果可靠上报 WMS：`outbound.manual_bin.completion_apply_report@v1`（详见第 5 节）；
- 人工出库线不使用出库合同 `outbound.bin.work_plan@v1`：工作位任务的可执行范围（拣哪些 Cell）完全由 WMS/PDA
  内部决定，WES 不查询、不持有、不校验该范围。

### 2.3 代码所有权与静态路由

- `src/app/wms_adapter/` 只拥有严格 DTO/parser、OpenAPI、公共 HTTP/JSON 校验、`InboundEvidence` 可靠接收与消息幂等 ACK；
  不读取人工线的 Epoch、点位或 `BinExecution`，也不决定料箱方向。
- `src/app/execution/` 只中立扩展 `WmsConfirmation` 的料箱关联和可靠生命周期，不识别人工线 operation 字面量或业务结果；
- 宿主 composition 对 `outbound.manual_bin.work_completed@v1` 使用唯一显式静态映射，将其交给
  `workline_plugins/manual_bin_processing/`；禁止按 plugin key 动态猜测、默认 handler 或其它工作线 fallback。
- `workline_plugins/manual_bin_processing/` 拥有 evidence 的业务应用：活动 Epoch、点位和 `BinExecution` 绑定，
  `task_id + bin_id` 单终态，业务幂等，`RECONCILING` 以及设备动作。
- 当前部署未安装或未显式绑定该 owner 时，入口按未支持 operation fail closed；不得把消息交给其它插件或共享默认逻辑。

## 3. 现场物理拓扑

一条自动出库产线的滚筒线上有 4 个扫码工位，依次记为点1～点4（现场设备编码 `STATION_SCANn ~ STATION_SCAN(n+3)`，
具体编号由部署配置提供，不在本文固定）。点1与点2之间是可容纳 3～4 个料箱的单向 FIFO 滚筒缓存；料箱经过点1后由
ECS/PLC 控制滚筒线自主步进到点2，WES 不为每个料箱下发从点1进入点2的方向命令。人工出库线在物理构造上与自动线相同，
唯一差异是点2旁挂一个人工工作位，由工人持 PDA 完成拣料，取代自动线上目标机械臂的 `PICK_AND_PUT`。

```mermaid
flowchart LR
    P1["点1 SCAN<br/>进入缓存与 FIFO 顺序证据"] --> BUF["点1→点2<br/>3～4 Bin 自主 FIFO 缓存"]
    BUF -->|滚筒自主步进| P2SCAN["点2 SCAN<br/>身份确认与工作位到达"]
    P2SCAN --> ASK["WES 请求 WMS<br/>当前 Bin 是否有任务"]
    ASK -->|WORK_REQUIRED| P2["点2 停留<br/>PDA 人工拣料<br/>(WMS 内部, 对 WES 黑盒)"]
    ASK -->|NO_WORK| P3
    ASK -->|WAIT / 响应未知| P2SCAN
    P2SCAN -->|条码不可读| P3
    P2 -->|WMS 最终释放决定<br/>WES 指令 point2 释放| P3["点3 SCAN<br/>NG 判定"]
    P3 -->|NG| EXIT["离开本线设备范围<br/>WES 持续管辖至 NGZone 人工接管"]
    P3 -->|正常| P4["点4 SCAN<br/>记录 RETURN_BUFFER"]
    P4 --> RB["RETURN_BUFFER FIFO<br/>outbound.bin.return_batch@v1"]
```

### 3.1 点1与上游缓存：进入证据和 FIFO 顺序

料箱到达点1后，WES 只保存进入本线缓存的设备证据，并按可靠到达顺序冻结点1至点2缓存中的 FIFO 身份顺序。point1 的
扫码结果不表示料箱已经到达人工工作位，也不触发 WES 下发 `MOVE_RIGHT` 或其它逐箱推进命令。滚筒线在 ECS/PLC 的容量和
安全互锁下自主步进；点2释放当前料箱后，FIFO 队首自动补位。

WES 对上游供箱只按已确认的缓存容量、FIFO 顺序和出库合同的准入条件控制；不以软件中的 point2 占用锁代替 ECS/PLC 的滚筒
步进和防撞互锁。缓存已满、顺序或位置不确定时停止新料箱入线，但不干预已经进入缓存的自主 FIFO 推进。

### 3.2 点2：人工工作位（PDA，对 WES 黑盒）

FIFO 队首自动进入 point2 后，point2 SCAN 设备扫码并由 ECS 上报 WES。该扫码和到位证据是当前实际料箱身份及“已到人工工作位”的
权威事实；point1 证据不能替代它。WES 不把扫码 Bin 与计划中的预期 Bin 做错箱比较，只使用实际 `bin_id` 请求 WMS 判断当前是否
存在人工任务。PDA 的呼叫、Cell 分配、拣料确认等内部流程完全由 WMS 负责，WES 不集成、不查询、不持有其中任何字段（对照 §2.2）。

WES 在点2的唯一职责是：

1. 接收并保存 point2 实际扫码和到位事实；
2. 调用 `outbound.manual_bin.work_admission_decide@v1` 请求 WMS 判断当前 `bin_id` 是否有任务；
3. `WORK_REQUIRED` 时保存 WMS 返回的 `task_id`，保持料箱停留并等待 Bin 级最终释放决定；`NO_WORK` 时把该 Bin 标记为正常直通并
   创建 point2 释放命令；`WAIT` 或响应未知时保持 point2 占用并按第 5 节重试；
4. 收到 `WORK_REQUIRED` 对应的完成决定后，把业务结果和释放权限原子绑定到当前活动 Epoch 内正在 point2 等待的唯一
   `BinExecution`；绑定成功后才向 point2
   下发 `MOVE_FORWARD` 释放当前料箱。料箱由滚筒线自动流向点3，后一个料箱自动进入 point2 并重新触发扫码上报。

point2 条码不可读时保留当前 `BinExecution` 和不可读证据，按确定 Bin NG 路径释放至 point3；不得请求 WMS 任务准入。合法且
可识别的任意实际 `bin_id` 都交由 WMS 返回 `WORK_REQUIRED | NO_WORK | WAIT`，本插件不建立错箱分支。

### 3.3 点3：NG 判定

point3 先对当前扫码事实校验 `owner_workline_id` 和 `line_run_epoch_id`。两者匹配当前 WorkLine 与活动 Epoch 时，才读取已经
绑定到当前 `BinExecution` 的确定结果。任一值不匹配或未知时不得静默重绑或进入正常退料，而是把当前物理 Bin 按确定 NG
路径送出：身份可识别时记录 `BIN_DIRECTION_INVALID`，身份不可读时保留预期身份并记录 `BIN_CODE_UNREADABLE`。point4 信任
point3 已完成的单次校验，不重复建立同线身份保护。

| 情形 | NG 结果来源 | 点3动作 |
| --- | --- | --- |
| point2 `WORK_REQUIRED` 且人工任务完成 | 第 5 节完成通知 payload 里的业务结果 | 结果为 NG → `MOVE_LEFT`；结果为 NORMAL → `MOVE_FORWARD` |
| point2 `NO_WORK` 正常直通 | WMS 任务准入决定 | `MOVE_FORWARD` |
| 点2条码不可读/异常且未进入人工业务 | 当前 `BinExecution` 保留的预期 `bin_id` 与不可读码证据 | `MOVE_LEFT` |
| point3 的 WorkLine/Epoch 不匹配或未知 | 当前扫码、不可变 owner/Epoch 与位置冲突证据 | 标记对应 Bin NG 原因并 `MOVE_LEFT` |
| owner/Epoch 匹配，但无可归属的确定业务结果 | 无 | 停止自动推进并进入 `RECONCILING` |

料箱在 point2 形成不可读码证据或在 point3 读取到确定 NG 结果后，仍保留当前 `BinExecution`、位置投影和设备证据。料箱借道下一线
到达现场 `NGZone` 后也不立即关闭；只有操作员在 `NGZone` 扫码并实际取走，WES 才关闭该执行并释放管辖权。NG 出口的 WMS
位置上报与人工接管生命周期复用出库合同，并按原因严格映射：point2 条码不可读使用 `BIN_CODE_UNREADABLE`；point3 的
WorkLine/Epoch 不匹配或未知使用 `BIN_DIRECTION_INVALID`；只有 `outbound.manual_bin.work_completed@v1` 的 `result=NG` 使用
`MANUAL_PICK_NG`。三者不得相互替代。

### 3.4 点4：记录退料队列

点4不做任何判断，只把经过的正常料箱计入本 Epoch 的 `RETURN_BUFFER` FIFO 队尾。WES 按出库合同 §9.2.2 从队首取候选，
调用 `outbound.bin.return_batch@v1` 请求目标货架，创建 `move_bins()` 搬回货架。

## 4. PDA 边界声明

PDA（人工拣料操作终端）是 WMS 侧功能，不属于 WES 集成范围：

- WES 不向 PDA 发送任何请求，也不接收 PDA 的直接回调；
- WES 不知道、也不需要知道点2内部具体拣了哪个 Cell、拣了几次、耗时多久；
- WES 与人工拣料结果的唯一交互点是第 5 节定义的完成通知。

本文对照 [`wes-wms-interface-requirements.md`](../integration/wes-wms-interface-requirements.md) §6「不提供 PDA、
打印和未批准的人工业务接口」这条既有边界：本文不违反该边界，因为 WES 侧确实不提供、不消费任何 PDA 接口，
PDA 全部内部逻辑归属 WMS。

## 5. 新增 operations：任务准入、完成释放与应用结果

### 5.1 `outbound.manual_bin.work_admission_decide@v1`

| 项 | 值 |
| --- | --- |
| 方向 | WES 到 WMS |
| 端点 | `POST {{WMS_BASE_URL}}/api/v1/wes/decisions` |
| 触发条件 | point2 SCAN 和到位证据已可靠保存，且读取到合法实际 `bin_id` |
| 成功响应 | `200 / DECIDED` |

请求信封复用出库合同公共信封（`operation_id + operation + timestamp + data`）：

```json
{
  "operation_id": "<uuid7>",
  "operation": "outbound.manual_bin.work_admission_decide@v1",
  "timestamp": 1788389900000,
  "data": {
    "bin_id": "BIN-001",
    "scanned_at": 1788389899900
  }
}
```

| 字段 | 必填 | 类型/格式 | 说明 |
| --- | --- | --- | --- |
| `data.bin_id` | 是 | 出库合同 Identifier | point2 SCAN 读取的当前实际料箱；WES 不发送预期 Bin，也不在本地做错箱比较 |
| `data.scanned_at` | 是 | positive integer / UTC Unix 毫秒 | point2 有效扫码和到位事实的设备发生时间；不得晚于信封 `timestamp` |

`data` 只允许上述两个必填字段；Identifier、未知字段、`null`、空字符串、错误类型和时间约束复用出库合同的严格规则。条码不可读时
禁止发送请求，按 §3.2 的 Bin NG 路径处理。

`200 / DECIDED` 的 `data` 是严格联合：

| `data.result` | 必填字段 | 禁止字段 | WES 动作 |
| --- | --- | --- | --- |
| `WORK_REQUIRED` | `task_id` | `retry_after_ms` | 原子保存当前 `bin_id + task_id` 关联，保持 point2 占用并允许 WMS/PDA 开始人工操作 |
| `NO_WORK` | 无 | `task_id`、`retry_after_ms` | 原子保存正常直通决定并创建唯一 point2 释放命令 |
| `WAIT` | `retry_after_ms` | `task_id` | 当前料箱停留 point2；到期或新业务事件唤醒后使用新 `operation_id` 重求值 |

`task_id` 复用出库合同 Identifier；`retry_after_ms` 复用出库合同正整数边界。WMS 根据其业务主账判断实际 Bin 当前是否有任务；
WES 不查询 Cell、不验证预期 Bin，也不把 `NO_WORK` 解释为 NG。

原 `operation_id` 和原请求重放必须返回首次完整响应。同 ID 内容漂移返回 `409 / CONFLICT`；响应未知或
`503 / UNAVAILABLE` 时，WES 保持当前 Bin、point2 占用和原 operation identity 重试，不以新 ID 猜测结果。只有已收到并可靠保存
`WORK_REQUIRED | NO_WORK | WAIT` 才能推进相应状态；HTTP 响应本身不表示任何设备动作已发生。

### 5.2 `outbound.manual_bin.work_completed@v1`

| 项 | 值 |
| --- | --- |
| 方向 | WMS 到 WES |
| 端点 | `POST {{WES_BASE_URL}}/api/v1/wms/events` |
| 触发条件 | WMS 已在同一持久化事务中提交该 Bin 相关的 PDA 子任务和业务结果，并形成 Bin 级最终释放决定 |
| 首次成功响应 | `202 / RECEIVED` |

请求信封复用出库合同公共信封（`operation_id + operation + timestamp + data`），`data` 严格字段如下：

```json
{
  "operation_id": "<uuid7>",
  "operation": "outbound.manual_bin.work_completed@v1",
  "timestamp": 1788390000000,
  "data": {
    "task_id": "PICK-20260902-001",
    "bin_id": "BIN-001",
    "result": "NORMAL",
    "completed_at": 1788389999000
  }
}
```

| 字段 | 必填 | 类型/格式 | 说明 |
| --- | --- | --- | --- |
| `data.task_id` | 是 | 出库合同 Identifier | 必须等于 point2 `WORK_REQUIRED` 响应冻结的 PickingTask |
| `data.bin_id` | 是 | 出库合同 Identifier | 必须等于该 `WORK_REQUIRED` 请求中的实际扫码 Bin；应用时还必须命中当前活动 Epoch 内正在 point2 等待的唯一 `BinExecution` |
| `data.result` | 是 | enum | `NORMAL \| NG`；`NORMAL` 授权离开点2进入正常回库路径，`NG` 授权离开点2进入 NG 路径 |
| `data.completed_at` | 是 | positive integer / UTC Unix 毫秒 | 人工拣料任务形成最终决定的时间；不得早于该 Bin 的 point2 `work_admission.scanned_at`，也不得晚于同一信封的 `timestamp` |

`data` 只允许上述四个字段，全部必填；不接受未知字段、`null`、空字符串、错误类型或枚举外取值。`task_id` 和
`bin_id` 复用出库合同 §4.4 的 `[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}` 约束，不得为人工线放宽或定义别名。
`completed_at` 必须保存到第 9.1 节的 per-Bin 最终结果记录，只用于审计和对账；不得用远端业务时间决定消息处理顺序、
DeviceCommand deadline 或自动超时。

WES 收到后先把原始消息持久化为 `InboundEvidence`，再按出库合同公共协议 ACK。全局唯一的 `operation_id` 是消息重试身份，完整
请求内容用于检测同一 ID 的内容冲突；
`task_id + bin_id` 是本 operation 的业务终态身份，同一业务身份只能形成一个最终结果，后到消息不得覆盖。

`work_completed` 同时表示业务完成和物理释放授权，不是“PDA 步骤已操作”的进度通知。WMS 不得在相关子任务与业务
结果持久化事务提交前发送该消息。WES 返回 `202 / RECEIVED` 只证明 evidence 已可靠接收，不证明料箱已移动；只有该决定成功
应用到当前点2的活动执行时，WES 才能创建放行设备命令。

WES 可靠保存 `WORK_REQUIRED` 后，以部署配置的人工处理 SLA 监测完成通知等待时间。超过阈值只触发告警并
停止新料箱进入本线；当前 Bin 保持 `WAITING_EXTERNAL`、point2 占用和原 `BinExecution`，已进入点1至点2缓存的料箱保持原 FIFO
顺序。超时不得自动释放、改判 NG、关闭执行或创建新命令身份。收到可关联的完成通知后仍按同一执行继续处理。

WMS 的内部人工拣料原因不跨系统传输；`result=NG` 已是本 operation 的完整业务决定。WES 将该决定持久化为人工拣料 NG
证据，并在物理到达 `NG_EXIT` 后把既有 `outbound.bin.ng_exit_report@v1` 的 `reason_code` 固定映射为 `MANUAL_PICK_NG`。

应用 evidence 时，人工业务模块必须先按 `(task_id, bin_id)` 查询既有最终结果。若相同 `result` 已成功应用，新 evidence 直接标记为
已应用的业务幂等 no-op，不再检查料箱是否仍在 point2，也不再创建设备命令；若既有结果不同，则 evidence 与受影响执行进入
`RECONCILING`。只有尚无最终结果的首次应用才继续在同一事务中锁定活动 Epoch、当前唯一活动 `BinExecution` 和 point2 位置，确认
其 `task_id + bin_id` 与消息一致且仍在等待 WMS 结果，验证通过后保存结果并创建设备命令。首次消息早到或晚到、找不到唯一活动
执行、执行不在 point2 或 Epoch 已结束时进入 `RECONCILING`，不得暂存后自动补绑，也不得下发默认方向命令。

### 5.3 完成通知的接收、重试与应用边界

`outbound.manual_bin.work_completed@v1` 复用出库合同 §4 的公共接收语义：

| 情形 | HTTP / `code` | 处理 |
| --- | --- | --- |
| 新 `operation_id` 且严格 DTO 合法 | `202 / RECEIVED` | 可靠持久化 evidence；不表示业务已应用或料箱已移动 |
| 原 `operation_id` 和完整请求重放 | `200 / DUPLICATE` | 返回第一次响应的 `timestamp + data` |
| 原 `operation_id`、但完整请求不同 | `409 / CONFLICT` | `data.reason_code=IDEMPOTENCY_CONFLICT` |
| 严格 DTO 或公共信封不合法 | 出库合同 §4 的 `400 / 413 / 422` 联合 | 不进入业务应用 |
| 当前无法可靠持久化 | `503 / UNAVAILABLE` | WMS 使用原 `operation_id` 和原请求重试 |

共享 HTTP 入口不读取人工线的 Epoch、点位或 `BinExecution` 来同步判定业务冲突。换新 `operation_id` 的合法消息仍先返回
`202 / RECEIVED`；`task_id + bin_id` 单终态和当前执行状态由人工业务模块在异步应用 evidence 时判定。

### 5.4 `outbound.manual_bin.completion_apply_report@v1`

| 项 | 值 |
| --- | --- |
| 方向 | WES 到 WMS |
| 端点 | `POST {{WMS_BASE_URL}}/api/v1/wes/facts` |
| 触发条件 | 一条 `work_completed` evidence 首次进入 `APPLIED` 或 `RECONCILING`，以及后续人工对账使状态发生确定变化 |
| 首次成功响应 | `200 / RECORDED` |

`data` 是严格条件联合：

| 字段 | `APPLIED` | `RECONCILING` | 说明 |
| --- | --- | --- | --- |
| `completion_operation_id` | 必填 | 必填 | 原 `work_completed.operation_id` |
| `task_id` / `bin_id` | 必填 | 必填 | 原完成决定的业务身份 |
| `apply_revision` | 必填 | 必填 | 从 1 开始严格递增的应用状态修订 |
| `apply_result` | `APPLIED` | `RECONCILING` | 本次确定应用状态 |
| `reason_code` | 禁止 | 必填 | `RESULT_CONFLICT \| FIRST_COMPLETION_OUT_OF_WINDOW \| BIN_EXECUTION_NOT_UNIQUE \| POINT2_BINDING_MISMATCH \| EPOCH_NOT_ACTIVE \| COMPLETED_AT_INVALID \| DEVICE_COMMAND_IDENTITY_CONFLICT` |
| `occurred_at` | 必填 | 必填 | WES 形成该应用状态的 UTC Unix 毫秒时间，不晚于信封 `timestamp` |

`APPLIED` 只证明最终结果已持久化且 point2 释放 DeviceCommand 已在同一事务创建，不证明命令已发送、ECS 已接纳或料箱已移动。
`RECONCILING` 不撤销 WMS 已形成的业务结果，也不授权 WMS 重发不同结果或 WES 换身份重创命令；双方按相同
`completion_operation_id` 对账。人工对账形成后续确定状态时使用新的 operation identity 和下一连续 `apply_revision` 上报，
不得覆盖历史修订或跳号。

每个报告修订使用稳定 `operation_id` 可靠发送；响应未知或 `503 / UNAVAILABLE` 时使用原 ID 和原内容重试。WMS 对同一报告 ID
同内容返回 `DUPLICATE`、内容漂移返回 `409 / CONFLICT`，并按 `completion_operation_id + apply_revision` 原子保存状态和告警。

## NOT in scope

- WES 不集成 PDA 的任何接口（第 4 节）；
- WES 不使用自动线 `outbound.bin.work_plan@v1`：`outbound.manual_bin.work_admission_decide@v1` 只返回当前实际 Bin 是否有
  人工任务，不返回 Cell 或 PDA 工作内容；
- point2 不建立“预期 Bin 与实际 Bin”错箱分支；合法实际 Bin 是否有任务完全由 WMS 返回 `WORK_REQUIRED | NO_WORK | WAIT`；
- `RETURN_BUFFER` 在停线/切换时选择排空货架面的 decision wire 已记录在 `TODOS.md`，不在本期实现；该 wire 获批前，非空
  `RETURN_BUFFER` 的停线/切换保持 Epoch 活动并禁止自动换面、换架或退箱；
- WES 不维护跨 WorkLine 的料箱 NG 状态查询或同步机制：NG Bin 的执行身份、位置投影和 NG 证据沿既有生命周期持续到整线
  `NGZone` 人工扫码实际取走，不由下一条 WorkLine 查询或复制；
- 本文不新增第二个 NGZone 上报 operation：NG 出口位置上报和人工接管继续复用出库合同，人工 NG 结果固定映射为 `MANUAL_PICK_NG`；
- 不新增第二套 Transport、Device、Evidence、Confirmation 或插件 runtime；
- 不复用出库合同以外的其它业务字段表达；
- 不提供旧接口、兼容字段或旧业务数据迁移；
- 供应商私有 ECS/PLC payload、滚筒步进算法和硬件互锁由设备侧拥有，本文只使用统一 SCAN/命令证据；
- 本机 Mock、HTTP ACK、健康检查和自动化测试不等于真实设备、现场流程或 WMS 业务验收。

## 7. 正式实施前确认项

| ID | 确认项 | 主要责任方 | 状态 |
| --- | --- | --- | --- |
| C1 | WMS、WES 联合审批本文，状态由 `ReviewRequired` 变为 `Approved` | 联合 | APPROVED（初审） |
| C2 | 联合确认 `outbound.manual_bin.work_admission_decide@v1` 的严格请求/响应联合、WMS 任务判断、PDA 开放边界、幂等和重试语义 | WMS、WES | APPROVED（初审） |
| C3 | 联合确认 `outbound.manual_bin.work_completed@v1` 的严格 DTO、Bin 级最终释放授权、消息幂等、业务单终态、异步应用冲突和错误响应联合 | WMS、WES | APPROVED（初审） |
| C4 | 联合确认 `outbound.manual_bin.completion_apply_report@v1` 的严格条件联合、修订、可靠发送和 WMS 告警责任 | WMS、WES | APPROVED（初审） |
| C5 | 确认现场扫码设备（`STATION_SCAN*`）条码不可读/异常事件的具体载荷形态，供点2/点3实现引用 | ECS、WES | APPROVED（初审） |
| C6 | 确认人工出库线现场设备编码与四个扫码点位的绑定关系（部署配置，不写入本文业务字段） | WES、现场 | APPROVED（初审） |
| C7 | 确认 NGZone 现场人工扫码取走 SOP，以及扫码前保持 `BinExecution`、位置投影和管辖权的责任边界 | 现场、WMS | APPROVED（初审） |

C1～C7 已于 2026-09-03 通过初审，本文构成当前基线的代码实施授权。后续发现细节需要优化时，应通过合同变更评审更新本文及对应机器合同；在变更获批前，不静默改变当前已批准语义。

## 8. 实施验收与测试所有权

本文修订是人类可读合同，不为文档正文新增 pytest。C1～C7 获批后，生产实现必须按下列唯一测试 owner 与合同分支完成验收。

### 8.1 WMS wire 合同

| 测试 owner | 必须覆盖 |
| --- | --- |
| `tests/contracts/wms_adapter/test_inbound_wire_acceptance.py` | `NORMAL` 与 `NG` 合法 DTO；未知字段、`null`、空字符串、错误类型、枚举外值和 `completed_at > timestamp` 全部拒绝 |
| `tests/contracts/wms_adapter/test_inbound_openapi.py` | OpenAPI 只暴露四个必填 data 字段、封闭对象、字段约束和完整 ACK/错误响应联合 |
| `tests/contracts/wms_adapter/` 的 Event handler 合同测试 | 显式静态 owner 路由；owner 未绑定 fail closed；新 ID 持久化后 `202`；同 ID 同内容 `200`；同 ID 不同内容 `409`；持久化失败 `503` 且无虚假 ACK |
| `tests/integration/wms_adapter/test_manual_bin_event_receipts.py` | 使用真实 PostgreSQL 验证并发重放只有一个收据 owner、digest 冲突、evidence/ACK 事务回滚与失败后原 identity 可重试 |
| `tests/contracts/wms_adapter/test_outbound_ng_exit_wire.py` | 公共 `ng_exit_report` 接受 `MANUAL_PICK_NG`；该原因要求 `bin_id` 且禁止 `expected_bin_id`；其他 reason 的条件字段保持原合同 |
| `tests/contracts/wms_adapter/test_outbound_openapi.py` | OpenAPI 的 `reason_code` 闭集包含 `MANUAL_PICK_NG`，并准确表达各 reason 的条件联合，不把插件业务判断写入 schema |
| `workline_plugins/manual_bin_processing/tests/test_work_admission.py` | point2 合法实际 Bin 构造严格两字段请求；`WORK_REQUIRED/NO_WORK/WAIT` 条件联合；不发送预期 Bin；条码不可读时零请求；`NO_WORK` 是正常直通而非 NG |
| `workline_plugins/manual_bin_processing/tests/integration/test_work_admission_postgresql.py` | 扫码到位事实与 `WmsConfirmation` 原子声明；原 ID 恢复响应未知；`WORK_REQUIRED` 冻结返回的 `task_id`；`NO_WORK` 最多一个 point2 释放命令；`WAIT` 零命令且新 ID 重求值 |
| `workline_plugins/manual_bin_processing/tests/test_completion_apply_report.py` | `APPLIED/RECONCILING` 严格条件联合、封闭 reason code、连续 revision；`APPLIED` 不冒充设备发送或物理移动完成 |
| `workline_plugins/manual_bin_processing/tests/integration/test_completion_apply_delivery_postgresql.py` | 应用状态与可靠报告义务原子声明；响应未知保留原 ID；WMS ACK 闭合当前 revision；对账后的下一 revision 不覆盖历史 |

前六项核心测试不导入 `manual_bin_processing` 插件，只证明共享 completion ingress、可靠接收、路由与 NG wire；任务准入与应用结果
两类 outbound operation 的请求数据及其因果恢复由后四项插件测试承接，底层 HTTP/JSON 继续复用共享 `WmsClient`，不在插件内重造传输。

`tests/runtime/execution/test_wms_confirmation_service.py` 负责共享 `WmsConfirmation` 回归：既有
`material_execution_id` 消费者行为不变；新增 `bin_execution_id` 后数据库与 Service 均要求两种关联恰好一个非空；相同
operation identity 和 payload 保持幂等，载荷冲突、发送未知和原 identity 恢复语义不变。对应 migration 必须在干净 PostgreSQL
验证现有料盘行升级、新 Bin 行写入、双空和双填约束拒绝。

### 8.2 人工业务决策与 evidence 应用

| 测试 owner | 必须覆盖 |
| --- | --- |
| `workline_plugins/manual_bin_processing/tests/test_work_completed_decision.py` | 纯 Decision 只依赖 SDK 不可变 Fact/Snapshot；`NORMAL` 和 `NG` 各返回封闭决策，不读数据库、HTTP、Celery 或 Repository |
| `workline_plugins/manual_bin_processing/tests/test_external_wait_policy.py` | `WORK_REQUIRED` 保存后启动人工处理 SLA；阈值内保持 `WAITING_EXTERNAL`；超时只告警并停止新入线，不释放 point2、不改 NG、不改 FIFO、不换执行或命令身份 |
| `workline_plugins/manual_bin_processing/tests/test_work_completed_application.py` | `task_id + bin_id` 命中冻结的 `WORK_REQUIRED`、唯一活动 Epoch、point2 当前料箱和 `BinExecution` 后，原子保存 `completed_at` 与结果并只创建一个 point2 `MOVE_FORWARD` 释放命令；`completed_at < work_admission.scanned_at` 进入 `RECONCILING`；已成功应用后换新 ID 的同结果消息即使料箱已离开 point2 仍为 no-op；冲突结果以及首次消息早到、晚到、错点位、无唯一执行或 Epoch 已结束均进入 `RECONCILING` 且零命令 |
| `workline_plugins/manual_bin_processing/tests/integration/test_work_completed_postgresql.py` | 真实 PostgreSQL 下按固定顺序锁定 Epoch、Bin execution 和点2位置；并发同结果最多一个 `MANUAL_BIN_POINT2_RELEASE` 命令；并发冲突结果 fail closed；任一写入失败时整个业务应用回滚 |

核心 `tests/runtime/` 继续只证明 `InboundEvidence`、`BinExecution`、`PositionProjection`、`DeviceCommand` 和静态绑定的中立不变量，
不导入人工插件，不代替上述业务测试。

### 8.3 扫码、物理分支与生命周期

| 测试 owner | 必须覆盖 |
| --- | --- |
| `workline_plugins/manual_bin_processing/tests/test_scan_decisions.py` | point1 只记录缓存进入和 FIFO 顺序且零方向命令；point2 对任意合法实际 Bin 请求 WMS 任务准入，不比较预期 Bin；条码不可读保留执行并释放至 point3 NG 分支；point3 的 owner/Epoch 不匹配或未知映射对应 Bin NG 并只创建 `MOVE_LEFT`，匹配时按 `NO_WORK` 或已绑定的 `NORMAL/NG` 创建方向命令；缺业务结果进入 `RECONCILING`；point4 不重复校验；同一阶段的重复扫码只取得原 DeviceCommand，载荷漂移时 fail closed |
| `workline_plugins/manual_bin_processing/tests/test_bin_lifecycle.py` | `NORMAL` 经点4加入 `RETURN_BUFFER`；退料严格从 FIFO 队首取连续前缀；到达 `NG_EXIT` 后按来源分别上报 `BIN_CODE_UNREADABLE`、`BIN_DIRECTION_INVALID` 或 `MANUAL_PICK_NG`；WMS ACK 和到达 `NG_EXIT` 都不提前关闭 `BinExecution`；只有 `NGZone` 操作员扫码并实际取走才关闭 |
| `workline_plugins/manual_bin_processing/tests/integration/test_manual_bin_flow_postgresql.py` | 真实 PostgreSQL 下验证 FIFO 并发不越过未闭合队首、冲突分支零命令、NG 执行在人工接管前持续占有管辖权，以及最终关闭与资源释放原子化 |

上述自动化测试只证明 WES 决策、事务和命令边界；不把 Mock 命令成功当作真实物理完成，也不代替 ECS/设备一致性验收与现场业务验收。

### 8.4 真实 worker 端到端装配

`workline_plugins/manual_bin_processing/tests/e2e/test_business_loop.py` 必须使用真实 PostgreSQL、broker 和 Celery worker，
安装并通过宿主静态 composition 激活真实 `manual_bin_processing` 插件，至少覆盖：

- point2 扫描实际 Bin → `work_admission_decide`；`WORK_REQUIRED` 停留并开放人工操作，`NO_WORK` 正常直通，`WAIT` 停留重求值，响应未知时用原 identity 重试；
- `NORMAL`：公共 WMS Event 入口 → evidence → worker → 插件应用 → 唯一 DeviceCommand → 正常返库路径；
- `NG`：同一公共入口和 worker 链路 → `MANUAL_PICK_NG` 证据 → NG 物理路径，不提前关闭执行；
- completion evidence 的 `APPLIED/RECONCILING` 均形成可靠 `completion_apply_report`，WMS ACK 丢失时用原 identity 重试；
- 原 `operation_id` 重放与换新 ID 的同结果业务重复均不产生第二个 DeviceCommand；
- worker 在 evidence 已提交后重启，仍使用原 evidence 和原执行身份继续收敛，不丢消息、不换身份重发。

ECS 在该 E2E 中使用 WES 公共 wire mock，不引入供应商私有协议。该绿灯只证明应用、队列和装配路径，不表示真实设备或现场业务验收通过。

### 8.5 代码路径与现场流程覆盖图

下图的 `[GAP]` 表示当前仅有合同和插件骨架，尚无 Phase 12 生产实现及对应绿灯；箭头后的章节是已指定的实施测试 owner。

```text
CODE PATHS                                              USER / ONSITE FLOWS
[+] WMS work admission                                  [+] point2 任务判断
  ├── [GAP→8.1] strict request / response union           ├── [GAP→8.1/8.4] WORK_REQUIRED -> 停留并开放 PDA
  ├── [GAP→8.1] original identity retry                   ├── [GAP→8.1/8.4] NO_WORK -> 正常直通
  ├── [GAP→8.1] WORK_REQUIRED / NO_WORK / WAIT            ├── [GAP→8.1/8.4] WAIT -> 停留后新 ID 重求值
  └── [GAP→8.1] response conflict -> RECONCILING          └── [GAP→8.1/8.4] 响应未知 -> 原 ID 重试

[+] Shared WMS ingress                                  [+] WMS 提交最终释放决定
  ├── [GAP→8.1] NORMAL DTO                              ├── [GAP→8.1/8.4] NORMAL 首次发送
  ├── [GAP→8.1] NG DTO                                  ├── [GAP→8.1/8.4] NG 首次发送
  ├── [GAP→8.1] unknown / null / empty / type / enum     ├── [GAP→8.1/8.4] ACK 丢失后原 ID 重试
  ├── [GAP→8.1] completed_at > timestamp                 └── [GAP→8.1]     同 ID 内容漂移被拒绝
  ├── [GAP→8.1] static owner / owner missing
  ├── [GAP→8.1] new ID -> 202
  ├── [GAP→8.1] same ID + same body -> 200
  ├── [GAP→8.1] same ID + different body -> 409
  └── [GAP→8.1] persistence failure -> 503

[+] Completion apply report                             [+] WMS 观察异步应用
  ├── [GAP→8.1] APPLIED / RECONCILING union               ├── [GAP→8.1/8.4] APPLIED 不冒充物理完成
  ├── [GAP→8.1] closed reason_code                        ├── [GAP→8.1/8.4] RECONCILING 触发双方对账
  ├── [GAP→8.1] monotonic apply_revision                  └── [GAP→8.1/8.4] ACK 丢失后原 ID 重试
  └── [GAP→8.1] report response conflict

[+] Plugin evidence application                          [+] point1→point2 自主 FIFO
  ├── [GAP→8.2] bind point2 execution + NORMAL            ├── [GAP→8.3] point1 仅记录进入与顺序
  ├── [GAP→8.2] bind point2 execution + NG                ├── [GAP→8.3] point2 实际 Bin 请求 WMS 判断
  ├── [GAP→8.2] new ID + same result -> applied no-op      └── [GAP→8.3] point2 不可读进入 Bin NG
  ├── [GAP→8.2] completed_at before scan -> RECONCILING
  ├── [GAP→8.2] conflicting result -> RECONCILING
  ├── [GAP→8.2] first early / late / no unique execution   [+] 点3到最终物理去向
  ├── [GAP→8.2] wrong point / ended epoch                  ├── [GAP→8.3/8.4] NORMAL -> 点4 -> RETURN FIFO
  ├── [GAP→8.2] concurrent same result -> one command      ├── [GAP→8.3/8.4] 三类 NG -> NG_EXIT -> 对应原因
  └── [GAP→8.2] concurrent conflict -> fail closed          ├── [GAP→8.3] owner/Epoch 异常按 Bin NG 送出
                                                          ├── [GAP→8.3] owner/Epoch 匹配但缺结果时停止推进
                                                          ├── [GAP→8.2] 人工 SLA 超时只告警并停止新入线
                                                          ├── [GAP→8.3] ACK 或到达 NG_EXIT 不关闭执行
[+] Runtime / composition                                └── [GAP→8.3] NGZone 扫码并实际取走才关闭
  ├── [GAP→8.4] public ingress -> broker -> real worker
  ├── [GAP→8.4] installed static plugin -> DeviceCommand   [+] 故障与恢复
  ├── [GAP→8.4] duplicate delivery -> no second command     ├── [GAP→8.4] worker 在 evidence 提交后重启
  └── [GAP→8.4] worker restart -> same identity              └── [GAP→8.2/8.3] 异常时保留 identity/证据/管辖权
```

新功能当前实现覆盖为 0（尚未进入 Task 2～7）；第 8.1～8.4 已为图中每个分支指定测试 owner。既有核心测试的绿灯只能复用为基础不变量证据，不计为人工线业务分支已覆盖。

## 9. 性能与并发边界

### 9.1 Bin 级最终结果的有界查找

Task 2 必须在 `manual_bin_processing` 业务所有权内建立一条窄的 per-Bin 最终结果记录，至少显式保存：

- `task_id`；
- `bin_id`；
- `result`；
- WMS 形成最终决定的 `completed_at`；
- 首次成功应用的 `source_evidence_id`；
- 当时绑定的 `bin_execution_id`。

数据库必须使用 `(task_id, bin_id)` 唯一约束直接保证单终态，并通过该唯一索引完成重复与冲突查找。不得扫描
`InboundEvidence.normalized_payload` JSON 重建当前业务状态，不得把人工任务字段加入共享 `BinExecution`，也不为该单行索引查询增加缓存。

该记录的 SQLModel、Repository 和业务查询位于 `workline_plugins/manual_bin_processing/` 应用层。建表、唯一约束和索引仍通过根仓库
`migrations/versions/` 的单一 Alembic revision 交付；迁移工具显式登记插件模型 metadata，但生产 `src/` 不导入具体插件。宿主只在
静态 composition 安装该插件时注入数据库 Session、基础 Service 端口和 Repository 依赖。migration 及插件模型路径必须同步加入
`docs/architecture/heavy-test-impact.toml` 的精确 mapping，并在干净临时 PostgreSQL 逻辑库验证 base → head。

### 9.2 有界锁事务与外部 I/O

evidence 的业务应用事务只允许数据库操作：

1. 复用既有 Repository/Service 的固定锁顺序，依次围栏当前 Epoch、当前 `BinExecution` 和当前位置；
2. 插入或锁定第 9.1 节的 per-Bin 最终结果记录，完成业务幂等或冲突判定；
3. 在同一事务中更新 evidence 并创建唯一 `DeviceCommand`；
4. 提交后才通过既有派发入口唤醒命令执行。

锁事务内禁止 ECS/WMS HTTP、broker 发布或其它外部 I/O。远程调用必须由既有可靠 `DeviceCommand`/worker 链路在提交后执行。
不得拆成“先保存释放授权、后创建命令”的两个业务事务，也不得为缩短表面延迟而在锁内直接发送物理命令。

### 9.3 DeviceCommand 物理义务身份

人工线的命令幂等身份绑定到 `BinExecution + 物理阶段`，不绑定可能重复产生的扫码事件：

| 物理义务 | `execution_ref_type` | `execution_ref_id` |
| --- | --- | --- |
| point2 释放当前料箱 | `MANUAL_BIN_POINT2_RELEASE` | 当前稳定 `bin_execution_id` |
| point3 执行 NG/正常分流 | `MANUAL_BIN_POINT3_ROUTE` | 当前稳定 `bin_execution_id` |

上述身份与既有 `line_run_epoch_id + device_code` 共同构成核心 DeviceCommand 幂等范围。同一身份和相同载荷只取得原命令；同一身份
载荷不同是幂等冲突，受影响执行进入 `RECONCILING`。重复 ECS 扫码、重复到位 evidence 或重复 WMS 完成消息不得派生新的
`execution_ref_id`。命令可能已送达或结果未知时继续保留原命令身份等待权威终态，不换身份重发。

### 9.4 `WmsConfirmation` 中立 Bin 关联

现有 `WmsConfirmation.material_execution_id` 改为可空，并新增可空 `bin_execution_id` 外键；数据库 CHECK 要求两者恰好一个非空。
既有料盘 confirmation 数据和行为保持不变。`outbound.manual_bin.work_admission_decide@v1` 与
`outbound.manual_bin.completion_apply_report@v1` 都关联原 `BinExecution`，复用现有 operation identity、payload digest、deadline、
派发 claim、响应 evidence 和结果未知恢复语义。

该共享模型不得增加 plugin key、人工结果、point2 或 PDA 字段；具体 operation payload 由人工线插件一次性冻结后交给中立可靠对象。
实现需要一条根 Alembic migration、现有料盘消费者回归、Bin 关联约束测试和精确 HEAVY mapping，不能通过插件专用 outbox 或直接
HTTP 绕开。

## What already exists

| 既有能力 | 本计划的处理 |
| --- | --- |
| `WmsClient` 与严格 HTTP/JSON 边界 | 直接复用；插件只提供 operation DTO 与解释，不重造传输 |
| `InboundEvidence`、冲突证据和持久化后 ACK | 直接承接 `work_completed`；共享入口不读取人工业务状态 |
| `WmsConfirmation` 可靠派发与结果恢复 | 复用生命周期，只做 `material_execution_id | bin_execution_id` 中立二选一扩展 |
| `BinExecution`、`PositionProjection`、`LineRunEpoch` | 继续承载物理身份、位置和 Epoch 围栏；不塞入 PDA/人工任务字段 |
| `DeviceCommand`、统一 ECS Adapter、ACK/CALLBACK | 直接复用；按 `BinExecution + 物理阶段` 提供稳定命令身份 |
| `outbound.bin.return_batch@v1` 与 `RETURN_BUFFER` FIFO | 正常运行直接复用；停线/切换排空 decision 留在 `TODOS.md` |
| `outbound.bin.ng_exit_report@v1` 与 NGZone 生命周期 | 复用 wire，只扩展并测试 `MANUAL_PICK_NG` 枚举分支 |
| `manual_bin_processing` 插件骨架 | 在原包内补齐模型、Decision、应用与测试；不新建动态 runtime 或 registry |

## 10. Failure modes

| 新代码路径 | 生产失败方式 | 测试 owner | 处理与用户可见性 |
| --- | --- | --- | --- |
| point1 缓存进入 | 事件丢失或 FIFO 顺序不确定 | `test_scan_decisions.py`、PostgreSQL flow | 停止新入线并告警；不干预已在滚筒缓存中的物理步进，明确可见 |
| point2 扫码 | 条码不可读 | `test_scan_decisions.py`、`test_bin_lifecycle.py` | 保留执行证据，以 `BIN_CODE_UNREADABLE` 单次释放至 NG，明确告警 |
| 任务准入请求 | 请求可能已送达但响应未知 | `test_work_admission_postgresql.py` | point2 保持占用，用原 operation identity 重试；Confirmation 状态与告警可见 |
| 任务准入响应 | `WAIT` 长期持续或响应结构非法 | `test_work_admission.py`、E2E | `WAIT` 用新 ID 按期重求值；非法响应进入对账，均不释放料箱 |
| `WORK_REQUIRED` 外部等待 | 人工处理超过 SLA | `test_external_wait_policy.py` | 只告警并停止新入线；当前 Bin 与上游 FIFO 不改向、不改身份 |
| `NO_WORK` 直通 | 并发重放创建两条释放命令 | `test_work_admission_postgresql.py` | 稳定 `MANUAL_BIN_POINT2_RELEASE` 身份保证最多一条，冲突 fail closed |
| completion ingress | DTO 非法或 evidence 无法落库 | 核心 wire、receipt integration | 返回确定 4xx 或 `503`；不产生虚假 `202`，WMS 可见 |
| completion 应用 | 首次消息早到/晚到、结果冲突或绑定不唯一 | `test_work_completed_application.py` | 零方向命令，进入 `RECONCILING` 并可靠发送 apply report |
| completion 重放 | 已成功应用后料箱已离开 point2 | `test_work_completed_application.py` | 相同业务结果为 no-op；不同结果进入对账，不重复命令 |
| DeviceCommand | ACK/结果未知或重复扫码 | scan/application integration | 保留原命令身份和资源围栏，等待权威终态；禁止换 ID 重发 |
| point3 分流 | owner/Epoch 不匹配或未知 | `test_scan_decisions.py` | 按 `BIN_DIRECTION_INVALID` Bin NG 单次送出；point4 不重复校验 |
| point3 正常路径 | owner/Epoch 匹配但缺少确定业务处置 | `test_scan_decisions.py` | 停止自动推进并进入 `RECONCILING`，不猜测默认方向 |
| NG 出口与人工接管 | WMS 已 ACK 但实物尚未被取走 | `test_bin_lifecycle.py`、E2E | 保留 `BinExecution`、位置和管辖权，直到 NGZone 扫码并实际取走 |
| 停线/切换排空 | `RETURN_BUFFER` 非空且排空 wire 未获批 | 合同/运行态门禁 | Epoch 保持活动并阻止自动换面、换架或退箱；P1 TODO 对现场可见 |

上述路径均具有指定测试、fail-closed 处理和可观察状态；本次 Review 未留下“无测试、无处理且静默”的 critical gap。

## 11. Worktree parallelization strategy

| Step | Modules touched | Depends on |
| --- | --- | --- |
| T1 合同与机器合同冻结 | `docs/contracts/`、WMS/WES OpenAPI | — |
| T2 模型与单一 migration | `src/app/execution/`、`workline_plugins/manual_bin_processing/application/`、`migrations/` | T1 |
| T3 共享 wire 与任务准入 | `src/app/wms_adapter/`、`tests/contracts/wms_adapter/`、插件 WMS request 层 | T1、T2 |
| T4 completion 决策与应用 | 插件 Decision/application、shared ingress tests | T1、T2 |
| T5 扫码、分流、NG 与 RETURN 生命周期 | 插件 scan/lifecycle、plugin integration tests | T1、T2 |
| T6 静态装配和真实 worker E2E | 宿主 composition、插件入口、plugin E2E、HEAVY mapping | T3、T4、T5 |

Lane A：T1 → T2（顺序执行；共同冻结 schema 与唯一 migration）。

Lane B：T3（T2 后独立处理共享 WMS wire）。

Lane C：T4 → T5（T2 后顺序执行；共享插件 application 与测试 fixture）。
T2 完成后可并行启动 Lane B 与 Lane C；两者合并并通过聚焦测试后，再顺序执行 T6。

冲突标记：T2、T4、T5 都涉及插件 application，必须顺序；所有 migration、`plugin.py`、composition 和
`heavy-test-impact.toml` 都由 T6 前的单一 owner 收口，不允许多个 worktree 并发编辑。

实施时只在非显然路径保留短 ASCII 注释：`application/work_admission.py` 标注
`SCAN2 → WMS decision → HOLD/RELEASE`；`application/work_completed.py` 标注“先查业务终态，再按 Epoch → BinExecution → point2
位置锁定”的事务顺序；`src/app/execution/models/wms_confirmation.py` 标注料盘/料箱关联 XOR。不在简单 DTO 或静态映射旁重复合同正文。

## Implementation Tasks

Synthesized from this review's findings. Each task derives from a specific finding above. Run with Claude Code or Codex; checkbox as you ship.

- [ ] **T1 (P1, human: ~1d / CC: ~2h)** — 合同 — 联合冻结三条人工 Bin operation 与机器合同
  - Surfaced by: Architecture / Outside Voice — point2 实际 Bin 任务准入、completion 异步应用反馈和 NG enum 必须形成闭合 wire。
  - Files: `docs/contracts/wms-manual-outbound-picking-integration-requirements.md`、`docs/contracts/wms-outbound-picking-task-integration-requirements.md`、`src/app/wms_adapter/` 的 OpenAPI schema。
  - Verify: C1～C7 均为 `APPROVED`；运行 WMS wire/OpenAPI 聚焦测试和 `git diff --check`。
- [ ] **T2 (P1, human: ~1.5d / CC: ~3h)** — 数据层 — 建立 Bin 可靠义务关联与人工结果唯一记录
  - Surfaced by: Performance / Claude — 禁止 JSON 扫描，且现有 `WmsConfirmation` 只能关联料盘。
  - Files: `src/app/execution/models/wms_confirmation.py`、execution Repository/Service、`workline_plugins/manual_bin_processing/src/manual_bin_processing/application/`、`migrations/versions/`、`docs/architecture/heavy-test-impact.toml`。
  - Verify: WmsConfirmation 料盘/料箱 XOR、`(task_id, bin_id)` 唯一约束、干净 PostgreSQL base → head migration 与相关回归通过。
- [ ] **T3 (P1, human: ~1.5d / CC: ~3h)** — point2 准入 — 实现实际 Bin 的 `WORK_REQUIRED | NO_WORK | WAIT` 决策
  - Surfaced by: 用户现场澄清 — point2 不做错箱比较，只向 WMS 确认当前料箱是否有任务。
  - Files: `src/app/wms_adapter/`、`workline_plugins/manual_bin_processing/src/manual_bin_processing/`、对应 contracts/integration tests。
  - Verify: `uv run pytest workline_plugins/manual_bin_processing/tests/test_work_admission.py workline_plugins/manual_bin_processing/tests/integration/test_work_admission_postgresql.py -q`。
- [ ] **T4 (P1, human: ~2d / CC: ~4h)** — completion — 实现最终结果应用、稳定释放命令与 apply report
  - Surfaced by: Code Quality / Claude — 业务幂等优先级、`completed_at`、异步失败反馈和 DeviceCommand identity 必须闭合。
  - Files: `src/app/wms_adapter/` completion ingress、插件 Decision/application、point2 release、apply-report tests。
  - Verify: `uv run pytest tests/contracts/wms_adapter workline_plugins/manual_bin_processing/tests/test_work_completed_decision.py workline_plugins/manual_bin_processing/tests/test_work_completed_application.py workline_plugins/manual_bin_processing/tests/integration/test_work_completed_postgresql.py -q`。
- [ ] **T5 (P1, human: ~2d / CC: ~4h)** — 物理生命周期 — 实现自主 FIFO、point3 分流、三类 NG 与正常退料
  - Surfaced by: Architecture / Test Review — 现场拓扑、point3 owner/Epoch 校验和 NGZone 管辖边界必须由证据驱动。
  - Files: 插件 scan/lifecycle application、DeviceCommand 接口、`RETURN_BUFFER`/NG tests。
  - Verify: `uv run pytest workline_plugins/manual_bin_processing/tests/test_scan_decisions.py workline_plugins/manual_bin_processing/tests/test_bin_lifecycle.py workline_plugins/manual_bin_processing/tests/integration/test_manual_bin_flow_postgresql.py -q`。
- [ ] **T6 (P1, human: ~1.5d / CC: ~3h)** — 装配与门禁 — 完成静态 composition、真实 worker E2E 与最终验证
  - Surfaced by: Test Review — 公共入口、broker、真实插件、PostgreSQL、设备 mock 和可靠恢复尚无纵向绿灯。
  - Files: 宿主 composition、`workline_plugins/manual_bin_processing/tests/e2e/`、插件配置、`docs/architecture/heavy-test-impact.toml`。
  - Verify: plugin E2E、聚焦 FAST、migration、QUALITY、staged selector HEAVY 全部通过；真实设备/现场/WMS 验收单独记录。
- [ ] **T7 (P2, human: ~30min / CC: ~10min)** — 测试治理 — 用结构化断言替换插件源码字符串黑名单
  - Surfaced by: Claude — `registry/discover/PhaseN` 文本搜索会误伤注释且不能证明静态装配。
  - Files: `workline_plugins/manual_bin_processing/tests/test_plugin_package.py`、宿主 composition tests。
  - Verify: 插件 AST 依赖、pyproject 无 entry point、唯一静态 handler 映射和 owner 缺失 fail-closed 测试通过。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
| --- | --- | --- | --- | --- | --- |
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | 未运行 |
| Codex Review | Claude outside voice | Independent 2nd opinion | 1 | CLEAR via Claude | 17 findings；12 项折叠进计划，5 项经现场事实或 diff 核对后驳回 |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 14 issues，0 critical gaps，0 unresolved |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | 后端合同不适用 |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | 未运行 |

### Completion Summary

- Step 0: Scope Challenge — 按现场事实缩减：移除 point2 错箱判断，`RETURN_BUFFER` 停线排空留在 TODO。
- Architecture Review: 3 issues found。
- Code Quality Review: 5 issues found。
- Test Review: diagram produced，4 gaps identified。
- Performance Review: 2 issues found。
- NOT in scope: written。
- What already exists: written。
- TODOS.md updates: 1 item proposed and added。
- Failure modes: 14 paths reviewed，0 critical gaps flagged。
- Outside voice: Claude ran；12 findings folded，5 rejected after independent verification。
- Parallelization: 3 lanes，2 parallel after schema freeze，1 sequential integration lane。
- Lake Score: 29/29 final decisions chose a complete, explicit behavior。

**VERDICT:** ENG + OUTSIDE VOICE CLEARED；C1～C7 INITIAL REVIEW APPROVED — 可按当前合同基线进入生产实现，后续细节调整须另行评审。

NO UNRESOLVED DECISIONS
