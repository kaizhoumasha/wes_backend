---
title: 回程段拆表：bin_line_returns（一次回程 execution，一张事实表）
status: Plan — 工程评审闭合，待实施
created_at: 2026-09-24
audience: WES 架构、人工拣料及后续使用同一回程通道的插件开发人员
scope: 只拆 SCAN3/SCAN4/RETURN_BUFFER 结构；不实现再次进入的触发规则
related:
  - docs/superpowers/specs/2026-09-22-bin-line-scan-retry-fix.md
  - docs/architecture/SRS.md
  - docs/contracts/wms-manual-outbound-picking-integration-requirements.md
  - docs/contracts/transport-fulfillment-contract.md
---

# 回程段拆表：`bin_line_returns`

> 本文是实施计划；代码和迁移尚未实施。WES 不引入全局 cycle/revision 领域概念。

## 1. 事实边界与范围

每条 WorkLine 有且只有一条独立回程通道：SCAN3 → SCAN4 → 串行滚筒位 → 唯一 OUTLET。正常运行无旁路和超车；滚筒位数量只是设备容量，WES 不建模 roller slot。该 WorkLine 的独立回程通道就是 FIFO scope。

- **Admission**：首次有效 SCAN4 到位 Evidence 表示料箱进入该通道的物理 FIFO；不等 SCAN4 放行命令成功才记录到位。
- **Order**：同一通道的 SCAN4 实际触发顺序。本轮已确认 Event `timestamp` 在实际扫码/触发时生成、同源可比较、原事件重发不变，且毫秒精度足以区分正常相邻过箱；取首次 Evidence 的原始 Unix 毫秒值为 `scan4_event_time`。`received_at` 留在 Evidence 中作技术审计，不用于 FIFO 排序。
- **Blocking**：已到位但尚无权威退出事实的队首始终阻挡后项；`MOVE_PENDING`、`READY`、`RETURN_REQUESTED` 只决定当前可否执行，不能改变物理顺序。`RETURN_REQUESTED` 不是出队。
- **Exit**：料箱从冻结的 OUTLET 来源被实际取走时结束 Return execution，并由 Transport execution 负责 OUTLET→Rack Slot。当前合同的逐箱 `SOURCE_PICKED` 表示来源已取出；只有它已应用、匹配原 `BIN_MOVE` 的冻结成员且该成员来源确为本线 OUTLET 时，才可据此关闭 Return。`TARGET_PLACED` 与 Transport `SUCCEEDED` 只关闭 Transport，不作为 Return 的正常关闭条件。人工取走等异常退出也须有明确的权威 Evidence。

正常路径上，前箱停在 SCAN4 时后箱不能进入并触发 SCAN4；前箱 Event 已耐久接收并形成放行命令后才能离开。因此不要求 ECS 为 FIFO 另设“后一 Event 等前一 Event 的 WES durable ACK”合同，不增加全局或 scoped sequence。接入验收须按本轮已确认的 Event 时间语义、重发不变性和实际分辨率验证供应商实现；同时间值只用稳定第二排序键保证查询确定性，不宣称恢复同毫秒物理先后。人工干预破坏正常拓扑时按异常事实对账。

本次只拆结构并把 FIFO key 从 WES 接收时间改为经确认的设备事件时间；不实现“旧 execution 退出后再次进入”的新触发逻辑，不改变 SCAN1/SCAN2/WMS 准入或物理放行规则。`bin_line_returns` 是唯一持久化回程事实；不建独立 queue 表、缓存或第二份业务队列真相。

**SCAN3 实际到位是执行段结束事实。** `ManualPickingPassage` 负责 INLET→SCAN3，匹配的首次 SCAN3 到位 Evidence 应用时同事务置为 `CLOSED`；不等待 SCAN3 方向 Command 成功。对已识别的正常料箱，同一事务创建 `BinLineReturn(NONE)` 接管 SCAN3→OUTLET；SCAN3 `MOVE_FORWARD` 是 Return 的 Action，命令可以因点2结果未决而稍后创建。已明确走 `MOVE_LEFT` 的 NG 分支不进入 SCAN4 回程通道，仍由原 Evidence/DeviceCommand 跟踪，不创建 Return。`Passage CLOSED + BinLineReturn OPEN + Transport EXECUTING` 是合法组合；任一段的结束不等待下一段完成。跨段 ownership handoff 原子提交，CLOSED Passage 不参与后续运行判断。

## 2. 最小数据结构

`wes_biz.bin_line_returns` 表示已识别正常料箱从 SCAN3 到位至 OUTLET 物理移出的一次回程 execution。记录 ID 就是 execution identity，同一料箱未来再次进入时创建新 ID，不使用 `cycle_no`、revision 或 rollover 字段。模型暂放 `workline_plugins/manual-picking/src/manual_picking/application/bin_line/`，表名和字段不绑定人工拣料插件；本次不抽独立包。`NONE` 表示 Return 已接管但尚无首次 SCAN4 到位事实，**不在 FIFO 内**；不增加交接中间状态。

`id` 是 Return execution identity；`workline_id + bin_code` 是物理对象作用域。其余核心字段按语义冻结如下：

| 类别 | 字段 | 语义与变更规则 |
| --- | --- | --- |
| A. 不可变生命周期/因果事实 | `scan3_evidence_id`（Evidence FK，唯一） | 首次 SCAN3 到位，即 Return BEGIN fact；创建时冻结，真实 SCAN3 重扫也不更新 |
| A. 不可变生命周期/因果事实 | `scan4_evidence_id`（Evidence FK，唯一） | 首次有效 SCAN4 到位，即 FIFO ADMISSION fact；后续 SCAN4 重扫不更新 |
| A. 不可变生命周期/因果事实 | `scan4_event_time`（bigint Unix 毫秒，可空） | 上述首次 SCAN4 Evidence 的扫码发生时间，即 FIFO ORDER fact；与 `scan4_evidence_id` 同事务冻结，重扫不更新 |
| A. 不可变生命周期/因果事实 | `return_batch_evidence_id`（Evidence FK，可空） | 导致本 Return 从可退状态进入 `RETURN_REQUESTED` 的原始 `return_batch READY` Evidence；与状态同事务冻结，写入后不可由其他批次覆盖；多个 Return 可共用 |
| B. 当前 Action Pointer | `scan3_command_code`、`scan4_command_code`（string，可空） | 当前 SCAN3/SCAN4 Command；符合重扫条件的新真实 Evidence 创建新 Command 后，可更新当前指针，旧 Command 留在 DeviceCommand 历史 |
| C. 当前状态 | `return_state`（enum/string） | `NONE / MOVE_PENDING / READY / RETURN_REQUESTED / EXITED / VOIDED`；`EXITED` 替代旧 `RETURNED`，不增加状态数 |

候选核心字段仅为 `id`、`workline_id`、`bin_code` 和表中七项；不加入 `retry_count`、`scan3_route`、`return_transport_task_id`、`exit_evidence_id` 或 cycle/revision。状态表示当前阶段，A 类事实表示已发生的生命周期与直接因果，B 类指针表示当前尝试；不能用当前指针覆写首次物理事实。

此表是当前运行所需的最小字段集。诊断累计不进入 Return；真实扫码和新 Command 的历史由 Evidence/DeviceCommand 保留，`DeviceCommand.attempt_count` 只表示同一 Command 的技术派发尝试，不表示物理重扫。

`received_at` 仍由 `InboundEvidence` 持有；诊断可计算 `received_at - scan4_event_time`，不在 return 表复制接收时间。首次有效 SCAN4 Evidence 及其事件时间一经冻结，重报、命令重试和再次处理原 Evidence 都不改写。

SCAN3/SCAN4 DeviceCommand 沿用当前 Evidence-based `execution_ref_id = manual-picking:<evidence_id>:<role>` 与现有 Command 唯一约束；不增加 `return:<id>:...` 协议或 DeviceCommand `return_id`。同一 Evidence 重领命中原 Command，新的真实扫码才有新的 Evidence 和 Command。先前不加 `scan3_evidence_id` 的理由依赖“Command 与 Return 同时创建”；现在 SCAN3 到位先于可能等待的 Command，这个前提已失效。新增该字段只为当前未闭合 Return 关联并恢复尚未形成 Command 的到位事实，不能靠 CLOSED Passage 或猜最新 Evidence 替代。Return 仍不复制 `scan3_route`；`task_id`、`source_plugin_key`、`source_ref` 也不进入 Return。

`return_batch_evidence_id` 的三问：必须加，因为 `RETURN_REQUESTED` 只说明当前状态，不指出是哪次 `return_batch` READY 使它进入该状态；现有候选只含 `bin_code / sequence_no / source`，同箱旧批次也可能留下已应用 `SOURCE_PICKED`。收益是在状态转移事务中冻结当前 execution → 原 WMS 响应 Evidence 的唯一因果边，恢复时不从历史批次按 bin 或时间猜测。不能不加：现有 Binding 从 Evidence 向 Transport 的后续关联完备，却没有从当前 Return 反向到该 Evidence 的字段或唯一约束；FIFO 当前队首身份不等于历史 Action identity。字段不复制物理事实，且不是第二张队列表；同一批次多个 Return 共用该 FK，不加唯一索引或无依据的额外业务状态。新版本中 `RETURN_REQUESTED` 必须有此值，退出后保留以供因果核对。

不增加 `return_transport_task_id`：`Return.return_batch_evidence_id → TransportDecisionBinding(source_evidence_id, step, workline_id, correlation_id) → TransportTask(client_request_id)` 已能精确定位原 Transport，复制 Task ID 不再带来额外正确性。`SOURCE_PICKED` 仍以原 `transport_task_id + container_id` 关联成员；核对该 Task、成员 bin、冻结本线 OUTLET 和原批次后才可关闭 Return，不接受同箱其他任务的位置事实。

持久化直接因果，不复制传递因果：若已可靠保存 `B → C`，则 `A` 只保存必要的 `A → B`，不再复制 `C` 的 identity。此处 Return 保存原 return_batch Evidence，后续 Transport identity 从现有 Binding 求得。

同一 Evidence 重领、消息重放及原命令技术重试始终复用已冻结的 Action Ref、DeviceCommand 身份和请求，不再生成新动作或刷新 deadline；设备绑定随后变化也不得改绑该命令。SCAN3 新真实扫码只在当前 Command 明确失败或确定超时且既有重扫规则允许时创建新 Command，并在同一事务更新当前 `scan3_command_code`；Return ID 与首次 `scan3_evidence_id` 不变，旧 Command 保留历史。例如 `E1 → C1 FAILED`、`E2 → C2` 后仍是 `scan3_evidence_id=E1`、`scan3_command_code=C2`。SCAN4 同理，`E3(timestamp=T1) → C3 FAILED`、`E4(timestamp=T2) → C4` 后仍是 `scan4_evidence_id=E3`、`scan4_event_time=T1`、`scan4_command_code=C4`。当前 Command 未终态则等待，已成功则不重复创建。无需 attempt/cycle/revision 模型。

约束与索引：

- `(workline_id, bin_code) WHERE return_state NOT IN ('EXITED', 'VOIDED')` 局部唯一：同一物理对象在明确业务作用域内最多一条未闭合 execution；同一 WorkLine 可同时有多个不同 bin。
- `scan3_evidence_id`、`scan4_evidence_id` 分别唯一；`scan4_evidence_id` 与 `scan4_event_time` 由同一次首次到位处理一起写入或保持同为空。非空的 `scan3_command_code` 指向真实存在且本线、本箱当前正常放行尝试的 Command；结果处理仍核对 `workline_id`、设备角色、Command 类型和当前指针。
- FIFO 局部索引 `(workline_id, scan4_event_time, scan4_evidence_id) WHERE scan4_evidence_id IS NOT NULL AND return_state NOT IN ('EXITED','VOIDED')`。第二排序键仅保证同时间值查询确定；不表示物理先后。
- `scan4_command_code` 标识当前 SCAN4 动作；首次 `scan4_evidence_id` 冻结后，真实重扫可更新当前 Command，但不得改写首次到位 Evidence 或排序时间。旧尝试保留在 DeviceCommand/Evidence 历史中，不得推进当前 Return 或影响新 execution。
- `return_batch_evidence_id` 在 `READY → RETURN_REQUESTED` 与原 `BIN_MOVE` 创建的同一事务设置，之后不改写；运行中 `RETURN_REQUESTED` 必须有此值。这是事务与应用不变量，暂不为该字段新增数据库 CHECK 或 UNIQUE。它指向固定 operation 的 READY 响应 Evidence，后续只沿该 Evidence 的冻结 operation identity 查原 Binding，不用 `latest/newest/max(created_at)`。
- `NO_BATCH` 不改变 Return 状态或该字段；`READY` 仅为 WMS 实际选中的连续前缀同时设置字段并进入 `RETURN_REQUESTED`，未选中项仍为 `READY` 且字段为空。`EXITED` 或有退出事实的 `VOIDED` 是终态，保留已冻结因果字段；同箱再次进入只能创建新 ID。

当前运行查询只看未闭合 Return，且未到 SCAN4 的 `NONE` 不进入 FIFO 查询；已 `EXITED` 的历史不参与后续准入、FIFO、工作量或恢复判断。`VOIDED` 与 `EXITED` 均须由权威退出 Evidence 支撑；原 Transport/Evidence 已保存退出证据链，Return 不复制 `exit_evidence_id`。切换前不能为了制造空态而改写旧记录的物理终态。

## 3. 状态与 FIFO 操作

```text
已识别正常箱首次 SCAN3 到位 → 同事务冻结 Return.scan3_evidence_id、创建 Return（NONE）并 CLOSED Passage
SCAN3 前置结果确定后 → 在原 Return 创建 MOVE_FORWARD Command、更新 scan3_command_code
SCAN3 MOVE_LEFT 判定 + Command 创建 → 同事务 CLOSED Passage；不创建 Return
SCAN3 当前 Command 明确失败后真实重扫 → 原 Return 保持 NONE，仅更新 scan3_command_code；scan3_evidence_id 保持首次值
首次有效 SCAN4              → 冻结 scan4_evidence_id + scan4_event_time；MOVE_PENDING，进入物理 FIFO
SCAN4 当前 Command 明确失败后真实重扫 → 仅更新 scan4_command_code；首次 SCAN4 Evidence 与事件时间不变
匹配的 SCAN4 MOVE_FORWARD SUCCESS → READY
WES 将 RETURN_BUFFER 连续 READY 前缀提交 return_batch，请 WMS 分配回架目标；READY 后同事务创建原 BIN_MOVE、冻结所选 Return.return_batch_evidence_id → RETURN_REQUESTED
原 BIN_MOVE 逐箱 SOURCE_PICKED 已应用，冻结来源为本线 OUTLET → 对应 Return EXITED
匹配的 BIN_MOVE 全部成员 TARGET_PLACED 并 SUCCEEDED → 仅 Transport 完成
其他权威异常物理退出 Evidence → VOIDED（本次不实现新触发）
```

`MOVE_LEFT` 的 DeviceCommand Result 无论成功或失败都由原 Command 留存；插件消费它时只核对原 Command 并确认已处理，不再改写 CLOSED Passage。`RETURN_REQUESTED` 队首在 OUTLET 取走事实提交前仍阻塞后项；对应 `SOURCE_PICKED` 已应用后即使 Transport 尚在回架途中，也不再占用 Return FIFO。不得用父任务完成、ACK、超时、人工备注或下次扫码释放队首。若同一 `(workline_id, bin_code)` 仍有未闭合 execution，新建请求失败关闭；下一次进入的业务触发另案设计。

物理资格与调度资格分别判断：`Dispatchable = Physical Eligible AND Scheduling Eligible`。`SOURCE_PICKED` 使前箱 `EXITED`，后箱可成为新的物理 FIFO 队首；现有同货架面 CTU 批次门禁等条件仍可能暂缓下一次 `return_batch`。这属于调度受阻，不把已退出箱重新算作 FIFO 队首，也不新增 `WAIT_CTU / WAIT_BATCH` 等状态。状态只保存已成立的业务事实，当前能否行动由条件判断。

事实持久化负责可靠性，wakeup 负责及时性，driver/reconcile 负责从持久事实恢复推进；wakeup 不是业务事实。先投影已经发生的事实，再判断是否允许创建下一动作；调度门禁不得阻止事实状态收敛。driver 在同一 WorkLine authority lock 下依次：核对当前未退出 execution 所需的已应用持久事实 → 将匹配 `SOURCE_PICKED` 的 Return 置为 `EXITED` → 重算物理 FIFO 前缀 → 判断 CTU/货架面等调度条件 → 必要时创建下一 Action。事实投影与调度决策分层；任何调度条件为 false 时，也必须先完成已成立的 `EXITED` 投影。

运行恢复必须沿已持久化的因果关系恢复 execution，不得通过同一物理对象的 latest/history 记录推断当前业务因果。Current State 说明 execution 现在处于什么阶段；Causal Identity 说明哪个 Action 导致该状态。恢复后续异步事实时两者不能混为一谈。

`unfinished_prefix_for_update(workline_id, limit=4)` 先按 `(scan4_event_time, scan4_evidence_id)` 查询并锁定**全部**已到位且未退出的前四条。`ready_prefix_for_update` 只在内存中从该结果的队首连续取 `READY`；遇 `MOVE_PENDING` 或 `RETURN_REQUESTED` 即停止。不得在 SQL 中先筛 `READY`。已应用的取走 Evidence 须经现有 TransportTask、TransportDecisionBinding、原 return_batch Evidence 与 TransportMember 核对本次请求、bin 和冻结 OUTLET 来源，再关闭对应未闭合 execution；不等待同批其他成员到达目标。

SCAN3 handoff 的锁顺序为 WorkLine authority → 当前 Passage → 当前 Return（重扫时）→ Command 创建/核对；正常箱的 SCAN3 到位事实、Return 插入和 Passage `CLOSED` 同事务提交，Command 若须等待前置结果则留给原 Evidence 的可靠重放。SCAN4 到位、Command Result、批次冻结和已应用 `SOURCE_PICKED` 的 Return 投影均先取得同一 WorkLine authority 锁，再锁相关 Return 行；行级 `FOR UPDATE` 锁住所读前缀，不用 `SKIP LOCKED` 跳过队首。数据库局部唯一约束兜住同 bin 并发创建。查询锁仅覆盖已存在的行，不能代替 WorkLine 根锁处理新到位记录与批次冻结的并发；独立 WorkLine 不互相串行。实施前核对现有 DeviceCommand/Evidence/Transport 回调的完整锁顺序，不额外升级根锁强度。

## 4. 代码迁移面

- `passage_model.py` 保留 SCAN1/SCAN2、WMS 准入、`disposition` 和作为前段结束事实的 `scan3_evidence_id`；删除 `scan3_command_code`、`scan3_route`、`scan4_evidence_id`、`scan4_received_at`、`scan4_command_code`、`return_state` 及对应回程约束/FIFO 索引。删除未确认需求下的混合重扫计数及阈值告警，不把诊断聚合搬到 Return，也不影响真实重扫准入和原 Command 幂等。
- 新增一个 `BinLineReturn` 模型和 Repository；`PassageRepository` 的 SCAN2 在途判断仍用未 CLOSED Passage，SCAN1 当前同箱检查、WorkLine 未完成业务量及前段/回程门禁需分别检查当前未闭合 Passage 与 Return。`NONE` 计入当前回程工作量，但不进入 SCAN4 FIFO。已 CLOSED Passage 历史不参与扫码、重试、恢复、清场或批次判断。
- `scan_flow.py` 在正常箱首次 SCAN3 到位时交接；若点2结果仍未决，Return 以 `scan3_evidence_id` 保留当前事实，原 Evidence 重放等待结果后创建 Command。新 SCAN3 重扫先查当前未闭合 Return 的 `scan3_command_code`，同 Evidence 重领沿现有 Command identity 幂等处理。SCAN4 只查当前 Return，首次到位先冻结事实，再核对当前 SCAN3 Command 的 `MOVE_FORWARD SUCCESS`；前置结果未到时延迟 SCAN4 Action。Result 按 `command_code` 找原 DeviceCommand，只有它仍是 Return 的当前 SCAN3/SCAN4 Command 且匹配本线、设备、动作时才推进；SCAN3 `MOVE_LEFT` Result 不再关闭或修改 Passage。`SOURCE_PICKED` 已应用后由回程 owner 关闭原 Return；Transport 最终结果仅推进 Transport，不反向改写已退出 Return。
- `batch_flow.py`、`batch_result.py`、`batch_driver.py`、`drain_flow.py` 及直接测试消费者改用同一 return Repository 的未退出前缀；`batch_result.py` 将所选 Return 的 `return_batch_evidence_id` 与 `RETURN_REQUESTED`、原 Transport 创建同事务提交，不引入 queue Repository。
- 现有 `archive-open-work` 可以归档前段 Passage，但 `archived_at` 不构成 Return 的物理退出事实。归档不得关闭未退出 Return；其工作量与停用/切换门禁继续按未闭合 Return 判断，接口成功不表示回程通道已清空。
- `EXITED` 只在经原 return_batch 关联确认的 `BIN_MOVE` 成员 `SOURCE_PICKED` 已应用且冻结来源为本线 OUTLET 后写入。Transport Evidence 已保存原事实，不复制 `exit_evidence_id`；不要把请求创建、`ACCEPTED` 或最终目标到位当作 Return 正常退出触发。Transport 的 Evidence 接收 ACK 不是已应用事实。正常主路径复用现有 `progress_wakeup → activate_picking_task_plans_batch → picking_task_batch_driver`：Transport Service 成功应用本线权威位置 Evidence 后，在同一事务用现有 `defer_wakeup` 登记无 payload 唤醒；只有提交成功才发布。当前 `progress_hook` 注入点虽属 Transport Service，其实现 `RackInboundWindowService.on_transport_progress` 专管货架进场窗口，不能在里面加入 Return 解释或回箱特例。现有位置事实应用后尚无通用 WorkLine 唤醒条件；在 Transport Service 中对已应用且有 `authority_workline_id` 的位置 Evidence 直接复用已有 `progress_wakeup`，无需新增 hook。现有队列唤醒不带 WorkLine ID，而是令激活任务扫描 active WorkLine；它只表示可能有新持久事实，不携带 `SOURCE_PICKED`、bin 或 task 业务数据，driver 必须重新读库。
- 现有计划激活服务先取得 WorkLine authority lock，再调用插件 driver；Return 事实收敛须置于该 driver 的最前面，早于已完成任务推进、当前任务状态检查及所有 CTU/容量门禁。只从 active WorkLine 的当前未退出 `RETURN_REQUESTED` 行出发，定位**属于该 execution 的**原 return_batch binding、TransportTask/member 和原始已应用 `TransportEvidence`，核对 `SOURCE_PICKED`、bin 和冻结本线 OUTLET 后幂等置 `EXITED`；不全表扫描 Transport Evidence，也不读已闭合 Passage/Return 历史作运行依据。随后重新计算未退出 FIFO 前缀和调度条件，在同一事务内按现有幂等身份创建可执行的下一 Action。现有每 10 秒计划激活任务执行同一 driver，只为唤醒丢失、worker 崩溃和重启兜底；不新增专用 worker、队列、Evidence 类型或投影表。

**D6 结论**：C 复用现有提交后唤醒、计划激活 worker 和周期恢复扫描，但当前尚未端到端接通。`progress_hook` 不是已有通用物理事实路由，它当前只调用货架进场窗口；位置事实应用后没有独立的通用 WorkLine wakeup，插件 driver 也尚未读取 `SOURCE_PICKED`。实施须补齐上述通用唤醒条件和 driver 的事实优先顺序，不能把诊断 SSE、Transport 最终结果或周期任务的存在本身当作已实现的 Return 推进证明。

**D7 因果链核查**：当前 `apply_return_in_session` 在同一事务创建 Transport 并将 Passage 置为 `RETURN_REQUESTED`，但 Passage 未保存批次身份；现有最终结果处理只按当前 FIFO 前缀与 `bin_code` 匹配。冻结候选中的箱号、批内序号和来源均不是 Return execution ID，不能从 `R(state=RETURN_REQUESTED)` 唯一反查哪个旧/新批次导致它进入该状态。因此只补 `return_batch_evidence_id` 这一条缺失的边，不加跨层 Transport ID。

| 从 `R` 出发的箭头 | 现有/新增确定 key 与约束 | 历史扫描或猜测 |
| --- | --- | --- |
| `R → return_batch Evidence` | 新增 `R.return_batch_evidence_id` FK，在 `READY → RETURN_REQUESTED` 事务中指向原 READY 响应 `InboundEvidence.id`；核对 `kind=WMS_RESULT`、`operation=outbound.bin.return_batch@v1`、结果 READY，以及唯一 `(operation, operation_id)` 的 WmsConfirmation 指向此 `response_evidence_id`。Evidence ID 唯一，一批多箱允许共用 | 直接主键读取；不用同箱历史或最新批次 |
| `Evidence → Binding` | Evidence 的 `operation_id` 对应 Binding `(workline_id, correlation_id=operation_id, step=MANUAL_PICKING_RETURN_BATCH)` 唯一约束，并核对 `source_evidence_id=Evidence.id`；Evidence 的 `(operation, operation_id)` 也唯一 | 按冻结身份查唯一行；不扫描历史 |
| `Binding → TransportTask` | `client_request_id` 在 Binding 与 TransportTask 各自唯一；创建发生在上一箭头状态转移的同一事务 | 按唯一 key 读取 |
| `TransportTask → TransportMember` | `(transport_task_id, object_id=R.bin_code)` 唯一，另核对 `object_type=BIN`、请求成员与冻结 source 为本线 OUTLET | 按任务与成员 key 读取 |
| `member → SOURCE_PICKED` | 原 Task 的 `TransportEvidence.transport_task_id`，operation 为位置变更、`payload.container_id=member.object_id`、`payload.milestone=SOURCE_PICKED` 且 `status=APPLIED`；Evidence `(operation, operation_id)` 唯一保证同身份重放幂等。不同身份的多个有效取走报告不影响“存在至少一个权威事实”的判断 | 只查该 Task 的已应用位置事实；不从全线或同箱旧任务找最新 |

任一箭头缺失、身份漂移或返回多个互相矛盾的候选时失败关闭，不把旧 Transport 的事实作用到当前 Return。clean cutover 后，这条链只从新版本创建的未闭合 Return 出发；旧批次历史不作为当前 Return 的身份来源。

**D5 结论及合同**：已查的 ECS 设备 Event 合同只有扫码类到位事实，没有逐 bin 的 OUTLET clear/released/taken/leave 事件；OUTLET 是 WorkLine 位置绑定，设备 Status 只是诊断快照，均不能证明某箱实际移出。因此复用现有 Transport 逐箱 `SOURCE_PICKED`，不增加 `OUTLET_EXIT` Event 类型。用户已确认此处所指为 `transport.task.member_position_changed@v1` 的逐箱 `SOURCE_PICKED` 回调，而非 `transport.task.resulted@v1` 最终 `SUCCEEDED`；Transport 合同已增加本线 OUTLET 回程 `BIN_MOVE` 的限定必报规则。每个 bin 实际取走时按原任务/成员身份可靠上报，WES 应用该 Evidence 后关闭对应 Return；技术重试保持原事件身份与完整消息，不伪造缺失里程碑。接入验收仍须验证供应商按此合同实现。

**实施 Gate 状态**：① 用户确认 OUTLET 回程采用逐箱 `SOURCE_PICKED` 位置回调，限定必报规则已写入 Transport 合同；② 用户确认 ECS 在 SCAN4 实际触发时生成 Event `timestamp`、原事件重发保持不变、毫秒精度足以区分正常相邻过箱；③ SRS 与人工拣料合同已按 Passage 到 SCAN3、Return 到 OUTLET 物理退出同步。三个设计/合同 Gate 已关闭；实际供应商行为仍由接入验收验证，不能以文档代替运行证据。

## 5. 清线切换与验证

**D8 已选 B：drain 后 clean cutover。** 现场发布流程允许停新箱入线并清线；本次不在线迁移 active execution，也不把已物理闭合的旧 Passage backfill 成 `BinLineReturn`。Schema migration 只建立新表、约束和索引，并按新代码需要调整 Passage 结构；新 Return 表从空运行态开始。旧 Passage 作为旧历史保留或按既有归档/保留规则处理，不进入新 Return 的运行查询。若删除旧回程列会影响历史保留，先按既有数据保留规则处理原始记录；不为保留历史而构建新领域 execution。

切换顺序：停止新业务和新箱进入目标 WorkLine，保持旧版本处理已有扫码、Command、WMS 结果和 Transport；等待执行段及 SCAN3→OUTLET 回程段中的箱取得各自权威完成/退出事实，确认 SCAN4 后 FIFO 为空。原版本的返回动作和未决结果仍沿原身份可靠收敛；有关 Command、Transport、待应用 Evidence 等旧义务须完成或按既有对账能力确定闭合，不能把未知结果当成功。发布检查同时覆盖 SCAN3 前的未闭合 Passage、SCAN3 后尚未到 SCAN4 的箱、已到 SCAN4 但尚未物理退出的箱；只查 FIFO 空不足以证明回程段空。现场人员按物理清线 SOP 最终确认通道与工作位为空，再停用/维护、执行 migration 并启动新版本。

`archive_open_work`/`archived_at`、设备 `IDLE`、无活动命令或数据库投影为空都不等于物理清线；已归档但仍有未澄清物理占用或原执行义务的旧记录也不能被忽略。不得批量 `VOIDED`、逻辑 CLOSED 或伪造 `SOURCE_PICKED` 以通过切换门禁。任一 WorkLine 无法证明已清线和旧义务收敛，就暂不切换该 WorkLine；保持原执行身份与旧版本处理/对账，不猜首次 SCAN3、SCAN4 或 return_batch 因果。B 的发布期确认不增加新的持久化清线模型、Evidence 或历史 backfill 脚本。

新版本只对其创建的 Return 冻结首次 SCAN3 BEGIN、首次 SCAN4 admission/order、当前 Command pointer 和 return_batch 直接因果。迁移验证在独占临时 PostgreSQL 中执行新鲜 schema/migration 链，并确认新表初始为空、局部唯一约束与 FIFO 索引有效；不以历史重建数量作为通过条件。

聚焦验收：

1. 已识别正常箱首次 SCAN3 到位时 Passage `CLOSED`、Return `NONE` 和 `scan3_evidence_id` 原子提交；点2结果未决时 `scan3_command_code` 可空，后续以原 Evidence 恢复并创建 Command。NG Passage 结束但不生成 Return，后续 Result 不修改 Passage。
2. SCAN3 当前 Command 未终态时重扫等待；明确失败后的新扫码更新同一 Return 的当前 Command；同 Evidence 重领不新建 Command，旧结果不推进当前 Return。
   `E1 → C1 FAILED` 后 `E2 → C2` 必须保持 `scan3_evidence_id=E1`、`scan3_command_code=C2`。
3. SCAN4 在 SCAN3 Result 未到时先冻结首次到位 Evidence 和 `scan4_event_time`，保持 `MOVE_PENDING` 并等待；`NONE` 不进入 FIFO。A 的 Event 先持久化、ACK 丢失、重试后仍保持同一个 Evidence 和排序时间；B 后到但 HTTP 顺序不决定 FIFO。
   `E3(timestamp=T1) → C3 FAILED` 后 `E4(timestamp=T2) → C4` 必须保持 `scan4_evidence_id=E3`、`scan4_event_time=T1`、`scan4_command_code=C4`，FIFO 顺序不变。
4. 前缀分别为 `MOVE_PENDING → READY`、`RETURN_REQUESTED → READY` 时，后项不能进入新 `return_batch`；队首匹配 `SOURCE_PICKED` 已应用后，下一次 driver 唤醒即将它置为 `EXITED`，后项成为物理可选前缀，不等待回架目标到位。若同货架面 CTU 批次门禁仍未释放，后项暂不调度新 `return_batch`，但不得重新视为物理 FIFO 被前箱阻塞。
5. 同一 bin 的未闭合第二条被数据库拒绝；有权威退出依据关闭后可创建新 ID，但再次进入的业务触发仍另案设计。旧 `SOURCE_PICKED` 的同身份重试只命中原 TransportEvidence，不产生第二次业务应用。
6. 回程 `BIN_MOVE` 的 `ACCEPTED`、`UNKNOWN`、只有 `TARGET_PLACED`/`SUCCEEDED` 且缺少取走事实时均不作为 Return 正常关闭触发；单个成员的匹配 `SOURCE_PICKED` 已应用且冻结来源为 OUTLET 时仅关闭对应 Return，其余成员保持原状态；Transport 可继续执行。错误任务、错误 bin、错误来源或非本次 return_batch 的位置事实不得关闭当前 Return。
7. 清理可清理的已闭合 Passage/Return 历史后，当前扫码、重试、恢复、工作量及 FIFO 仍只依赖当前未闭合事实、原 Command/Evidence；NG Command Result 不修改已闭合 Passage。
8. 检查 FIFO 查询计划走局部索引；同时间值的第二排序键只提供确定性，不作为物理顺序验收证据。
9. `archive-open-work` 归档前段 Passage 后，未取得权威退出事实的 Return 仍保持未闭合，并继续计入工作量和停用/切换门禁。
10. `SOURCE_PICKED` durable commit 后的提交后唤醒是正常主路径；driver 重读原事实并在 CTU/容量门禁之前置 Return `EXITED`。提交前失败不发唤醒；已提交但唤醒丢失、投影前崩溃或 worker 重启时，现有周期计划激活扫描仅从 active WorkLine 的当前未退出 Return 出发恢复。重复唤醒或同 Evidence 重试不重复推进，其他位置事实也不伪造退出。
11. Return `EXITED` 已提交但下一可执行 Action 尚未建立时，同一 driver 重跑从当前未退出前缀及可靠批次/Command/Transport 身份继续推进；若投影与创建 Action 处于同一事务，崩溃时两者一起提交或一起回滚。调度门禁暂时不满足时，Return 仍保持 `EXITED`，随后依据新事实或周期恢复重算，不依赖已闭合 Return 历史。
12. 同一 WorkLine/bin 曾有已完成旧 return_batch、当前又有新版本创建的 `RETURN_REQUESTED` execution 时，`return_batch_evidence_id` 只指向当前批次，旧批次的 `SOURCE_PICKED` 不得关闭当前 Return；字段缺失时失败关闭，不选“最新”或任意匹配行。
13. 一个 READY 批次的多个 Return 同事务共享同一 `return_batch_evidence_id` 并进入 `RETURN_REQUESTED`；事务失败时两者都不提交。切换验收覆盖旧执行段、尚未进入 FIFO 的旧回程箱、FIFO 和未决可靠义务；只有现场确认物理清线、旧义务收敛且新 Return 表为空才放行新版本。

按 `tests/README.md` 和 `heavy-test-impact.toml` 选择受影响的聚焦、迁移及 HEAVY 验证；本计划不预设全量测试。接入验收复核已确认的 SCAN4 Event 时间生成/重发语义、现场分辨率和 OUTLET 回程逐箱 `SOURCE_PICKED`，不以本地单测冒充供应商实现证明。
