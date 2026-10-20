---
title: WMS/WES Integration Contract Amendment Proposal：自动拣料
status: ReviewRequired
created_at: 2026-09-21
updated_at: 2026-10-20
audience: WMS 开发组、WES 架构组
scope: §1–8 保留既有确认基线；§9 收窄为 9H 容量接续与核销补证；实现和联合验收状态分别记录
related:
  - docs/contracts/wms-outbound-picking-task-integration-requirements.md
  - docs/contracts/wms-manual-outbound-picking-integration-requirements.md
  - docs/integration/outbound-picking-plan-delta-joint-freeze.md
  - docs/integration/device-annex-automatic-picking-arms.md
---

# WMS/WES Integration Contract Amendment Proposal：自动拣料

WMS 已确认接受本文 §1–8 的既有候选项，包括跨 revision 成员规则、§4–6 的目标请求/响应形态和退料货架整架任务完成 API。确认发生于 2026-09-23，由 WES 记录；[出库主合同](../contracts/wms-outbound-picking-task-integration-requirements.md)承接已明确的业务规则与目标 wire。严格 DTO、OpenAPI、生产接线和联合验收仍按各自状态执行；尚未给出具体取值的设备映射及字段边界由对应联合项继续冻结。

本文总状态 `ReviewRequired` 指 §9 的容量接续与核销细节尚待补证，不撤销 §1–8 及人工线已有 drain 基线。9H 不再预设新增字段或要求整体重新冻结；2026-09-23 Accepted、离线验证或接口返回成功均不自动证明待补证细节已通过。

## 1. Identity Rules

| 身份 | 含义 | 范例 |
| --- | --- | --- |
| Plan Member Identity | 某版 `plan_delta` 新增的一条来源 Requirement | `task_id + plan_revision + rack_id + rack_face`；DirectPick 增加精确 `source_locator` |
| Passage Identity | 同一物理料箱的一次经过；`task_id + bin_code` 可对应多次 Passage | `BOX-A/P1` 与 `BOX-A/P2` |
| Action / Operation Identity | 一次外部逻辑动作或决定 | 入线批次、准入、`work_plan`、Transport 的原 operation/task 身份 |
| Message Identity | 消息本身的可靠接收、重试和去重身份 | 顶层 `operation_id` |

`plan_revision` 解决 **Plan Member identity**，不解决 **Passage identity**。消息身份不一定等于业务关联身份：`work_completed` 顶层 `operation_id` 标识完成通知；通知携带 `task_id + bin_code`，WES 在执行任务所属 WorkLine 匹配当前等待完成的料箱。技术重试保持同一顶层身份、`timestamp` 和冻结正文；形成新的业务 Action 才创建新身份。WMS Response 的顶层 `operation_id` 匹配原请求，不需要重复附加请求中的关联字段。

## 2. 接口身份矩阵与修订状态

| API | 业务关联域 | 当前可用身份 / 拟修订字段 | WMS Decision |
| --- | --- | --- | --- |
| `plan_delta` | Plan stream / Plan Member | 已有 `data.plan_revision`；跨 revision 同来源的成员规则按 §3 | WMS Accepted |
| `inbound_batch` | Plan Member | 新增 `data.plan_revision` | WMS Accepted |
| `PLAN_MEMBERS cancel` | Plan Member | 两类成员选择器各新增 `plan_revision` | WMS Accepted |
| `direct_pick_completed` | Plan Member | WES 当前严格 DTO 已有 `data.plan_revision`；AUTO 复用该 operation | WMS Accepted |
| `material.decide`，`RACK_SLOT` | Plan Member | 新增 `data.plan_revision` | WMS Accepted |
| `source.empty_decide`，`RACK_SLOT` | Plan Member | 新增 `data.plan_revision` | WMS Accepted |
| `material.movement_report`，`RACK_SLOT` | Plan Member | 新增 `data.plan_revision` | WMS Accepted |
| `work_plan` | Passage / Work | SCAN2 实扫 Bin 后由 WMS 跨来源 revision 决定 `READY | NO_WORK | WAIT`；确定 `WAIT` 后以新 `operation_id` 重问，WES 在本地关联同次 Passage；`READY` 提供编码 `cell_id` 与独立 `cell_index` | WMS Accepted |
| `material.decide`、`source.empty_decide`、`material.movement_report`，`BIN_CELL` | WMS 业务决定 / 结果记录 | WMS 按各请求的来源、扫码或逐盘事实决定和记录；WES 本地按 Passage 与 Action 身份编排 | WMS Accepted |
| `work_completed` | 当前料箱任务 | WMS 通知 `task_id + bin_code + result + completed_at`；WES 本地匹配当前等待的 Passage | 当前 wire |
| `return_rack.arrival_report` | Physical Action | `transport_task_id` 关联原搬运事实 | 现行 wire |
| `return_batch` | Passage / FIFO | 以当前 RETURN_BUFFER FIFO 候选形成请求；沿人工线既有 drain 基线，不新增 reservation 字段 | 现行 wire；容量核销补证见 §9 |
| 退料货架整架任务完成 API | Rack task / Plan stream | `data.through_plan_revision` 作为完成范围截止点；operation、方向和 DTO 见 §6.1 | WMS Accepted；WES 未实现 |

下文示例使用与主合同相同的公共信封。`Current Wire` 指向既有实现字段；`Target Wire` 是 WMS 已接受的目标字段和完整示例。示例响应只展示代表性业务结果，其他结果和 `409/422/503` 仍以主合同为准，除非下文明确修改。

## 3. Plan Member 跨 revision 规则

**Problem：** 同一 PickingTask 的相同物理货架/面可由不同 revision 再次安排：

`T1/rev1 → Rack A → A1/A2`；`T1/rev2 → Rack A → A3`；`T1/rev3 → Rack B/C → A1/A3`。

**Current Wire → Target Wire：** `plan_delta.data.plan_revision` 已存在，不增加字段；把来源成员去重作用域改为 `task_id + plan_revision + source member`。同 revision 重复成员为 duplicate；不同 revision 的同架同面是新成员。`last_applied_plan_revision` 只作计划流游标，不使旧成员自动失效。

**Request Example：** 更高版本再次安排 A：

```json
{
  "operation_id": "019f3401-4a10-7b1a-aab5-f2df785324d2",
  "operation": "outbound.picking_task.plan_delta@v1",
  "timestamp": 1786060816000,
  "data": {
    "task_id": "PICK-20260811-001",
    "plan_revision": 2,
    "added_bin_source_racks": [
      {
        "rack_id": "RACK-A",
        "rack_face": [
          "A"
        ]
      }
    ]
  }
}
```

**Response Example：** `202`：

```json
{
  "operation_id": "019f3401-4a10-7b1a-aab5-f2df785324d2",
  "code": "RECEIVED",
  "timestamp": 1786060816100,
  "data": {}
}
```

**Identity / Retry / Compatibility：** 同消息身份、同正文重报复用原成员；新 revision 使用新消息身份并建立独立成员。成员去重以 `task_id + plan_revision + source member` 为作用域，撤销指向具体 revision。实现中的去重规则、索引和测试须同步替换。

**WMS Decision：Accepted。** 增量按新增与显式取消处理；同 revision 内按来源成员业务 key 去重。

## 4. Plan Member API 修订

以下五项目标 wire 的 `plan_revision` 均为本次来源 Requirement Member 的正整数，不是 WMS 最新 revision。WES 不从历史货架或当前最高 revision 猜测。所有相同 `operation_id` 技术重试冻结原正文；不同 revision 或已闭合 Action 后的新业务尝试使用新 `operation_id`。错误结果不因新增字段改变原 `409/422/503` 联合语义。

### 4.1 `outbound.bin.inbound_batch@v1`

**Problem / Current Wire → Target Wire：** 现行 `data={task_id,rack_id,rack_face}` 不能区分跨 revision 同架同面。新增必填 `data.plan_revision`；`READY | RACK_FACE_DONE` 响应联合不变。

**Request Example：**

```json
{
  "operation_id": "019f3405-1111-7b01-8b01-000000000001",
  "operation": "outbound.bin.inbound_batch@v1",
  "timestamp": 1786064700000,
  "data": {
    "task_id": "PICK-20260811-001",
    "plan_revision": 2,
    "rack_id": "RACK-A",
    "rack_face": "A"
  }
}
```

**Response Example：** 该成员已完成：

```json
{
  "operation_id": "019f3405-1111-7b01-8b01-000000000001",
  "code": "DECIDED",
  "timestamp": 1786064700100,
  "data": {
    "result": "RACK_FACE_DONE"
  }
}
```

**Identity / Compatibility：** 以 `task_id + plan_revision + rack_id + rack_face` 冻结本次成员清单。已可靠形成的请求在父任务完成或成员后续取消后仍沿原身份交付。替换严格 DTO 与 WMS 成员查询，不做旧形态兼容。

**WMS Decision：Accepted。** WMS 按该来源成员返回独立结果。

### 4.2 `outbound.picking_task.cancel@v1` 的 `PLAN_MEMBERS`

**Problem / Current Wire → Target Wire：** 现行 `bin_source_racks[]={rack_id,rack_face[]}`、`direct_pick_sources[]={rack_id,rack_face,slot_ids[]}` 无 revision。各选择器新增必填 `plan_revision`；`TASK` 取消保持原 wire。

**Request Example：**

```json
{
  "operation_id": "019f3401-4a10-7b1a-aab5-f2df785324d3",
  "operation": "outbound.picking_task.cancel@v1",
  "timestamp": 1786060817000,
  "data": {
    "task_id": "PICK-20260811-001",
    "cancel_scope": "PLAN_MEMBERS",
    "bin_source_racks": [
      {
        "plan_revision": 1,
        "rack_id": "RACK-A",
        "rack_face": [
          "A"
        ]
      },
      {
        "plan_revision": 2,
        "rack_id": "RACK-A",
        "rack_face": [
          "A"
        ]
      }
    ],
    "direct_pick_sources": [
      {
        "plan_revision": 2,
        "rack_id": "RETURN-RACK-01",
        "rack_face": "A",
        "slot_ids": [
          "A-03"
        ]
      }
    ]
  }
}
```

**Response Example：**

```json
{
  "operation_id": "019f3401-4a10-7b1a-aab5-f2df785324d3",
  "code": "RECEIVED",
  "timestamp": 1786060817100,
  "data": {}
}
```

**Identity / Compatibility：** 按 revision 内成员排重；撤销 rev2/A 不影响 rev1/A。已发生的物理 Fact 和已经持久化的交付义务不因取消消失。现行按全任务 rack 排重的 DTO/查询须替换。

**WMS Decision：Accepted。** 部分选择器已取消或不存在时沿主合同逐项幂等处理。

### 4.3 `outbound.manual_rack.direct_pick_completed@v1`

**Current WES Wire / WMS 已确认：** WES 当前严格 DTO 已要求 `data.plan_revision`，并按该 revision 结清面级直接取料成员；AUTO 工作线复用同一 operation。人工线由 WMS/PDA 发完成事实，自动线按相同面级业务事实发送。

**Request Example：**

```json
{
  "operation_id": "019f3404-a100-7b01-8b01-000000000002",
  "operation": "outbound.manual_rack.direct_pick_completed@v1",
  "timestamp": 1788390100000,
  "data": {
    "task_id": "PICK-20260902-001",
    "plan_revision": 2,
    "rack_id": "RETURN-RACK-01",
    "rack_face": "A",
    "completed_at": 1788390099000
  }
}
```

**Response Example：**

```json
{
  "operation_id": "019f3404-a100-7b01-8b01-000000000002",
  "code": "RECEIVED",
  "timestamp": 1788390100100,
  "data": {}
}
```

**Identity / Compatibility：** 顶层身份去重消息；`task_id + plan_revision + rack_id + rack_face` 结清成员。WES 当前 DTO 已包含该身份；本提案不要求再次修改 WES 面级 wire。

**WMS Decision：Accepted。** WMS 按指定 revision 发送面级完成事实；AUTO 工作线复用该 operation。整架任务完成见 §6.1。

### 4.4 `outbound.material.decide@v1`，`RACK_SLOT`

**Problem / Current Wire → Target Wire：** 既有 `data={task_id,source_locator,six_in_one,scanned_at}` 无法区分跨 revision 同储位。目标请求增加 `data.plan_revision`，以 `barcode` 原文替换 `six_in_one`；响应目标坐标与下一来源结构见第 6 节。下例为 WMS 已接受的目标 wire。

**Request Example：**

```json
{
  "operation_id": "019f3410-af77-71fd-9bde-0df75fcdeea2",
  "operation": "outbound.material.decide@v1",
  "timestamp": 1786063000000,
  "data": {
    "task_id": "PICK-20260811-001",
    "plan_revision": 2,
    "source_locator": {
      "type": "RACK_SLOT",
      "rack_id": "RETURN-RACK-01",
      "rack_face": "A",
      "slot_id": "A-03"
    },
    "barcode": "P032-0561-000HF,Q3000,M0805S106K160CT,D260505,L100R028291,S72052872716100149602",
    "scanned_at": 1786062999900
  }
}
```

**Response Example：** 直接取料储位处理一次后不返回下一盘对象：

```json
{
  "operation_id": "019f3410-af77-71fd-9bde-0df75fcdeea2",
  "code": "DECIDED",
  "timestamp": 1786063000100,
  "data": {
    "result": "ACCEPT",
    "target_locator": {
      "type": "RACK_SLOT",
      "rack_id": "TRANSFER-RACK-01",
      "rack_face": "A",
      "slot_id": "A-05",
      "rack_layer": 1,
      "rack_column": 1
    }
  }
}
```

**Identity / Compatibility：** `plan_revision` 绑定原 DirectPick 成员；物料结果仍匹配原请求 `operation_id`。WES 严格 DTO 按已接受的目标 wire 一次性替换。

**WMS Decision：Accepted。** `plan_revision`、`barcode`、目标坐标与下一来源结构按本文目标形态处理；具体字段边界见 §6。

### 4.5 `outbound.source.empty_decide@v1`，`RACK_SLOT`

**Problem / Current Wire → Target Wire：** 现行 `data={task_id,source_locator,observed_at}`；新增必填 `data.plan_revision`。自动插件 MVP 暂不消费空取，但长期合同保留。

**Request Example：**

```json
{
  "operation_id": "019f3420-01be-7e11-b265-10de42c881f3",
  "operation": "outbound.source.empty_decide@v1",
  "timestamp": 1786065100000,
  "data": {
    "task_id": "PICK-20260811-001",
    "plan_revision": 2,
    "source_locator": {
      "type": "RACK_SLOT",
      "rack_id": "RETURN-RACK-01",
      "rack_face": "A",
      "slot_id": "A-03"
    },
    "observed_at": 1786065099900
  }
}
```

**Response Example：**

```json
{
  "operation_id": "019f3420-01be-7e11-b265-10de42c881f3",
  "code": "DECIDED",
  "timestamp": 1786065100100,
  "data": {
    "result": "SOURCE_DONE"
  }
}
```

**Identity / Compatibility：** `plan_revision` 绑定发生空取的原成员；`RETRY | WAIT | SOURCE_DONE` 联合不变。新一次确定空取是新业务 Action，响应未知沿原请求重发。

**WMS Decision：Accepted。**

### 4.6 `outbound.material.movement_report@v1`，`RACK_SLOT`

**Problem / Current Wire → Target Wire：** 现行 `data={task_id,source_locator,PkgID,to_locator,occurred_at}`；新增必填 `data.plan_revision`，须等于前序物料决定的来源成员 revision。

**Request Example：**

```json
{
  "operation_id": "019f3422-f4a8-7247-98f0-8118dfb7f461",
  "operation": "outbound.material.movement_report@v1",
  "timestamp": 1786065500000,
  "data": {
    "task_id": "PICK-20260811-001",
    "plan_revision": 2,
    "source_locator": {
      "type": "RACK_SLOT",
      "rack_id": "RETURN-RACK-01",
      "rack_face": "A",
      "slot_id": "A-03"
    },
    "PkgID": "PKG-001",
    "to_locator": {
      "type": "RACK_SLOT",
      "rack_id": "TRANSFER-RACK-01",
      "rack_face": "A",
      "slot_id": "A-05"
    },
    "occurred_at": 1786065499900
  }
}
```

**Response Example：**

```json
{
  "operation_id": "019f3422-f4a8-7247-98f0-8118dfb7f461",
  "code": "RECORDED",
  "timestamp": 1786065500100,
  "data": {}
}
```

**Identity / Compatibility：** WMS 以 `task_id + plan_revision + source_locator + PkgID` 对应前序最终决定；同消息身份重放返回原记账结果。严格 DTO 与业务关联一次性按来源成员 revision 替换。

**WMS Decision：Accepted。**

## 5. Passage / Work 关联提案

**场景：** 同一任务的物理料箱可以再次投入：`T1/BOX-A/P1 → work_plan OP100`；后来 `T1/BOX-A/P2 → work_plan OP200`。WES 为每次实际 SCAN2 经过保存独立 Passage 和工作请求身份；WMS 按当前业务事实决定每次是否取料。

**跨 revision 的业务决定：** SCAN2 确认实际 Bin 后，WES 为本次 Passage 创建一个首个 `work_plan` 请求。WMS 可把多个 revision 指向同一 Bin 的物料纳入这次决定，并根据业务需求、库存和料箱实况确定取料内容及后续动作；WES 按 WMS 的确定结果和设备权威事实编排执行。后续 revision 再次命中同一 Bin 时，正在执行的 Passage 依据已授权范围及第 6 节的后继来源合同继续或结束；料箱实际再次到达 SCAN2 时，WES 创建新 Passage 并请求 WMS 返回 `READY | NO_WORK | WAIT`。`NO_WORK` 后，Bin 按正常退箱路径流出；已确定的 `READY` 保持本次冻结范围。

**工作身份：** WES 将每次 `work_plan` 请求及响应关联到本次真实 SCAN2 的本地 Passage。WMS 返回确定 `WAIT` 后，WES 使用新顶层 `operation_id` 重问，`task_id + bin_code + scanned_at` 沿用本次 SCAN2 的值；WMS 根据当前业务事实重新决定。WES 在本地将确定的 `READY | NO_WORK` 作为本次 Passage 的最终工作决定。`material.decide`、`source.empty_decide`、`material.movement_report` 分别提交来源、扫码或逐盘事实，由 WMS 决定和记录；WES 在本地按 Passage、原 Action 和设备事实约束后继执行。新一次真实 SCAN2 生成新 Passage。同箱再次经过和迟到请求的联合验收需覆盖 WMS 对旧结果与新工作的区分。

### 5.1 `outbound.bin.work_plan@v1`

**Current Wire → Target Wire：** 首问和确定 `WAIT` 后的重问均发送 `data={task_id,bin_code,scanned_at}`；重问使用新顶层 `operation_id`。`READY` 目标响应给出单个 `cell_id + cell_index` 及物理参数。WMS 希望 `cell_id` 保持 `BIN_ID + INDEX` 业务编码，并以 `cell_index` 提供料格在料箱内的定位号；WES 将该定位号用于经 O16 确认的设备映射。下例以 `BIN_ID=BIN-001`、`INDEX=3` 示意；确切编码、`cell_index` 类型与值域仍需 WMS 冻结。

**Request Example：** 假设 rev1 与 rev2 均有与 `BIN-001` 相关的物料；本次 SCAN2 发起一条首问，由 WMS 统一决定本次可取内容。

```json
{
  "operation_id": "019f3407-8cf2-750a-af59-43366bc44e20",
  "operation": "outbound.bin.work_plan@v1",
  "timestamp": 1786064800000,
  "data": {
    "task_id": "PICK-20260811-001",
    "bin_code": "BIN-001",
    "scanned_at": 1786064799900
  }
}
```

**Response Example：** `READY` 结构仍待 WMS 确认：

```json
{
  "operation_id": "019f3407-8cf2-750a-af59-43366bc44e20",
  "code": "DECIDED",
  "timestamp": 1786064800100,
  "data": {
    "result": "READY",
    "cell_id": "BIN-0013",
    "cell_index": 3,
    "bin_type": 3,
    "reel_layer": 1,
    "reel_thickness": 20,
    "reel_diameter": 15
  }
}
```

**`WAIT` 后重问示例（同箱的另一次 Passage）：** 本次真实 SCAN2 产生新的 Passage；WMS 确定返回 `WAIT` 后，WES 以新 `operation_id` 对同次 Passage 重新请求：

```json
{
  "operation_id": "019f3407-8cf2-750a-af59-43366bc44e30",
  "operation": "outbound.bin.work_plan@v1",
  "timestamp": 1786065800000,
  "data": {
    "task_id": "PICK-20260811-001",
    "bin_code": "BIN-001",
    "scanned_at": 1786065799900
  }
}
```

```json
{
  "operation_id": "019f3407-8cf2-750a-af59-43366bc44e30",
  "code": "DECIDED",
  "timestamp": 1786065800100,
  "data": {"result": "WAIT", "retry_after_ms": 1000}
}
```

```json
{
  "operation_id": "019f3407-8cf2-750a-af59-43366bc44e31",
  "operation": "outbound.bin.work_plan@v1",
  "timestamp": 1786065802000,
  "data": {
    "task_id": "PICK-20260811-001",
    "bin_code": "BIN-001",
    "scanned_at": 1786065799900
  }
}
```

```json
{
  "operation_id": "019f3407-8cf2-750a-af59-43366bc44e31",
  "code": "DECIDED",
  "timestamp": 1786065802100,
  "data": {
    "result": "READY",
    "cell_id": "BIN-0013",
    "cell_index": 3,
    "bin_type": 3,
    "reel_layer": 1,
    "reel_thickness": 20,
    "reel_diameter": 15
  }
}
```

**后续 revision 再次命中同箱，但 WMS 决定直接流出的示例（另一实际 Passage）：** 即使上游有新的来源成员，`work_plan` 仍按这次 SCAN2 的实际 Bin 请求；WMS 判断无需取料时返回 `NO_WORK`。

```json
{
  "operation_id": "019f3407-8cf2-750a-af59-43366bc44e40",
  "operation": "outbound.bin.work_plan@v1",
  "timestamp": 1786066800000,
  "data": {
    "task_id": "PICK-20260811-001",
    "bin_code": "BIN-001",
    "scanned_at": 1786066799900
  }
}
```

```json
{
  "operation_id": "019f3407-8cf2-750a-af59-43366bc44e40",
  "code": "DECIDED",
  "timestamp": 1786066800100,
  "data": {"result": "NO_WORK"}
}
```

**Identity / Retry / Compatibility：** 每次实际 SCAN2 对应独立的本地 Passage。确定 `WAIT` 后，WES 以新 `operation_id` 对同次 Passage 请求新的业务决定；响应未知时按原身份和冻结正文重发。WES 在本地关联各次请求及最终 `READY | NO_WORK`；`READY` 响应按目标单 Cell DTO 替换，`reel_totalthickness` 由 WES 本地计算。

**WMS Decision：Accepted。** WMS 对每次请求按实际 Bin、当前相关 revision 及库存决定 `READY | NO_WORK | WAIT`；同箱实际再次经过时重新决定。`cell_id` 保持 `BIN_ID + INDEX` 编码，并单独提供 `cell_index`。编码与 `bin_code` 的对应规则、`cell_index` 的最终类型/值域及设备附录 O16 映射按设备联合项冻结。

### 5.2 `outbound.material.decide@v1`，`BIN_CELL`

**Current Wire → Target Wire：** 请求携带 `task_id + source_locator + scanned_at`；已接受的目标字段以单个 `barcode` 替换 `six_in_one`。WMS 根据该料盘扫码原文及当前业务事实决定去向。

**Request Example：**

```json
{
  "operation_id": "019f3410-af77-71fd-9bde-0df75fcdeea1",
  "operation": "outbound.material.decide@v1",
  "timestamp": 1786064900000,
  "data": {
    "task_id": "PICK-20260811-001",
    "source_locator": {
      "type": "BIN_CELL",
      "rack_id": "RACK-5F-001",
      "rack_face": "A",
      "bin_code": "BIN-001",
      "cell_id": "BIN-0013"
    },
    "barcode": "P032-0561-000HF,Q3000,M0805S106K160CT,D260505,L100R028291,S72052872716100149602",
    "scanned_at": 1786064899900
  }
}
```

**Response Example：** `ACCEPT`，下一盘需要执行时携带对象；无下一盘时整个字段省略：

```json
{
  "operation_id": "019f3410-af77-71fd-9bde-0df75fcdeea1",
  "code": "DECIDED",
  "timestamp": 1786064900100,
  "data": {
    "result": "ACCEPT",
    "target_locator": {
      "type": "RACK_SLOT",
      "rack_id": "TRANSFER-RACK-01",
      "rack_face": "A",
      "slot_id": "A-05",
      "rack_layer": 1,
      "rack_column": 1
    },
    "next_source_action": {
      "cell_id": "BIN-0013",
      "cell_index": 3,
      "bin_type": 3,
      "reel_layer": 1,
      "reel_thickness": 20,
      "reel_diameter": 15
    }
  }
}
```

**Identity / Retry / Compatibility：** 请求沿自身 `operation_id` 和冻结正文重试；WES 在本地确认该 Passage 已获 `READY`、来源动作可执行后发起。WMS 根据来源、扫码原文及当前业务事实决定结果；`source_locator.cell_id` 使用 WMS 业务编码，下一来源由 WMS 给出 `cell_id + cell_index`。`barcode` 为非空 UTF-8 原文、最多 1000 字符；超界按 `INVALID_DATA` 拒绝；`REJECT`、`WAIT` 结果按长期合同处理。`target_preparation=ROTATE/REPLACE` 及换 Cell 的具体时点见第 6 节。

**WMS Decision：Accepted。** `barcode`、目标坐标和下一来源结构按目标示例；同箱再次经过及迟到原身份重试纳入联合验收。

### 5.3 `outbound.source.empty_decide@v1`，`BIN_CELL`

**Current Wire → Target Wire：** 请求沿用 `data={task_id,source_locator,observed_at}`；WMS 根据来源和空取事实返回 `RETRY | WAIT | SOURCE_DONE`。自动插件 MVP 后续实施本分支。

**Request Example：**

```json
{
  "operation_id": "019f3420-01be-7e11-b265-10de42c881f0",
  "operation": "outbound.source.empty_decide@v1",
  "timestamp": 1786065100000,
  "data": {
    "task_id": "PICK-20260811-001",
    "source_locator": {
      "type": "BIN_CELL",
      "rack_id": "RACK-5F-001",
      "rack_face": "A",
      "bin_code": "BIN-001",
      "cell_id": "BIN-0013"
    },
    "observed_at": 1786065099900
  }
}
```

**Response Example：**

```json
{
  "operation_id": "019f3420-01be-7e11-b265-10de42c881f0",
  "code": "DECIDED",
  "timestamp": 1786065100100,
  "data": {
    "result": "RETRY"
  }
}
```

**Identity / Retry / Compatibility：** 同一 Passage 的新一次确定空取形成新 Action；请求结果未知沿原身份重发。WMS 按请求身份与当前业务事实解释迟到结果，并返回 `RETRY | WAIT | SOURCE_DONE`。

**WMS Decision：Accepted。** 按现行来源和空取事实决定结果。

### 5.4 `outbound.material.movement_report@v1`，`BIN_CELL`

**Current Wire → Target Wire：** 请求沿用 `data={task_id,source_locator,PkgID,to_locator,occurred_at}`；WMS 根据该盘业务身份和位置事实记录结果。

**Request Example：**

```json
{
  "operation_id": "019f3422-f4a8-7247-98f0-8118dfb7f45e",
  "operation": "outbound.material.movement_report@v1",
  "timestamp": 1786065500000,
  "data": {
    "task_id": "PICK-20260811-001",
    "source_locator": {
      "type": "BIN_CELL",
      "rack_id": "RACK-5F-001",
      "rack_face": "A",
      "bin_code": "BIN-001",
      "cell_id": "BIN-0013"
    },
    "PkgID": "PKG-001",
    "to_locator": {
      "type": "RACK_SLOT",
      "rack_id": "TRANSFER-RACK-01",
      "rack_face": "A",
      "slot_id": "A-05"
    },
    "occurred_at": 1786065499900
  }
}
```

**Response Example：**

```json
{
  "operation_id": "019f3422-f4a8-7247-98f0-8118dfb7f45e",
  "code": "RECORDED",
  "timestamp": 1786065500100,
  "data": {}
}
```

**Identity / Retry / Compatibility：** WMS 以来源、`PkgID` 和已保存的最终物料决定核对本次位置事实，同事务记录一次位置/库存结果；原 `operation_id` 重放返回已保存结果。

**WMS Decision：Accepted。** 按逐盘业务身份记录库存与位置结果。

## 6. Automatic Picking 的 WMS 已接受目标 wire

以下四项已由 WMS 一并接受。上面的请求/响应示例展示组合后的目标 wire；WES 严格 DTO、OpenAPI 与生产接线按主合同推进。

| 修订项 | Current Wire → Target Wire | 待决语义 | Compatibility Impact | WMS Decision |
| --- | --- | --- | --- | --- |
| A1 `work_plan.READY` | `cell_ids[]` → 单个 `cell_id + cell_index + bin_type + reel_layer + reel_thickness + reel_diameter` | `cell_id` 保持 `BIN_ID + INDEX` 编码，`cell_index` 单独定位料箱内料格；设备类型/值域由 O16 冻结 | 一次性替换 READY DTO | WMS Accepted |
| A2 `material.decide` 请求 | `six_in_one` 六字段 → `barcode` 原文 | 非空 UTF-8 原文，最多 1000 字符；超界按 `INVALID_DATA` 拒绝 | 一次性替换请求 DTO | WMS Accepted |
| A3 `material.decide.ACCEPT.target_locator` | 增加 `rack_layer`、`rack_column` | WMS 直接给出，`ROTATE/REPLACE` 后同样提供 | 一次性替换 ACCEPT 目标 DTO | WMS Accepted |
| A4 `material.decide.ACCEPT.next_source_action` | `CONTINUE/SOURCE_DONE` → 下一盘/下一 Cell 对象，含编码 `cell_id` 与独立 `cell_index`；无后续时省略 | 同 Cell 参数及换 Cell 决定由 WMS 按当次业务事实提供 | 一次性替换 ACCEPT 结果 DTO | WMS Accepted |

A4 的 `RACK_SLOT` 来源是单盘储位，目标响应省略 `next_source_action`，其余直接取料来源由插件按当前有效 plan member 继续编排。`BIN_CELL` 的对象可表示同 Cell 下一盘或另一个 Cell；在当前授权和获批后继来源合同范围内，同一 Passage 是否继续取其他 revision 涉及的物料由 WMS 决定，WES 不按 revision 切断当前 Bin 工作。当前栈顶可靠取出且后继授权有效后，WES 可按主合同 §10.3 在当前盘 PUT 或报告尚未完成时下发下一条来源命令，实际交接由 ECS/PLC 硬件锁裁决；依赖当前盘位置报告的完成条件仍等待 WMS 确认。`REJECT` 继续使用 `business_exception_code + ng_locator + source_disposition`；`WAIT` 继续携带 `retry_after_ms`。自动插件 MVP 暂不执行 NG，不得据此删除长期合同中的 NG 协议。

设备映射须与[机械臂附录](device-annex-automatic-picking-arms.md)共同确认：`rack_side` 与 `rack_face` 的值域（O11）；直接取料 `slot_id` 是否能由 WMS 给出 ECS `bin_*` 参数（O13）；独立 `cell_index` 如何映射 ECS `bin_cell_location`（O16），不得把编码 `cell_id` 直接用作该设备字段；DirectPick 如需 `bin_type/reel_*` 参数，`plan_delta.added_direct_picks[]` 是否应携带这些数据（O17）。这些问题未闭合前，不把设备参数推断写入 WES 主合同。

### 6.0 字段类型确认（2026-10-16）

WMS 已补充确认：`cell_index` 为 int，值为 1–7；`bin_type` 为 int，值为 2、3、6；
`reel_layer`、`rack_layer`、`rack_column` 为 int；`reel_thickness`、`reel_diameter` 为 float；
`barcode` 为原始 str，最多 1000 字符（沿用主合同非空 UTF-8，不裁剪、不拆分）。
未确认的层数/坐标/尺寸物理范围不额外推定；设备字段映射、`cell_id` 编码对应及 `PkgID` 来源仍在各自关口处理，不阻塞共享 DTO 与 typed 接线。

### 6.1 退料货架整架任务完成 API（WMS 已确认，WES 待实施）

**本次涉及 revision 的 API 全量摘录：** 下表列出计划版本来源、全部按来源成员定位的交互，以及本节整架完成 API。各 API 的完整请求/响应示例见所指章节；“WES 已实现”只描述本地接线状态。

| API | 方向 | revision 字段与作用 | 当前状态 / 示例 |
| --- | --- | --- | --- |
| `outbound.picking_task.plan_delta@v1` | WMS → WES | 已有 `data.plan_revision`；连续计划版本，也是本版新增成员的来源 revision | WMS 已确认；WES 已实现，见 §3 |
| `outbound.bin.inbound_batch@v1` | WES → WMS | `data.plan_revision` 指向五层来源货架面成员 | WMS 已确认；WES 已实现，见 §4.1 |
| `outbound.picking_task.cancel@v1`，仅 `PLAN_MEMBERS` | WMS → WES | `data.bin_source_racks[].plan_revision`、`data.direct_pick_sources[].plan_revision` 精确选择待取消成员；`TASK` 按任务身份处理 | WMS 已确认；WES 已实现，见 §4.2 |
| `outbound.manual_rack.direct_pick_completed@v1` | WMS → WES | `data.plan_revision` 指向本次完成的面级直接取料成员 | WMS 已确认；WES 已实现，见 §4.3 |
| `outbound.material.decide@v1`，仅 `RACK_SLOT` | WES → WMS | `data.plan_revision` 指向本次取料的来源储位成员 | WMS 已确认；WES 待实施，见 §4.4 |
| `outbound.source.empty_decide@v1`，仅 `RACK_SLOT` | WES → WMS | `data.plan_revision` 指向本次空取的来源储位成员 | WMS 已确认；WES 待实施，见 §4.5 |
| `outbound.material.movement_report@v1`，仅 `RACK_SLOT` | WES → WMS | `data.plan_revision` 须与前序物料决定指向同一来源成员 | WMS 已确认；WES 待实施，见 §4.6 |
| `outbound.return_rack.task_completed@v1` | WMS → WES | `data.through_plan_revision` 是整架完成范围的计划截止点 | WMS 已确认；WES 待实施；请求/响应示例见本节下文 |

§4.1–§4.6 的成员 `plan_revision` 取原 `plan_delta` 对应成员的版本。整架完成的 `through_plan_revision` 覆盖该任务/货架截至指定计划版本的**所有**成员，逐成员结果仍分别闭合。`outbound.picking_task.completion_confirm@v1` 使用已有的 `data.last_applied_plan_revision` 核对任务计划流；`return_rack.arrival_report` 等物理/Passage API 按各自的 Transport 或 Passage 身份关联，见 §7。

[人工出库货架搬运规则](manual-outbound-rack-transport.md)涉及退料货架换面和离场，但当前已实现的
`outbound.manual_rack.direct_pick_completed@v1` 仅表示 WMS 确认一个 `task_id + plan_revision + rack_id + rack_face`
成员的直接取料完成。任务级 `outbound.picking_task.completion_confirm@v1`、离场去向决定
`outbound.rack.departure_decide@v1` 和 Transport 物理终态各有独立含义，均不能代替退料货架**整架任务完成**的
独立合同。本 API 的 operation 与目标 wire 已获 WMS 接受，WES 严格 DTO、OpenAPI 与生产接线仍待实施。

**WMS 已接受的目标合同：** WMS 向 WES 报告一个 PickingTask 在指定退料货架上的**全部业务工作完成**。面级工作通过 `direct_pick_completed` 分别结清，货架换面与离场的物理结果沿各自 Transport 身份收敛。

| 项 | 目标要求 |
| --- | --- |
| 方向 / 端点 | WMS → WES；`POST {{WES_BASE_URL}}/api/v1/wms/events`，复用公共回调信封 |
| operation | `outbound.return_rack.task_completed@v1` |
| 触发 | WMS 已确认该 `task_id + rack_id` 截至 `through_plan_revision` 的全部直接取料成员完成或取消；后续 revision 仍可向同架追加，同架其他 PickingTask 分别判定 |
| 业务完成范围 | `task_id + rack_id + through_plan_revision`；每条通知沿原 `operation_id` 和冻结正文重试，扩大范围使用新通知身份；当前完成截止版本只前进 |
| ACK | `ack_mode=EVIDENCE_ACCEPTED`；首次 `202 / RECEIVED` 前同事务提交 `InboundEvidence` 和消息接收身份，后续关联及业务应用可异步进行 |

目标 `data` 为严格对象，只含下表字段；顶层 `operation_id + operation + timestamp + data`、UUIDv7、Identifier 和响应格式遵守[公共回调合同](../contracts/wms-async-callback-envelope-contract.md)。

| 字段 | 类型 / 要求 | 含义 |
| --- | --- | --- |
| `task_id` | 必填，出库合同 Identifier | 该次 PickingTask；不能只用 `rack_id` 跨任务收口 |
| `rack_id` | 必填，出库合同 Identifier | 已纳入该任务直接取料计划的退料货架 |
| `through_plan_revision` | 必填，正整数 / WMS | 本次整架完成声明覆盖的最高 `plan_delta` 版本；须为 WMS 已发出的连续计划流版本，不能取最后一个面级成员的 revision 代替 |
| `completed_at` | 必填，正整数 UTC Unix 毫秒，且不晚于 `timestamp` | WMS 确认整架业务工作完成的时间；不是物理离场时间 |

**首次提交示例：**

```http
POST {{WES_BASE_URL}}/api/v1/wms/events
```

```json
{
  "operation_id": "019f3404-a100-7b01-8b01-000000000099",
  "operation": "outbound.return_rack.task_completed@v1",
  "timestamp": 1788390200000,
  "data": {
    "task_id": "PICK-20260902-001",
    "rack_id": "RETURN-RACK-01",
    "through_plan_revision": 2,
    "completed_at": 1788390199000
  }
}
```

```json
{
  "operation_id": "019f3404-a100-7b01-8b01-000000000099",
  "code": "RECEIVED",
  "timestamp": 1788390200100,
  "data": {}
}
```

**同一请求重放示例：** WMS 未收到首次响应时，原样重发上面的完整 JSON，包括 `operation_id` 和 `timestamp`；若首次接收已提交，WES 返回 `200 / DUPLICATE`，响应的 `timestamp + data` 沿用首次保存值。

```json
{
  "operation_id": "019f3404-a100-7b01-8b01-000000000099",
  "code": "DUPLICATE",
  "timestamp": 1788390200100,
  "data": {}
}
```

**同身份内容漂移示例：** 若重发时把上例 `data.rack_id` 改为 `RETURN-RACK-02`，其余身份保持不变，返回 `409 / CONFLICT`；WMS 停止自动重发并核查，不能仅换一个 `operation_id` 规避冲突。字段不合法且身份可识别时返回 `422 / REJECTED`；无法持久接收时返回 `503 / UNAVAILABLE`，WMS 以原身份和原正文重试。`400`、`413` 及确定拒绝后的修正方式沿用公共回调合同，不另设错误体系。

```json
{
  "operation_id": "019f3404-a100-7b01-8b01-000000000099",
  "code": "CONFLICT",
  "timestamp": 1788390200200,
  "data": {}
}
```

**业务应用与验收边界：** WES 接收后先确认同任务计划流已连续应用至 `through_plan_revision`，再将整架通知作为 `task_id + rack_id` 且 `plan_revision <= through_plan_revision` 的成员已闭合的权威业务事实，不等待面级结果，也不补造逐面完成或取消证据。计划未齐时保留 Evidence 由持久 owner 继续等待。同任务后续 revision 允许再次向同架追加成员，旧完成事实不能阻止或闭合新成员；已有后续成员不阻止旧通知应用。后续范围完成使用新通知身份推进最大截止版本，旧通知重放不回退当前完成范围。同架另一 PickingTask 独立核对；已接纳而结果未知的换面/离场动作继续沿原 Transport 身份对账。整架完成与 `departure_decide`、WMS 释放占用的先后关系按对应工作线离场合同执行。

**当前修订：** 按已批准的 SRS 原则修正整架完成的作用范围；原“同任务同架以后不再追加”的终结性承诺退出当前合同。整架完成是按 `task_id + rack_id + through_plan_revision` 限定范围的 WMS 业务事实，ACK、错误及应用规则如上。联合验收覆盖多面、完成后同任务同架跨 revision 再次追加、旧完成不闭合新成员、范围推进、取消、先完成任务后离场、重复/迟到及内容漂移。严格 DTO/OpenAPI 和现有接线沿用，双方联调按本次范围语义验收。

## 7. 其他 API 的身份依据与 MVP 边界

`return_rack.arrival_report` 以 `transport_task_id` 关联物理到位 Fact，同一 Fact 可供多个 Requirement 求值；`work_completed` 以通知的 `task_id + bin_code` 匹配当前等待完成的料箱；`return_batch` 消费 RETURN_BUFFER 的 Passage/FIFO 候选；`departure_decide` 处理当前货架离场；`completion_confirm.last_applied_plan_revision` 核对计划流游标。各 API 按自身业务身份关联。

WMS 接受自动线独立 WorkLine 候选 `workline_code=KT11` 及 `task_type=AUTO` 的登记与处理；`issued`、`queue_changed`、`return_batch`、`drain_rack_decide`、`departure_decide` 在 AUTO 下沿已确认的 operation wire，具体触发由自动插件业务流程决定。`prepare` 的 DTO 沿用主合同；`plan_delta` 的 T1-A 联合验收仍单独关闭。

**MVP implementation scope：** 首版自动插件只实现正常放置（BIN_CELL 与 RACK_SLOT）及目标换面/换架。`source.empty_decide`、单盘/Cell/Bin NG 可留到后续切片；这是插件实施范围，不是删除主合同 operation 或修改 WMS 长期能力。收到当前插件不能安全处理的确定结果时须保留 Evidence 并按已批准恢复合同处理，不能猜成正常成功。

## 8. 确认记录和后续实施

WMS 对第 2 节截至 2026-09-23 的既有候选项答复为 `Accepted`，由 WES 根据当时确认记录。该记录不代替 §9 的容量接续与核销证据；9H 按收窄后的两项记录双方结论。WES 以 SCAN2 证据创建本地 Passage、确定 `WAIT` 后以新 `operation_id` 重问、响应未知时沿原身份重试；WMS 在三个 BIN_CELL API 中按业务事实处理迟到/重复请求和新旧工作。最终字段边界、设备映射和联合用例按 §6 的具体依赖继续冻结与验收。

已确认的业务规则与目标 wire 写入[出库主合同](../contracts/wms-outbound-picking-task-integration-requirements.md)。后续同步严格 DTO/OpenAPI/业务调用、完成联合用例验证及设备映射；既有条目完成且新增 §9 已有双方冻结及独立验收记录后，本文才可归档为历史确认记录。主合同作为目标合同真源，联调仍须核对双方实际接线版本。


## 9. 9H 正常补架的双方冻结确认清单

### 9.1 收窄范围、基线与现有证据

本节保留原导航，只核对[主合同 §9.2.3 的 9H 差额](../contracts/wms-outbound-picking-task-integration-requirements.md#9h-replenishment-freeze)中的容量接续与回库核销证据，不另建协议或设计。复用[人工线 §5.6](../contracts/wms-manual-outbound-picking-integration-requirements.md)的现有 drain 基线；撤回旧 F1–F8 作为整组冻结前提的做法，以及 drain/return_batch 必须新增 `reservation_operation_id` 的提案。WES 私有 current 交接归插件设计和验收，不要求 WMS 确认其字段结构。

| 已有基线 | 当前依据与边界 |
| --- | --- |
| 两类请求 | drain 的 data 仍只有 `workline_code + required_slot_count`；普通 return_batch 仍使用当前 `workline_code + rack_id + rack_face + return_candidates`。既有 SDK/DTO、typed converter、Adapter/OpenAPI 与人工消费者不因 9H 默认迁移；完整示例沿主合同 §9.2.2–9.2.3 |
| 身份与结果 | 公开幂等键、同身份同正文重放、内容漂移冲突沿原合同；技术重试不换身份，确定 WAIT 到期和正常耗尽后的新决定使用新 operation。原 READY/Transport 结果不可改写 |
| 排空接续 | NO_BATCH 后换面/离场，供架耗尽后再次申请；已发布 NO_BATCH/未分配尾部可被合法新请求承接，已分配或未知继续原依赖，不越过物理 FIFO 队头 |
| 容量与物理所有权 | WMS 内部按工作线与 drain operation 关联活动容量，普通 return_batch 按当前架面消费；未知物理结果不释放，本次数量全部权威回库后关闭。WES 本地轮次退出、ACK 或 ACCEPTED 不证明容量已核销或物理成功 |
| 自动插件差额 | 已有首次 READY 纯规则、只读 PostgreSQL、单次 current 原子接续、补架 WAIT 续问/责任归零出口及连续 READY 供架的离线实现；验证状态见实施计划，真实容量接续与核销仍待补证。保留原 execution 范围是自动插件本地责任约束，不要求 WMS 接收该私有身份 |

**已核对的联调证据及限度。**2026-10-20 对联调服务器 WES 库的只读核对，发现 KT16 两轮：`01a155d6-99df-77ae-9077-2e9c0270bd6a` 请求数量 1、供架 510003，后续 return_batch 三个候选仅分配 A000000395；`01a155db-f102-78cb-8520-5d8e6ce5e0d0` 请求数量 2、供架 510004/510034，前轮未分配的 A000002688、A000000288 以相同 Return ID 进入新请求。两轮均无新增关联字段；决定 Evidence 已应用发布，相关 BIN_MOVE 和货架进出场有匹配成功/已知终位记录。

第一轮最初只申请 1 个名额且已履约，另外两个箱在其后到达；因此这些记录证明“旧未分配尾部可进入新轮次”，不能证明“原 READY 承诺未履约完就耗尽”的容量接续。它们是 WES 留存的联调结果，不是 WMS 内部容量账或现场物理验收证据。现有真实 worker 测试使用 Mock WMS，也不承接下方容量核销验收；同 operation 重试换架造成的响应冲突更不能当作正常补架证据。

### 9.2 仅保留两项 WMS 补证

| 编号 | 请 WMS 回复的现有规则与证据 | 已确认边界 | 当前状态 |
| --- | --- | --- | --- |
| C1 原承诺未履约完的继续供架 | 原 READY 例如承诺 3 箱、权威回库 1 箱后供架耗尽，WES 用现有工作线和当前正数数量、新 operation 再申请时，内部如何关联剩余容量与新决定？如何与原需求已结束后的新需求区分？WAIT/READY、并发申请及旧请求重放如何避免数量重复累计、原义务误释放或改写旧响应？请提供内部关联依据、事务/串行边界、版本和完整时序 | 已有无新增字段的内部关联合同继续有效；不预设必须增加外部 reservation 引用，也不预设“无引用遇活动容量”必须冲突 | WMS 待提供；WES 待核对 |
| C2 原分配的回库核销 | 普通/drain 两入口和不同承接架的分配如何关联容量责任？部分 READY/NO_BATCH/未选尾部如何计数？由哪些匹配的分配、Transport/member 与成功终位事实核销一次，何时关闭容量？重复/回调早于 ACK/迟到旧结果/同箱码新经过如何不串账？请提供内部键、账务前后样例、关闭条件和歧义/冲突处理依据 | ACK、未知、失败不能冒充成功回库；不从 WES archived_at 推断 WMS 释放。具体内部实现及错误适用由 WMS 提供，不指定新账表、唯一匹配算法或新错误码 | WMS 待提供；WES 待核对 |

每项补证记录关联双方结论、确认人及日期、实现/合同版本、请求/响应和容量账或等价证据位置；目前不预填。已有合同与实装一致则记录证据，不为形式新增字段。只有实际证明现有机制存在无法消除的关联歧义，才回到主合同评审最小差额；具体决定未形成前不猜字段或错误码。合同结论、WES 离线验证、WMS 实装及联合验收分别记录，不能互相替代。

### 9.3 差额验收正反例（待执行，不新增请求字段）

下表 C1/C2 的账务预期需以 WMS 回复补齐可观察证据；WES 的本地原子交接另由插件测试拥有。示例数量、箱名和位置仅为讨论向量，不是生产配置或映射。

| 正例 | 输入/时序 | 需验证的结果与证据 |
| --- | --- | --- |
| P1 未履约完时补架 | 首次 READY 承诺 3 箱，仅 1 箱权威回库，所有本轮供架已合法交接；2 箱仍为合格 FIFO，现有 DTO 新 operation 请求数量 2 | C1 说明如何承接原剩余容量、不重复累计；WES 只产生一个 successor，旧退出不当作全部回库 |
| P2 补架 WAIT | P1 得到确定 WAIT；到期后再用新 operation 和当前正数前缀数量求值 | C1 提供 WAIT 前后容量账和后续 READY；WES 保存固定到期点及原责任，未知请求不进入此分支 |
| P3 已履约后的新需求 | 原承诺全部权威回库且原供架交接，新到成员按当前 FIFO 创建新 drain | C1 区分新需求与未完续供；不让旧请求重放改绑新需求。现有 KT16 证据只覆盖这类接续范围 |
| P4 普通入口/不同架承接 | 原成员由合法普通 return_batch 得到目标并通过原 BIN_MOVE 成功回库，或 READY 只选择部分候选 | C2 提供实际分配与核销变化，未分配尾部不冒充履约；WES 按精确 execution 同事务更新当前责任 |
| P5 重复和先回调后 ACK | 原 Transport/member 的匹配成功与终位先于 ACK，随后结果重放 | C2 证明只核销一次；WES 原动作留证、幂等应用，后到 ACK 不生成另一搬运或回库成功 |

| 反例 | 输入/冲突 | 应保留或拒绝的边界 |
| --- | --- | --- |
| N1 同身份漂移 | 重试原决定时修改数量、候选或已保存响应的供架 | 沿现有幂等/响应冲突合同保留原身份和证据，不当正常补架、不覆盖旧决定 |
| N2 非成功核销 | 只有 ACK/ACCEPTED、未知/FAILED，或成功终位不匹配原分配 | 不作成功回库或释放依据；C2 给出实际核销门禁，不新增已接纳后 FAILED 重决策 |
| N3 旧结果串新轮次 | 新 current/新容量需求已存在，旧结果迟到或出现同箱码新 execution | WES 校验原因果与成员，不重开旧 current；C2 证明结果记回原归属，不能给新需求充数 |
| N4 无可请求前缀 | 原责任全在独立动作中，或实际 FIFO 队头未知/未合格/已分配 | WES 保留责任与原 owner，不发零数量、不越序，不阻止无直接依赖的独立动作 |
| N5 原请求未决 | 请求交付未知或结果未应用发布，即使当前候选减少 | Confirmation/FactProcessor 先恢复原义务，不新建等价决定、不把未知当 WAIT 或耗尽 |

### 9.4 后续顺序与恢复 owner

自动插件已用现有 DTO 和显式离线前提实现单次新 Confirmation、旧 current 退出、新 current 成立及完整剩余责任的事务交接，并补齐补架 WAIT 持久续问、责任归零出口及连续 READY 供架。后者按当前请求范围读取，复用 9B–9G，覆盖两轮耗尽接续和正常收口；验证状态见实施计划。离线能力不代替下述 C1/C2 的真实补证与联合验收，也不因此启用生产装配。不再把新增字段、两域消费者迁移或整组 F1–F8 冻结作为前提，不重复首次 READY 已覆盖的纯规则/只读/多架组合矩阵。

自动 batch 负责准入、current 和到期续问，Confirmation 负责原身份交付，FactProcessor 负责应用发布，原 Transport/Motion/Allocation 保持物理动作及直接因果恢复；WMS 维护容量与核销。本清单不取得运行时 owner，不把补架业务放进基础层，不做 bin-line-common 抽取。

C1/C2 未补证仅阻塞原承诺尚未履约完时的真实补架/核销验收；不阻塞上述离线事务、既有已确认路径或独立任务。设备映射、扫码关联、PkgID 和首批期限仍只限制各自路径。不接完成确认、已接纳后 FAILED 重决策或真实派发；本次文档整理不改生产代码、不接持久执行、不提交、不部署，也不表示已向 WMS 发送、已获新确认或已完成现场验收。
