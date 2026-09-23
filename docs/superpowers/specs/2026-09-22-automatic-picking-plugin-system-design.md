---
title: 自动拣料插件系统设计
status: NeedsRebase
created_at: 2026-09-22
audience: WES 架构、WMS 对接开发、ECS 供应商、插件二次开发人员
scope: 历史自动拣料插件与 bin-line-common 方案；待按当前 SRS 和合同重写，不作为实施依据
related:
  - docs/contracts/wms-outbound-picking-task-integration-requirements.md
  - docs/contracts/wms-manual-outbound-picking-integration-requirements.md
  - docs/integration/third_party_integration_whitepaper.md
  - docs/integration/device-annex-automatic-picking-arms.md
  - docs/integration/wms-joint-confirmation-automatic-picking.md
  - docs/architecture/SRS.md
  - docs/contracts/transport-fulfillment-contract.md
  - docs/superpowers/specs/2026-09-22-bin-line-scan-retry-fix.md
  - docs/plugin_development_guide.md
---

# 自动拣料插件系统设计

## 1. Abstract

本设计新增第二个业务插件 `automatic-picking`：SCAN2 到位后，由机械臂从工作位料箱吸出料盘、放到扫码平台，
扫码后由 WMS 决定目标储位，机械臂放到转运货架。宿主继续拥有 `PickingTask`、`plan_delta`、Transport、
`DeviceCommand`、Evidence、`WmsConfirmation` 和 WorkLine 生命周期不变。

自动线与人工线在 SCAN2 之前、SCAN3~4 之后几乎完全一致（入线、货架循环、回程、drain、任务完成）。本设计同时把
这部分通用逻辑从 `workline_plugins/manual-picking/` 抽取为不可独立部署的共享包 `workline_plugins/bin-line-common/`，
两个插件各自依赖它，互不依赖。

设计过程中发现现有回程段（SCAN3/SCAN4）"决定一次即冻结"的规则在联调中造成过度阻塞：滚筒线料箱物理错位后，
即使工人已把料箱正确放回，系统也拒绝重新处理，导致现场卡死。这是一个**当下正在发生的现场问题**，评审时判定
不应该被本设计的外部依赖（WMS 合同确认、设备附录）拖慢，已拆分为独立文档
[《回程段扫码重试修正》](2026-09-22-bin-line-scan-retry-fix.md)单独评审、单独落地，不等本设计整体通过。

自动出库合同（`wms-outbound-picking-task-integration-requirements.md`）当前仍为 `ReviewRequired`，设备附录
（[device-annex-automatic-picking-arms.md](../../integration/device-annex-automatic-picking-arms.md)）为 `Draft`。
本设计是这两份合同冻结前的实现前设计，不把代码就绪当作业务验收。
原稿引用的 WMS 联合确认清单 §C 已重整为[主合同修订提案](../../integration/wms-joint-confirmation-automatic-picking.md)第 2–6 节；其中来源成员跨 revision、同箱多次 Passage 身份和 wire 均待逐项确认。**以下旧表结构、路线状态机、`(task_id, bin_code)` 唯一约束和 common 搬迁清单均已撤回，不是实施依据。** 当前语义以 [SRS 第 0 章](../../architecture/SRS.md)、[出库主合同](../../contracts/wms-outbound-picking-task-integration-requirements.md)及[人工拣选合同](../../contracts/wms-manual-outbound-picking-integration-requirements.md)为准。

## 2. Goals / Non-Goals

| Goals | Non-Goals |
| --- | --- |
| G1 新增 `automatic-picking` 插件，`supported_line_types=("AUTO",)`，独立 WorkLine，不与 `manual-picking` 共线、不做插件切换 | N1 不复制人工线的 PDA 准入语义，不建立第二套 Bin 工作计划表达 |
| G2 抽取 `bin-line-common`：入线段、货架循环、回程段、drain、任务完成，两个插件复用同一套通用逻辑 | N2 不建立动态 registry 或通用工作流 DSL；共享包是显式装配的库，不是运行时插件 |
| G3 v1 覆盖正常放置 + 换面/换架（`ACCEPT.target_preparation`）+ 退料货架直接取料 | N3 v1 不做空取（`source.empty_decide`）与单盘/Bin NG，作为第二切片 |
| G4（见独立文档）回程段过度冻结的修正不在本设计范围内，见上方拆分说明 | N4 不放松 WES 自身的重复提交防护（同设备未闭合命令门禁、identity 幂等） |
| G5 设备合同附录草案先行，供应商确认前不写生产 Adapter | N5 不用 Mock/健康检查/本机测试冒充真实设备或 WMS 业务验收 |

## 3. Background

- 现有手工插件（`workline_plugins/manual-picking/`）应用层 3570 行，其中约 2500 行（`batch_driver.py`、
  `batch_repository.py`、`batch_flow.py`、`batch_result.py`、`batch_policy.py`、`batch_transport.py`、
  `drain_flow.py`、`drain_repository.py`、`transport_outcome.py`、`rack_readiness.py`、`completion_flow.py`、
  `passage_repository.py` 的入线/回程部分、`handlers/scan1.py`、`scan3.py`、`scan4.py`）不含任何手工专属字段，
  只是尚未被抽出来。
- 直接复制这套代码给自动插件会重复约 2000 行，且两份实现后续各自修复容易漂移；把 `AUTO` 分支塞进宿主 `src/`
  会让基础层反向依赖业务，违反分层。
- 手工线（[wms-manual-outbound-picking-integration-requirements.md](../../contracts/wms-manual-outbound-picking-integration-requirements.md)）
  C1~C7 代码 `PARTIAL`、C7（静态装配+真实 worker 主链路）`PLANNED`，现场验收全部 `NOT ACCEPTED`。这不阻塞本设计：
  自动线依赖的是基础能力（Transport/DeviceCommand/Evidence/WmsConfirmation），不是手工插件本身；但共享包的真实
  代码抽取需要等当前基础层改动按 [SRS](../../architecture/SRS.md) 和
  [Transport 合同](../../contracts/transport-fulfillment-contract.md) 验证并合入，
  见 §9。

## 4. 架构：依赖方向与三段切法

```
src/(基础能力)  ←  wes_plugin_sdk  ←  bin-line-common(共享包，非插件)  ←  manual-picking
                                                                    ←  automatic-picking
```

- `bin-line-common` 没有 `plugin_key`，不能被 WorkLine 绑定，不导入任何具体插件。
- 两个插件互不依赖，同一 WorkLine 同时只绑一个插件（不引入插件切换）。
- 装配仍是宿主静态 composition，显式加载，不做动态发现。

物理链路按"谁的业务逻辑不同"切三段：

| 段 | 内容 | 归属 | 差异点 |
| --- | --- | --- | --- |
| 入线段 | SCAN1、点1→点2 FIFO、点1 NG 直达点3 | `bin-line-common` | 两插件完全一致 |
| 货架循环 | `inbound_batch`、CTU01 步骤幂等与到位依赖、同架换面、换架离场、`plan_delta` 应用到 F01/CTU01 意图 | `bin-line-common` | 两插件完全一致；物理准入由 RCS 裁决 |
| 回程段 | SCAN3、SCAN4、`RETURN_BUFFER` FIFO、`return_batch`、drain、任务完成、下一任务准备 | `bin-line-common` | 两插件完全一致；含[独立文档](2026-09-22-bin-line-scan-retry-fix.md)的重试修正，该修正先在 `manual-picking` 内落地，抽取时一并带过来 |
| **工作段** | SCAN2 之后到"处置结果形成" | **各插件私有** | 手工：PDA 准入+完成；自动：`work_plan`+Cell 循环+双臂+`material.decide`+`movement_report` |

工作段是唯一不同的部分。

```
三段数据流（→ 表示物理料箱前进方向，│ 表示归属边界）

  ┌─────────── bin-line-common ───────────┐   ┌─ 各插件私有 ─┐   ┌────── bin-line-common ──────┐
  │  入线段         货架循环                │   │   工作段      │   │         回程段                │
  │                                        │   │              │   │                              │
  │  SCAN1 → 点1→点2 FIFO → inbound_batch  │ → │  SCAN2 到位   │ → │  处置结果(disposition)         │
  │  → CTU01 请求 → 到位后换面/换架        │   │  → 手工:PDA   │   │  → SCAN3 → SCAN4              │
  │                                        │   │    自动:双臂   │   │  → RETURN_BUFFER FIFO         │
  │                                        │   │              │   │  → return_batch/drain          │
  └────────────────────────────────────────┘   └──────────────┘   └──────────────────────────────┘
                                                       ↑
                                        唯一接缝：release_point2()
                                        （工作段决定 NORMAL/NG 后回调，见 §6.3）
```

## 5. 数据模型

### 5.1 `MaterialExecution` 保留不变

宿主 `MaterialExecution`（`src/app/execution/models/material_execution.py`）不是"记录机械动作"，是跨设备命令的
**关联身份**：一盘料要经过 `ARM01`（取料）与 `ARM02`（放置）两条独立 `DeviceCommand`，中间插一次 WMS 往返
（`material.decide`）。`DeviceCommand` 本身已有 `material_execution_id` 外键（[command.py:95](../../../src/app/device/models/command.py:95)），
`WmsConfirmation` 的 owner 三选一恰好一个也包含它（[wms_confirmation.py:38-41](../../../src/app/execution/models/wms_confirmation.py:38)）。
去掉它会导致 `material.decide` 这条往返无处挂靠，等同于重新发明一套关联机制。这个模式已有先例：已下线的粗分
入库插件用同一套 `material_trace_id` 字段解决"扫码识别→WMS 决定目标→PUT"的相同形状问题
（[fact_builder.py:58](../../../src/app/execution/services/fact_builder.py:58)）。

自动插件私有表通过 `material_execution_id` 外键挂在它下面，只补充宿主没有的业务字段。

### 5.2 `bin-line-common`：两张共享表

三张表（本节两张 + §5.3 一张）Mixin 组合统一为 `EnterpriseMixin, DataTableMixin`（无 `SoftDeleteMixin`，无乐观锁）：
它们是作业期执行证据，不是业务主档，不需要软删或乐观锁；这与现有 `ManualPickingPassage` 拆分前的实际用法一致，
不引入新约定。状态类字段（`disposition`、`return_state`）一律 `VARCHAR + CHECK`，不用 PostgreSQL 原生 ENUM
（CLAUDE.md 强制项）。

| 表 | 字段（要点） | 说明 |
| --- | --- | --- |
| `bin_line_passages` | `workline_id`、`task_id`、`bin_code`、SCAN1 证据/命令、SCAN2 到位证据（含异常证据）、点2释放命令、`disposition`（OPEN/NORMAL/NG/CLOSED）、`reason_code`、`archived_at` | 入线段与处置结果的唯一事实；`disposition` 是入线/工作/回程三段共用的处置结果接口 |
| `bin_line_returns` | `passage_id`（外键）、SCAN3 证据/命令/去向、SCAN4 证据/命令/到达时间、`return_state`、`retry_count`（诊断用，默认 0，见[独立文档](2026-09-22-bin-line-scan-retry-fix.md)） | 回程段状态；只在 SCAN3 首次关联到某次经过后创建；`RETURN_BUFFER` FIFO 索引在这里，不带 `task_id`，可跨任务 |

从现有 `ManualPickingPassage`（107 行宽表）迁出：`admission_*`、`wms_*`、`wms_completed_*` 全部离开共享表。

```
bin_line_passages.disposition 状态机

         SCAN1 读到非法码
              │
              ▼
  ┌────────┐  SCAN1/SCAN2 正常  ┌──────┐  工作段决定 NORMAL  ┌────────┐  SCAN3/4 正常放行  ┌────────┐
  │  OPEN  │ ──────────────────▶│ OPEN │────────────────────▶│ NORMAL │───────────────────▶│ CLOSED │
  └────────┘                    └──┬───┘                     └────────┘                     └────────┘
       │                           │ 工作段决定 NG                                                ▲
       │ SCAN1/SCAN2 读码异常      ▼                                                              │
       │                        ┌──────┐   SCAN3 判定 NG（MOVE_LEFT，ECS SUCCESS）                │
       └───────────────────────▶│  NG  │─────────────────────────────────────────────────────────┘
                                 └──────┘

  旁支：SCAN2 读到合法但非预期 Bin ——不改变 disposition，冻结在原地等独立恢复 wire（合同 §9.3），
        不在此状态机内，不自动流向 NORMAL/NG/CLOSED 任一分支。
```

### 5.3 手工插件私有表（Mixin 同 §5.2）

本节原拟将手工工作字段迁移到 `manual_picking_works`，该拆表方案已撤回。同任务同箱可以多次经过；本次 Passage 通过唯一的 `admission_operation_id` 关联完成结果，不能保留跨 Passage 的 `(task_id, bin_code)` 终态唯一约束。当前实现与合同以[人工拣选合同](../../contracts/wms-manual-outbound-picking-integration-requirements.md)为准。

### 5.4 自动插件私有表 —— **TBD，等 [主合同修订提案](../../integration/wms-joint-confirmation-automatic-picking.md)第 2–6 节逐项书面确认后设计**

自动插件需要一张私有表，通过 `material_execution_id` 外键挂在宿主 `MaterialExecution` 下（§5.1 的关联身份不变），
承接 WMS 通过 `work_plan`/`material.decide` 返回的 Cell、六合一码、目标储位等字段。**具体字段列表在此不给出**：
写评审时发现，联合确认清单的变更 1～4 已提议把 `work_plan.READY.cell_ids[]`（数组）改成单个 `cell_id`、下一个
Cell 由每次 `material.decide.ACCEPT.next_source_action` 增量给出、六合一码从六个字段改成单个 `barcode`
字符串（无 `pkg_id`）、`target_locator` 新增 `rack_layer`/`rack_column`——这是对当前批准合同 wire 形状的实质性
改写，WMS 还没有回复。在此之前写下具体字段清单，大概率是一份写完就要重写的 schema。

重新设计时需要遵守的两条教训（本轮评审已经踩过）：

1. **Cell 工作幂等与物理堆叠顺序是两件事。** 原文“不要用 LIFO”已撤回；当前出库合同明确 `BIN_CELL` 按物理栈顶 LIFO 逐盘抓取。WES 不代替 ECS/PLC 做物理互斥，也不能用同一 Cell 的未闭合 Action 越过尚未确认的栈顶。
2. **唯一索引必须按 `(passage_id, cell_id)` 或等价的复合键限定范围。** `cell_id` 取值是 `1..7` 的数字字符串
   （O16），在不同 Bin 之间会重复；只写 `cell_id` 会让两个不同 Bin 各自的"Cell 3"互相阻塞，这是一个真实会在
   多 Bin 并行时发生的数据完整性问题，不是边界情况。

Mixin 组合和 ENUM 约定同 §5.2（`EnterpriseMixin, DataTableMixin`，状态字段 VARCHAR+CHECK），这一点不受
上述 wire 不确定性影响，设计时直接沿用。

不单独建"工作计划"表：`work_plan` 的请求/响应由宿主 `WmsConfirmation` 已可靠保存；`NO_WORK` 时不产生行，
直接走处置结果。直接取料没有料箱，不走 `bin_line_passages`，挂在宿主 `DirectPickExecution` 上。

宿主 `MaterialExecutionService.assert_fifo_head`（[material_execution_service.py:139](../../../src/app/execution/services/material_execution_service.py:139)）
按 `workline_id` 找队头，粒度与自动线"按 Cell 分组"的互斥不同，**不复用**，插件自己实现。

## 6. `bin-line-common` 原文件归属提案（已撤回，不实施）

### 6.1 文件归属表

| 现有文件 | 去向 | 依据 |
| --- | --- | --- |
| `batch_driver.py`（745 行） | `bin-line-common` 原样搬 | 只用通用位置槽位常量和回程段查询，零工作段字段 |
| `batch_repository.py`、`batch_flow.py`、`batch_result.py`、`batch_policy.py`、`batch_transport.py` | `bin-line-common` | CTU 门禁、`inbound_batch`/`return_batch` 应用，通用 |
| `drain_flow.py`、`drain_repository.py`、`transport_outcome.py`、`rack_readiness.py`、`completion_flow.py` | `bin-line-common` | 通用 |
| `passage_model.py`/`passage_repository.py` | 拆分：入线+回程共享方法留 `bin_line_passages`/`bin_line_returns`；`by_admission_operation_for_update`、`waiting_for_completion_for_update`、`uniquely_completed_for_update` 三个方法搬到手工插件私有仓储（读 `manual_picking_works`） | 见 §6.2 |
| `completion_repository.ready_to_confirm` | `bin-line-common`，原生 SQL `wms_result IS NULL` 改为 `disposition == 'OPEN'` | `disposition` 已是共享字段，改查询即可，不需要跨包接口 |
| `scan_flow.py`（773 行） | 拆分：SCAN1/3/4 + 批次/drain/departure/Transport 结果分发进 `bin-line-common`；`_apply_admission_result`、`_apply_completed`、SCAN2 分支留手工插件 | 见 §6.3 |
| `handlers/scan1.py`、`scan3.py`、`scan4.py`、`scan_types.py` | `bin-line-common` | 纯函数，零插件常量依赖 |
| `handlers/scan2.py`、`plan_admission.py`、`prepare_policy.py`、`picking_task_plan_applied.py`、`plugin.py` | 手工插件保留 | 装配与策略，插件天然拥有 |

### 6.2 完成条件检查的化简

`completion_repository.py:75` 原生查询 `passage.wms_result.is_(None)` 是工作段字段，拆表后不在共享表。不需要
新接口——`disposition` 字段本身就是共享的"处置结果"，一个 Bin 只要还没结算，`disposition` 就还是 `OPEN`。改成
查 `disposition == 'OPEN'` 语义完全一致，且是纯共享表查询。

### 6.3 唯一的真实接缝：回调参数，不是抽象接口

`InstalledWorkLinePlugin` 只接受一个 `business_evidence_consumer`（[plugin.py:31](../../../workline_plugins/manual-picking/src/manual_picking/application/plugin.py:31)），
不能拆成两个。773 行的 `scan_flow.py` 里只有两个方法插件私有（`_apply_admission_result`、`_apply_completed`），
以及 `role == "SCAN2"` 这一支。解决办法不是造抽象基类，是共享包提供一个带回调参数的分发函数：

```python
# bin_line_common.entry.dispatch_scan_event 的接口形状（非完整实现）
async def dispatch_scan_event(db, evidence, workline, bindings, *, on_scan2): ...
```

各插件自己的壳只有几十行：路由 + 自己的两三个工作段方法，没有继承关系，不为一次性实现造接口。

共享的点2释放动作（当前散落在 `_apply_admission_result`/`_apply_completed` 结尾三行和 `_move` 辅助方法里）抽成
`bin_line_common.entry.release_point2(db, *, plugin_key, workline_id, bindings, passage, disposition, reason_code=None)`，
`plugin_key` 参数化，替代硬编码的 `"manual-picking"` 前缀。

### 6.4 机械改名清单（不是架构决策）

以下 step 常量全部只在插件自己的文件里定义和使用，宿主 `picking_task_plan_activation.py` 的三个"入场"
step（`PICKING_TASK_TARGET_RACK_IN`、`PICKING_TASK_BIN_SOURCE_RACK_IN`、`PICKING_TASK_RETURN_RACK_IN`）已经是
通用命名，不需要改：

| 现有值 | 定义位置 | 改名方向 |
| --- | --- | --- |
| `MANUAL_PICKING_TRANSFER_RACK_OUT` | `batch_driver.py:46` | `BIN_LINE_TRANSFER_RACK_OUT` |
| `MANUAL_PICKING_SOURCE_RACK_ROTATE` / `_SOURCE_RACK_OUT` | `drain_repository.py:19-20` | `BIN_LINE_SOURCE_RACK_ROTATE` / `_OUT` |
| `MANUAL_PICKING_RETURN_RACK_ROTATE` / `_RETURN_RACK_OUT` | `drain_repository.py:21-22` | `BIN_LINE_RETURN_RACK_ROTATE` / `_OUT` |
| `MANUAL_PICKING_RETURN_BUFFER_DRAIN_RACK_IN` / `_ROTATE` / `_OUT` | `drain_repository.py:15-17` | `BIN_LINE_RETURN_BUFFER_DRAIN_RACK_IN` / `_ROTATE` / `_OUT` |
| `"MANUAL_PICKING_INBOUND_BATCH"` / `"MANUAL_PICKING_RETURN_BATCH"` | `scan_flow.py:227,313` | `"BIN_LINE_INBOUND_BATCH"` / `"BIN_LINE_RETURN_BATCH"` |
| `execution_ref_id=f"manual-picking:{evidence_id}:{role}"` | `scan_flow.py:761` | 改为参数化 `plugin_key`，由调用方传入 |
| 日志原因码 `manual_picking.return_rack_arrival_*` | `batch_driver.py:518,533,546` | 去掉插件名前缀 |

## 7. 自动插件工作段（v1 范围）

v1 覆盖：SCAN2 到位 → `work_plan`（`READY`/`NO_WORK`/`WAIT`）→ `ARM01` 取料 → 扫码 → `material.decide`
（`ACCEPT`，含 `target_preparation.ROTATE/REPLACE`）→ `ARM02` 放置 → `movement_report`；以及退料货架直接取料
（`added_direct_picks` → `ARM01` 直接取料 → 扫码 → `material.decide` → `ARM02` 放置）。

不覆盖（第二切片）：`source.empty_decide`（空取）、`REJECT`（单盘/Cell NG）。工作段状态流的完整设计（含每个
`material.decide` 分支、超时、重放场景）留待下一份文档，本文只锁定范围边界和数据模型（§5.4）。

## 8. 回程段重试修正（已拆分为独立文档）

见[《回程段扫码重试修正：新物理扫码事件覆盖旧决定》](2026-09-22-bin-line-scan-retry-fix.md)。评审判定该修正
不依赖 automatic-picking 的任何内容（不涉及 wire 字段、operation 或新增合同范围），不应等本文档整体通过评审
才能落地，已独立评审、独立排期。落地顺序见该文档"落地顺序"一节：先在 `manual-picking` 内部落地，合入
`develop` 后，`bin-line-common` 抽取（§9）才开始。

## 9. 与基础层可靠恢复改动的顺序依赖

本设计编写时，基础层可靠恢复相关的代码改动（`reliable_rack_transport.py`、`position_projection_service.py`、`transport/service.py` 等 18 个核心
文件，2 个新迁移）尚未提交，且直接修改了 `batch_driver.py`、`scan_flow.py`、`drain_flow.py`、`batch_repository.py`、
`rack_readiness.py`——正是 §6.1 要抽取的文件。

- M1（本文档评审、合同冻结沟通）、M2（插件骨架、静态装配、`bin-line-common` 包结构占位）**不等**这批改动合入。
- `bin-line-common` 的**真实代码抽取**（把 §6.1 表格里的文件内容搬过去）**必须等**这批改动合入 `develop` 之后
  再做，并以合入后的版本为准重新核对 §6 的行号引用，不能照抄本文档写作时的版本。
- 自动插件 M3（真实执行代码）同样等待。

**第二个顺序依赖**（评审新增）：[《回程段扫码重试修正》](2026-09-22-bin-line-scan-retry-fix.md)改的是 §6.1 表格里
同一批"零行为变化"文件（`scan_flow.py` 的 SCAN3/4 分支）。这是一次行为变更，不能和"零回归抽取"混在同一次改动里
验证（§12 的原则）。落地顺序固定为：该修正先在 `manual-picking` 内部落地（含重写 `test_scan3_rescan_does_not_
change_frozen_route_or_command`、`test_scan4_rescan_preserves_first_fifo_order_and_command` 两个回归测试）→
合入 `develop` → 再做 `bin-line-common` 抽取。抽取时这两个测试断言的已经是修正后的新行为，不会在合并时出现
"旧断言与新行为冲突"的问题。

原因：该 SPEC 自陈现场验收状态为 `FIELD FAILURE EVIDENCE CAPTURED — NOT ONSITE VERIFIED`，多条验收标准
（AC4/AC6/AC34/AC36）现场有反例，AC39（供应商同 identity 重发安全性）无真实合同证据。合入不等于现场验完，
但至少代码层面的抽取基准要稳定，避免搬移过程中同时追着两处变动走。

## 10. WorkLine 与部署

自动线建独立 WorkLine，不与人工线共用，不引入插件切换（人工插件 README 已明确"项目决定不再引入插件切换"）。
设备角色建议 `SOURCE_ARM`、`TARGET_ARM`，位置槽位复用 `FIVE_RACK`/`RETURN_RACK`/`TRANSFER_RACK`/`INLET`/`OUTLET`
（`bin-line-common` 定义，两插件共用同一套槽位常量，见 §6.4 改名清单里位置槽位常量本就通用，不属于改名范围）。

## 11. 设备合同附录状态

[device-annex-automatic-picking-arms.md](../../integration/device-annex-automatic-picking-arms.md)（`Draft`）已
基于现场真实设备状态（`ARM01`/`ARM02`，`GET /api/v1/device/status`）和联调实际报文起草，含 O1~O17 待确认项，
覆盖扫码平台设备身份、六合一码拆分方、料盘测量字段来源、层列映射、直接取料位置类型等。供应商书面确认前，
不得据此编写生产 Adapter 或 handler（M2 阶段允许骨架/纯 Decision，不接真实设备）。

## 12. 测试所有权

沿用宿主既有分层（[device-command-contract.md](../../architecture/device-command-contract.md) §7）：

| 测试范围 | 所有者 |
| --- | --- |
| `DeviceCommand`/Transport/Evidence/`WmsConfirmation` 通用生命周期 | 核心 `tests/` |
| `bin-line-common` 的入线/货架循环/回程/drain/任务完成行为 | `workline_plugins/bin-line-common/tests/` |
| 手工线工作段（PDA 准入与完成） | `workline_plugins/manual-picking/tests/` |
| 自动线工作段（`work_plan`/Cell 循环/双臂/`material.decide`/`movement_report`） | `workline_plugins/automatic-picking/tests/` |
| 供应商 ECS 实现是否符合白皮书与设备附录 | 供应商一致性验收，不进入核心/插件业务测试 |

抽取 `bin-line-common` 前，先在手工插件内部按 §6 边界拆分子模块，保证 `test_scan_handlers.py`、`test_scan_flow.py`、
`test_batch_*`、`test_drain_*`、`test_completion_*` 全绿、零行为变化；再整体搬迁到 `bin-line-common`，同一批测试
再跑一遍；最后自动插件才开始依赖它。任何一步出问题，回溯范围小，不把重构和新功能混在一次改动里验证。

**REGRESSION（强制，见独立文档）**：`test_scan_flow.py` 里的 `test_scan3_rescan_does_not_change_frozen_route_
or_command`、`test_scan4_rescan_preserves_first_fifo_order_and_command` 当前断言"冻结后重扫不改变命令"，
与[回程段扫码重试修正](2026-09-22-bin-line-scan-retry-fix.md)的新行为直接矛盾，必须在该修正落地时同步重写
（改断言方向），不是新增测试覆盖新场景就够——这两个测试要在进入上述拆分/抽取流程之前就已经是修正后的版本。

## 13. Milestones

| Milestone | 交付 | 退出条件 |
| --- | --- | --- |
| M1 | 本文档评审通过；设备附录待确认项、联合确认清单发给 WMS/供应商（含索要 `automatic-picking` 的 `workline_code=KT11` 在 WMS 侧登记，见 §14） | 书面接受本文档语义；设备附录无未决高风险边界 |
| M2 | `bin-line-common` 包结构占位、`automatic-picking` 插件骨架、`definition.py`（含 `workline_code=KT11` 接入系统）、静态 composition；`bin-line-common` 真实代码抽取（等待 §9 前置条件：可靠恢复改动合入 **且**[回程段重试修正](2026-09-22-bin-line-scan-retry-fix.md)已先在 `manual-picking` 落地合入） | 空插件基础、manual-only、automatic-only、双插件组合分别可启动；插件 FAST 通过 |
| M3 | 接入一个自动线纵向切片：`plan_delta` 应用 → 货架/Bin Transport → `SOURCE_ARM`/`TARGET_ARM` → 逐盘位置确认 | 真实 PostgreSQL/Redis/worker、WMS fixture、ECS 一致性证据齐全 |
| M4 | 有限 WorkLine 联调与发布 | 任务、计划、设备结果、WMS 确认在目标线稳定运行，不把健康检查当物理验收 |

## 14. Open Questions

- 设备附录 O1~O17（见 [device-annex-automatic-picking-arms.md](../../integration/device-annex-automatic-picking-arms.md) §9）。
- 自动出库合同 `plan_delta` J01~J11 联合验收是否关闭（该合同当前仍 `ReviewRequired`）。
- [主合同修订提案](../../integration/wms-joint-confirmation-automatic-picking.md)第 2–6 节的身份与 wire 提议需 WMS
  书面回复；回复前 §5.4 保持 TBD，不得据此开始编码。
- `bin-line-common` 真实代码抽取的起始时间取决于 §9 依赖的基础层改动、以及[回程段扫码重试修正](2026-09-22-bin-line-scan-retry-fix.md)何时先合入 `manual-picking`。
- **`workline_code` 已确定为 `KT11`**（内部已分配，不是 WMS 待给值）；M1 前置项是把它送进联合确认清单让 WMS
  登记接受 `task_type=AUTO` 绑定该编码，内部阻塞点是 `automatic-picking` 的 `definition.py` 尚未定稿——没写完
  插件静态声明，`KT11` 就无法接入系统，不能只等 WMS。
- 工作段（§7，等 §5.4 一起设计）设计时必须显式评估是否存在与
  [回程段重试修正](2026-09-22-bin-line-scan-retry-fix.md)同类的"决定即冻结导致现场卡死"风险；本轮评审已经
  在回程段踩过这个坑，工作段设计不应该重新独立发现同一类问题。

## What already exists

| 既有能力 | 本设计的处理 |
| --- | --- |
| `PickingTask`/`plan_delta`/Transport/`DeviceCommand`/Evidence/`WmsConfirmation`/WorkLine 生命周期 | 直接复用，不改 |
| `MaterialExecution` | 直接复用，见 §5.1 |
| `wms_adapter/outbound_picking/` 的 `work_plan`/`material_decide`/`movement_report`/`source_empty` wire 和 adapter | 直接复用 |
| 手工插件的入线/货架循环/回程/drain/任务完成代码 | 抽取为 `bin-line-common`，见 §6 |
| 设备统一接口白皮书、错误语义标准化 | 直接复用，设备附录只补充 `ARM01`/`ARM02` 专属字段 |
| 事件幂等机制（白皮书 §4.2，按内容判定） | [回程重试修正](2026-09-22-bin-line-scan-retry-fix.md)复用它区分真实重扫与网络重试，零新增判定逻辑 |

## NOT in scope

- 不做空取（`source.empty_decide`）与单盘/Bin NG（第二切片）。
- 不做插件切换或人工线/自动线共线。
- 不新增动态 registry、通用工作流引擎或跨供应商错误码注册表。
- 不在设备附录未获供应商书面确认前编写生产 Adapter。
- 不把手工线现场未验收（C1~C7 `NOT ACCEPTED`）当作阻塞自动线设计的理由，但阻塞 `bin-line-common` 真实代码抽取的时间点见 §9。
- **§8 回程重试修正不跟随本文档的评审/实施进度**（eng review 评审新增）：已拆分为
  [独立文档](2026-09-22-bin-line-scan-retry-fix.md)，独立评审、独立落地，不因本设计的外部依赖（WMS/供应商回复）被拖慢。

## Failure modes（eng review 评审产出）

| 新代码路径 | 生产失败方式 | 测试是否覆盖 | 错误处理是否存在 | 用户可见性 |
| --- | --- | --- | --- | --- |
| `bin_line_returns` 新物理事件覆盖旧决定 | 扫码器硬件故障反复误触发（非离场-再进场、非手动 PLC 重置这两种现场确认过的合法触发源） | 需新增场景（独立文档 §5），尚未写代码 | 有（§3 告警阈值，log/metric） | 有可观测信号（告警面板），不是静默失败 |
| `automatic_picking_cells/reels` schema（§5.4，TBD） | 无法评估——字段未定 | 无法评估 | 无法评估 | 已知阻塞项，不构成 critical gap |
| `bin-line-common` 抽取（§6） | 抽取过程引入回归，破坏手工线现有行为 | 有（§12：先内部拆分验证测试全绿，再搬迁） | 有（小步骤+可回滚） | 测试红灯直接暴露 |
| `retry_count` 告警阈值 | 阈值不合适（过高漏报/过低噪音） | 无调优反馈机制 | 部分（实施时取保守估计，无回调整流程） | 阈值不合适时靠事后调参发现 |

Critical gap 判定（无测试 AND 无错误处理 AND 静默失败三者同时成立）：0 个。`retry_count` 阈值调优缺口是真实
运维缺口，但不属于"新代码路径静默失败"类别，留给实施阶段处理。

## Worktree parallelization strategy（eng review 评审产出）

| Step | Modules touched | Depends on |
| --- | --- | --- |
| §8 回程重试修正落地 | `workline_plugins/manual-picking/` | — |
| M2 骨架占位（definition.py、静态 composition、包目录） | `workline_plugins/bin-line-common/`（新）、`workline_plugins/automatic-picking/`（新） | — |
| 设备附录 O1~O17 / 主合同修订提案逐项外部沟通 | `docs/integration/`（文档） | — |
| `bin-line-common` 真实代码抽取 | `workline_plugins/manual-picking/`、`workline_plugins/bin-line-common/` | §8 修正已合入 且 可靠恢复基础层已合入 |
| §5.4/§7 工作段 schema 与状态流设计 | `docs/superpowers/specs/`（新文档） | WMS 逐项确认主合同修订提案第 2–6 节 |
| 自动插件真实执行代码（M3） | `workline_plugins/automatic-picking/` | 抽取完成 且 工作段设计完成 且 设备附录确认 |

Lane A（§8 修正）、Lane B（M2 骨架）、Lane C（外部沟通）三条独立并行；Lane A 合入后解锁抽取，抽取完成 + Lane C
拿到回复后解锁工作段设计，设计完成后解锁 M3。Lane A 与"`bin-line-common` 抽取"触碰同一批文件，是严格先后而非并行，
已在 §9/独立文档锁定顺序。

## Implementation Tasks

Synthesized from this review's findings.

- [ ] **T1（P1，human:~15min / CC:~3min）** — 测试 — 重写两个 SCAN3/4 回归测试以匹配新的重试覆盖行为
  - Surfaced by: 测试审查 — `test_scan3_rescan_does_not_change_frozen_route_or_command`/`test_scan4_rescan_preserves_first_fifo_order_and_command` 断言方向与新行为矛盾
  - Files: `workline_plugins/manual-picking/tests/test_scan_flow.py`
  - Verify: `uv run pytest workline_plugins/manual-picking/tests/test_scan_flow.py -k "rescan"`
- [ ] **T2（P1，human:~2h / CC:~30min）** — manual-picking — 落地回程扫码重试修正（含 `retry_count` 与告警阈值）
  - Surfaced by: 独立文档 `2026-09-22-bin-line-scan-retry-fix.md`
  - Files: `application/scan_flow.py`、`application/passage_model.py`
  - Verify: T1 通过 + 新增覆盖/告警场景测试全绿
- [ ] **T3（P2，human:~30min / CC:~5min）** — 合同 — 提交回程重试修正给 WMS/WES 联合评审（§3.3/§3.4 措辞）
  - Surfaced by: 独立文档 §4
  - Files: `docs/contracts/wms-manual-outbound-picking-integration-requirements.md`
  - Verify: WMS 书面确认
- [ ] **T4（P2，human:~10min / CC:~2min）** — WMS 沟通 — 索要 `workline_code=KT11` 登记确认
  - Surfaced by: 跨模型发现 6
  - Files: `docs/integration/wms-joint-confirmation-automatic-picking.md`
  - Verify: WMS 书面回复
- [ ] **T5（P2，human:~1d / CC:~1h）** — automatic-picking — 补齐 `definition.py` 静态声明，接入 `KT11`
  - Surfaced by: spec M2 退出条件
  - Files: `workline_plugins/automatic-picking/src/automatic_picking/definition.py`（待建）
  - Verify: 插件骨架 FAST 测试通过，静态 composition 可启动
- [ ] **T6（P3，human:~1h / CC:~10min）** — spec — WMS 逐项确认主合同修订提案后重写 §5.4
  - Surfaced by: 架构问题 1A
  - Files: `docs/superpowers/specs/2026-09-22-automatic-picking-plugin-system-design.md`
  - Verify: 新 schema 与 §6.4 改名清单、device annex §4.2 字段对齐

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
| --- | --- | --- | --- | --- | --- |
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | 未运行 |
| Outside Review | Claude subagent（Codex 未认证降级） | Independent 2nd opinion | 1 | COMPLETED | 6 findings，全部折叠进本文档与独立文档 |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | ISSUES_OPEN | 10 findings（架构4 + 跨模型6），0 unresolved，6 项实施任务待执行（T1~T6） |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | 后端设计文档不适用 |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | 未运行 |

- **OUTSIDE COVERAGE：** Codex 状态 `not_authed`（已安装未认证），按流程降级为 Claude subagent（fresh context，独立于本会话）。subagent 完成一轮独立复核，产出 6 项发现，全部经用户逐条确认（其中跨模型发现 6 由用户提供了比预设选项更准确的现场事实：`workline_code=KT11` 已分配，阻塞点是 `definition.py` 而非 WMS）。
- **CROSS-MODEL：** 无直接分歧——outside voice 的 6 项发现都是原生四节评审未覆盖的新问题（战略排期、行为变更与零回归重构的顺序冲突、Mixin/ENUM 合规缺口、工作段同类风险未标注、LIFO 命名误导、`workline_code` 追踪缺失），不是对同一话题的不同结论，因此没有需要用户仲裁的对立观点。
- **VERDICT：** ENG REVIEW 已完成，全部发现折叠进文档，0 unresolved decisions；`§8` 回程重试修正与 `automatic-picking` 主设计已拆分为独立评审/实施轨道。eng review required 才能进入 `/ship`——但当前状态是"设计已锁定，等待外部回复与 T1~T2 实施"，不是常规代码 PR 的 CLEAR/NOT CLEARED 判定，下一步是执行 Implementation Tasks，不是 `/ship`。

NO UNRESOLVED DECISIONS
