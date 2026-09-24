---
title: manual-bin-line 重写与 WES 职责收敛对齐
status: Proposed
created_at: 2026-09-22
audience: WES 架构、WMS/RCS/ECS 对接开发、手工/自动拣料插件二次开发人员
scope: 用全新插件 manual-bin-line + bin-line-common 取代原地重构 manual-picking 的路线；
  v1（manual-picking）冻结不动，仅接受回程扫码重试这一条独立补丁；两个插件按新的 WES 系统边界原则设计。
related:
  - docs/architecture/wes-responsibility-convergence-ledger.md
  - docs/superpowers/specs/2026-09-22-bin-line-scan-retry-fix.md
  - docs/superpowers/specs/2026-09-22-automatic-picking-plugin-system-design.md
  - docs/contracts/wms-manual-outbound-picking-integration-requirements.md
  - docs/architecture/authority-matrix.md
supersedes:
  - ../archive_docs/wes_backend/docs/superpowers/plans/2026-09-22-bin-line-common-extraction.md（实施路径已废弃：渐进重构+搬迁不再执行；
    其三表 DDL、`dispatch_scan_event` 接口、Review Focus 测试场景等目标架构设计被本文继承，见"旧文档处置"一节）
---

# manual-bin-line 重写与 WES 职责收敛对齐

## 定位

本文取代 [bin-line-common 共享包抽取计划](../../../../archive_docs/wes_backend/docs/superpowers/plans/2026-09-22-bin-line-common-extraction.md)的**实施路径**，
但继承它已经评审过的**目标架构设计**（三表 DDL、`dispatch_scan_event` 接口、Review Focus 测试场景，详见"旧文档
处置"一节）。原计划假设"在 `manual-picking` 内部渐进重构、验证零行为变化后再搬迁"，前提是 v1 的代码和数据要素
会被后续版本直接继承。经过评审确认三件事后，这个前提不再成立，渐进重构+搬迁这套实施路径不再执行：

1. 联调服务器正在用 v1（`manual-picking`）对接真实 WMS/RCS/ECS，收集真实过程数据；v1 在整个重写期间必须保持
   现状可运行，不能因为重构承担额外风险。
2. revision identity、evidence-driven 来源货架调度、三表拆分、扫码重试修正——这几项原本作为"阻塞抽取的前置
   合入项"——本质上是 v2 的目标设计本身，不是需要先在 v1 上验证、再迁移的中间态。
3. [WES 职责收敛账本](../../architecture/wes-responsibility-convergence-ledger.md)已经对 v1 现有代码做过第一轮只读
   盘点，识别出多处替 RCS 做物理资源判断的删除候选（R01/R02 等）；v2 应该直接按账本的目标终态设计，不必先在
   v1 上执行删除、验证、再搬迁这一整套动作。

结论：新建独立插件 `manual-bin-line`（`plugin_key=manual-bin-line`）与共享包 `bin-line-common`，两者都是
全新代码，不引用、不搬迁 v1 的任何文件；v1 除下方"v1 补丁"一节外完全冻结。

**命名说明**：新插件不叫 `manual-picking-v2`，因为它未来要承载不止出库拣料一种 `task_type`，还包括入库上架；
"picking"是具体业务动作词，会预设错误的边界。改名 `manual-bin-line`，呼应"人工操作的料箱产线"这个更准确的定位——
入线/货架循环/回程段本来就与具体是拣料还是上架无关，`manual-bin-line` 只是"人在这条料箱产线上完成工作段动作"
的通用容器，工作段内部按 `task_type` 分流到不同业务逻辑。本文只解决出库拣料这一种 `task_type` 的完整设计；
入库上架的工作段细节留给后续独立评审，但 schema（`manual_bin_line_works` 带 `task_type` 字段）和插件命名从第
一天就不预设"只有拣料"。

## WES 系统边界原则（本文与后续所有相关设计的共同前提）

WMS 决定业务意图、变化和终态；WES 将业务意图编排成自动化 SOP（把"做什么"转换成"如何自动化执行"）；ECS/RCS
裁决物理资源分配并执行设备动作；权威事实推动 WES 进入下一步骤。四句话概括：

> WMS owns business intent. WES owns automation workflow. ECS/RCS own physical execution.
> Facts drive workflow progression.

核心执行模型收敛为 `Intent → Action → Fact → Next Action`，例如"WMS 要求货架到工作位 → WES 请求 Transport →
RCS 调度 AGV → 货架实际到位 → WES 启动依赖该货架的下一作业步骤"，而不是"预计位置 → 资源预占 → 到位顺序预测 →
围栏 → 冲突推断"——后者是在 WES 内部重新实现一部分 RCS。

区分两个概念：

- **Dependency Gating（可以存在）**：当前自动化步骤依赖的事实还没发生，这一步就不能启动。例如"没有 SCAN4
  到位事实，不能进入 return_batch"；"货架尚未实际到达工作位，不能启动依赖它的拣选步骤"。这是正常的业务因果
  关系，属于插件的 SOP 逻辑。
- **Resource Fencing（不应该存在于 WES）**：因为某个任务的物理动作未完成，就推断"整个工作站/其他货架/其他
  无依赖任务全部阻塞"。例如按历史 Transport 状态计算"占窗"、按下发顺序推断到位顺序、维护 `ExpectedPosition`/
  `PositionFence`/`ResourceConflictResolver` 类的预测性状态。这些属于 RCS/ECS 的职责，WES 不建第二套。

同理：`Command order ≠ Physical order`——先下发的货架不保证先到位，B 先到就按 B 的事实推进 B，不等 A。料箱路径
（`SCAN1→SCAN2→SCAN3→SCAN4/NGZone` 或 `SCAN1→SCAN3→SCAN4/NGZone`）只判断"当前观察到的事实转换是否属于合同
允许的物理路径"，不维护 `ExpectedSequence`。`return_batch` 的顺序真源是 SCAN4 首次实际到位顺序，不按 WMS 原始
列表、创建顺序或命令下发顺序重排。

**后续审查任何新增 WES 控制逻辑，统一问三个问题**（本文和账本共用的判断标准）：

1. 这个事实的权威所有者是谁？如果是 WMS/RCS/ECS，WES 应消费事实，不复制能力。
2. 这是通信可靠性还是业务规则？通信可靠性属于基础层；自动化业务决定属于业务插件。
3. 这是保护当前流程的因果依赖，还是在预测/锁定物理资源？前者可以存在，后者原则上不属于 WES。

数据库模型同一原则：优先保存 `WMS Intent`、`Workflow Instance`、`Observed Fact`、`Issued Action`、
`Action Result`、`Idempotency Identity`；谨慎或删除 `PredictedRackPosition`、`ReservedResource`、
`ExpectedArrivalOrder`、`VirtualOccupancy`、`ResourceFence` 一类"WES 对物理世界的预测"。

## 决策记录（本次评审对话确定，后续实施不再重新讨论）

| 决策点 | 结论 | 理由 |
| --- | --- | --- |
| 重构方式 | 新建 `manual-bin-line` 插件 + `bin-line-common` 共享包，全新代码，不 `git mv`、不复用 v1 文件 | 系统未发布不等于零约束——联调服务器已有真实 WMS/RCS/ECS 集成在跑，v1 必须保持稳定 |
| v1 处置 | 冻结不动，仅接受一条独立补丁 | 见下方"v1 补丁"一节 |
| v1 补丁范围 | 立即落地[回程段扫码重试修正](2026-09-22-bin-line-scan-retry-fix.md) | 现场可能正在经历"扫了没反应"的卡死，修正已评审通过、范围小、风险低，不应该等 v2 整体上线 |
| v2 验证路径 | 阶段一：用联调服务器 v1 产生的真实 `InboundEvidence` 离线回放，对比 v2 服务层是否符合 spec 定义的目标行为（不要求跟 v1 结果一致）。阶段二：新开一条真实 WorkLine，`plugin_key=manual-bin-line`，接入真实 WMS/RCS/ECS 做在线验证 | 离线回放低风险验证基本正确性；在线验证覆盖回放无法覆盖的真实时序和设备交互 |
| 核心层职责收敛节奏 | 账本 R03/R04/R06/R07/R16/R18 等核心层（`src/app/`）收敛工作可以独立推进，不必等 v1 退役，但必须保持向后兼容——不删除 v1 现在依赖的字段/查询路径，只是新代码（v2）不再依赖其中已经不该有的物理预测语义 | 核心 Transport/WMS 集成层只有一份，v1、v2 都要用；核心层收敛与插件重写是两条可以并行、但需要互相守约束的轨道 |
| 退役路径 | v2 在真实 WorkLine 上跑稳定后，删除 v1 目录、`plugin_key`、`manual_picking_passages` 表；验收标准由实施时现场数据决定，本文不预先设定 | 现在下判断为时过早，先把 spec 焦点放在怎么写对 v2 |

## 范围与产物

- 新建 `workline_plugins/manual-bin-line/`（Python 包 `manual_bin_line`，`plugin_key=manual-bin-line`）。
- 新建 `workline_plugins/bin-line-common/`（Python 包 `bin_line_common`），从第一天按目标形状直接编写，供
  `manual-bin-line` 和未来 `automatic-picking` 共同依赖；不导入任何具体插件。
- `workline_plugins/manual-picking/`（v1）除下方补丁外不接受任何改动，继续绑定现有联调 WorkLine。
- 旧的三表模型（`EntryPassage`/`ReturnLeg`/`ManualPickingWork`）、`dispatch_scan_event` 接缝、revision identity
  wire、evidence-driven 来源货架调度、扫码重试修正——原计划里作为"迁移目标"的这些设计——直接作为 v2 建表和
  写代码的第一版形状，不需要"先在旧宽表上拆分，再搬迁"的中间步骤。

## v2 设计要点

以下每一点都要能回答"账本三个问题"里的第 3 问（因果依赖 vs 物理预测）：

1. **三表拆分**：`bin_line_passages`（入线+处置，`bin-line-common`）、`bin_line_returns`（回程，`bin-line-common`）、
   `manual_bin_line_works`（PDA 准入与完成，插件私有，带 `task_type` 字段区分出库拣料/入库上架，本文只实现拣料
   分支，上架分支的具体字段和状态机留给后续独立评审）。这是从零建的新 schema，不需要 `manual_picking_passages`
   的 downgrade 兼容或数据迁移。
2. **`dispatch_scan_event` 分发接缝**：入线（SCAN1）、货架循环、回程（SCAN3/SCAN4）归 `bin-line-common`；工作段
   （SCAN2 之后）归各插件私有方法，通过 `on_scan2` 回调交接。
3. **回程扫码重试修正内置**：新的物理扫码事件（离场-再进场，或工人手动 PLC 重置，两者按同一规则处理，见
   [独立文档](2026-09-22-bin-line-scan-retry-fix.md)）覆盖旧决定；不增加混合重扫计数或阈值告警。
4. **revision identity**：`plan_revision` 是人工准入/完成 wire 的显式字段；WMS 完成回调按 `(task_id, plan_revision,
   bin_code)` 精确匹配，不用最新 `id` 或时间顺序猜测轮次。落地前需要 WMS 侧确认这个字段已经/将要携带在真实
   wire 里——这是外部协调项，不是代码问题，会影响阶段二真实 WorkLine 验证的开始时间，不影响阶段一的开发和离线
   回放。
5. **`BinLineBatchDriver` 不建来源货架历史投影**：任务 owner 从当前工作位 `PositionProjection.source_transport_task_id`
   反查 `TransportDecisionBinding.picking_task_id/source_evidence_id` 得到；binding 缺失/未闭合/与事实不匹配时
   停止对账，不猜测。不新建 `PickingTaskBinSourceRack` 的等价物。
6. **inbound/return 公平交替**：有 task owner 时按"本次到位 Binding 之后最近封闭的 Batch Evidence"推导优先方向，
   一个方向明确无候选时同一 tick 尝试另一方向；无 task owner 时只尝试 `return_batch`。不新增轮转状态表。
7. **`_device_has_unclosed` 类检查的语义边界（账本 R10 的延伸）**：v2 里任何"下发前检查是否有未闭合动作"的
   代码，必须能清楚回答"这是同一料箱这一步的因果依赖（保留），还是在假设设备当前被占用、替 ECS 做互斥（删除，
   ECS 自己会拒绝或排队）"。写这类检查时在代码注释或测试里明确写出是哪一种，不允许含糊。
8. **`return_batch` 排序真源**：`(workline_id, scan4_received_at, scan4_evidence_id)`，只反映 SCAN4 首次到位的真实
   时间顺序，不受任务创建顺序、WMS 原始列表顺序影响。

## 账本对齐（[wes-responsibility-convergence-ledger.md](../../architecture/wes-responsibility-convergence-ledger.md)）

| 账本编号 | v2 处理方式 |
| --- | --- |
| R01/R02（跨任务读历史 Transport 状态算占窗/复用限制） | 不复刻。`BinLineBatchDriver` 不做这类历史推断，货架是否可提交由 RCS 裁决 |
| R05（当前货架到位事实判断当前步骤可否启动） | 保留，作为 Dependency Gating 的正面样本；不得扩展为其他货架或整线的准入锁 |
| R09（首次 SCAN4 顺序 + 连续前缀） | 保留为 `return_batch` 排序真源，见"v2 设计要点"第 8 条 |
| R10（下发前检查本设备旧命令未闭合） | 见"v2 设计要点"第 7 条，v2 实施时要显式做出取舍，不能照抄 |
| R03/R04（`resource_fence_id`、Position 投影的物理占用语义） | 核心层收敛工作独立推进（见"决策记录"），v2 直接依赖收敛后的语义：只消费事实投影和幂等身份，不读取任何物理占用字段 |
| R06/R07/R08（Transport 通信可靠性、超时对账） | v2 直接复用收敛后的核心能力，不在插件层重建重试或超时判断 |
| R16（`plugin_composition.py` 显式装配） | 扩展为同时识别 `manual-picking`、`manual-bin-line` 两个 `plugin_key`，见"部署"一节 |
| R18（WMS outbound plan activation/prepare） | v2 复用同一套核心接收逻辑；WES 只在其中选择"可执行的下一自动化步骤"，来源/目标/优先序继续来自 WMS |

## 部署 / composition

- `deployment/plugin_models.py`、`plugin_composition.py` 同时识别两个 `plugin_key`：`manual-picking`（v1，私有表
  `manual_picking_passages`）、`manual-bin-line`（私有表 `manual_bin_line_works`，与 `automatic-picking` 共享
  `bin_line_passages`/`bin_line_returns`）。
- 一条 WorkLine 只能绑一个 `plugin_key`；联调服务器现有 WorkLine 继续绑 v1，新增一条 WorkLine 绑 v2（阶段二验证
  用）。
- 架构依赖扫描器扩展为：`bin-line-common` 不能导入任何具体插件；`manual-picking` 与 `manual-bin-line` 互相不能
  导入。

## 验证路径

**阶段一：离线回放。** 新增一个回放工具（原计划没有这一项），从联调服务器 v1 产生的真实 `InboundEvidence` 记录
导出样本，作为输入依次喂给 v2 的 `dispatch_scan_event`/`BinLineBatchDriver` 等服务层，产出对比报告。断言标准是
"是否符合本文定义的目标行为"，不是"是否与 v1 当前结果一致"——v2 在冻结点、revision 处理、调度方式上就是设计成
不一样。

**阶段二：真实 WorkLine。** 离线回放通过后，新开一条真实 WorkLine 绑定 `manual-bin-line`，接入真实 WMS/RCS/
ECS。这一步依赖两个外部协调项：现场确定试点产线/工位；WMS 确认 `plan_revision` 字段已经在真实 wire 里携带。这两
项不阻塞 v2 的开发和阶段一验证，只阻塞阶段二的开始时间。

## 退役路径（占位）

v2 在真实 WorkLine 上稳定运行后，删除 `workline_plugins/manual-picking/`、`plugin_key=manual-picking`、
`manual_picking_passages` 表及其迁移；如需保留历史数据用于追溯，走归档而非照搬旧表结构。具体验收标准（跑多久、
覆盖多少真实任务、允许的异常率）留到实施临近退役阶段时，根据届时的真实运行数据另行确定，本文不预先设定数字。

## NOT in scope

- 不改动 v1 的核心业务逻辑，唯一例外是回程扫码重试修正这一条独立补丁。
- 不删除或改变 v1 依赖的 `PickingTaskBinSourceRack` 及其核心层查询方法，直到 v1 实际退役。
- 不要求 WMS 现在就切换到 v2 的 wire 约定；`plan_revision` 字段的真实生效时间由外部协调决定。
- `automatic-picking` 的工作段、双臂流程和私有 schema 设计不受本文影响，只是它依赖的 `bin-line-common` 现在的
  来源是"从零设计"而不是"从 manual-picking 搬迁"，[自动拣料设计文档](2026-09-22-automatic-picking-plugin-system-design.md)
  里引用抽取计划的地方需要在其自身评审流程中同步更新为引用本文（不在本文范围内代为修改）。
- 不在本文里展开核心层职责收敛（R01～R19）的具体执行计划，那是独立于本文的工作，本文只声明它与 v2 的依赖关系
  和节奏约束（见"决策记录"）。
- 不预先设定退役验收的具体数字标准。

## 旧文档处置

[bin-line-common 共享包抽取计划](../../../../archive_docs/wes_backend/docs/superpowers/plans/2026-09-22-bin-line-common-extraction.md)分两部分处理，不是整体作废：

- **作废的是实施路径**：它假设"在 v1 内部渐进重构、Stage 1 拆分验证零行为变化、Stage 2 `git mv` 搬迁、Stage 3
  接线"，这套过渡步骤的前提（v1 会被后续版本直接继承）不再成立，不会被执行。
- **继承的是目标架构设计**：该计划里已经评审过的具体设计——Task 1.1 Step 4～9 的三表 DDL 和字段（`EntryPassage`/
  `ReturnLeg`/`ManualPickingWork` 的形状，只是表名和插件私有表要按本文改为 `bin_line_passages`/`bin_line_returns`/
  `manual_bin_line_works`）、Task 1.2 的 `dispatch_scan_event`/`on_scan2` 接口签名、Stage 2.3 Step 3 的 owner 反查
  逻辑（Position→Binding）、Stage 2.3 Step 4～5B 的 Review Focus 测试场景清单（约束 A/B/C、跨 WorkLine 隔离、
  同料箱多轮 revision、inbound/return 公平交替）——这些内容是正确的，直接作为 manual-bin-line 和 bin-line-common
  建表、写接口、写测试用例时的参考依据，不需要重新设计一遍。实施计划阶段会逐项标注具体继承哪一节。

该文件已移至项目外归档，完整保留被引用的设计内容；其实施步骤不再作为可执行计划。
[回程段扫码重试修正 spec](2026-09-22-bin-line-scan-retry-fix.md)继续有效，同时是 v1 补丁和 manual-bin-line 原生
行为的依据，不需要修改。

## Open Questions

1. `manual-bin-line` 与 `automatic-picking` 对 `bin-line-common` 的接口需求是否完全一致？本文假设一致（沿用
   原抽取计划 §6.3 的 `dispatch_scan_event` 签名设计），实际编码时如果两个插件对同一段共享逻辑有分歧，需要
   回来修订本文，不能在实现时自行扩展共享包的可配置项。
2. 核心层职责收敛（R03/R04/R06/R07/R16/R18）与 v2 开发并行推进时，谁负责保证向后兼容不破坏 v1？建议在启动
   核心层收敛工作时，把"v1 现有测试全绿"列为该项工作自己的验证条件之一，而不是依赖 v2 这边事后发现。
