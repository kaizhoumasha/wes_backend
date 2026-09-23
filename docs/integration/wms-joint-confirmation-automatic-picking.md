---
title: WMS 联合确认清单：自动拣料（automatic-picking）
status: Draft
created_at: 2026-09-21
audience: WMS 开发组、WES 架构组
scope: automatic-picking 插件需要 WMS 书面确认或联合冻结的 API 范围，不新增接口字段
related:
  - docs/contracts/wms-outbound-picking-task-integration-requirements.md
  - docs/integration/outbound-picking-plan-delta-joint-freeze.md
  - docs/integration/device-annex-automatic-picking-arms.md
---

# WMS 联合确认清单：自动拣料（automatic-picking）

## 1. 为什么需要这份清单

[出库合同](../contracts/wms-outbound-picking-task-integration-requirements.md)全文状态仍是 `ReviewRequired`。
其中一部分 operation 已经被 manual-picking 真实消费并跑通（wire、幂等、Evidence 全部有代码和自动化测试证据，即使现场验收还是
`NOT ACCEPTED`）；另一部分 operation（`work_plan`、`material.decide`、`movement_report`、`source.empty_decide`）从合同写出到现在从未
有真实插件消费过，只存在于文档里。automatic-picking 是这批 operation 的第一个真实消费者。

本文把要跟 WMS 谈的问题分成四类，按风险从低到高排列：A、B 类不阻塞插件骨架（M1/M2）；C、D 类必须在 M3（接入真实
Transport/Device/WMS）之前书面确认，否则不得开始设备实现（合同 §17）。

## A. 只需确认「AUTO 任务类型下行为一致」，wire 本身不变

以下 operation 的字段已随 manual-picking 验证过一轮真实 record/replay 路径，automatic-picking 复用同一份 wire，
不新增字段。需要 WMS 确认的只是业务侧在 `task_type=AUTO` 时是否按同样规则处理：

| operation | 确认内容 |
| --- | --- |
| `outbound.picking_task.issued@v1` | 发布 `task_type=AUTO` 时 `workline_code` 指向新的自动线编码（见 §4 前置项） |
| `outbound.picking_task.queue_changed@v1` | 无额外确认 |
| `outbound.picking_task.cancel@v1` | `PLAN_MEMBERS` 的 `bin_source_racks[]`、`direct_pick_sources[]` 选择器对自动线同样适用（自动线两条来源路径都做，见 §5） |
| `outbound.return_rack.arrival_report@v1` | 无额外确认 |
| `outbound.bin.inbound_batch@v1` | 无额外确认 |
| `outbound.bin.return_batch@v1` | 无额外确认 |
| `workline.return_buffer.drain_rack_decide@v1` | 无额外确认 |
| `outbound.rack.departure_decide@v1` | 无额外确认 |
| `outbound.picking_task.completion_confirm@v1` | 无额外确认 |

## B. 已批准，但有一个前置未决项

| operation | 状态 | 前置项 |
| --- | --- | --- |
| `outbound.picking_task.prepare@v1` | `Approved`（2026-09-04），字面量与 DTO 已冻结 | 需要 §4 的自动线 `workline_code` 才能联调 |
| `outbound.picking_task.plan_delta@v1` | 首批/追加 wire 已定义，但 [T1-A 联合验收](outbound-picking-plan-delta-joint-freeze.md)（prepare 未定暂存 503、计划冲突修正重放，J01–J11）尚未取得 WMS 书面确认 | T1-A 必须先关闭。这个缺口不分 AUTO/MANUAL，但 automatic-picking 是第一个真正依赖它拿首批计划的场景，建议借这次机会一并推动关闭，不要让 automatic-picking 建在一个未冻结的地基上 |

## C. AUTO 专属、从未被实现过的 operation：请 WMS 重新核对文档仍然准确

这四个 operation 写进合同已经超过一个月，从未有真实消费者跑过。需要 WMS 团队确认的是文档字段是否仍是你们当前的实现计划，
而不是我们去猜合同哪里可能过时。

| operation | 用途 | 请确认的点 |
| --- | --- | --- |
| `outbound.bin.work_plan@v1` | SCAN2 到位后请求 Cell 级工作计划 | 已定合同变更 1，见下方「提议的合同变更」 |
| `outbound.material.decide@v1` | 扫码台读码后请求目标 SLOT | `target_preparation.mode`（`ROTATE`/`REPLACE`）触发条件；`business_exception_code` 只有 `MATERIAL_REJECTED`、`SOURCE_CELL_MISMATCH` 两个值是否够用；已定合同变更 2、3、4，见下方「提议的合同变更」 |
| `outbound.material.movement_report@v1` | PUT 完成后上报实际结果 | 无字段疑问，只需确认 WMS 侧已就绪接收 |
| `outbound.source.empty_decide@v1` | 确认空取后请求下一步 | 第一版自动线不实现空取分支（见 §5 范围说明）。v1 上线期间如果设备物理空取，automatic-picking 会把该对象暂停对账，不调用这个 operation；空取分支作为第二切片单独排期 |

### 提议的合同变更（4 处，需 WMS 逐条评审）

#### 变更 1：`work_plan.READY` 结构（`work_plan` 的调用方式不变，仍是每个 Bin 一次）

现状：

```json
{"result": "READY", "cell_ids": ["CELL-03"]}
```

改为：

```json
{
  "result": "READY",
  "cell_id": "3",
  "bin_type": "3",
  "reel_layer": "1",
  "reel_thickness": "20",
  "reel_diameter": "15"
}
```

`cell_ids[]` 数组 → 单个 `cell_id`（该 Bin 的第一个 Cell）；新增 `bin_type`、`reel_layer`、`reel_thickness`、`reel_diameter`。
`reel_totalthickness` 由 WES 本地按 `reel_thickness × reel_layer` 计算，不需要加进响应。该 Bin 之后要不要换 Cell、什么时候
结束，由变更 4 的 `material.decide.ACCEPT.next_source_action` 决定，`work_plan` 不会为同一个 `bin_code` 被再次调用。

`cell_id` 直接用 ECS `bin_cell_location` 的取值（`1..7` 的数字字符串），不使用抽象编号。WES 原样把这个值用在 ARM01 命令的
`bin_cell_location` 上，不需要额外映射（见 O16）。

`NO_WORK`（这个 Bin 从一开始就没有 Cell 要处理）和 `WAIT` 不受本变更影响，字段和语义与现有合同完全一致：

```json
{"result": "NO_WORK"}
```

```json
{"result": "WAIT", "retry_after_ms": 1000}
```

#### 变更 2：`material.decide` 请求字段

现状（合同 §10.2）：`data.six_in_one.{HHPN, MfrPN, Qty, DateCode, LotCode, PkgID}` 六个独立字段。

改为（`six_in_one` 整体替换为单字段 `barcode`，字段名以 WMS 确认为准，下面是占位提案）：

```json
{
  "data": {
    "task_id": "PICK-20260811-001",
    "source_locator": {"type": "BIN_CELL", "rack_id": "RACK-5F-001", "rack_face": "A", "bin_code": "BIN-001", "cell_id": "3"},
    "barcode": "P032-0561-000HF,Q3000,M0805S106K160CT,D260505,L100R028291,S72052872716100149602",
    "scanned_at": 1786062999900
  }
}
```

需 WMS 补充：`barcode` 的长度、字符集、`REJECTED`/`INVALID_DATA` 校验规则（WES 不再对内容做任何结构假设）。

顺带确认：`work_plan` 现在每次只发一个 `cell_id`（变更 1），WMS 是否已经能通过 `task_id + bin_code` 唯一确定当前在途
的来源？如果是，请求里的 `source_locator` 字段是否可以省略，只保留 `task_id + bin_code + barcode + scanned_at`？

#### 变更 3：`material.decide.ACCEPT.target_locator` 新增 `rack_layer`、`rack_column`

现状：

```json
{
  "result": "ACCEPT",
  "target_locator": {"type": "RACK_SLOT", "rack_id": "TRANSFER-RACK-01", "rack_face": "A", "slot_id": "620001-A5C01-401"},
  "next_source_action": "CONTINUE"
}
```

改为（`next_source_action` 的新对象结构见变更 4，这里一并展示完整响应）：

```json
{
  "result": "ACCEPT",
  "target_locator": {
    "type": "RACK_SLOT",
    "rack_id": "TRANSFER-RACK-01",
    "rack_face": "A",
    "slot_id": "620001-A5C01-401",
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
```

`target_locator` 新增 `rack_layer`、`rack_column`，由 WMS 直接给出，WES 不再从 `slot_id` 反推。`target_preparation.mode=ROTATE/REPLACE`
时新架新面的 `target_locator` 同样需要这两个字段，规则一致，例如换面（`ROTATE`）：

```json
{
  "result": "ACCEPT",
  "target_locator": {
    "type": "RACK_SLOT", "rack_id": "TRANSFER-RACK-01", "rack_face": "B", "slot_id": "B-01",
    "rack_layer": "1", "rack_column": "1"
  },
  "target_preparation": {"mode": "ROTATE"},
  "next_source_action": {
    "cell_id": "3",
    "bin_type": "3",
    "reel_layer": "1",
    "reel_thickness": "20",
    "reel_diameter": "15"
  }
}
```

换架（`REPLACE`）：

```json
{
  "result": "ACCEPT",
  "target_locator": {
    "type": "RACK_SLOT", "rack_id": "TRANSFER-RACK-02", "rack_face": "A", "slot_id": "A-01",
    "rack_layer": "1", "rack_column": "1"
  },
  "target_preparation": {"mode": "REPLACE"},
  "next_source_action": {
    "cell_id": "3",
    "bin_type": "3",
    "reel_layer": "1",
    "reel_thickness": "20",
    "reel_diameter": "15"
  }
}
```

三个例子里的 `next_source_action` 都假设这一盘还有下一盘（同一个 `cell_id`）；如果这一盘是当前 Cell 或整个 Bin 的最后一盘，
按变更 4 的规则省略这个字段，不受 `target_locator`/`target_preparation` 影响。

#### 变更 4：`material.decide.ACCEPT.next_source_action` 从枚举改为「对象或省略」（承接变更 1，让 `work_plan` 只需调用一次）

`next_source_action` 不再是 `CONTINUE | SOURCE_DONE` 字符串枚举，改成条件字段：有下一盘要取就带一个对象（字段与
`work_plan.READY` 一致），没有就整个省略这个字段。是否同一个 Cell，WES 用返回的 `cell_id` 和自己本地已经在处理的
`cell_id` 比较即可，不需要 WMS 再额外说明"是不是同一个 Cell"。

这只是字段格式统一，不是行为统一。`BIN_CELL` 才会真正返回对象（同一 Cell 下一盘，或换下一个 Cell）；`RACK_SLOT`（直接取料）
按合同定义天生是单料盘储位（§4.4 位置对象表），没有"这个储位还有下一盘"，储位之间接着取哪个也不是 `material.decide` 决定的——
WES 早从 `plan_delta.added_direct_picks[]` 拿到本任务全部直接取料清单，自己本地决定顺序。所以 `RACK_SLOT` 的
`next_source_action` 永远省略，这一点和原来固定 `SOURCE_DONE` 是同一个意思，但机制上跟 `BIN_CELL` 完全不同，只是共享了同一个
字段的表达方式。

有下一盘：

```json
{
  "result": "ACCEPT",
  "target_locator": {...},
  "next_source_action": {
    "cell_id": "3",
    "bin_type": "3",
    "reel_layer": "1",
    "reel_thickness": "20",
    "reel_diameter": "15"
  }
}
```

- `cell_id` 与 WES 当前在处理的 `cell_id` 相同：同一个 Cell 还有下一盘。
- `cell_id` 不同：本 Bin 换到下一个 Cell，WES 直接用这个对象下发下一次 ARM01 取料，不调用 `work_plan`。

没有下一盘（本 Bin 结束，或直接取料的这一盘就是全部）：

```json
{
  "result": "ACCEPT",
  "target_locator": {...}
}
```

`next_source_action` 整个字段省略，不发送 `null`。这和合同 §4.4 的既有规则一致（可选字段无值时必须省略，禁止用 `null`
代替省略）。`BIN_CELL` 场景下，WES 在这一盘 `movement_report` 确认后把该 Bin 放行到 SCAN3。

需 WMS 确认两点：

1. 同一个 Cell 内，`bin_type`/`reel_layer`/`reel_thickness`/`reel_diameter` 在返回同一个 `cell_id` 时是否始终不变（WES 沿用
   `work_plan` 或上一次收到的值），还是每盘都可能不同、需要每次重新给出？
2. 换 `cell_id` 要求 WMS 在当前这一盘的 `movement_report` 还没发生之前就选定下一个 Cell。请确认这个时点 WMS 能不能做出这个决定，
   还是必须等这一盘物理放置确认后才能选。

#### `material.decide` 的其他结果（`REJECT`、`WAIT`，不受变更 1～4 影响）

`REJECT` 用的是 `business_exception_code + ng_locator + source_disposition`，不是 `next_source_action`，所以变更 4 不影响它：

```json
{
  "result": "REJECT",
  "business_exception_code": "MATERIAL_REJECTED",
  "ng_locator": {"type": "NG_ZONE", "zone_code": "MATERIAL_NG_01"},
  "source_disposition": "CONTINUE"
}
```

```json
{
  "result": "REJECT",
  "business_exception_code": "SOURCE_CELL_MISMATCH",
  "ng_locator": {"type": "NG_ZONE", "zone_code": "CELL_NG_01"},
  "source_disposition": "CLOSE"
}
```

`WAIT`：

```json
{"result": "WAIT", "retry_after_ms": 1000}
```

第一版自动线不处理 NG 分支（见 §5 范围声明），所以 v1 上线期间如果 WMS 返回 `REJECT`，automatic-picking 会把该对象暂停对账，
不会按 `source_disposition` 分流到 NG 区；NG 分支和空取分支一起放进第二个切片。

## D. 设备附录衍生的字段映射问题（WMS 与 ECS 都要回答）

这几条来自[设备合同附录草案](device-annex-automatic-picking-arms.md)，字段本身是设备侧的，但取值来自 WMS 的业务授权，
所以需要 WMS 一起确认：

| # | 问题 |
| --- | --- |
| ~~O10~~ | 已确定：`material.decide.ACCEPT.target_locator` 新增 `rack_layer`、`rack_column`，由 WMS 直接给出，见「提议的合同变更」变更 3 |
| O11 | `rack_side`（ECS 字段，A/B）与合同 `rack_face` 是否同一值域，取值是否一一对应？ |
| O13 | 退料货架直接取料的 `source_locator.slot_id`，映射到 ECS 的 `bin_*` 字段时能否也由 WMS 直接给出（做法同 O10，字段名不同），还是需要 WES 从 `slot_id` 反推？ |
| O16 | 已提议：`cell_id` 直接使用 `1..7` 的数字字符串，等同于 ECS `bin_cell_location`，不再单独映射。请确认 WMS 能否按这个格式返回 `cell_id`。 |
| O17 | 直接取料的 ARM01 测量字段（`bin_type`、`reel_layer`、`reel_thickness`、`reel_diameter`，如果 O13 确认需要）目前没有任何 operation 携带：`plan_delta.added_direct_picks[]` 只有 `source_locator`，直接取料没有 `work_plan` 这种到位后再问一次的机制。如果确实需要，建议扩展 `added_direct_picks[]` 每项带上这些字段（做法同变更 1）。 |

## E. 一个我们已经想清楚、只需要 WMS 确认预期一致的设计点

人工线用 `outbound.manual_rack.direct_pick_completed@v1` 上报"某货架面全部直接取料完成"，因为 PDA 对 WES 是黑盒，
WES 没有逐 SLOT 的完成信号。自动线不需要这个信号：`added_direct_picks[]` 里每个 `source_locator` 都会走一次
`material.decide` + `movement_report`，WES 能从自己的执行记录里直接判断"这个货架面的全部直接取料是否已结清"，
不需要 WMS 再发一个面级完成事实。

请 WMS 确认这个理解正确，不需要为自动线新增类似 `direct_pick_completed` 的 operation。

## 4. 前置阻塞项（不属于合同问题，但排在最前面）

自动线是独立 WorkLine（架构决策，见设计第 1 段），`workline_code` 已确定为 **`KT11`**——这是 WES/现场内部
已分配的值，不需要向 WMS 索要。本清单发出时需要 WMS 确认接受 `task_type=AUTO` 绑定 `KT11`，但这只是登记
确认，不是等待 WMS 决定这个值。

真正的前置阻塞在 WES 内部：`automatic-picking` 插件的 `definition.py`（设备角色、位置槽位静态声明）尚未
定稿，没写完插件的静态声明，`KT11` 就无法接入系统绑定实际 WorkLine——`issued`、`prepare`、`work_plan` 等
operation 的联调因此仍然等这一步，不是等 WMS。

## 5. 范围声明（避免 WMS 按合同全量范围排期）

本轮 automatic-picking 只覆盖：

- 正常放置（Bin 来源 + 直接取料来源，两条路径都做）；
- 转运货架同架换面（`ROTATE`）与换架（`REPLACE`）。

不覆盖：空取（`source.empty_decide`）、单盘/Cell/Bin 级 NG 分支。这两类在合同里已有定义，作为第二个切片单独排期，
不在本轮 WMS 联调范围内。

## 6. 待办

- [ ] 发给 WMS，索要自动线 `workline_code`
- [ ] 推动关闭 T1-A（plan_delta J01–J11）
- [ ] 收集 WMS 对 A/B/C/D/E 各项的书面回复
- [ ] 回复收齐后，把本文状态改为 `Reviewed`，并把关键结论并入 `docs/superpowers/specs/` 的 automatic-picking 设计 spec
