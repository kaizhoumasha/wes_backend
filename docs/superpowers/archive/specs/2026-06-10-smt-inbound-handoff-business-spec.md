# SMT 入库 Handoff 业务 SPEC

> 状态：已实现并归档 - 后端闭环已在 `c62066b9 v0.6.0.0 feat: SMT 入库 handoff 后端闭环` 落地
> 日期：2026-06-10
> 兼容策略：当前系统未发布，按破坏性优化处理；不保留旧 `smt_full_box_exchange` 插件、旧候选扫描任务或兼容 alias。
> 关联背景：
>
> - `docs/architecture/SRS.md`
> - `docs/architecture/adr/2026-05-13-wes-wms-rcs-resource-boundary.md`
> - `docs/superpowers/archive/plans/2026-05-22-bin-operation-domain.md`
> - `docs/superpowers/specs/2026-06-06-wes-single-layer-rack-orchestration-boundary-spec.md`
> - `docs/superpowers/specs/2026-06-09-workline-resource-constrained-parallelism-spec.md`
>
> 文档职责：本文是 SMT 粗分机到分拣机业务衔接的目标、边界、状态流和验收合同真源；执行任务计划已创建，实施前必须先复审
> `docs/superpowers/plans/2026-06-10-smt-inbound-handoff-business.md`。
> 文档边界：本文不承载完整任务拆分、迁移步骤、代码实现或测试代码；这些内容进入 execution plan。

## 0. 实施前置合同

本 SPEC 只能作为目标合同。进入代码实施前，execution plan 必须补齐并通过工程评审以下事项：

- **Claim 后恢复合同**：明确 `SORTING_SOURCE_PICK_REQUESTED` 内部 Inbox 创建后，`PICK_REQUESTED`、`CLAIMED_BY_SORTING` 或 `source_pick_inbox_id` 对应 Inbox 进入 `FAILED`、`DEAD_LETTER`、stale `PROCESSING`、未产生命令时的恢复路径。
- **内部 Inbox 事件合同**：`SORTING_SOURCE_PICK_REQUESTED` 必须使用一等内部事件 Inbox kind/helper 表达；execution plan 必须补齐 Inbox enum/check constraint migration、source system、payload envelope、`session_id` / `workline_id` 绑定、`claim_bucket_key` 归属和幂等键，不得由 handoff service 临时拼 raw inbox payload 或伪装成设备事件。
- **Release fact producer 合同**：唯一生产入口来自 `MOVE_OUT_ACTIVE_RACK` 成功后的现有后端 WorkLine 事实链；producer 接入 `ROUGH_SORTER_RELEASE_FACT` / resource fact 应用链并调用 handoff service，不能由前端/API、粗分机普通 session 状态、Beat 或资源投影查询反向猜测创建 demand。
- **目标 WorkLine 路由合同**：必须采用“配置候选 + 运行态准入”两阶段；不得从粗分机 release fact 推断目标线，也不得在 handoff service 内硬编码目标线。
- **Command correlation 合同**：明确 `SORTING_SOURCE_PICK_REQUESTED`、生成的 `SORTING_SOURCE_PICK` command、Outbox dispatch、command result、`current_material` 和 handoff source item 的稳定关联字段；command/outbox 创建后的 runtime effect 写回是正常链路，recovery 只做兜底校验和修复。
- **Demand 聚合状态合同**：demand 状态必须由单一聚合入口按 source item 计数、满箱交换状态、人工 hold 和对账状态重算，避免 API、Beat、人工动作和 `available_actions` 各自推断。
- **Handoff 原因码 catalog**：定义 `SmtInboundHandoffReasonCode` 或等价单一 catalog，驱动 `failure_code`、`available_actions`、API 响应和测试断言。
- **数据库热路径合同**：定义 demand/source item 的唯一约束、部分索引、claim 排序字段、内部 Inbox `claim_bucket_key` 非 `serial:unknown` 约束和 PostgreSQL 访问路径验证。
- **测试矩阵**：覆盖 usage policy、满箱交换回调、route selection、claim 并发、内部 Inbox kind/envelope、release producer 唯一性、command correlation 写回、Demand 聚合、claim 后恢复、Beat 兜底、API 权限/结构、插件 manifest/event handler 和旧入口不可用；这些合同必须先有 RED 测试再实现。
- **GitNexus 门禁**：execution plan 必须在修改 `WorklineInbox`、session resolver、runtime effect、现有 WorkLine service/plugin 等函数、类或方法前列出 GitNexus impact analysis 门禁；HIGH/CRITICAL 风险先向用户汇报。
- **评审状态更新**：execution plan 创建并吸收上述合同后，仍必须先完成工程复审；复审通过前本文不得标记为可直接实施。

后续生产化事项：SMT Handoff 运营看板、告警阈值和 Runbook 不进入本阶段实现；应在首版产生真实运行数据后作为 P2 follow-up 追踪。

## 1. 背景

SRS 描述了 SMT 入库的两段能力：

- 粗分机将收货物料按料箱/料格放入单层货架。
- 单层货架后续进入混合入库：可选满箱交换，或进入分拣机逐盘入库。

代码调查确认，当前系统已经具备多项低级能力：

- `rough_sorter` 与 `workline_session` 能处理粗分机单盘物料。
- `rack_tasks` 能处理单层货架移出、补给和到位回调。
- `SMT_SORTING_INBOUND` 能处理分拣机单盘流程，但当前从 `SORTING_SOURCE_PICK` 成功回调开始。
- `handling` 域已提供 CTU/料箱级低级执行能力，包含 `RACK_BIN_EXCHANGE_REQUEST` 与 `FULL_BOX_EXCHANGE` 回调闭环。

真正缺口不是低级能力，而是一个生产可达的业务入口：把粗分机释放的单层货架转化为后续的满箱交换或分拣 source demand。

## 2. 目标

建立 SMT 入库 Handoff 能力，打通以下业务链路：

```text
粗分机单盘装箱
  -> 单层货架释放
  -> 可选满箱交换
  -> 分拣机 source demand
  -> 分拣机逐盘 SORTING_SOURCE_PICK
```

目标行为：

- 粗分机释放单层货架后，WES 幂等创建一条 handoff demand。
- Handoff 根据释放快照和 WMS/RCS 执行结果决定是否发起满箱交换。
- 满箱交换复用 `handling` 系统低级能力，不复活旧满箱交换插件。
- 分拣首盘取盘必须通过 `SMT_SORTING_INBOUND` 插件事件进入 runtime，由插件生成 `SORTING_SOURCE_PICK` command。
- 目标 SMT 分拣 WorkLine 由后端显式路由合同选择；粗分机 release fact 不承担分拣线选择。
- 前端只提供可观测、异常处置和联调能力，不作为正常业务推进按钮。
- 正常业务由后端事件驱动推进，Celery Beat 仅兜底扫描。

## 3. 非目标

本阶段不做以下事项：

- 不复活 `smt_full_box_exchange` 插件。
- 不恢复 `scan_smt_full_box_exchange_candidates_batch`。
- 不把 SMT 专用 handoff 逻辑写入 runtime core。
- 不让 service 直接绕过插件创建设备命令。
- 不做通用跨 WorkLine workflow engine。
- 不让 WES 锁定五层空箱、不本地判断 CTU 路径、不交换库存属性、不替代 WMS/RCS 权威。
- 不把前端按钮作为正常业务流转入口。
- 不在本阶段实现前端运营页面；后端第一版只提供结构化查询/处置 API 合同。
- 不一次性建设完整运营看板；第一版只约束低基数指标边界，完整看板和告警阈值后续基于真实运行数据补齐。

## 4. 架构原则

- DRY：满箱交换复用 `handling`，货架搬运复用 `rack_tasks`，单盘分拣复用 `SMT_SORTING_INBOUND`。
- KISS：第一版只服务 SMT 粗分机到分拣机 handoff。
- SOLID：业务决策、低级执行、资源投影、session 推进和前端操作分层。
- YAGNI：不提前抽象通用编排平台；只有已存在的低级执行能力放在系统层。
- 插件边界：插件负责业务动作和设备 command；service 负责 demand、幂等、状态推进和跨域协调。

## 5. 核心业务对象

新增业务账本分为两层：

- `SmtInboundHandoffDemand`：一条记录对应一次粗分机单层货架释放，承载主状态、满箱交换决策和兜底推进字段。
- `SmtInboundHandoffSourceItem`：一条记录对应一个可被分拣机认领的 source item，承载逐项状态和并发 claim 事实。

`source item` 有独立生命周期和并发认领风险，不使用 `source_items_json` 作为热路径状态存储。

数据约束和热路径访问合同必须在 execution plan 中固化：

- `demand_key` 和 `rack_release_id` 必须保证幂等唯一，避免同一次释放重复触发满箱交换或分拣。
- `SmtInboundHandoffSourceItem` 必须用 `handoff_demand_id + item_key` 保证 demand 内 item 唯一。
- READY item claim 必须使用数据库事务、行级锁和稳定排序，不能依赖应用内内存锁。
- demand 兜底扫描必须有按 `status`、`next_attempt_at`、`updated_at`/`id` 的热路径索引。
- claim 后恢复必须能按 `status`、`source_pick_inbox_id`、`updated_at` 定位卡住 item，不能全表扫描。
- 内部 `SORTING_SOURCE_PICK_REQUESTED` Inbox 必须写入目标 `session_id` / `workline_id`，并生成 `session:{id}` 或 `workline:{id}` 等明确 `claim_bucket_key`；不得落入 `serial:unknown` 热队列。
- 插件生成 `SORTING_SOURCE_PICK` command 后，source item 必须记录 command correlation evidence；不能只依赖 `source_pick_inbox_id` 判断首盘命令是否已创建或完成。

`SmtInboundHandoffDemand` 关键字段：

| 字段 | 说明 |
| --- | --- |
| `demand_key` | 幂等键，格式 `smt-inbound-handoff:{rack_release_id}` |
| `rack_release_id` | 粗分机释放货架的稳定事实 ID |
| `source_workline_id` / `source_workline_code` | 粗分机工作线 |
| `target_workline_id` / `target_workline_code` | 后端路由选出的目标分拣工作线；不来自粗分机 release fact |
| `single_layer_rack_code` | 被释放的单层货架 |
| `release_reason_code` | 释放原因，例如 `NO_COMPATIBLE_OR_EMPTY_CELL` |
| `bin_snapshots_json` | 释放时 4 个料箱及料格快照，只作为 release evidence |
| `decision_status` | 满箱交换决策状态 |
| `handling_operation_key` | 满箱交换 handling operation key |
| `sorting_source_demand_key` | 分拣 source demand 幂等键 |
| `status` | handoff demand 主状态 |
| `failure_code` / `failure_message` | 阻断或人工处理原因，使用 handoff 域受控原因码 |
| `next_attempt_at` | Beat 兜底扫描下一次可尝试时间 |
| `trace_id` | 跨粗分机、handling、分拣机的追踪 ID |

`SmtInboundHandoffSourceItem` 关键字段：

| 字段 | 说明 |
| --- | --- |
| `handoff_demand_id` | 所属 handoff demand |
| `item_key` | demand 内稳定幂等键 |
| `bin_code` / `bin_cell_index` / `bin_cell_code` | source 料箱和料格位置 |
| `material_identity_key` / `pkg_code` | source 物料身份 |
| `reel_thickness_mm` | 盘厚 evidence，用于后续分拣/容量判断 |
| `status` | source item 主状态 |
| `target_workline_id` / `target_workline_code` | 实际认领该 item 的分拣工作线 |
| `sorting_session_id` | 认领后关联的 `SMT_SORTING_INBOUND` session |
| `claim_attempt_no` | source pick request 代次；同一 item 人工释放后重试必须递增，避免复用旧死信 Inbox |
| `source_pick_inbox_id` | 创建的 `SORTING_SOURCE_PICK_REQUESTED` 内部 Inbox |
| `source_pick_command_id` / `source_pick_command_code` | 插件生成的首盘 `SORTING_SOURCE_PICK` command correlation evidence |
| `source_pick_dispatch_key` | 首盘 command 对应 Outbox / dispatch evidence，用于诊断设备派发等待 |
| `failure_code` / `failure_message` | item 级阻断或人工处理原因 |
| `claimed_at` / `completed_at` | 认领和完成时间 |

`SmtInboundHandoffSourceItem.status` 使用：

```text
READY
PICK_REQUESTED
PICKED
SORTING
SORTED
EXCHANGED
SKIPPED
MANUAL_HOLD
```

Handoff 受控原因码必须由单一 catalog 命名并导出，至少覆盖以下类别；具体枚举名在 execution plan 中落地：

- release fact 缺关键字段。
- release 快照无效或 usage 无法计算。
- WMS/RCS 拒绝、超时或回调 `rack_release_id` 不一致。
- 满箱交换缺 `post_exchange_relations`，需要对账。
- 后端无可用分拣路由、目标 WorkLine 不可用、source station busy。
- ECS realtime 准入非 `IDLE`。
- source item 并发 claim 冲突、内部 Inbox envelope 无效、插件事件创建失败或 command correlation 缺失。

`failure_code`、`failure_message`、`available_actions`、API filter 和测试断言必须引用同一 catalog，不允许在 service、API 和测试中散落自由字符串。

## 6. Handoff 状态机

`SmtInboundHandoffDemand.status` 使用以下状态：

```text
CREATED
EVALUATING
WAITING_FULL_BOX_EXCHANGE
RECONCILING
FULL_BOX_EXCHANGED
READY_FOR_SORTING
CLAIMED_BY_SORTING
SORTING_IN_PROGRESS
COMPLETED
MANUAL_HOLD
CANCELLED
```

状态含义：

- `CREATED`：release fact 已记录，尚未做业务决策。
- `EVALUATING`：正在判断满箱交换或分拣路径。
- `WAITING_FULL_BOX_EXCHANGE`：已发起 `SINGLE_LAYER_FULL_BOX_EXCHANGE`，等待 WMS/RCS 回调和对账。
- `RECONCILING`：物理动作已有结果，但业务完成所需证据不完整，需自动或人工补充对账事实。
- `FULL_BOX_EXCHANGED`：满箱交换业务完成，待派生剩余分拣物料。
- `READY_FOR_SORTING`：存在可被分拣机认领的 source item。
- `CLAIMED_BY_SORTING`：某个 source item 已被分拣 session 认领。
- `SORTING_IN_PROGRESS`：分拣机正在处理该 demand 的物料。
- `COMPLETED`：所有 source item 已交换、分拣完成或明确跳过。
- `MANUAL_HOLD`：缺少关键事实、回调不一致、外部失败或人工待处理。
- `CANCELLED`：业务明确取消，不再自动推进。

主状态流：

```text
CREATED
  -> EVALUATING
  -> WAITING_FULL_BOX_EXCHANGE
       -> RECONCILING
       -> FULL_BOX_EXCHANGED
  -> READY_FOR_SORTING
  -> CLAIMED_BY_SORTING
  -> SORTING_IN_PROGRESS
  -> COMPLETED

任一自动路径遇到不可自动恢复的事实缺口：
  -> MANUAL_HOLD
```

Demand 聚合状态不变量：

- `SmtInboundHandoffSourceItem` 行是逐项进度真源；`SmtInboundHandoffDemand.status` 是聚合摘要，不允许 API、Beat、前端各自从 raw JSON 重新推断。
- 所有 source item 推进、满箱交换回调、claim 后恢复、人工处置和重试释放完成后，必须经由 `SmtInboundHandoffService.recalculate_demand_status(...)` 或等价单一入口重算 demand 摘要状态和 item 计数。
- `READY_FOR_SORTING` 表示至少存在一条可 claim 的 `READY` item，且 demand 没有 demand-level hold、取消或未完成对账。
- `CLAIMED_BY_SORTING` 表示至少一条 item 已创建 `SORTING_SOURCE_PICK_REQUESTED` 或首盘 command，但尚未收到可证明源端取盘完成的 command result。
- `SORTING_IN_PROGRESS` 表示至少一条 item 已进入 `PICKED` 或 `SORTING`，即现有分拣插件已经打开或推进 `current_material`。
- `COMPLETED` 只能在所有 item 均进入 `SORTED`、`EXCHANGED` 或 `SKIPPED`，且没有未解决 `MANUAL_HOLD` / `RECONCILING` evidence 时写入。
- 任一 demand-level 不可自动恢复缺口，或任一 item 进入需要人工处置的 `MANUAL_HOLD`，demand 必须进入 `MANUAL_HOLD` 并暴露 item 计数和原因；恢复后再按 item 计数重新计算聚合状态。

Claim 后恢复语义：

- `SmtInboundHandoffSourceItem` 进入 `PICK_REQUESTED` 后，必须能通过 `source_pick_inbox_id` 追踪内部 Inbox 的处理状态。
- 内部 Inbox 仍可正常依赖 WorkLine Inbox 的 retry、stale PROCESSING 和 dead-letter 机制；handoff 账本必须同步感知最终失败。
- 插件事件处理成功但未生成 `SORTING_SOURCE_PICK` command 或未写回 command correlation 时，item 不得永久停留在 `PICK_REQUESTED`。
- 可自动恢复的资源暂忙回到可重试状态并写入 `next_attempt_at`；不可自动恢复的 payload、路由或插件合同错误进入 `MANUAL_HOLD`。

## 7. 释放事实入口

释放事实必须来自稳定业务事实，不来自前端按钮：

- 粗分机 active rack 已被移出或 rack operation 成功。
- 有稳定 `rack_release_id`。
- 有释放时的 `single_layer_rack_code` 和 `bin_snapshots_json`。
- 当前粗分机 session 不依赖旧 rack 恢复。

Release fact producer 合同：

- 唯一正常生产入口是后端确认 `MOVE_OUT_ACTIVE_RACK` 对应旧单层货架已经成功移出，并且 release evidence 已持久化后触发。
- producer 必须接在现有 WorkLine 应用层 release fact 链路上：`SingleLayerRackOrchestrationService` 产出的 `ROUGH_SORTER_RELEASE_FACT` / runtime resource fact 应用链完成后，调用 `SmtInboundHandoffService.create_or_get_from_release(...)`。
- `rough_sorter` 普通 session 状态、前端按钮、调试 API 和列表页操作都不能直接创建 handoff demand；它们最多提供 trace、诊断或非生产 mock evidence。
- 资源投影是 release 快照和 active rack evidence 的来源，但不得由资源查询结果在多个调用点反向猜测创建 demand；Beat 和 projection replay 只能重放同一 release fact 并返回同一 demand，不得成为第二生产入口。
- producer 传入 `create_or_get_from_release(...)` 的 payload 必须包含 `rack_release_id`、`single_layer_rack_code`、source workline、release reason、bin snapshots、trace/causation evidence；缺任一关键字段时创建可观测 `MANUAL_HOLD` demand，而不是静默丢弃 release fact。
- 同一 release fact 在 rack callback、resource projection replay 或 Beat 兜底中重复出现时，只能返回同一 demand，不得重复发起满箱交换或重复创建 source item。

入口服务：

```text
SmtInboundHandoffService.create_or_get_from_release(...)
```

幂等规则：

- 同一 `rack_release_id` 只能创建一条 handoff demand。
- 重复 release fact 返回已有 demand，不重复发起满箱交换或分拣。
- 缺少 `rack_release_id`、`single_layer_rack_code` 或可用快照时进入 `MANUAL_HOLD`。
- release fact 不携带也不决定目标分拣 WorkLine；目标 WorkLine 由后端 handoff 路由在 claim 阶段选择。

## 8. 满箱交换决策

`SmtInboundHandoffService.evaluate(...)` 只选择下一步业务动作：

| 条件 | 下一步 |
| --- | --- |
| `usage >= 0.8` 且 WMS/RCS 接受交换请求 | 发起 `SINGLE_LAYER_FULL_BOX_EXCHANGE` |
| `0.5 <= usage < 0.8` | 可发起优先交换请求；拒绝后转分拣 |
| `usage < 0.5` | 直接转分拣 |
| 快照不足或物料身份不完整 | `MANUAL_HOLD` |

`usage` 使用 `0..1` 规范值，不使用 `0..100` 百分数。解析、空值处理、字段兼容和阈值常量必须抽成共享 usage policy，供 handoff 和现有 SMT rack/bin 调度逻辑复用。

WES 不本地锁定五层空箱。WMS/RCS 授权通过请求接受、拒绝或回调表达。

满箱交换执行复用：

- `RuntimeIntent.rack_bin_exchange_request(...)`
- `HandlingOperationService.request_bin_operation(...)`
- `operation_type = SINGLE_LAYER_FULL_BOX_EXCHANGE`
- `callback_type = WMS_FULL_BOX_EXCHANGE_RESULT`
- `completion_policy = CALLBACK_PLUS_RECONCILIATION`

回调语义：

- `BUSINESS_COMPLETED`：交换完成，source item 按回调和 `post_exchange_relations` 更新为 `EXCHANGED` 或继续 `READY`。
- `PHYSICAL_COMPLETED` 但缺少 `post_exchange_relations`：demand 和 handling 均进入 `RECONCILING`，保持对账可见。
- `FAILED`、`REJECTED`、`TIMEOUT`：demand 进入 `MANUAL_HOLD`，允许人工重试或转分拣。
- `rack_release_id` 与 demand 不一致：demand 进入 `MANUAL_HOLD`，不得自动恢复。

## 9. 分拣机启动

分拣机 `START` 只表示 WorkLine READY，不启动业务。

分拣首盘启动必须满足：

- 存在 `READY_FOR_SORTING` demand。
- 目标分拣 WorkLine 为 READY。
- source station lease 可用。
- source rack/bin/cell 快照可执行。
- 当前分拣 session 没有未关闭的 `current_material`。
- 设备命令下发前仍由 ECS realtime `IDLE` probe 做准入。

首版不新增独立 `SmtSortingSourceDemandService`。source item claim 仍属于 handoff 聚合，由同一个服务在单一事务中更新 demand、item、session 和 inbox：

```text
SmtInboundHandoffService.claim_next_source_item(...)
```

目标分拣 WorkLine 来源：

- 路由分为“配置候选”和“运行态准入”两阶段，不能把临时 busy 误判为无路由。
- 配置候选来自显式 route config / runtime config，必须复用 WorkLine `plugin_key`、插件 manifest、required device roles、supported events/commands 和 single-layer boundary 信息，不从 release fact 推断目标线。
- 运行态准入再检查 `runtime_status`、station lease、当前 session `current_material` 和设备命令下发前 ECS realtime `IDLE`。
- 路由选择必须有稳定排序和冲突处理规则；多条配置候选同时可用时不能依赖数据库偶然返回顺序。
- 没有配置候选时进入 `MANUAL_HOLD`，原因码表达为路由缺失。
- 有配置候选但 WorkLine 未 READY、station lease busy、session `current_material` 未关闭或 ECS 非 `IDLE` 时，不转人工异常；保持可重试状态并写入 `next_attempt_at`。
- ECS realtime `IDLE` probe 是设备命令下发前的准入真源；WES 本地设备状态只能作为诊断和筛选 evidence。

推进方式：

1. 在数据库事务内按索引和行级锁从 `SmtInboundHandoffSourceItem` 选择一条 `READY` item。
2. 幂等创建或复用 `SMT_SORTING_INBOUND` session。
3. 用稳定 idempotency key 创建内部 Inbox 事件 `SORTING_SOURCE_PICK_REQUESTED`，并记录 `source_pick_inbox_id`。
4. `SMT_SORTING_INBOUND` 插件处理该事件，并发出 `SORTING_SOURCE_PICK` command。
5. 现有 `SORTING_SOURCE_PICK` 成功回调继续负责 `MATERIAL_UNMOUNTED` 和 `current_material`。
6. 后续 `WORKING_BIN_SCAN`、`SORTING_TARGET_PLACE`、NG 流程继续由现有插件处理。

内部 Inbox envelope 合同：

- `SORTING_SOURCE_PICK_REQUESTED` 必须作为 WorkLine runtime 可路由的插件事件入库；implementation plan 必须新增 `INTERNAL_EVENT` 或等价一等内部事件 kind/helper，补齐 Inbox enum/check constraint migration、session resolver、orchestrator 分发映射和 normalizer 合同测试。不得复用 `DEVICE_EVENT`、`EXTERNAL_HTTP` 或 `TIMER_TIMEOUT` 来伪装内部插件事件。
- Inbox `source_system` 必须表达系统内部来源；payload 必须包含 `message_type` / `event_type` / `canonical_event_type` 中的可路由事件名、`data`、`event_id`、`causation_id` 和 `trace_id`。
- Inbox 必须绑定目标 `workline_id` 和 `sorting_session_id`，并落入该 session 或 workline 的 `claim_bucket_key`，不得落入 `serial:unknown` 热队列。
- `event_id` / idempotency key 必须由 `handoff_source_item_id + claim_attempt_no` 或等价稳定代次生成；WorkLine Inbox 自动 retry 复用同一个 Inbox，人工释放 dead-letter 后重新发起必须递增代次并生成新的 Inbox。
- payload `data` 必须携带 `handoff_demand_id`、`handoff_source_item_id`、`claim_attempt_no`、source rack/bin/cell、material identity、pkg、trace evidence 和 route evidence；插件 handler 不得从 handoff 表外重新猜测 source item。
- 重复创建同一代次 source pick request 必须返回既有 Inbox 并校验其 source item、session 和 route evidence 一致；发现不一致时进入 `MANUAL_HOLD`。

Command correlation 合同：

- 插件 handler 只返回 `SORTING_SOURCE_PICK` command intent；runtime effect 创建 `DeviceCommand` 和设备派发 Outbox 后，必须立即把 `source_pick_command_id` / `source_pick_command_code` / `source_pick_dispatch_key` 或等价 command evidence 写回 source item；未写回视为 `SOURCE_PICK_COMMAND_NOT_CREATED`。
- command payload、command result 归属、`MATERIAL_UNMOUNTED` fact 和 `current_material` evidence 必须持续携带 `handoff_source_item_id` 或等价 correlation key。
- source item 从 `PICK_REQUESTED` 到 `PICKED` 的推进依据是已关联的 `SORTING_SOURCE_PICK` 成功结果；从 `PICKED` 到 `SORTING` / `SORTED` / `MANUAL_HOLD` 的推进依据是现有分拣插件后续事件与同一 correlation key。
- 设备 busy 属于 Outbox / dispatch 资源等待，handoff item 保持可诊断的等待 evidence；不得把设备 busy 转成新的 source pick Inbox 或重复创建 command。

严禁 service 直接创建设备 command 或直接修改分拣插件 context 来跳过插件。

## 10. 粗分机与分拣机解耦

粗分机当前 session 只关心当前料盘能否完成。

当粗分机 active rack 无可用料格时：

- 当前料盘触发 `MOVE_OUT_ACTIVE_RACK + ALLOCATE_AND_MOVE_RACK`。
- 旧 rack 释放事实创建 handoff demand。
- 当前粗分机 session 只等待新 rack 到位后继续当前料盘。
- 满箱交换结果不得恢复粗分机当前 session。

分拣机按 `READY_FOR_SORTING` demand 认领 source item，不直接读取粗分机当前 session 状态。

## 11. 后端推进模型

正常业务使用事件驱动，Beat 只做兜底：

- rack release fact 落地后即时创建或推进 handoff demand。
- WMS full exchange 回调后即时推进 handoff demand。
- `SORTING_SOURCE_PICK_REQUESTED` 处理、`SORTING_SOURCE_PICK` command 创建、command result 和后续 `current_material` 关闭事件必须即时回写 source item correlation/status。
- 分拣线 READY、station lease 释放或 source item 完成后即时尝试认领下一盘。
- Beat 定期扫描停留在 `CREATED`、`EVALUATING`、`FULL_BOX_EXCHANGED`、`READY_FOR_SORTING` 且 `next_attempt_at` 到期的 demand，补偿漏触发。
- Beat 或等价恢复入口必须覆盖 claim 后卡住的 source item：`PICK_REQUESTED`、`CLAIMED_BY_SORTING`、stale `source_pick_inbox_id`、Inbox `FAILED` / `DEAD_LETTER`、Inbox processed 但缺 command correlation、command result 已到但 item 未推进。
- Beat 使用队列式 claim：按状态、`next_attempt_at`、`updated_at`/`id` 稳定排序，批量上限固定，避免全表扫描和重复处理不可推进记录。
- `RECONCILING` 不由 Beat 盲目自动推进；只有收到新的对账事实或人工处置后才重新进入可推进状态。

新增兜底任务：

```text
scan_smt_inbound_handoff_demands_batch
```

不得恢复：

```text
scan_smt_full_box_exchange_candidates_batch
```

## 12. API 与前端能力

前端需要交互能力，但定位是监控、异常处置和联调。

必须提供查询：

- handoff demand 列表和详情，列表返回摘要，详情返回完整 source item 明细。
- rack release 快照。
- 满箱交换 decision 和 handling operation trace。
- source item 逐项状态、失败原因和认领 session。
- `source_pick_inbox_id` 对应 Inbox 状态、错误和 dead-letter evidence。
- `source_pick_command_id` / command code / dispatch key 对应 command、Outbox 和结果 evidence。
- 分拣 source demand 认领状态。
- failure / hold / reconciling 原因。
- `available_actions`，由后端根据状态机和原因码计算，不让前端解析 raw JSON 推断按钮。

列表页只返回低成本摘要：

- demand 主状态、decision、failure code。
- source item 状态计数。
- handling operation trace 摘要。
- claim 后卡住或 dead-letter 摘要。
- 可执行操作集合。

详情页可返回 release 快照、source item 明细、handling trace、source pick Inbox / command / Outbox correlation 和对账 evidence。

允许提供操作：

- 重新评估 handoff demand。
- 重试满箱交换 handling operation。
- 将 WMS 拒绝或失败的 demand 转为分拣。
- 释放 `MANUAL_HOLD`。
- 对 `RECONCILING` 补充人工对账结果。
- 非生产环境触发 mock 回调或兜底扫描。

禁止提供操作：

- 直接创建 `SORTING_SOURCE_PICK`。
- 直接修改资源投影。
- 绕过 WMS/RCS 授权发满箱交换。
- 直接编辑 outbox payload。
- 用前端按钮替代正常业务推进。

监控边界：

- 指标只使用低基数标签，例如 demand status、item status、failure_code、workline_code、operation_type。
- `demand_key`、`item_key`、`material_identity_key` 不作为 metric label；单条明细通过 detail API、timeline、trace_id 查询。

## 13. 验收标准

- 粗分机 release rack 后自动幂等创建 handoff demand。
- `usage < 0.5` 时跳过满箱交换，进入 `READY_FOR_SORTING`。
- `usage >= 0.8` 时发起 `SINGLE_LAYER_FULL_BOX_EXCHANGE` handling operation。
- `usage` policy 覆盖 `0`、`0.5`、`0.8`、`1`、缺失和非法值，且 handoff 与现有 SMT rack/bin 调度复用同一口径。
- 满箱交换 `BUSINESS_COMPLETED` 后 demand 进入 `READY_FOR_SORTING` 或 `COMPLETED`。
- 满箱交换 `PHYSICAL_COMPLETED` 缺 `post_exchange_relations` 时 demand 和 handling 均保持 `RECONCILING` 可见。
- WMS/RCS 回调 `rack_release_id` 不一致时进入 `MANUAL_HOLD`。
- 目标 WorkLine 路由测试覆盖路由缺失、多候选稳定排序、WorkLine 未 READY、station lease busy、ECS realtime 非 `IDLE`。
- 分拣线 READY 后通过 `SmtInboundHandoffService.claim_next_source_item(...)` 自动认领 source item，并通过插件事件下发首盘 `SORTING_SOURCE_PICK`。
- `SORTING_SOURCE_PICK_REQUESTED` 以明确的内部 Inbox envelope 入库，包含系统来源、可路由事件名、session/workline 绑定、claim bucket、幂等键和 handoff source item correlation。
- 内部 Inbox 使用一等内部事件 kind/helper，数据库 enum/check constraint migration 后可在 PostgreSQL 写入；不得伪装成设备事件或外部 HTTP 回调。
- Runtime orchestrator 必须把内部 Inbox kind 分发到插件事件 handler，input normalizer 必须保留 `SORTING_SOURCE_PICK_REQUESTED` canonical event，并有回归测试防止落回默认 `DEVICE_EVENT`。
- release fact 只能由 `MOVE_OUT_ACTIVE_RACK` 成功后的后端 producer 触发，重复 callback、projection replay 或兜底扫描不会创建第二条 demand。
- release producer 接在现有 WorkLine 应用层 `ROUGH_SORTER_RELEASE_FACT` / resource fact 应用链；Beat 只能重放同一 release fact。
- 路由测试区分“无配置候选”与“配置存在但 WorkLine/Station/ECS 暂不可用”，前者 `MANUAL_HOLD`，后者 retry + `next_attempt_at`。
- `SORTING_SOURCE_PICK_REQUESTED` 对应 Inbox 失败、dead-letter 或 stale 时，handoff item/demand 不会永久停留在 `PICK_REQUESTED` / `CLAIMED_BY_SORTING`。
- 插件生成 `SORTING_SOURCE_PICK` command 后，source item 写入 command correlation；command result 和 `current_material` evidence 能把 item 推进到 `PICKED`、`SORTING`、`SORTED` 或 `MANUAL_HOLD`。
- Demand 聚合状态由 source item 计数和 demand-level evidence 统一计算；API、Beat 和 `available_actions` 不各自解析 raw JSON。
- execution plan 明确 GitNexus impact 门禁，覆盖 `WorklineInbox`、session resolver、runtime effect、现有 WorkLine service/plugin 等共享 symbol；提交前仍需运行 detect changes。
- Docker/PostgreSQL 集成测试证明并发 claim 不会重复认领同一个 source item。
- Docker/PostgreSQL 集成测试证明 handoff demand 扫描、source item READY claim、内部 Inbox claim bucket、claim 后恢复使用索引友好访问路径；内部事件不得落入 `serial:unknown`。
- 分拣单盘后续流程仍由现有 `SMT_SORTING_INBOUND` 完成。
- 同一 release、同一回调、同一兜底扫描重复执行均幂等。
- 设备 busy 时只等待对应资源，不阻塞整条 WorkLine。
- `scan_smt_inbound_handoff_demands_batch` 使用索引友好的队列式 claim，具备 `next_attempt_at`、batch limit 和稳定排序。
- `SmtInboundHandoffReasonCode` 或等价 catalog 驱动 `failure_code`、`available_actions`、API filter 和测试断言。
- API list/detail/action 返回结构化合同；list 返回摘要，detail 返回 source item 明细和 claim 后恢复 evidence，前端不解析 raw JSON 推断业务状态。
- 插件 manifest 明确支持 `SORTING_SOURCE_PICK_REQUESTED` 事件。
- 旧 `smt_full_box_exchange` 插件和旧 candidate scan task 仍不可用。
- curl + MOCK 联调能观察完整链路：粗分机 release、可选 exchange、分拣 source pick。
- execution plan 明确 pytest 矩阵：单元、API、插件、Beat、PostgreSQL 并发、恢复、权限和旧入口不可用。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | 范围与策略 | 0 | 未运行 | 本次为后端 SPEC 工程修正，未触发 |
| Codex Review | `/codex review` | 独立二次意见 | 0 | 未运行 | 跳过 |
| Eng Review | `/plan-eng-review` + `$systematic-debugging` / `$investigate` | 架构与测试，必需 | 4 | CLEAR | 本轮实施前复审确认 SPEC/PLAN 对齐，并补齐 runtime orchestrator / normalizer 的 INTERNAL_EVENT 分发合同；0 个 open issue |
| Design Review | `/plan-design-review` | UI/UX 缺口 | 0 | 未运行 | 前端运营页面不在本轮后端 SPEC 范围内 |
| DX Review | `/plan-devex-review` | 开发者体验缺口 | 0 | 未运行 | 未运行 |

- **UNRESOLVED:** 0 个交互决策未决；0 个实施前阻断项。
- **VERDICT:** ENG CLEARED FOR IMPLEMENTATION；可按同步后的 execution plan 进入代码实施，实施阶段仍必须遵守 GitNexus impact analysis、TDD RED/GREEN 和提交前 detect changes 门禁。
