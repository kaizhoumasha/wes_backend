---
title: WMS / WES 自动出库 PickingTask 交互草案
status: Draft
created_at: 2026-08-07
updated_at: 2026-08-07
audience: WMS 系统开发人员、WES 出库业务开发人员、联调与测试人员
scope: PickingTask 事件、工作线队列、启动锁、逐盘决定、NG、补充来源、执行事实确认
related:
  - docs/superpowers/specs/2026-08-06-wes-outbound-operation-top-level-design.md
  - docs/contracts/wms-northbound-interaction-contract.md
  - docs/architecture/authority-matrix.md
  - docs/architecture/adr/2026-05-13-wes-wms-rcs-resource-boundary.md
related_device_wire: docs/integration/third_party_integration_whitepaper.md
---

# WMS / WES 自动出库 PickingTask 交互草案

## 1. 文档定位

本文只描述 WMS 与 WES 如何交互，不约束 WMS 内部如何生成任务、校验库存或选择来源。

本文初步冻结：

- 交互方向和同步/异步模式。
- 统一 JSON 信封。
- PickingTask、启动锁、逐盘决定、NG、来源补充和执行事实的上下文 Payload。
- 幂等、版本、迟到消息、任务完成和失败关闭语义。

本文仍为双方评审草案。逻辑 `operation` 和 JSON 结构可用于联调评审，但部署 path、认证、HTTP 状态映射、超时、Payload
上限和正式 schema 仍需双方书面批准后才能实施。

## 2. 共同交互原则

本文参考当前第三方设备统一接口白皮书中跨系统 HTTP/JSON 的共同交互习惯，但不继承设备业务字段：

- 使用 `POST + application/json + UTF-8`。
- 顶层只放协议控制字段，业务字段统一放入 `data`。
- 接收 ACK 只表示消息已可靠接收，不表示业务或物理执行完成。
- 使用稳定请求/事件/事实 ID 实现幂等，时间使用 UTC Unix 毫秒。
- 业务结果通过同步决定或后续独立报告表达，不在 Event ACK 中夹带执行指令。

本文不复用设备协议中的 `device_code`、`command_code`、`task_type`、`params`、设备重试次数或设备命令生命周期。WMS
业务交互不是 ECS 设备命令，不能为了表面一致而混用两套语义。

## 3. 系统边界

| 系统 | 权威事实 | 本合同中的职责 |
| --- | --- | --- |
| WMS | 业务任务、来源 Cell、来源锁、库存、`PkgID`、SixInOne、目标储位、NG 业务处置和补充来源 | 发布任务和控制事件；返回启动、逐盘、空取和补充来源决定；接收执行事实 |
| WES | 本地任务队列、工作线准入、设备/位置证据、执行对象、资源仲裁和可靠外部义务 | 可靠接收事件；请求业务决定；执行已授权动作；报告稳定事实 |
| RCS/AGV/CTU | 运输调度和运输终态 | 由独立 Transport 合同负责，不进入 PickingTask 业务 API |
| ECS/PLC/设备 | 扫码、抓取、放置、输送、安全互锁和设备终态 | 通过设备合同向 WES 提供证据，不直接调用本文 WMS 业务 operation |

WES 不接收出库单或波次单，不在本地重新分配库存，也不根据现场猜测替代来源、目标储位或 Cell 业务完成。

## 4. 统一 JSON 信封

### 4.1 请求信封

```json
{
  "request_id": "REQ-20260807-000001",
  "operation": "outbound.picking_task.issued@v1",
  "timestamp": 1786060800000,
  "data": {}
}
```

| 字段 | 规则 |
| --- | --- |
| `request_id` | 一次逻辑请求的稳定幂等 ID；重发不得更换 |
| `operation` | 闭集 operation 名和版本；接收方不接受未知版本 |
| `timestamp` | UTC Unix 毫秒 |
| `data` | operation 专属闭集 DTO；业务字段不得拍平到顶层 |

### 4.2 响应信封

```json
{
  "request_id": "REQ-20260807-000001",
  "code": "RECEIVED",
  "message": "Event persisted",
  "timestamp": 1786060800123,
  "data": {}
}
```

`code` 是业务/接收结果，不得由 HTTP 200 推断。消息一旦被接纳，相同 `request_id` 和相同 Payload 必须返回相同结果；
相同 `request_id` 与不同 Payload 必须返回 `CONFLICT`。尚未接纳的瞬时背压不写入业务幂等结果。

### 4.3 通用接收 ACK

| `code` | 含义 |
| --- | --- |
| `RECEIVED` | 首次可靠接收并持久化 |
| `DUPLICATE` | 相同业务 ID 和相同 Payload 已接收，不重复产生副作用 |
| `REJECTED` | envelope、operation 或 DTO 非法，未接纳为有效业务消息 |
| `CONFLICT` | 相同业务 ID 对应不同 Payload |
| `BUSY` | 瞬时背压，消息未接纳；必须返回 `retryable=true` 和建议重试时间 |

## 5. Operation 总览

| operation | 方向 | 模式 | 作用 |
| --- | --- | --- | --- |
| `outbound.picking_task.issued@v1` | WMS → WES | Event + 同步 ACK | 提前发布一张可排队 PickingTask |
| `outbound.picking_task.queue_changed@v1` | WMS → WES | Event + 同步 ACK | 更新未开始任务的排队参数 |
| `outbound.picking_task.start@v1` | WES → WMS | 同步决定 | 原子锁定来源 Cell 并授权开始 |
| `outbound.material.decide@v1` | WES → WMS | 同步决定 | 根据扫码证据返回物料、目标、Cell 后续动作或 NG |
| `outbound.cell.empty_decide@v1` | WES → WMS | 同步决定 | 根据可靠空取证据决定重试、等待或结束 Cell |
| `outbound.bin.ng_report@v1` | WES → WMS | 可靠事实 + 同步 ACK | 报告整箱不可执行及受影响 Cell |
| `outbound.picking_task.source_recovery_decided@v1` | WMS → WES | Event + 同步 ACK | 引用现有 Cell 或追加并锁定新的补充 Cell |
| `outbound.material.movement_report@v1` | WES → WMS | 可靠事实 + 同步 ACK | 报告逐盘确定位置变化或 NG 落点 |
| `outbound.picking_task.completion_report@v1` | WES → WMS | 可靠事实 + 同步 ACK | 报告全部已接纳 Cell 已闭合 |

所有 operation 使用 POST。正式 relative path 由双方在部署 wire 中批准；不得仅依据本文名称生成生产路由。

## 6. PickingTaskIssued Event

### 6.1 WMS 请求 Payload

```json
{
  "request_id": "REQ-PICK-000001",
  "operation": "outbound.picking_task.issued@v1",
  "timestamp": 1786060800000,
  "data": {
    "event_id": "EVT-PICK-000001",
    "task_id": "PICK-20260807-001",
    "task_version": 1,
    "priority": 50,
    "dispatch_sequence": 1024,
    "issued_at": 1786060799000,
    "not_before": 1786064400000,
    "workline_code": "SMT_OUTBOUND_01",
    "pick_cells": [
      {
        "cell_execution_id": "CELL-EXEC-001",
        "source_locator": {
          "type": "BIN_CELL",
          "rack_id": "RACK-5F-001",
          "rack_face": "A",
          "bin_id": "BIN-001",
          "cell_id": "CELL-01"
        }
      },
      {
        "cell_execution_id": "CELL-EXEC-002",
        "source_locator": {
          "type": "RACK_SLOT",
          "rack_id": "RETURN-RACK-001",
          "rack_face": "A",
          "slot_id": "SLOT-03"
        }
      },
      {
        "cell_execution_id": "CELL-EXEC-003",
        "source_locator": {
          "type": "BIN_CELL",
          "rack_id": "RACK-5F-001",
          "rack_face": "A",
          "bin_id": "BIN-001",
          "cell_id": "CELL-02"
        }
      }
    ]
  }
}
```

任务中禁止出现 `PkgID`、SixInOne、预估盘数、顶部顺序、目标储位、AGV/CTU 任务或缓存状态。任务发布时不锁 Cell，
因此也不携带 `cell_lock_generation`。

`not_before` 可省略。存在时只表示最早允许尝试启动，不是准点执行承诺。尚未到达 `not_before` 的任务不阻塞同一工作线
中已经具备启动资格的后续任务。

### 6.2 WES 接收 ACK

```json
{
  "request_id": "REQ-PICK-000001",
  "code": "RECEIVED",
  "message": "PickingTask persisted",
  "timestamp": 1786060800123,
  "data": {
    "event_id": "EVT-PICK-000001",
    "task_id": "PICK-20260807-001",
    "task_version": 1
  }
}
```

WES 同步只校验信封、字段闭集、版本、身份唯一性、locator 结构、幂等冲突和本地工作线/队列是否可接纳。WES 不重新
校验 WMS 库存或来源业务资格，也不再异步发送 AdmissionReport。

工作线队列已满时返回 `BUSY`、`reason_code=WORKLINE_QUEUE_FULL`、`retryable=true` 和 `retry_after_ms`；该事件未被接纳，
WMS 可使用相同请求/事件 ID 和相同 Payload 重试。`BUSY` 不能被缓存成永久幂等结果。

## 7. 多任务队列与任务控制

- WMS 可以提前向 WES 下发多张 PickingTask。
- 不同工作线可以并行执行不同任务。
- 同一工作线可以有多张 `QUEUED` 任务，但同一时刻只允许一张任务处于 `STARTING | EXECUTING`。
- WES 只在已经达到 `not_before` 的任务中按 `priority DESC, dispatch_sequence ASC` 选择启动候选。
- `dispatch_sequence` 必须在同一工作线形成无歧义顺序；WES 不按任务内容重新计算业务优先级。
- 任务启动前不锁 Cell，不创建该任务的机械臂动作或 CTU 投箱动作。
- 下一任务仍需通过设备、缓存、目标架和活动运输对象的本地准入检查。

未开始任务的 `priority`、`dispatch_sequence` 或 `not_before` 只通过
`outbound.picking_task.queue_changed@v1` 更新，并携带稳定 `event_id + queue_revision`。任务载荷本身保持不可变；执行中的
任务不接受队列参数更新，也不被更高优先级任务抢占。

```json
{
  "request_id": "REQ-QUEUE-000001",
  "operation": "outbound.picking_task.queue_changed@v1",
  "timestamp": 1786061000000,
  "data": {
    "event_id": "EVT-QUEUE-000001",
    "task_id": "PICK-20260807-001",
    "task_version": 1,
    "queue_revision": 2,
    "priority": 80,
    "dispatch_sequence": 900,
    "not_before": 1786062600000,
    "changed_at": 1786060999900
  }
}
```

暂停、恢复和取消涉及“请求已接收”与“现场已安全生效”两个时点，不得塞入上述队列事件。正式实施前应另行批准控制命令、
结果报告和不可取消窗口；在此之前 WES 不接受这三类控制操作。

## 8. Start 同步锁定与启动

WES 本地准备就绪后请求 WMS 原子锁定任务的完整初始 Cell 集合。成功响应既是来源锁事实，也是任务启动授权，不再额外发送
`picking_task.started` 回调。

### 8.1 WES 请求

```json
{
  "request_id": "REQ-START-000001",
  "operation": "outbound.picking_task.start@v1",
  "timestamp": 1786064500000,
  "data": {
    "start_request_id": "START-REQ-000001",
    "source_event_id": "EVT-PICK-000001",
    "task_id": "PICK-20260807-001",
    "task_version": 1,
    "execution_id": "EXEC-PICK-000001",
    "workline_code": "SMT_OUTBOUND_01"
  }
}
```

### 8.2 WMS 授权

```json
{
  "request_id": "REQ-START-000001",
  "code": "START_GRANTED",
  "message": "Source cells locked",
  "timestamp": 1786064500100,
  "data": {
    "start_request_id": "START-REQ-000001",
    "task_id": "PICK-20260807-001",
    "task_version": 1,
    "execution_id": "EXEC-PICK-000001",
    "cell_set_revision": 1,
    "lock_generation": 1,
    "locked_cell_execution_ids": ["CELL-EXEC-001", "CELL-EXEC-002", "CELL-EXEC-003"],
    "target_rack": {
      "rack_id": "TRANSFER-RACK-001",
      "capacity": 40,
      "face_sequence": ["A", "B"],
      "open_face": "A",
      "face_window_generation": 1
    },
    "transport_targets": {
      "source_rack_ids": ["RACK-5F-001"],
      "return_rack_ids": ["RETURN-RACK-001"],
      "target_rack_ids": ["TRANSFER-RACK-001"]
    },
    "granted_at": 1786064500050
  }
}
```

WES 只有收到 `START_GRANTED` 后才进入 `EXECUTING`，并创建任务专属运输和设备动作。

暂时无法锁定时返回 `START_WAIT`，其中 `queue_action` 只能是：

- `HOLD_QUEUE`：保持该任务为当前候选，不得越过。
- `TRY_NEXT`：允许 WES 尝试同一工作线的下一张具备启动资格的任务。

```json
{
  "request_id": "REQ-START-000001",
  "code": "START_WAIT",
  "message": "Source cells temporarily unavailable",
  "timestamp": 1786064500100,
  "data": {
    "start_request_id": "START-REQ-000001",
    "decision_id": "START-DECISION-000001",
    "queue_action": "TRY_NEXT",
    "reason_code": "SOURCE_LOCK_BUSY",
    "retry_after_ms": 5000
  }
}
```

不可启动时返回 `START_REJECTED` 和封闭 `reason_code`。同一 `start_request_id` 重复提交必须返回同一锁代际和同一决定；
`START_WAIT` 需要重新求值时使用新的 `start_request_id` 并关联上一 `decision_id`，不得改写原响应。

## 9. 逐盘扫码决定

### 9.1 WES 请求

```json
{
  "request_id": "REQ-MATERIAL-000001",
  "operation": "outbound.material.decide@v1",
  "timestamp": 1786065000000,
  "data": {
    "decision_request_id": "MAT-DECISION-REQ-000001",
    "task_id": "PICK-20260807-001",
    "task_version": 1,
    "execution_id": "EXEC-PICK-000001",
    "cell_execution_id": "CELL-EXEC-001",
    "lock_generation": 1,
    "scan_evidence_id": "SCAN-EVIDENCE-000001",
    "scan_codes": [
      {
        "code_type": "REEL_BARCODE",
        "value": "REEL-20260807-0099"
      }
    ],
    "scanned_at": 1786064999900
  }
}
```

`scan_codes[].code_type` 是双方批准的闭集。设备上报字段必须足以让 WMS 唯一确定一盘 `PkgID`。

### 9.2 接受结果

```json
{
  "request_id": "REQ-MATERIAL-000001",
  "code": "DECIDED",
  "message": "Material accepted",
  "timestamp": 1786065000100,
  "data": {
    "decision_request_id": "MAT-DECISION-REQ-000001",
    "decision_id": "MAT-DECISION-000001",
    "decision_version": 1,
    "result": "ACCEPT",
    "pkg_id": "PKG-000099",
    "six_in_one_code": "SIX-000099",
    "material_version": 12,
    "target_locator": {
      "rack_id": "TRANSFER-RACK-001",
      "rack_face": "A",
      "slot_id": "SLOT-A-05",
      "face_window_generation": 1
    },
    "cell_action": "CONTINUE"
  }
}
```

`cell_action` 只能是 `CONTINUE | CELL_DONE`。`CELL_DONE` 只表示当前盘闭合后不再从该 Cell 创建下一次取盘，不表示当前盘
已经完成物理放置。

### 9.3 WAIT 与终局

`WAIT` 是不可变的非终局快照。恢复条件满足后，WES 使用新的 `decision_request_id`、同一 `scan_evidence_id` 和
`previous_decision_id` 请求下一版本。相同请求 ID 重发必须返回原快照。

同一 `scan_evidence_id` 最多只能有一个不可变终局 `ACCEPT` 或 `REJECT`。结果缺失、迟到、版本倒退、无法关联或出现两个
终局时，WES 失败关闭，不把未知结果解释为 NG、继续或 Cell 完成。

## 10. 三类 NG

NG 必须明确作用域，不能使用一个 `ng=true` 同时控制料盘、Cell 和 Bin。

| `ng_scope` | 含义 | 当前对象动作 | 同 Bin 其他 Cell | Bin 最终去向 |
| --- | --- | --- | --- | --- |
| `MATERIAL` | 当前料盘 NG | 当前盘进入 NG 区；WMS 决定当前 Cell 是否继续 | 不受影响 | 正常退箱 |
| `CELL` | 当前 Cell 内容与需求不匹配 | 当前盘进入 NG 区；当前 Cell 关闭 | 继续执行 | 完成其他 Cell 后进入 NG 出口 |
| `BIN` | 方向、条码或身份异常导致整箱不可信 | 整箱进入 NG 流程；全部 Cell 不可执行 | 全部不可执行 | 立即进入 NG 出口 |

### 10.1 料盘 NG

`outbound.material.decide@v1` 可以返回：

```json
{
  "request_id": "REQ-MATERIAL-000002",
  "code": "DECIDED",
  "message": "Material rejected",
  "timestamp": 1786065100100,
  "data": {
    "decision_request_id": "MAT-DECISION-REQ-000002",
    "decision_id": "MAT-DECISION-000002",
    "decision_version": 1,
    "result": "REJECT",
    "ng_scope": "MATERIAL",
    "reason_code": "MATERIAL_QUALITY_NG",
    "material_action": "MOVE_TO_NG",
    "cell_action": "CONTINUE",
    "bin_action": "KEEP_WORKING",
    "source_recovery": "NOT_REQUIRED"
  }
}
```

当前盘可靠进入 NG 区后，`CONTINUE` 才允许同一 Cell 取下一盘。

### 10.2 Cell NG

```json
{
  "request_id": "REQ-MATERIAL-000003",
  "code": "DECIDED",
  "message": "Cell material mismatch",
  "timestamp": 1786065150100,
  "data": {
    "decision_request_id": "MAT-DECISION-REQ-000003",
    "decision_id": "MAT-DECISION-000003",
    "decision_version": 1,
    "result": "REJECT",
    "ng_scope": "CELL",
    "reason_code": "CELL_MATERIAL_MISMATCH",
    "material_action": "MOVE_TO_NG",
    "cell_action": "CLOSE_AS_NG",
    "bin_action": "CONTINUE_OTHER_CELLS_THEN_NG",
    "source_recovery": "PENDING"
  }
}
```

Cell NG 时，Bin 不能只保存一个会立即阻断全部工作的 `NG` 状态。WES 必须分别维护：

```text
work_eligibility = PARTIAL
exit_route = NG_EXIT
```

当前 Cell 物理 NG 闭合后停止执行；同 Bin 其他未完成 Cell 继续执行，最后将 Bin 移入 NG 出口。

### 10.3 Bin NG

方向错误、条码异常或身份冲突等设备证据使整个 Bin 不可信时，WES 不等待 WMS 才执行安全的 NG 路由，但必须可靠报告：

```json
{
  "request_id": "REQ-BIN-NG-000001",
  "operation": "outbound.bin.ng_report@v1",
  "timestamp": 1786065200000,
  "data": {
    "report_id": "BIN-NG-REPORT-000001",
    "task_id": "PICK-20260807-001",
    "task_version": 1,
    "execution_id": "EXEC-PICK-000001",
    "bin_execution_id": "BIN-EXEC-001",
    "bin_id": "BIN-001",
    "ng_evidence_id": "BIN-NG-EVIDENCE-001",
    "reason_code": "BIN_BARCODE_INVALID",
    "affected_cell_execution_ids": ["CELL-EXEC-001", "CELL-EXEC-003"],
    "work_eligibility": "NONE",
    "exit_route": "NG_EXIT",
    "occurred_at": 1786065199900
  }
}
```

WMS 返回通用接收 ACK。所有受影响 Cell 保持未闭合，直到 WMS 的来源恢复决定明确其关闭结果和替代来源。

## 11. 空取决定

设备对指定 Cell 给出可靠“无料”终态且没有产生扫码证据时，WES 调用
`outbound.cell.empty_decide@v1`，携带 `source_observation_id`、任务/Cell/锁代际、设备命令终态和发生时间。

WMS 只返回：

- `RETRY`：允许重新执行同一来源。
- `WAIT`：保持 Cell 和相关资源未决。
- `CELL_DONE`：允许在没有未决物料动作后关闭该 Cell。

设备结果未知或无法关联时不得调用空取决定来猜测 Cell 已空。

请求和响应示例：

```json
{
  "request_id": "REQ-EMPTY-000001",
  "operation": "outbound.cell.empty_decide@v1",
  "timestamp": 1786065250000,
  "data": {
    "decision_request_id": "EMPTY-DECISION-REQ-000001",
    "task_id": "PICK-20260807-001",
    "task_version": 1,
    "execution_id": "EXEC-PICK-000001",
    "cell_execution_id": "CELL-EXEC-001",
    "lock_generation": 1,
    "source_observation_id": "EMPTY-EVIDENCE-000001",
    "command_result_id": "CMD-RESULT-EMPTY-000001",
    "observed_at": 1786065249900
  }
}
```

```json
{
  "request_id": "REQ-EMPTY-000001",
  "code": "DECIDED",
  "message": "Cell may be closed",
  "timestamp": 1786065250100,
  "data": {
    "decision_request_id": "EMPTY-DECISION-REQ-000001",
    "decision_id": "EMPTY-DECISION-000001",
    "decision_version": 1,
    "result": "CELL_DONE"
  }
}
```

## 12. 来源恢复与追加式补料

WMS 通过 `outbound.picking_task.source_recovery_decided@v1` 对 MATERIAL/CELL/BIN NG 给出来源恢复结果：

- `USE_EXISTING_CELLS`：引用当前 PickingTask 中尚未执行的 Cell，不新增成员。
- `ADD_SOURCE_CELLS`：追加并锁定新的来源 Cell。
- `CLOSE_WITHOUT_REPLACEMENT`：明确关闭受影响 Cell，不补充来源。

禁止直接修改、删除或替换已接纳 Cell，也禁止只追加 Rack 或 Bin 而不明确到 Cell。

| `action` | 必填字段 | 约束 |
| --- | --- | --- |
| `USE_EXISTING_CELLS` | `close_affected_cell_execution_ids`、`selected_existing_cell_execution_ids` | 被引用 Cell 必须属于当前已接纳集合且尚未开始 |
| `ADD_SOURCE_CELLS` | `close_affected_cell_execution_ids`、`cell_set_revision`、`lock_generation`、`additional_cells` | 只允许追加全新 CellExecution |
| `CLOSE_WITHOUT_REPLACEMENT` | `close_affected_cell_execution_ids` | 不新增来源，WMS 明确接受缺口闭合 |

新增来源示例：

```json
{
  "request_id": "REQ-RECOVERY-000001",
  "operation": "outbound.picking_task.source_recovery_decided@v1",
  "timestamp": 1786065300000,
  "data": {
    "event_id": "EVT-RECOVERY-000001",
    "supplement_id": "SUPPLEMENT-000001",
    "task_id": "PICK-20260807-001",
    "task_version": 1,
    "execution_id": "EXEC-PICK-000001",
    "cause_ng_evidence_ids": ["BIN-NG-EVIDENCE-001"],
    "action": "ADD_SOURCE_CELLS",
    "close_affected_cell_execution_ids": ["CELL-EXEC-001", "CELL-EXEC-003"],
    "cell_set_revision": 2,
    "lock_generation": 2,
    "additional_cells": [
      {
        "cell_execution_id": "CELL-EXEC-NEW-001",
        "replaces_cell_execution_ids": ["CELL-EXEC-001", "CELL-EXEC-003"],
        "source_locator": {
          "type": "BIN_CELL",
          "rack_id": "RACK-5F-009",
          "rack_face": "A",
          "bin_id": "BIN-042",
          "cell_id": "CELL-03"
        }
      }
    ]
  }
}
```

追加规则：

- `supplement_id` 幂等；同 ID 不同 Payload 必须冲突。
- `cell_set_revision` 只能递增，追加内容形成新的不可变成员代际。
- WMS 发布 `ADD_SOURCE_CELLS` 前必须原子锁定新增 Cell，并返回仅属于该补充集合的 `lock_generation`。
- 若 WES 返回 `REJECTED` 或 `CONFLICT`，WMS 必须解除本次未生效的新增锁；只有 `RECEIVED | DUPLICATE` 表示补充集合已被 WES 接纳。
- 每个 CellExecution 永久使用自己所属的锁代际；新代际不能使旧 Cell 的在途响应失效。
- 已存在于任务中的 Cell 只能通过 `USE_EXISTING_CELLS` 引用，不能重复追加。
- 新货架或新 Bin 尚未到位时，WES 创建独立 TransportTask；运输状态不进入 PickingTask 完成条件。
- 受影响 Cell 只有在物理 NG 去向确定且 WMS 恢复决定已到达后，才以 NG outcome 闭合。

## 13. 逐盘位置事实

WES 每完成一盘的正常 PUT 或 NG 放置，就可靠发送 `outbound.material.movement_report@v1`：

```json
{
  "request_id": "REQ-MOVE-000001",
  "operation": "outbound.material.movement_report@v1",
  "timestamp": 1786065500000,
  "data": {
    "fact_id": "MOVE-FACT-000001",
    "task_id": "PICK-20260807-001",
    "task_version": 1,
    "execution_id": "EXEC-PICK-000001",
    "cell_execution_id": "CELL-EXEC-001",
    "material_execution_id": "MAT-EXEC-000001",
    "scan_evidence_id": "SCAN-EVIDENCE-000001",
    "pkg_id": "PKG-000099",
    "from_locator": {
      "type": "BIN_CELL",
      "rack_id": "RACK-5F-001",
      "bin_id": "BIN-001",
      "cell_id": "CELL-01"
    },
    "to_locator": {
      "type": "RACK_SLOT",
      "rack_id": "TRANSFER-RACK-001",
      "rack_face": "A",
      "slot_id": "SLOT-A-05"
    },
    "command_result_id": "CMD-RESULT-000001",
    "occurred_at": 1786065499900
  }
}
```

未唯一识别的 NG 料盘不得伪造 `pkg_id`，应通过执行 ID、扫码证据和 NG 落点关联。WMS 只返回通用接收 ACK；ACK 不修改
已经由设备终态建立的 WES 本地位置事实。

## 14. PickingTask 完成

PickingTask 的成员集合为：

```text
初始 START_GRANTED 锁定的 Cell
+ 后续 ADD_SOURCE_CELLS 接纳的 Cell
```

每个 CellExecution 使用统一 `COMPLETED` 状态和独立 outcome，例如：

```text
SUCCESS | NG_REPLACED | NG_CLOSED | UNAVAILABLE_BY_BIN_NG
```

本地完成条件为：

```text
ALL(已接纳 CellExecution.status == COMPLETED)
AND 不存在未决 NG / 来源恢复决定
```

完成条件仍然不读取 Rack、Bin、AGV、CTU、工作位或清场状态。任务完成后，这些对象继续由各自 owner 闭环，不得反向
改写已完成 PickingTask。

逐盘位置事实必须先被 WMS 接收，WES 才可靠发送 `outbound.picking_task.completion_report@v1`：

```json
{
  "request_id": "REQ-COMPLETE-000001",
  "operation": "outbound.picking_task.completion_report@v1",
  "timestamp": 1786066000000,
  "data": {
    "completion_report_id": "COMPLETE-REPORT-000001",
    "task_id": "PICK-20260807-001",
    "task_version": 1,
    "execution_id": "EXEC-PICK-000001",
    "final_cell_set_revision": 2,
    "completed_at": 1786065999900,
    "cell_results": [
      {
        "cell_execution_id": "CELL-EXEC-001",
        "outcome": "UNAVAILABLE_BY_BIN_NG"
      },
      {
        "cell_execution_id": "CELL-EXEC-002",
        "outcome": "SUCCESS"
      },
      {
        "cell_execution_id": "CELL-EXEC-003",
        "outcome": "UNAVAILABLE_BY_BIN_NG"
      },
      {
        "cell_execution_id": "CELL-EXEC-NEW-001",
        "outcome": "SUCCESS"
      }
    ]
  }
}
```

WMS 对完成事实只返回通用接收 ACK，不在 ACK 中下发后续任务或运输指令。

## 15. 幂等、版本与失败关闭

- 同一请求/事件/事实 ID 和相同 Payload 重发，必须返回原结果且不重复产生副作用。
- `BUSY` 表示尚未接纳，不固化为业务幂等结果；重试最终转为 `RECEIVED` 后才建立稳定接收结果。
- 同一 ID 与不同 Payload 必须 `CONFLICT`。
- PickingTask 同一 `task_version` 的原始 Payload 不可变化；队列参数更新使用独立控制版本。
- `lock_generation` 作用于它锁定的 Cell 集合，不是会使全部旧 Cell 失效的全局最新值。
- `WAIT` 后续求值必须创建新请求并关联上一决定；不得改写原请求结果。
- 同一扫码证据或来源观察最多一个不可变终局决定。
- 超时、交付未知、响应非法、版本倒退或无法关联时，WES 保持相关资源占用并失败关闭。
- `WmsClient` 只执行一次 HTTP/JSON 访问；可靠重试、Outbox、因果排序和状态推进由具体业务 owner 负责。

## 16. 联调验收清单

| 场景 | 预期结果 |
| --- | --- |
| WMS 连续下发同线多任务 | WES 全部可靠排队，同线只启动一个任务 |
| WMS 下发不同工作线任务 | 不同工作线可并行启动 |
| 任务尚未到 `not_before` | 不参与启动候选，也不阻塞已具备资格的任务 |
| 同一 Event 重复提交 | 返回 `DUPLICATE`，不重复建任务 |
| 同一 ID 不同 Payload | 返回 `CONFLICT` |
| 工作线接收队列瞬时已满 | 返回可重试 `BUSY`，不把事件标记为已接纳 |
| 启动前 Cell 可锁定 | 返回 `START_GRANTED` 后 WES 才创建任务专属物理动作 |
| 启动暂时等待且 `HOLD_QUEUE` | 不越过当前候选 |
| 启动暂时等待且 `TRY_NEXT` | 可尝试下一张具备启动资格的任务 |
| 料盘 NG 且 Cell 可继续 | 当前盘进入 NG 区后继续当前 Cell |
| Cell NG | 当前 Cell 关闭；同 Bin 其他 Cell 继续；Bin 最终进入 NG 出口 |
| Bin NG | 整箱所有 Cell 停止执行并进入 NG 出口 |
| 补充来源已在任务内 | 只引用现有 Cell，不重复追加 |
| 补充来源不在任务内 | 追加新成员代际和独立锁代际 |
| WMS 决定超时或无法关联 | 不推断 NG、继续、Cell 完成或替代来源 |
| 全部已接纳 Cell 闭合 | PickingTask 完成，不聚合 Rack/Bin/Transport/清场状态 |
| 逐盘事实尚未被 WMS 接收 | 完成报告保持等待，不越过逐盘事实 |

## 17. 正式实施前仍需批准

- 每项 operation 的 relative path、HTTP 状态集合和响应 media type。
- 认证方式；若当前隔离网络不需要认证，应明确为 `NONE`。
- DTO 正式 JSON Schema、字符串长度、数组上限、Payload 上限和枚举闭集。
- 超时、可重试性、最大重试窗口和交付未知后的查询/对账方式。
- 业务 `reason_code` 字典、人工处置流程和 SLA。
- 暂停、恢复、取消的控制命令、结果报告和不可取消窗口。
- 双方共享的成功、WAIT、NG、冲突、迟到消息和来源补充 fixture。

上述内容未批准前，不得创建占位 API、宽泛 `dict` DTO、兼容别名、动态 registry 或业务状态机。
