---
title: WMS/WES Integration Contract Amendment Proposal：自动拣料
status: Draft
created_at: 2026-09-21
updated_at: 2026-09-23
audience: WMS 开发组、WES 架构组
scope: 出库主合同的逐项修订提案；未获双方确认的字段和示例不构成现行接口合同
related:
  - docs/contracts/wms-outbound-picking-task-integration-requirements.md
  - docs/contracts/wms-manual-outbound-picking-integration-requirements.md
  - docs/integration/outbound-picking-plan-delta-joint-freeze.md
  - docs/integration/device-annex-automatic-picking-arms.md
---

# WMS/WES Integration Contract Amendment Proposal：自动拣料

本文只供 WMS/WES **逐项确认主合同修订**。[出库主合同](../contracts/wms-outbound-picking-task-integration-requirements.md)仍是当前接口真源。下文第 4–6 节的 Target Wire 均为提案，当前 DTO、OpenAPI 和生产代码尚未支持；不得直接用于联调。WMS 对每项可答复 `Accepted`、`Revised` 或 `Rejected`，未答复保持 `Pending`。获批后一次性替换主合同的严格 DTO、示例及处理规则，再将本文标为 `Archived`；系统尚未发布，不保留旧字段别名、双路径或旧数据迁移。

## 1. Identity Rules

| 身份 | 含义 | 范例 |
| --- | --- | --- |
| Plan Member Identity | 某版 `plan_delta` 新增的一条来源 Requirement | `task_id + plan_revision + rack_id + rack_face`；DirectPick 增加精确 `source_locator` |
| Passage Identity | 同一物理料箱的一次经过；`task_id + bin_code` 可对应多次 Passage | `BOX-A/P1` 与 `BOX-A/P2` |
| Action / Operation Identity | 一次外部逻辑动作或决定 | 入线批次、准入、`work_plan`、Transport 的原 operation/task 身份 |
| Message Identity | 消息本身的可靠接收、重试和去重身份 | 顶层 `operation_id` |

`plan_revision` 解决 **Plan Member identity**，不解决 **Passage identity**。消息身份不一定等于业务关联身份：`work_completed` 顶层 `operation_id` 标识完成消息；`data.admission_operation_id` 关联原准入 Action 和 Passage。技术重试保持同一顶层身份、`timestamp` 和冻结正文；形成新的业务 Action 才创建新身份。WMS Response 的顶层 `operation_id` 匹配原请求，不需要重复附加请求中的关联字段。

## 2. 接口身份矩阵与修订状态

| API | 业务关联域 | 当前可用身份 / 拟修订字段 | WMS Decision |
| --- | --- | --- | --- |
| `plan_delta` | Plan stream / Plan Member | 已有 `data.plan_revision`；跨 revision 同来源的成员规则待确认 | Pending |
| `inbound_batch` | Plan Member | 新增 `data.plan_revision` | Pending |
| `PLAN_MEMBERS cancel` | Plan Member | 两类成员选择器各新增 `plan_revision` | Pending |
| `direct_pick_completed` | Plan Member | 新增 `data.plan_revision` | Pending |
| `material.decide`，`RACK_SLOT` | Plan Member | 新增 `data.plan_revision` | Pending |
| `source.empty_decide`，`RACK_SLOT` | Plan Member | 新增 `data.plan_revision` | Pending |
| `material.movement_report`，`RACK_SLOT` | Plan Member | 新增 `data.plan_revision` | Pending |
| `work_plan` | Passage / Work | 提议复用原 `inbound_batch.operation_id` 关联本次入线；`work_plan.operation_id` 关联已确定工作计划 | Pending |
| `material.decide`、`source.empty_decide`、`material.movement_report`，`BIN_CELL` | Passage / Work | 提议新增 `data.work_plan_operation_id`，指向该 Passage 的确定 `work_plan` | Pending |
| `work_completed` | Passage / Admission | 已有 `data.admission_operation_id`；不加 revision | 无本轮 wire 变更 |
| `return_rack.arrival_report` | Physical Action | 已有 `transport_task_id`；不加 revision | 无本轮 wire 变更 |
| `return_batch` | Passage / FIFO | 当前 RETURN_BUFFER 候选；不加 revision，Passage 关联单独核实 | 无本轮 revision 字段变更 |

下文示例使用与主合同相同的公共信封。`Current Wire` 指向主合同现行字段；`Target Wire` 是本修订案的候选字段和完整示例。示例响应只展示代表性业务结果，其他结果和 `409/422/503` 仍以主合同为准，除非下文明确拟修改。

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

**Identity / Retry / Compatibility：** 同消息身份、同正文重报不新增成员；新 revision 使用新消息身份。不得按整个 `task_id + rack_id + rack_face` 排重；撤销必须指向具体 revision。当前 wire 不变，实现中的全任务排重、索引和测试须同步替换。

**WMS Decision：Pending。** 请确认增量是新增与显式取消，而非“新 revision 自动覆盖全部旧成员”；并确认同 revision 内来源重复的业务 key。

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

**WMS Decision：Pending。** 请确认 WMS 能按该成员返回独立结果。

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

**WMS Decision：Pending。** 请确认部分选择器已取消或不存在时沿主合同逐项幂等处理。

### 4.3 `outbound.manual_rack.direct_pick_completed@v1`

**Problem / Current Wire → Target Wire：** 现行 `data={task_id,rack_id,rack_face,completed_at}` 无 revision。新增必填 `data.plan_revision`；人工线仍由 WMS/PDA 发完成事实，自动线不因本提案新增面级回调。

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

**Identity / Compatibility：** 顶层身份去重消息；`task_id + plan_revision + rack_id + rack_face` 结清成员。替换现行严格 DTO 和面级完成唯一键。

**WMS Decision：Pending。**

### 4.4 `outbound.material.decide@v1`，`RACK_SLOT`

**Problem / Current Wire → Target Wire：** 现行 `data={task_id,source_locator,six_in_one,scanned_at}`；同储位跨 revision 无法归属。新增必填 `data.plan_revision`。`six_in_one → barcode` 与响应坐标/下一来源结构是另一个待确认修订项，见第 6 节；下例展示**两项提案同时接受时**的目标 wire。

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
      "rack_layer": "1",
      "rack_column": "1"
    }
  }
}
```

**Identity / Compatibility：** `plan_revision` 绑定原 DirectPick 成员；物料结果仍匹配原请求 `operation_id`。若 `6` 的 barcode/响应改动未获接受，本项 revision 修订仍须独立决定，不能把合并示例当作批准结果。严格 DTO 一次性替换。

**WMS Decision：Pending。** 请分别答复 revision、barcode、目标坐标、下一来源结构。

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

**WMS Decision：Pending。**

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

**Identity / Compatibility：** WMS 以 `task_id + plan_revision + source_locator + PkgID` 对应前序最终决定；同消息身份重放不得重复扣账。替换 DTO/业务关联，不保留无 revision 的 RACK_SLOT 分支。

**WMS Decision：Pending。**

## 5. Passage / Work 关联提案

**Problem：** 同一任务同一物理料箱可以重复投入：`T1/BOX-A/P1 → work_plan OP100`；后来 `T1/BOX-A/P2 → work_plan OP200`。现行 `work_plan` 只带 `task_id + bin_code + scanned_at`，后续 BIN_CELL API 只带箱码和 Cell；`task_id + bin_code`、`plan_revision` 及“最新一条”均不能精确关联这两次工作。原稿“同一 Bin 只调用一次”的约束必须删除。

**Target identity 候选：** 复用已经存在的 `inbound_batch.operation_id` 和确定的 `work_plan.operation_id`，不新增全局 Passage token。`work_plan.data.inbound_batch_operation_id` 指向本次选出该 Bin 的原批次；同批内 `bin_code` 唯一。WMS `READY` 结果所对应的 `work_plan.operation_id` 冻结为该 Passage 的工作身份，后续三个 BIN_CELL API 使用 `data.work_plan_operation_id`。`WAIT` 后的新 `work_plan` 请求保留原 `inbound_batch_operation_id`，直到得到确定 `READY | NO_WORK`。这只是待 WMS 核实的候选：双方须证明每次重新投入都有不同的入站批次身份，且 WES 能从实际 Passage 确定原批次；若不成立，须修订身份方案，不能按箱码猜。

### 5.1 `outbound.bin.work_plan@v1`

**Current Wire → Target Wire：** 现行请求 `data={task_id,bin_code,scanned_at}`；新增必填 `data.inbound_batch_operation_id`。原 `READY.cell_ids[]` 拟改为单个 `cell_id` 及物理参数；`NO_WORK`、`WAIT` 形态不变。

**Request Example：**

```json
{
  "operation_id": "019f3407-8cf2-750a-af59-43366bc44e20",
  "operation": "outbound.bin.work_plan@v1",
  "timestamp": 1786064800000,
  "data": {
    "task_id": "PICK-20260811-001",
    "inbound_batch_operation_id": "019f3405-1111-7b01-8b01-000000000001",
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
    "cell_id": "3",
    "bin_type": "3",
    "reel_layer": "1",
    "reel_thickness": "20",
    "reel_diameter": "15"
  }
}
```

**Identity / Retry / Compatibility：** P1 与 P2 使用不同的 `inbound_batch_operation_id` 和 `work_plan.operation_id`。同请求未知沿原身份、原冻结正文重发；`WAIT` 后重新求值使用新 `work_plan.operation_id`，但仍属同一 Passage。旧 `task_id + bin_code` 工作计划唯一约束及 `cell_ids[]` DTO 均须替换；`reel_totalthickness` 由 WES 本地计算，不加入 wire。

**WMS Decision：Pending。** 请确认批次身份可确定 Passage、`cell_id` 可直接取 ECS `bin_cell_location` 的 `1..7` 字符串，以及参数值域/来源。`NO_WORK`、`WAIT` 不改；请分别确认它们在同箱重复经过时的幂等边界。

### 5.2 `outbound.material.decide@v1`，`BIN_CELL`

**Current Wire → Target Wire：** 现行 `data={task_id,source_locator,six_in_one,scanned_at}`；提议新增必填 `work_plan_operation_id`，`six_in_one` 替换为单个 `barcode`。不加 `plan_revision`。

**Request Example：**

```json
{
  "operation_id": "019f3410-af77-71fd-9bde-0df75fcdeea1",
  "operation": "outbound.material.decide@v1",
  "timestamp": 1786063000000,
  "data": {
    "task_id": "PICK-20260811-001",
    "work_plan_operation_id": "019f3407-8cf2-750a-af59-43366bc44e20",
    "source_locator": {
      "type": "BIN_CELL",
      "rack_id": "RACK-5F-001",
      "rack_face": "A",
      "bin_code": "BIN-001",
      "cell_id": "3"
    },
    "barcode": "P032-0561-000HF,Q3000,M0805S106K160CT,D260505,L100R028291,S72052872716100149602",
    "scanned_at": 1786062999900
  }
}
```

**Response Example：** `ACCEPT`，下一盘需要执行时携带对象；无下一盘时整个字段省略：

```json
{
  "operation_id": "019f3410-af77-71fd-9bde-0df75fcdeea1",
  "code": "DECIDED",
  "timestamp": 1786063000100,
  "data": {
    "result": "ACCEPT",
    "target_locator": {
      "type": "RACK_SLOT",
      "rack_id": "TRANSFER-RACK-01",
      "rack_face": "A",
      "slot_id": "A-05",
      "rack_layer": "1",
      "rack_column": "1"
    },
    "next_source_action": {
      "cell_id": "3",
      "bin_type": "3",
      "reel_layer": "1",
      "reel_thickness": "20",
      "reel_diameter": "15"
    }
  }
}
```

**Identity / Retry / Compatibility：** `work_plan_operation_id` 必须命中同任务、同箱、同一次 Passage 的确定 `READY`，不能用最新箱码工作计划。barcode 字段名、长度、字符集和 `INVALID_DATA` 边界待确认；`REJECT`、`WAIT` 结果继续保留在长期合同。`target_preparation=ROTATE/REPLACE` 及换 Cell 的具体时点见第 6 节。

**WMS Decision：Pending。** 请分别确认关联字段、barcode、坐标和下一来源结构。

### 5.3 `outbound.source.empty_decide@v1`，`BIN_CELL`

**Current Wire → Target Wire：** 现行 `data={task_id,source_locator,observed_at}`；新增必填 `work_plan_operation_id`，不加 `plan_revision`。自动插件 MVP 不实施本分支。

**Request Example：**

```json
{
  "operation_id": "019f3420-01be-7e11-b265-10de42c881f0",
  "operation": "outbound.source.empty_decide@v1",
  "timestamp": 1786065100000,
  "data": {
    "task_id": "PICK-20260811-001",
    "work_plan_operation_id": "019f3407-8cf2-750a-af59-43366bc44e20",
    "source_locator": {
      "type": "BIN_CELL",
      "rack_id": "RACK-5F-001",
      "rack_face": "A",
      "bin_code": "BIN-001",
      "cell_id": "3"
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

**Identity / Retry / Compatibility：** 同一 Passage 的新一次确定空取形成新 Action；请求结果未知沿原身份重发。`RETRY | WAIT | SOURCE_DONE` 联合不变，WMS 不得以另一 Passage 的同箱 Cell 结果关闭本次工作。严格 DTO 替换。

**WMS Decision：Pending。**

### 5.4 `outbound.material.movement_report@v1`，`BIN_CELL`

**Current Wire → Target Wire：** 现行 `data={task_id,source_locator,PkgID,to_locator,occurred_at}`；新增必填 `work_plan_operation_id`，不加 `plan_revision`。

**Request Example：**

```json
{
  "operation_id": "019f3422-f4a8-7247-98f0-8118dfb7f45e",
  "operation": "outbound.material.movement_report@v1",
  "timestamp": 1786065500000,
  "data": {
    "task_id": "PICK-20260811-001",
    "work_plan_operation_id": "019f3407-8cf2-750a-af59-43366bc44e20",
    "source_locator": {
      "type": "BIN_CELL",
      "rack_id": "RACK-5F-001",
      "rack_face": "A",
      "bin_code": "BIN-001",
      "cell_id": "3"
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

**Identity / Retry / Compatibility：** `work_plan_operation_id`、来源、`PkgID` 对应同 Passage 的最终物料决定；WMS 同事务记录一次位置/库存结果，原身份重放不重复扣账。替换当前仅凭 `task_id + bin_code` 或“最新决定”的查询。

**WMS Decision：Pending。**

## 6. Automatic Picking 的其余待决 wire

这些是原稿提出的**独立**合同变更，不随 `4` 的 revision 或 `5` 的 Passage 身份自动获批。上面的目标请求/响应示例将它们组合展示，便于 WMS 审阅；WMS 可分别接受、修订或拒绝。

| 修订项 | Current Wire → Target Wire | 待决语义 | Compatibility Impact | WMS Decision |
| --- | --- | --- | --- | --- |
| A1 `work_plan.READY` | `cell_ids[]` → 单个 `cell_id + bin_type + reel_layer + reel_thickness + reel_diameter` | 单 Cell 起点、后续由 `material.decide` 决定；值域、来源及首次 READY 能否给全参数 | 替换 READY DTO，不保留数组别名 | Pending |
| A2 `material.decide` 请求 | `six_in_one` 六字段 → `barcode` 原文 | 字段名、长度、字符集和 `INVALID_DATA` 边界 | 替换请求 DTO，不双传两种条码 | Pending |
| A3 `material.decide.ACCEPT.target_locator` | 增加 `rack_layer`、`rack_column` | WMS 直接给出，`ROTATE/REPLACE` 后同样提供 | 替换 ACCEPT 目标 DTO，不从 `slot_id` 反推 | Pending |
| A4 `material.decide.ACCEPT.next_source_action` | `CONTINUE/SOURCE_DONE` → 下一盘/下一 Cell 对象，或无后续时省略 | 同 Cell 参数是否每盘变化；换 Cell 能否在当前盘 `movement_report` 前决定 | 替换 ACCEPT 结果 DTO，不保留枚举兼容 | Pending |

A4 的 `RACK_SLOT` 来源是单盘储位，目标响应省略 `next_source_action`，其余直接取料来源由插件按当前有效 plan member 继续编排。`BIN_CELL` 的对象可表示同 Cell 下一盘或另一个 Cell；WES 仍等当前盘权威取放和 WMS 结果满足因果条件后才下发后继动作。`REJECT` 继续使用 `business_exception_code + ng_locator + source_disposition`；`WAIT` 继续携带 `retry_after_ms`。自动插件 MVP 暂不执行 NG，不得据此删除长期合同中的 NG 协议。

设备映射须与[机械臂附录](device-annex-automatic-picking-arms.md)共同确认：`rack_side` 与 `rack_face` 的值域（O11）；直接取料 `slot_id` 是否能由 WMS 给出 ECS `bin_*` 参数（O13）；`cell_id` 是否直接等于 `bin_cell_location`（O16）；DirectPick 如需 `bin_type/reel_*` 参数，`plan_delta.added_direct_picks[]` 是否应携带这些数据（O17）。这些问题未闭合前，不把设备参数推断写入 WES 主合同。

## 7. 不增加 revision 的 API 与 MVP 边界

`return_rack.arrival_report` 是 `transport_task_id` 关联的物理到位 Fact，同一 Fact 可供多个 Requirement 求值；`work_completed` 以 `admission_operation_id` 关联本次 Passage；`return_batch` 消费 RETURN_BUFFER 的 Passage/FIFO 候选；`departure_decide` 处理当前货架离场；`completion_confirm.last_applied_plan_revision` 仅为计划流游标。以上均不因来源成员修订而机械加 `plan_revision`。

自动线独立 WorkLine 的候选 `workline_code=KT11`，仍须 WMS 确认 `task_type=AUTO` 的登记与处理；这不改变来源身份规则。`issued`、`queue_changed`、`return_batch`、`drain_rack_decide`、`departure_decide` 等在 AUTO 下的同合同语义也需 WMS 确认。`prepare` 已批准的 DTO 不随本提案修改；`plan_delta` 的 T1-A 联合验收仍单独关闭。

**MVP implementation scope：** 首版自动插件只实现正常放置（BIN_CELL 与 RACK_SLOT）及目标换面/换架。`source.empty_decide`、单盘/Cell/Bin NG 可留到后续切片；这是插件实施范围，不是删除主合同 operation 或修改 WMS 长期能力。收到当前插件不能安全处理的确定结果时须保留 Evidence 并按已批准恢复合同处理，不能猜成正常成功。

## 8. 逐项确认和归档

WMS/WES 对第 2 节每个 Pending 行记录：`Decision = Pending | Accepted | Revised | Rejected`、确认人、日期、可追溯记录、最终字段/示例与双方合同指纹。双方另需确认本提案的 `inbound_batch_operation_id → work_plan.operation_id` Passage 关联是否覆盖同箱再次投入、`WAIT` 重求值、迟到/重复 Response；若不覆盖，先修订该行，不在 WES 以箱码或时间窗口推断。

确认顺序：逐项评审本提案 → 冻结最终身份和 wire → 将获批内容**写回出库主合同**并同步严格 DTO/OpenAPI/业务调用 → 联合用例验证 → 本文标记 `Archived / Historical / Not Current Design Authority`。主合同保持唯一 Current Authority；本提案的 Pending 示例不能代替已批准合同或代码验收。
