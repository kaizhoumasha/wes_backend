---
title: WMS PickingTask 取消、计划成员撤销、统一货架离场与非阻塞恢复方案
status: Approved
created_at: 2026-09-16
updated_at: 2026-09-17
scope: WMS 已发布 PickingTask 后的 WorkLine 指派、整单取消、执行中计划成员撤销、关联查询、货架面和 Bin 的后续收口、RETURN_BUFFER 排空计划、统一货架离场去向决定及外部设备异常的非阻塞恢复
system_stage: pre_release
migration_strategy: direct_replacement
related:
  - docs/architecture/SRS.md
  - docs/contracts/wms-outbound-picking-task-integration-requirements.md
  - docs/superpowers/specs/2026-08-06-wes-outbound-operation-top-level-design.md
---

# 1. 目标

定义 WMS 通过 `outbound.picking_task.issued@v1` 发布任务后又发生取消时的最小处理合同，覆盖：

- WMS 在 `issued` 中指定唯一工作 WorkLine，WES 不再自主选线；
- 任务仍在 `QUEUED` 时的整单取消；
- 任务进入 `EXECUTING` 后，按已下发计划中的五层货架面或退料货架来源明细撤销；
- PickingTask、业务 Decision 与 Transport 的可查询关联，以及 Transport 请求摘要的保留边界；
- 已执行物理动作、在途货架、滚筒线 Bin 和未知结果的安全收口。

本方案不要求 WMS 订阅 WES 实时进度。WMS 只发送取消范围，WES 根据本地可靠事实清理尚未执行的计划成员。

# 2. 核心决策

1. `issued` 必须携带不可变 `workline_code`；WMS 决定任务在哪条 WorkLine 工作，WES 只校验和可靠执行。
2. 取消统一为新的 typed WMS→WES operation，不修改原 `issued` 或 `plan_delta` Evidence。
3. `QUEUED` 只支持整单取消；任务已经进入 `EXECUTING` 后不再接受整单取消，只接受计划成员范围取消。
4. 取消只停止未来业务准入，不撤销 ECS/RCS 已接纳的 Transport 或 DeviceCommand。
5. WES 对已执行成员忽略取消效果，但必须继续保留其原身份、证据和最终结果处理；业务 binding 只用于身份关联和幂等，不得被解释为 WES 持有的物理占用锁。
6. 取消 operation 的 ACK 只表示取消指令已可靠保存，不表示物理动作已停止、资源已释放或所有范围都已清理。
7. 取消后遗留的 `RETURN_BUFFER` 仍按 WorkLine 级物理义务排空；WMS 的排空决定一次返回按货架聚合的有序货架/货架面计划。
8. PickingTask 关联使用本地整数外键承载于业务关联表和明确归属的 InboundEvidence；共享 Transport 核心不复制 PickingTask 所有权，业务幂等摘要和出站字节摘要均保留。
9. WES 保留当前对象和物理队列的直接业务门禁：CTU01 窗口、feed_complete、RETURN_BUFFER FIFO、同架复用和 SCAN1 权威事实校验；
   只删除历史已归档或无关对象造成的全线永久阻断，不实现第二套 RCS 路径调度。
10. `ACCEPTED` 只表示外部系统可靠接管，不生成到位或完成事实；`SUCCEEDED/FAILED` 保持确定终态，只有
    `RECONCILING/UNKNOWN` 可以由同一 identity 的更高版本结果收敛。RCS/ECS 若仍会内部重试，不得提前报告确定 `FAILED`。
11. inbound/return BIN_MOVE 与来源货架、FIFO 的业务释放仍以匹配 `SUCCEEDED` 和精确最终位置为准；不把两个独立 Transport 的物理先后
    完全委托给未定义的跨任务 RCS 关联。
12. SCAN1 保留计划来源、目标货架、Bin 入口位置和原入站 Transport 权威结果校验；只是不要求来源架在 Bin 已成功入线后继续保持原位置。
13. 未知物理结果仍保留原身份和证据，但阻塞范围收敛到直接依赖该结果的动作；历史失败、归档记录和对账状态不得成为 WorkLine 级永久门闩，恢复不得要求用户直接清理数据库或 Redis。
14. 所有货架离开当前工作位前都必须调用 `outbound.rack.departure_decide@v1`，由 WMS 返回唯一权威 `rack_destination`；WES 不再硬编码 `WH01`、从计划目标猜测去向或从其它 operation 复用离场目标。离场 Transport 的 RCS 模板仍由货架角色和业务步骤决定，不由 WMS 响应选择。
15. departure request 的 `data.task_id` 是必填但可为 `null` 的严格字段；只有 `TRANSFER_RACK_OUT` 必须传当前真实 PickingTask 的 `task_id`，其它货架离场固定传 `null`，不得借用最近完成任务或历史任务填充。

# 3. Operation

## 3.1 issued 指定 WorkLine

`outbound.picking_task.issued@v1` 的 `data.workline_code` 改为必填且发布后不可变：

```json
{
  "operation_id": "019f33f0-58d7-7b4d-a23a-1b90aa5d4473",
  "operation": "outbound.picking_task.issued@v1",
  "timestamp": 1786060800000,
  "data": {
    "task_id": "PICK-20260811-001",
    "task_type": "AUTO",
    "workline_code": "SORTING-LINE-01",
    "queue_revision": 1,
    "dispatch_sequence": 100,
    "not_before": 1786060800000
  }
}
```

接收规则：

- `workline_code` 必须命中未删除、已启用且插件与 `task_type` 匹配的 WorkLine；不存在返回 `REFERENCE_CONFLICT`，停用或类型不匹配返回 `STATE_CONFLICT`；
- WorkLine 当前忙碌不拒绝 issued；任务可靠保存为绑定该 WorkLine 的 `QUEUED`，等待该线满足 prepare 条件；
- 同一 WorkLine 可以积压多个 `QUEUED` 任务，但同时只能有一个 `PREPARING | EXECUTING` 任务；
- `queue_changed@v1` 仍只修改 `dispatch_sequence` 和 `not_before`，不得修改 `workline_code`；需要换线时取消原任务并由 WMS 使用新的 `task_id` 发布，不增加重绑定 operation；
- issued ACK 只表示任务及其 WorkLine 指派已可靠保存，不表示该线当前空闲、已准备或已开始执行。

`outbound.picking_task.prepare@v1` 保留。WES 在指定 WorkLine 满足本地启动条件后发送 prepare，请求中的 `workline_code` 必须与 issued
冻结值一致；它用于触发 WMS 资源计算，不再承担 WES 选线或换线语义。原 `available_since + workline_code` 候选线选择逻辑退出。

`PickingTask.workline_id` 从 issued 接收事务开始绑定并保留用于审计，目标形态是列级 `NOT NULL` 外键，而非可空字段配合组合 CHECK：
现有 `picking_task_binding_matches_status`（“`QUEUED` 必须 `workline_id IS NULL`”）这条随状态机分支变化的组合约束需要直接删除，
不用新的 CHECK 表达式替代——`workline_id` 从创建起即非空，不再需要按状态区分是否允许为空。

配套的 `ix_picking_tasks_queue`（当前为 `(task_type, dispatch_sequence, id)`，仅在 `status = 'QUEUED'` 时生效）需要加入 `workline_id`
作为前导列（例如 `(workline_id, task_type, dispatch_sequence, id)`），避免多 WorkLine 并行排队时按 `workline_id` 过滤的
`claim_next_queued` 退化为跨线扫描。任务取消、完成或归档不能删除其原始 WorkLine 指派。

## 3.2 统一取消 operation

新增一个带严格 discriminator 的 operation：

```text
outbound.picking_task.cancel@v1
```

`data.cancel_scope` 只允许 `TASK` 或 `PLAN_MEMBERS`。两个分支是封闭联合，不能混合字段，也不能省略范围。

### `TASK`：整单取消

请求最小结构：

```json
{
  "operation_id": "019f3410-0000-7000-8000-000000000001",
  "operation": "outbound.picking_task.cancel@v1",
  "timestamp": 1786065200000,
  "data": {
    "task_id": "PICK-20260811-001",
    "cancel_scope": "TASK"
  }
}
```

适用条件：

- 任务状态为 `QUEUED` 或 `PREPARING`；
- 没有 `plan_delta`、Bin 或货架 Transport（即尚未进入 `EXECUTING`）；
- 任务虽已由 issued 绑定 WorkLine，或已发出 `prepare` 请求，但均尚未产生计划成员或物理动作。

WES 在同一事务中保存取消 Evidence，将任务置为明确的 `CANCELLED` 终态，并停止后续 prepare 唤醒。`TASK` 分支禁止携带货架面或储位选择器。重复请求按
`(operation, operation_id)` 原样重放；同一 `task_id` 使用新 identity 重复取消或与当前状态冲突时返回确定冲突。

若任务当前处于 `PREPARING`（已发出 `prepare` 请求，尚未收到 `plan_delta`），取消不撤销已发出的 `prepare` 请求本身：其 `WmsConfirmation`
按既有可靠交付机制继续处理 ACK/CALLBACK/deadline，取消动作只把任务置为 `CANCELLED` 并阻止后续基于该确认的激活。若取消后仍收到迟到的
`plan_delta`，因任务状态已不是 `EXECUTING`，按 `STATE_CONFLICT` 拒绝，不创建计划成员、不激活任务，也不放宽 `plan_delta` 自身的版本、身份或来源校验。
`PickingTaskConfirmationOwnerService` 必须仅对 `outbound.picking_task.prepare@v1` 把 `CANCELLED` 加入 dispatch/response owner 允许状态，
使未发送、发送中、响应未知和迟到响应都沿原 Confirmation 身份闭合；其他 PickingTask operation 不因该规则放宽 owner。prepare 的确定响应
可以保存为 Evidence/Confirmation 结果，但任何激活或 plan_delta 应用仍必须复核任务不是 `CANCELLED`。

### `PLAN_MEMBERS`：计划成员撤销

请求使用严格的成员选择器，不接受自由路径、任意 operation 字符串或裸 `dict`：

```json
{
  "operation_id": "019f3410-0000-7000-8000-000000000002",
  "operation": "outbound.picking_task.cancel@v1",
  "timestamp": 1786065201000,
  "data": {
    "task_id": "PICK-20260811-001",
    "cancel_scope": "PLAN_MEMBERS",
    "bin_source_racks": [
      {"rack_id": "RACK-5F-001", "rack_faces": ["90", "270"]}
    ],
    "direct_pick_sources": [
      {"rack_id": "RETURN-RACK-01", "rack_face": "A", "slot_ids": ["A-03", "A-04"]}
    ]
  }
}
```

字段语义：

- `bin_source_racks[]` 选择 `plan_delta.added_bin_source_racks[]` 展开的五层来源货架面；元素形状（`rack_id` + `rack_faces[]`）复用
  `wes_plugin_sdk.RackFaceSequence`，与 `plan_delta.added_bin_source_racks[]`、`workline.return_buffer.drain_rack_decide@v1` 的
  `READY.racks[]`（见 5.4 节）共用；该纯值对象只包含 `rack_id + rack_faces`，不携带 Evidence、revision 或业务状态；
- `direct_pick_sources[]` 选择 `plan_delta.added_direct_picks[]` 中的退料货架来源储位；
- 空数组不产生业务变化；请求至少包含一种非空选择器；
- 选择器必须命中当前任务“当前活跃”的计划成员——已被本任务此前某次成功 cancel 请求移除的成员不再计入“活跃”范围；选择器命中不存在或已被
  取消的成员时，整条请求统一返回 `409 / CONFLICT`，不区分“从未存在”还是“已被取消”，也不当作幂等 no-op 接受；
- 同一 identity（相同 `operation_id`）的重试必须保持完整选择器和时间戳不变，视为幂等重放；不同 `operation_id` 但选择器与历史结果重叠时，
  按上一条“命中已取消成员”的规则处理，不当作幂等重放；
- cancel 处理必须在同一事务内先对目标 `PickingTask` 加行锁（复用 `lock_task_identity` 或等价语义），再读取计划成员状态并写入取消边界，
  与 `issued`/`plan_delta` 保持一致的加锁顺序，避免并发窗口下计划成员集合读取与取消边界写入产生竞态。

该分支只适用于 `EXECUTING`。WES 接收后记录不可变取消边界：未执行成员从本地调度集合移除，已执行成员保持原状。
取消范围可以同时包含未执行和已执行成员，WES 不要求 WMS 先查询实时进度。

取消边界直接写入既有计划成员表：`direct_pick_executions.cancelled_evidence_id` 与
`picking_task_bin_source_racks.cancelled_evidence_id` 均为可空外键，指向本次 cancel `InboundEvidence.id`。`NULL` 表示当前活跃，
非空表示该成员已被对应 Evidence 撤销；字段只允许在成功 cancel 事务中执行一次 `NULL → evidence_id`，提交后不可改绑或清空。
已执行成员虽然不撤销原物理事实，也写入同一取消 Evidence，以阻止后续再次产生业务动作并保留完整审计。现有成员唯一约束保持不变，
取消后不得通过后续 `plan_delta` 重新添加相同任务、货架面或储位身份。

`PLAN_MEMBERS` 分支至少包含一种非空选择器；`TASK` 分支和 `PLAN_MEMBERS` 分支不能混用。内部可以使用两个 typed intent/service 分支，但对外只暴露这一个 operation。

# 4. 执行边界

“已执行”按 WES 的可靠事实判断，不由 WMS 在取消请求中声明：

| 本地事实 | 取消效果 |
| --- | --- |
| 计划成员已接收，但尚未创建任何货架、Bin 或直接取料动作 | 标记取消，不再创建后续 Transport 或业务请求 |
| Transport 仅在 WES 本地创建，尚未发出任何外部请求 | 调用共享 finalize-unsent 能力，记录 `FAILED / TRANSPORT_WITHDRAWN_BEFORE_SEND`，不发送该 Transport |
| Transport 已发出但尚未被 RCS/ECS 接纳 | 忽略取消；保留原 Transport identity，继续按原正文可靠提交 |
| Transport 已被 RCS/ECS 接纳、在途、等待点位或处于 `RECONCILING` | 忽略取消；WES 保存后续结果，结果未知沿原 identity 收敛，确定终态不改写 |
| 货架已到位，但尚未发送 `inbound_batch` | 货架动作继续闭合；到位后调用一次 `inbound_batch`，WMS 返回空清单 `RACK_FACE_DONE`，关闭该面后续取料 |
| `inbound_batch` 已发送、响应未知或已返回 Bin | 忽略取消；保留原 `operation_id`、响应和 Bin 成员，不以新 identity 替换 |
| Bin 已进入滚筒线但尚未形成 `work_plan` | Bin 到工作位后调用 `work_plan`，WMS 返回 `NO_WORK`，Bin 进入正常退料路径 |
| `work_plan` 已返回 `READY.cell_ids[]` 或料盘动作已开始 | 忽略取消；已授权 Cell 和物理动作按原合同闭合 |
| Bin 已可靠进入 `RETURN_BUFFER` | 不按原任务删除或移出 FIFO；继续按 WorkLine 级退料合同处理 |

取消不创建替代货架、替代 Bin、替代 Transport，也不改写已应用的 `plan_delta`。这保持现有计划追加、不可删除规则。

# 5. 任务和货架语义

## 5.1 任务状态

- `QUEUED + cancel`：整单进入 `CANCELLED`；保留 issued 冻结的 WorkLine 指派用于审计，不触发 prepare。
- `PREPARING + cancel(TASK)`：整单进入 `CANCELLED`；已发出的 `prepare` 请求不撤销，其 `WmsConfirmation` 按既有可靠交付机制继续处理
  ACK/CALLBACK/deadline；prepare dispatch/response owner 显式允许 `CANCELLED`，但结果不得重新激活任务。取消后到达的迟到 `plan_delta`
  按 `STATE_CONFLICT` 拒绝，不创建计划成员、不激活任务。
- `EXECUTING + cancel_scope=PLAN_MEMBERS`：任务继续保留，未取消成员按原计划推进，取消成员不再产生新动作。
- 任务最终业务状态仍由 WMS 裁决；WES 不因为取消 ACK 直接宣布业务完成。
- 当所有未取消业务成员已处理，且尚未被外部系统接纳的本地可靠义务已经提交后，WES 即可满足任务级完成/归档条件。已经由 RCS/ECS
  `ACCEPTED` 的物理义务继续独立收口，不得反向阻塞 PickingTask 归档；其完成、失败或未知结果仍保留原身份和证据，不能被取消覆盖。

## 5.2 五层来源货架面

取消某个来源面不会撤销该面已经消费的 Bin，也不会撤销已经到位或在途的货架。取消面再次到位时，WES 仍通过
`inbound_batch` 取得 WMS 的空清单终态；该请求用于关闭业务面，不用于撤销物理货架 Transport。

## 5.3 退料货架来源

取消 `added_direct_picks[]` 中尚未执行的来源储位后，WES 不再安排该储位的直接取料。已创建或已接纳的直接取料动作继续闭合。

这不等同于取消 `RETURN_BUFFER` 中 Bin 的回库目标。Bin 进入 `RETURN_BUFFER` 后属于 WorkLine 级 FIFO，必须继续使用
`outbound.bin.return_batch@v1` 和既有 Transport 事实收口。

## 5.4 RETURN_BUFFER 多货架、多面排空计划

取消或任务完成不会删除已经进入 `RETURN_BUFFER` 的 Bin。需要为这些 Bin 选择承接货架时，继续调用
`workline.return_buffer.drain_rack_decide@v1`。其 `READY` 响应直接替换当前单值 `rack_id + rack_face`，统一使用与
`plan_delta.added_bin_source_racks[]` 相同的按货架聚合表达：

```json
{
  "operation_id": "019f3406-2200-7b03-8b01-000000000003",
  "code": "DECIDED",
  "timestamp": 1788390200100,
  "data": {
    "result": "READY",
    "racks": [
      {
        "rack_id": "RACK-5F-001",
        "rack_faces": ["90", "270"]
      },
      {
        "rack_id": "RACK-5F-002",
        "rack_faces": ["90"]
      }
    ]
  }
}
```

严格规则：

- `racks[]` 为非空有序数组，`rack_id` 不得重复；数组顺序是 WES 的跨货架执行顺序；
- 每个 `rack_faces[]` 为非空有序数组，同一货架内不得重复；数组顺序是该货架的换面顺序；
- `required_slot_count` 不得超过该 WorkLine 配置的 `RETURN_BUFFER` 容量；所有 `rack_faces[]` 的总面数必须在
  `1..required_slot_count`，每个返回面至少承担一个预留槽位，不返回零贡献面；
- WMS 返回 `READY` 前，必须在同一事务中确认所有返回面的合计可用容量能够承接本次冻结候选，并保持相应容量义务；
- 响应不返回 `slot_id`、Bin 列表或单面容量；精确储位继续由后续 `outbound.bin.return_batch@v1` 分批决定；
- `WAIT` 仍只返回 `result + reason_code + retry_after_ms`，禁止携带 `racks`；
- 同一 `operation_id` 的技术重试返回首次完整响应；`READY` 保存后不得改写货架或货架面顺序；
- 旧的 `READY.rack_id + READY.rack_face` 响应直接退役并拒绝，不保留别名、兼容 parser 或双路径。

WMS 内部以 `(workline_code, drain_operation_id)` 保存唯一活动容量 reservation，不增加 wire 字段。单一 WorkLine 同时最多一个活动 drain；
后续 `return_batch` 使用已有 `workline_code + rack_id + rack_face` 命中该 reservation 的当前有序面并消费精确 slot。物理动作、位置或结果未知时
reservation 不释放；本次 `required_slot_count` 对应成员全部取得权威回库结果后释放剩余容量并关闭 drain。新进入 RETURN_BUFFER 的 Bin 不追加到
旧 reservation，由下一次 drain 重新计算。
当前面至少完成一个 `READY` 批次后，再收到 `NO_BATCH` 才表示该面 reservation 已耗尽；首批即 `NO_BATCH`、请求非当前有序面，或最后一面
耗尽后本次数量仍未闭合，均进入对账。`UNAVAILABLE` 或响应未知只重试原 return_batch identity，不推进面。

WES 严格按 `racks[]` 和 `rack_faces[]` 形成业务动作顺序。当前货架未到位时复用原 `RACK_MOVE`；同一货架只有在匹配进场/前次换面
`SUCCEEDED` 且精确在位后才能创建下一面 `RACK_ROTATE`；跨货架时，当前架 departure 获 `ACCEPTED` 后即可提交下一架进场，不等待旧架最终位置。
Transport 被 RCS 接纳后，点位占用、物理串行和恢复顺序由 RCS
负责，WES 不再使用历史 `FAILED`、WorkLine 占窗或全局“未闭合货架动作”查询阻塞后续独立任务。只有尚未形成下一个动作所需业务输入时才等待；
同一动作的物理结果继续保持原身份收口，不因排空计划切换或失败重试创建替代 Transport。

## 5.5 外部执行接纳与对象级恢复

### 5.5.1 权威状态边界

WES 负责可靠创建、身份冻结、结果留证和业务顺序；RCS/ECS 负责已接纳动作的路径、设备互斥和内部重试。两层不能互相替代：

- `ACCEPTED` 只表示外部系统可靠接管，不生成到位、完成或位置事实；
- `SUCCEEDED` 与位置明确的 `FAILED` 是确定终态，后续结果不得改写；
- 可能已接纳但结果或位置未知时使用 `RECONCILING/UNKNOWN`，只有该状态允许同一 identity 的更高版本结果收敛为
  `SUCCEEDED | FAILED`；
- RCS/ECS 若仍会自动重试，不得提前向 WES 报告确定 `FAILED`；只有自身确认不再执行且位置明确时才报告失败；
- WES 不换 identity 重发已接纳或结果未知动作，也不通过数据库/Redis 清理伪造恢复。

该规则复用现有 Transport/DeviceCommand 状态机，不增加 `FAILED → SUCCEEDED`、兼容分支或插件私有状态覆盖。

### 5.5.2 保留直接业务依赖，删除历史无关门闩

WES 保留当前对象和物理队列真正需要的门禁：

- CTU01 窗口、同架复用、当前架和货架面权威到位按 SRS 与 Transport 合同现行规则执行；
- inbound BIN_MOVE 与全部成员取得权威 `SUCCEEDED`、结果发布且到达绑定 HANDOFF_POSITION 后，当前面才达到 `feed_complete`；
- return BIN_MOVE 只有权威成功和最终位置保存后，成员才退出活动 RETURN_BUFFER FIFO；
- 同架换面必须等待匹配进场/前次换面 `SUCCEEDED` 和精确位置；跨架时旧架 departure `ACCEPTED` 后可以提交下一架进场，
  由 RCS 排队，旧架最终结果继续按原 identity 收口；
- SCAN1 保留计划来源、当前目标架、Bin 入口位置和原入站 Transport 权威结果校验；来源架后续离位不反向否定已成功入线的 Bin。

删除的只是历史或无关对象造成的全线阻断：已归档业务、其它任务的旧失败、已经被更高版本 UNKNOWN 结果闭合的对账记录，不得成为当前
WorkLine 的永久 gate。查询必须限定当前任务、当前 binding、当前 FIFO 成员和最新权威版本，不允许无界扫描历史失败。

### 5.5.3 基础能力与业务能力边界

| 层级 | 只负责 | 明确禁止 |
| --- | --- | --- |
| Transport 基础能力 | 冻结 identity/请求、可靠提交、保存 ACK/最终结果/位置、UNKNOWN 同 identity 收敛、可证明未发送的本地终止 | PickingTask、FIFO、CTU 窗口和业务完成判断 |
| DeviceCommand 基础能力 | 冻结 command、可靠提交、保存 ACK/CALLBACK、结果未知收敛 | SCAN 路由、passage 状态和业务取消 |
| manual-picking 业务能力 | PickingTask、计划成员、FIFO、feed_complete、货架循环、取消和离场时机 | 供应商协议、硬件重试、改写基础状态机 |
| WMS integration 业务能力 | typed operation、严格 DTO、业务结果解释和可靠 Evidence | RCS 路径调度、ECS 设备恢复和物理结果伪造 |

可证明未发送的 Transport 复用共享 finalize-unsent 能力转为 `FAILED / TRANSPORT_WITHDRAWN_BEFORE_SEND`。该能力必须锁定 Transport 行并
以 `PENDING + send_started_at IS NULL + 无活动 claim + 无 Evidence` 做条件更新；dispatcher 在发网前仍以同一 claim/send 快照复核。
PickingTask 行锁只保护业务成员和取消边界，不能替代 Transport 发送围栏。

### 5.5.4 其余业务阻塞收口

- direct pick 能力未完整实现前，manual-picking 对包含 `added_direct_picks` 的 plan_delta 整批 fail closed；获批后一次性实现完整路径并删除拒绝分支；
- 归档 passage 与已取消计划成员退出活动查询，但不删除 Evidence、binding、Transport 或位置事实；
- WMS Confirmation `RECONCILING` 只阻塞直接依赖该 operation 的动作；独立 reconciler 复用原 identity 处理，不形成整线门闩；
- 所有恢复通过正常 API、原 identity 结果或自动 reconciler 完成，管理入口不得伪造 `SUCCEEDED` 或要求 SQL/Redis 清理。

## 5.6 统一货架离场去向决定

### 5.6.1 唯一权威来源

与 WMS 团队确认后，人工拣料工作线所有货架离场统一使用 `outbound.rack.departure_decide@v1`。适用范围至少包括：

- PickingTask 初始目标/转运货架离开 `TRANSFER_RACK`；
- 五层来源货架完成当前任务所需货架面后离开 `FIVE_RACK`；
- `RETURN_BUFFER` drain 货架完成当前 drain 计划后离开 `FIVE_RACK`；
- direct pick 能力获批后，退料来源货架完成当前面/任务后离开对应工作位；
- 取消后已经到位或在途、最终仍需要离开工作位的上述货架。

WES 只有在业务判断“该货架不再承担当前工作”且已经取得精确 `current_location + current_face` 后才发起决定。`READY.rack_destination`
是离场去向的唯一权威来源；未取得 `READY` 时不得创建离场 Transport。`WAIT` 按 `retry_after_ms` 和最新现场事实使用新
`operation_id` 重新求值；请求或响应未知时只重试原 identity 和冻结正文。

以下旧路径直接退出：

- 五层来源货架和 drain 货架直接创建 `CTU03 → ZONE WH01`；
- 任何业务代码硬编码离场 `ZONE`、库位或默认目的地；
- 从进场计划、WorkLine 配置、PositionProjection 或历史离场结果猜测下一去向；
- `outbound.material.decide@v1` 的 `target_preparation.mode=REPLACE` 同时携带当前目标架的 `rack_destination`。

`target_preparation.mode=REPLACE` 只表达当前物料需要更换目标货架及新目标准备要求。当前目标架真正离场前，WES 必须单独调用
`outbound.rack.departure_decide@v1` 获取目的地；否则 material decision 和 departure decision 会形成两个权威来源。系统尚未发布，
该字段直接从 material contract、SDK、wire、OpenAPI、fixture 和业务代码中删除，不保留兼容解析或双路径。

### 5.6.2 RCS 模板规则保持不变

`rack_destination` 只回答“去哪里”，不回答“用哪个 RCS 模板”。离场模板继续由 manual-picking 业务步骤静态决定：

| 离场步骤 | WMS 决定 | RCS 模板 |
| --- | --- | --- |
| 五层来源货架离场 `SOURCE_RACK_OUT` | `outbound.rack.departure_decide@v1 → rack_destination` | `CTU03` |
| drain 货架离场 `DRAIN_RACK_OUT` | `outbound.rack.departure_decide@v1 → rack_destination` | `CTU03` |
| 目标/转运货架离场 `TRANSFER_RACK_OUT` | `outbound.rack.departure_decide@v1 → rack_destination` | `F01` |
| 后续获批的 direct pick 退料货架离场 | 同一 departure operation | 继续使用该货架角色获批的既有模板，不在 departure 响应增加模板字段 |

WMS 不返回 `rcs_template_id`、Transport method 或步骤名。插件不得根据 `rack_destination` 类型动态切换模板；`ZONE` 与
`RACK_POSITION` 都只转换为 Transport 目标位置，模板仍由上述静态业务映射决定。

### 5.6.3 身份、幂等与非阻塞恢复

每次 departure `READY` 只派生一个稳定离场 Decision/binding 和一个 Transport `client_request_id`。绑定必须引用原 departure
WmsConfirmation/Result Evidence，并冻结货架、当前位置、当前面、WMS 目的地和业务步骤；同一决定不得拆分成多个离场 Transport。

离场 Transport 获得 RCS `ACCEPTED` 后，继续遵循第 5.5 节的对象级模型：RCS 负责目标点位等待，结果未知沿原 identity 收敛；
确定 `FAILED` 只留证，不重新请求 departure decision、不换 destination、不创建替代 Transport。只有 WMS 对尚未生成离场 Transport 的
`WAIT` 重新求值，或者明确业务事实变化导致上一决定尚未形成 Transport 时，才允许使用新 operation identity 再次决定。

### 5.6.4 `task_id` 传值规则

`outbound.rack.departure_decide@v1` 保留单一 DTO，不增加 owner discriminator 或 generic owner。`data.task_id` 字段必须存在，类型直接替换为
`string | null`：

| 离场步骤 | `data.task_id` |
| --- | --- |
| `TRANSFER_RACK_OUT` | 必须是当前目标/转运货架所属 PickingTask 的真实 WMS `task_id`，禁止为空 |
| `SOURCE_RACK_OUT` | 固定 `null` |
| `DRAIN_RACK_OUT` | 固定 `null` |
| 后续获批的 direct pick 退料货架离场 | 固定 `null` |

该字段只表达 WMS 是否需要按 PickingTask 判断转运货架离场，不承担 WES 内部 owner 关联。来源货架即使仍可通过
`TransportDecisionBinding.picking_task_id` 关联原任务，wire `task_id` 也必须为 `null`；WorkLine drain 同样不得填入“最近完成任务”或触发 drain
的历史任务。`null` 与非空值的合法性由具体离场步骤在创建 intent 时静态校验，不允许调用方任意选择。

# 6. WMS 与 WES 责任

| 系统 | 责任 |
| --- | --- |
| WMS | issued 时指定不可变 WorkLine；发送整单或明确成员范围的取消意图；处理业务异常并继续接收位置事实和业务回调；维护库存、来源、目标和最终业务状态；为每次货架离场返回唯一权威 `rack_destination`；对原 identity 的 WES 请求提供幂等重放和迟到结果 |
| WES | 校验并冻结 issued 的 WorkLine 指派；可靠创建外部动作并保存 Evidence；按本地事实停止未执行计划；在外部 `ACCEPTED` 后解除本地调度依赖；消费原 identity 的恢复结果；不实现点位占窗、硬件重试或第二套物理调度 |
| RCS | 可靠持久化已接纳 Transport；管理点位占用、路径冲突、对象级顺序和硬件重试；点位释放或人工处理后恢复原 `transport_task_id` 并回调更高版本结果 |
| ECS | 可靠持久化已接纳 DeviceCommand；管理设备执行、故障重试和人工恢复；恢复原 `command_code` 并回调最终结果 |

取消 ACK 不得被解释为：

- Transport 已取消；
- 货架或 Bin 已回到原位；
- WMS 的库存或容量预留已释放；
- 所有指定货架面均已完成现场清理。

# 7. 数据库关联与请求摘要

## 7.1 PickingTask 外键边界

数据库关联统一使用本地 `picking_task_id → wes_biz.picking_tasks.id`，不使用 WMS 字符串 `task_id` 作为外键。外部 `task_id` 仍是 wire
业务身份，查询时通过 `PickingTask` 唯一记录转换为本地主键。

现有 `direct_pick_executions`、`picking_task_bin_source_racks` 和任务 owner 类型的 `wms_confirmations` 已经使用
`picking_task_id`，保持不变。新增关联只落在真正承接业务 Decision 到 Transport 映射的
`wes_biz.transport_decision_bindings`：

```text
picking_task_id BIGINT NULL
  FK → wes_biz.picking_tasks.id

INDEX (workline_id, picking_task_id, step)   -- 服务 WorkLine 级 drain 查询（WHERE workline_id = ? AND picking_task_id IS NULL）
INDEX (picking_task_id)                       -- 服务按任务查询 Transport（WHERE picking_task_id = ?）
```

规则如下：

- PickingTask 所属的目标货架进场、五层来源货架进场/换面/离场、直接取料相关搬运及 Bin 入站批次必须写入 `picking_task_id`；
- WorkLine 级 drain、非 PickingTask 业务和独立 Transport 联调保持 `NULL`；
- 外键不级联删除，任务取消、完成或归档后仍保留 Decision、Transport 和物理结果审计链；
- 现有 `correlation_id="pt:{task.id}:..."` 可以继续作为稳定 Decision identity，但不得再通过字符串前缀解析任务归属；
- 查询使用 `PickingTask → TransportDecisionBinding.picking_task_id → client_request_id → TransportTask.client_request_id`，
  该路径以 `picking_task_id` 为前导过滤条件，需要单独的 `(picking_task_id)` 索引支撑——PostgreSQL 不会为外键自动建索引，
  仅靠 `(workline_id, picking_task_id, step)` 无法让该路径有效走索引；
- `TransportDecisionBinding` 已有 Decision identity 和 `client_request_id` 唯一约束继续负责一对一稳定映射，不另建同义关联表。

不在 `wes_runtime.transport_tasks`、`transport_members` 或位置投影中增加 `picking_task_id`。这些表属于共享 Transport/物理事实能力，
同时服务上架、WorkLine drain、联调和其他非 PickingTask 消费者；直接复制 PickingTask 外键会形成可空多业务 owner、重复事实及跨域耦合。
需要按任务查询 Transport 时，通过业务 binding 连接，不解析 `request_json`、`caller_json`、Evidence JSON 或 `correlation_id`。

## 7.2 InboundEvidence 任务关联

`wes_biz.inbound_evidences` 增加可空的本地任务外键和任务时间线索引：

```text
picking_task_id BIGINT NULL
  FK → wes_biz.picking_tasks.id

INDEX (picking_task_id, received_at, id)
```

`InboundEvidence` 已经使用 `workline_id`、`material_execution_id`、`transport_task_id`、设备 identity 和 WMS operation identity
表达结构化关联；新增 `picking_task_id` 延续同一模式，不增加 `business_ref_type + business_ref_id` generic owner，也不把外部字符串
`task_id` 冗余保存为外键。

写入规则：

| Evidence | `picking_task_id` |
| --- | --- |
| `outbound.picking_task.issued@v1` | 首次接收先保存 Evidence，再创建 PickingTask，并在同一事务、ACK 前回填本地主键 |
| `queue_changed`、`plan_delta`、`cancel` | 通过已严格校验的 `task_id` 取得本地 PickingTask 后写入 |
| PickingTask owner 的 WMS Result | 从原 `WmsConfirmation.picking_task_id` 复制 |
| `inbound_batch`、`work_plan` 等明确任务 operation | 从已校验的本地 PickingTask 写入 |
| WorkLine drain、`return_batch` | 保持 `NULL`；这些义务可能已经脱离原任务 |
| 原始 Transport Result | 保持 `NULL`，保留 `transport_task_id`；通过 TransportTask 与 `TransportDecisionBinding.picking_task_id` 关联 |
| 插件形成的任务级派生 Evidence | 只有 binding 已明确冻结 `picking_task_id` 时才复制该本地主键 |
| 无任务设备、联调及其他 Evidence | 保持 `NULL` |

issued 的 Evidence 与 PickingTask 会形成双向引用，但不需要新增关联表或延迟 ACK：

```text
插入 issued Evidence，picking_task_id 暂为 NULL
→ 创建 PickingTask，issued_evidence_id 指向该 Evidence
→ 回填 Evidence.picking_task_id
→ 同一事务提交
→ 返回 RECEIVED
```

该回填属于首次接收事务内的对象构造，不是提交后的 Evidence 改写。事务提交后 `picking_task_id` 不可变；重复 issued 必须通过
既有 `task_id` 取得同一本地主键，并校验 Evidence 关联一致。任何 handler 都不得为了补关联而在 ACK 后扫描 JSON 并回写历史 Evidence。

任务排查统一使用：

```text
PickingTask
├── InboundEvidence.picking_task_id
├── WmsConfirmation.picking_task_id
└── TransportDecisionBinding.picking_task_id
    └── TransportTask.client_request_id
        └── 原始 Transport Evidence.transport_task_id
```

因此 `normalized_payload`、`payload_digest` 和原始 operation identity 继续作为不可变证据内容与幂等依据，但不再充当 PickingTask 查询索引。

## 7.3 计划成员取消关联

既有两张计划成员表分别增加：

```text
direct_pick_executions.cancelled_evidence_id BIGINT NULL
picking_task_bin_source_racks.cancelled_evidence_id BIGINT NULL
  FK → wes_biz.inbound_evidences.id
```

计划成员行不删除、不重建，也不增加可变状态字符串。调度和取消选择器统一以 `cancelled_evidence_id IS NULL` 判定当前活跃成员；成功 cancel
在 PickingTask 行锁和同一数据库事务内创建 Evidence、验证全部选择器，并对全部命中成员执行条件更新
`WHERE cancelled_evidence_id IS NULL`。实际更新数量与选择器展开数量不一致时整笔回滚并返回冲突，避免并发取消产生部分应用。

取消时间读取 Evidence 的 `received_at`，取消 identity 读取 Evidence 的 `operation + operation_id`，不在成员表重复保存时间戳、原因或外部
`task_id`。已有物理动作继续通过原 binding/Transport 收口；`cancelled_evidence_id` 只控制未来业务准入，不伪造物理完成。

## 7.4 `request_digest`

`transport_tasks.request_digest` 是创建 Transport 时的**业务请求语义摘要**。它对规范化后的 caller、Transport data 和
`execution_authority` 计算 SHA-256，不包含随后生成的 `transport_task_id`。

该字段与唯一 `client_request_id` 共同保证创建幂等：相同 `client_request_id` 和相同语义返回原 Transport；相同
`client_request_id` 但来源、目标、成员、caller 或 authority 发生变化时必须报幂等冲突。它不能由 `submit_request_body_digest`
替代，因为后者包含本次生成的 wire identity 和时间戳。

## 7.5 `submit_request_body_digest`

`transport_tasks.submit_request_body_digest` 是冻结后的**实际出站 JSON 字节摘要**。它对完整 `submit_request_body` 计算 SHA-256，
覆盖 `operation_id`、`timestamp`、`transport_task_id` 和 data。

该字段用于：

- 技术重试发送完全相同的字节正文；
- Adapter 发送前校验冻结正文没有漂移或损坏；
- HTTP 返回后的 fenced writeback 确认当前任务、operation 和请求正文仍是发送时快照；
- 阻止过期 claim、迟到响应或并发修改写回另一份请求。

`request_digest` 与 `submit_request_body_digest` 分别保护业务幂等和实际 wire 快照，语义不同且存储成本固定为两个 64 字符摘要，均保留。
`request_json` 用于结构化审计，`submit_request_body` 用于原字节重试，也不能用运行时重新编码互相替代。

# 8. 不变量与错误处理

- 原 `issued`、`plan_delta`、`inbound_batch`、`work_plan` 和 Transport identity 不被修改或复用。
- `issued.workline_code` 发布后不可变；队列更新、取消、prepare、计划增量和重试都不能重绑定任务。
- 取消请求只追加 Evidence/取消边界，不删除历史计划成员和物理事实。
- 计划成员取消只允许 `cancelled_evidence_id` 从 `NULL` 写入本次 cancel Evidence；禁止改绑、清空或取消后以新 revision 重建同一身份。
- 业务取消不得向共享 Transport 增加 `CANCELLED` 状态；只有可证明未发送的 `PENDING` 任务才能由中立 finalize-unsent 能力转为
  `FAILED / TRANSPORT_WITHDRAWN_BEFORE_SEND`，已开始发送或存在 Evidence 时必须保留原 identity 收口。
- 取消范围命中已执行成员时可以成功接收，但该成员的取消效果为 no-op；不返回伪造的“已停止”。
- 选择器不存在、任务不属于当前 WorkLine、任务处于不允许的状态或计划身份不匹配时，整条取消请求拒绝，不部分写入。
- WMS 取消后发送的迟到 `plan_delta` 仍按原 operation 合同校验；不得因为取消而放宽版本、身份或来源约束。
- 物理结果 `UNKNOWN/RECONCILING` 不构成可取消条件，必须保留原身份和证据；它只阻塞直接依赖该物理事实的动作，不构成 WorkLine 级占窗。
- `CANCELLED` 只对 prepare Confirmation 的可靠派发和响应保存开放 owner；不得成为其他 PickingTask operation 的通用允许状态。
- `ACCEPTED` 只解除 WES 调度依赖，不生成物理成功或位置事实；需要真实到位的步骤仍只接受匹配的 `SUCCEEDED` 和精确最终位置。
- `SUCCEEDED/FAILED` 是确定终态；只有 `RECONCILING/UNKNOWN` 使用原 `transport_task_id`/`command_code` 接收更高版本纠正。历史失败不得被无界扫描为全线 gate。
- 活动队列、完成条件和归档查询必须排除已归档业务记录，并按当前对象、当前周期和最新结果版本判断，禁止无界扫描 WorkLine 历史失败记录。
- 任何正常恢复流程都不得要求用户删除数据库记录、Redis key、Evidence、Transport binding 或位置事实。
- 所有货架离场 Transport 必须由匹配当前货架、精确位置和当前面的 `outbound.rack.departure_decide@v1 READY` 派生；不存在 READY Evidence 时禁止创建离场 Transport。
- 离场 `rack_destination` 只能来自当前 departure outcome；禁止硬编码 `WH01`、复用其它 operation 的目的地或根据货架角色猜测。
- departure outcome 不拥有 RCS 模板选择权；模板只由业务步骤静态映射，WMS 响应不得携带模板或 Transport method。
- `target_preparation.mode=REPLACE` 不再携带当前架离场去向；material decision 与 departure decision 不得重复拥有同一事实。
- departure wire 的 `task_id` 必须是必填 nullable 字段；只有 `TRANSFER_RACK_OUT` 允许且要求非空，其它离场步骤固定为 `null`。
- cancel 处理必须与 `issued`/`plan_delta` 共用同一行锁顺序（先锁定 `PickingTask` 再读写状态），避免并发窗口下计划成员读取与取消边界写入竞态。
- 选择器命中已被取消的计划成员与命中不存在的选择器同等对待，均按 `409/CONFLICT` 拒绝，不当作幂等重放接受。
- 业务代码不得从 `correlation_id`、JSON 正文或字符串前缀推导 PickingTask 外键；任务关联在创建 binding 时显式冻结。
- 已明确属于 PickingTask 的 Evidence 必须在首次接收事务中冻结 `picking_task_id`；提交后不得扫描 payload 补写或改绑。
- `request_digest` 或 `submit_request_body_digest` 不匹配时 fail closed，不发送漂移请求，也不接受迟到写回。

# 9. 实施缺口与验收

当前代码状态尚未提供该 operation，也没有 `PickingTask.CANCELLED` 或计划成员级取消状态。实施前需要联合冻结：

1. issued 必填 `workline_code` 的严格 DTO、静态 WorkLine 校验、忙碌排队语义和不可变指派；
2. 一个 cancel operation 的 `TASK | PLAN_MEMBERS` 严格联合 DTO、ACK 模式、错误码、幂等和重试语义；
3. `CANCELLED` 任务终态及数据库约束，或等价的不可变取消状态承载方式；
4. 两张计划成员表的 `cancelled_evidence_id` 外键、单向终态更新、选择器唯一性和本地调度过滤规则；
5. `inbound_batch` 空清单 `RACK_FACE_DONE`、`work_plan` `NO_WORK` 与取消范围的联合 fixture；
6. `drain_rack_decide READY.racks[].rack_faces[]` 的严格结构、容量义务、执行顺序、`WAIT` 联合及直接替换规则；`racks[].rack_faces[]`
   元素形状须与 `plan_delta.added_bin_source_racks[]`、cancel 的 `bin_source_racks[]` 复用纯 `wes_plugin_sdk.RackFaceSequence`，不允许
   三处各自定义结构相同的独立 DTO，也不得误用携带 Evidence/revision 的 `PickingTaskRackPlan`；
7. `TransportDecisionBinding.picking_task_id` 的 owner 规则、非级联外键、索引和全部任务 Transport 创建入口；
8. `InboundEvidence.picking_task_id` 的适用 operation、issued 同事务回填、不可变重放和任务时间线索引；
9. `request_digest` 与 `submit_request_body_digest` 的双层幂等/快照合同及 fail-closed 验收；
10. 已执行、在途、响应未知、滚筒线 Bin 和 `RETURN_BUFFER` 的迟到结果验收；
11. `PREPARING` 状态取消处理路径：`TASK` 分支扩展到 `PREPARING`、已发出 `prepare` 的 `WmsConfirmation` 在取消期间的处理规则、取消后迟到
    `plan_delta` 的 `STATE_CONFLICT` 拒绝规则，以及 prepare-only 的 `CANCELLED` dispatch/response owner；
12. `issued` 必填 `workline_code`、`workline_id` 非空绑定、`claim_next_queued` 改造、`picking_task_binding_matches_status` 约束替换
    四类改动的原子发布要求与回滚路径。
13. direct pick 未支持时的计划准入拒绝、归档记录活动查询过滤，以及 WMS confirmation 对象级 reconciler；
14. 所有货架离场统一通过 `outbound.rack.departure_decide@v1` 获取目标，删除来源架/drain 的固定 `WH01` 和 material REPLACE 的离场目标；
15. departure request 的 nullable `task_id` 规则、各离场步骤到既有 RCS 模板的静态映射，以及 READY Evidence 到离场 binding 的一对一关联；
16. 共享 Transport finalize-unsent 的中立合同：`PENDING`、`send_started_at IS NULL`、无活动 claim、无 Evidence 才允许转
    `FAILED / TRANSPORT_WITHDRAWN_BEFORE_SEND`；业务层不得直接改 Transport 状态。

WorkLine 指派实施范围至少包括：

- issued wire/OpenAPI、Event handler、`PickingTaskIssuedService` 和 WorkLine Repository 校验；
- `PickingTask` 模型约束（`workline_id` 改 `NOT NULL` 外键、删除 `picking_task_binding_matches_status` 组合 CHECK）、队列索引
  （`ix_picking_tasks_queue` 加入 `workline_id` 前导列）、随机 revision Alembic migration 和干净 PostgreSQL 迁移验证；
- 实施前对 `claim_next_queued`、`has_active_for_workline` 等因语义变更（从任务类型全局候选改为按 `workline_id` 过滤）而受影响的
  生产符号跑 GitNexus upstream impact 分析，冻结全部调用者，不得只在排空计划部分执行该分析；
- prepare batch/coordinator 从“任务与候选线匹配”改为“指定线领取自己的最高优先级任务”；
- `issued` 必填 `workline_code`、`workline_id` 非空绑定、`claim_next_queued` 改造、`picking_task_binding_matches_status` 约束替换
  这四类改动必须在同一次原子发布内一起上线并给出回滚路径；分阶段部署会在中间状态下产生约束冲突或抢占，不接受先上线部分改动；
- queue_changed、整单取消（含 `PREPARING` 状态取消）、任务归档、计划接收、完成确认及 WorkLine 单活动任务约束回归；
- `PickingTaskConfirmationOwnerService` 仅为 prepare operation 接受 `CANCELLED`，派发/响应继续但业务激活 fail closed；
- SRS、出库权威合同、人工出库合同和顶层设计中所有“WES 选线/issued 禁止 workline_code”旧表述的直接替换与残留扫描。

任务关联实施范围至少包括：

- `TransportDecisionBinding`、`InboundEvidence` 模型、Repository、Service correlation 校验和随机 revision Alembic migration；
- 所有 PickingTask 所属 rack/bin Transport 创建入口显式传入本地 `picking_task_id`，WorkLine drain 和非任务入口保持 `NULL`；
- 删除 `list_task_resource_fence_ids` 等查询对 `correlation_id.startswith("pt:...")` 的依赖，改用外键和 `(workline_id, picking_task_id, step)` /
  `(picking_task_id)` 两条索引，分别服务 WorkLine 级 drain 与按任务查询两种主要查询形状；
- issued、queue_changed、plan_delta、cancel、任务 WMS Result 和插件任务级派生 Evidence 在 ACK/提交边界内冻结正确 `picking_task_id`；
- 删除任务排查及业务应用对 `normalized_payload.data.task_id` 的读取依赖；原始 Transport Result 继续通过 `transport_task_id` 和 binding join；
- 按任务查询 Transport 的 Service/Repository 使用 binding join，不向 `TransportTask` 增加业务 owner；
- 干净 PostgreSQL migration、双向 issued 引用事务、外键约束、取消后关联保留、查询计划和现有非 PickingTask Evidence/Transport 回归。

计划成员取消实施范围至少包括：

- `DirectPickExecution`、`PickingTaskBinSourceRack` 增加可空 `cancelled_evidence_id` 外键，并纳入同批随机 revision migration；
- plan_delta 插入、计划成员读取、货架/Bin 调度、直接取料完成与任务完成聚合统一过滤 `cancelled_evidence_id IS NULL`；
- cancel Service 在 PickingTask 行锁下先验证全部活跃成员，再创建/冻结 Evidence 并条件更新全部命中行；更新计数不一致时整笔回滚；
- 保留原唯一约束，验证取消成员不能由更高 `plan_revision` 重新添加；取消时间和 identity 通过 Evidence 查询，不复制字段。
- 将 `TransportService.finalize_unsent_debug_task_in_session` 的未发送判定提炼为共享中立能力；debug wrapper 继续复用，manual-picking cancel
  只传稳定 Transport identity，不把 PickingTask/cancel DTO 下沉到 Transport；共享方法固定写入 `TRANSPORT_WITHDRAWN_BEFORE_SEND`。

Transport 摘要不做 schema 删除或合并。实施只补充字段注释、约束/测试缺口及诊断说明，不改变摘要算法时不得制造无意义 migration。

排空计划实施范围至少包括：

- 权威出库合同与人工出库合同中的请求/响应示例、字段表和执行语义；
- SDK 的 typed drain outcome、宿主 `return_buffer_drain` wire/typed adapter 和 OpenAPI；
- 新增纯 `RackFaceSequence(rack_id, rack_faces)` 值对象，plan_delta wire、cancel selector 和 drain READY 复用；业务计划层继续单独保存 Evidence/revision；
- `manual-picking` 的 drain repository/flow、到位应用、Transport outcome 和逐面/逐架推进；
- WMS 侧 `(workline_code, drain_operation_id)` 唯一活动 reservation、return_batch 消费、未知结果保留和 drain 完成释放的联合 fixture；
- 核心合同测试、插件聚焦测试、持久化/恢复测试及 HEAVY mapping；
- 旧 `rack_id + rack_face` 构造、fixture、断言和文档残留的完整扫描。

对象级恢复实施范围至少包括：

- 保持 Transport/DeviceCommand 现有确定终态：`SUCCEEDED/FAILED` 不改写；补齐 `RECONCILING/UNKNOWN` 同 identity 高版本收敛测试；
- RCS/ECS 合同明确：仍会内部重试的动作不得报告确定 FAILED，已接纳任务重启不丢失，结果和位置未知时保持可查询；
- manual-picking 保留 CTU01 窗口、同架复用、feed_complete、RETURN_BUFFER FIFO 和 SCAN1 直接业务门禁；
- 活动查询限定当前任务、当前 binding、当前 FIFO 成员和最新结果版本，排除归档 passage 与无关历史失败；
- inbound/return BIN_MOVE 只有匹配 SUCCEEDED 和精确最终位置后才释放来源业务依赖或 FIFO 成员；
- SCAN1 保留计划来源、目标架、入口位置和原入站 Transport 权威结果校验，来源架后续离位不反向否定已入线 Bin；
- WMS confirmation reconciler 只精确重放原 operation identity 和冻结正文，不创建业务 fallback 或新 identity；
- 管理与诊断入口只展示/触发正常恢复，不提供删除过程数据、清 Redis 或伪造成功。

统一货架离场实施范围至少包括：

- 直接替换出库权威合同、人工出库合同、顶层设计和集成说明中“五层来源架/drain 直接 `CTU03 → WH01`”的全部表述；
- 扩展现有 `RackDepartureIntent`、wire、OpenAPI、Adapter、Scheduler 和 ResultReader，使 `task_id` 成为必填 nullable 字段；
  `TRANSFER_RACK_OUT` 必须传真实 task_id，其它步骤必须传 `null`；
- 删除 `target_preparation.mode=REPLACE.rack_destination` 的 SDK/wire/OpenAPI/业务消费与 fixture，material decision 不再决定当前架离场目标；
- 来源架、drain 架、目标/转运架在满足各自业务离场条件时统一创建 departure intent，`WAIT`/重试/READY 使用现有可靠 WMS 基础能力；
- `SOURCE_RACK_OUT`、`DRAIN_RACK_OUT` 保持 `CTU03`，`TRANSFER_RACK_OUT` 保持 `F01`；模板映射留在 manual-picking，不下沉到 WMS operation 或 Transport 基础层；
- 离场 binding 冻结 departure Result Evidence 和 WMS 原样目的地；Transport 结果继续使用原 binding、原 `transport_task_id` 和第 5.5 节恢复语义；
- 删除所有硬编码 `TransportZonePosition("WH01")`、默认离场区、从计划/位置猜测目的地及绕过 departure READY 的创建入口；
- 对 departure operation 的全部消费者运行 GitNexus upstream impact analysis，重点检查 material REPLACE、来源架离场、drain、取消收口、联调调试和任务归档；
- 核心合同测试证明 nullable `task_id` wire、幂等和可靠派发；插件测试证明各离场步骤的 task_id 取值、业务离场条件与模板映射，禁止使用插件测试替代共享 operation 合同测试。

这是共享 WMS 合同和插件执行路径的高风险直接替换。实施时必须先对上述生产符号运行 upstream impact analysis，冻结消费者与测试所有权，
再按 TDD 完成旧合同失败、新合同通过、顺序恢复和异常物理结果回归；不得只修改人工出库文档。

最小验收场景：

- issued 缺少、引用不存在、停用或 `task_type` 不匹配的 WorkLine 时严格拒绝且不创建 PickingTask；
- 指定 WorkLine 忙碌时 issued 仍可靠进入该线队列，空闲后只由该线领取，其他空闲线不得抢占；
- prepare 的 `workline_code` 与 issued 冻结值一致，queue_changed 和取消均不能重绑定；
- PickingTask 所属的 rack/bin Transport 均能通过 `picking_task_id` 和 binding join 查询，不扫描 JSON 或解析 correlation 前缀；
- WorkLine drain、联调和非 PickingTask Transport 的 binding 保持 `picking_task_id=NULL`，共享 `transport_tasks` 无业务外键；
- issued Evidence、PickingTask 和 WorkLine 指派在一个事务中提交；失败时三者全部回滚且不返回虚假 ACK；
- queue_changed、plan_delta、cancel 和任务 WMS Result 可按 `InboundEvidence.picking_task_id` 构建有序时间线，不读取 payload JSON；
- PLAN_MEMBERS 成功后所有命中成员指向同一 cancel Evidence；并发或部分命中导致更新计数不一致时零成员被修改；
- 已取消成员不再进入任何新调度、完成聚合或后续 plan_delta，且不能被重新添加；已执行 Transport/DeviceCommand 继续按原身份闭合；
- 原始 Transport Result 保持 `picking_task_id=NULL`，仍可经 `transport_task_id → binding` 纳入任务排查，且不会错误绑定 WorkLine drain；
- 重复 Evidence 必须匹配首次冻结的 `picking_task_id`；内容相同但任务关联漂移时返回冲突，不改写历史 Evidence；
- 相同 `client_request_id` 的相同语义请求幂等返回原 Transport，语义漂移由 `request_digest` 拒绝；
- 技术重试复用原 `submit_request_body` 和摘要，正文漂移、摘要不匹配及迟到 writeback 均 fail closed；
- `QUEUED` 整单取消后不创建 prepare；
- `PREPARING` 期间收到 `TASK` 取消后任务进入 `CANCELLED`；已发出的 prepare `WmsConfirmation` 按原可靠交付机制继续处理，不被撤销；
  未发送、发送中、响应未知和迟到响应都可按原 identity 保存；取消后到达的 `plan_delta` 按 `STATE_CONFLICT` 拒绝，不创建计划成员、不激活任务；
- 同一执行任务取消两个未消费五层货架面后，后续不再为其创建 `inbound_batch`；
- 五层来源货架面对应的 Transport 已在本地创建但尚未提交外部请求时被取消，该 Transport 原记录转为
  `FAILED / TRANSPORT_WITHDRAWN_BEFORE_SEND`，不被发出，也不创建替代记录；已开始发送、存在 claim 或 Evidence 的并发边界必须拒绝终止并继续原身份收口；
- 命中已被之前某次成功 cancel 请求移除的计划成员时，新的取消请求（不同 `operation_id`）返回 `409/CONFLICT`，不当作幂等 no-op 接受；
- 取消面货架已在途时，Transport 不被取消，到位后 `inbound_batch` 返回 `RACK_FACE_DONE`；
- 已进入滚筒线的 Bin 到工作位后 `work_plan` 返回 `NO_WORK`；
- 已返回 Bin、已接纳 Transport、`UNKNOWN` Transport 和已授权 Cell 不受取消覆盖；
- drain `READY` 可以返回同一货架多个有序面以及多个有序货架，WES 按顺序执行且不提前跨越未闭合面；
- 同一 WorkLine 第二条活动 drain 被拒绝；return_batch 只能消费当前 reservation 的当前 rack/face，数量闭合后释放，未知结果不释放；
- 当前面至少一个 READY 后的 NO_BATCH 才推进下一面；首批 NO_BATCH、越序请求或最后一面耗尽后数量仍未闭合均进入对账；
- `racks` 为空、货架重复、面为空、同架面重复、`WAIT` 携带 `racks` 或旧单值响应均被严格拒绝；
- `required_slot_count` 超过 WorkLine RETURN_BUFFER 容量、总面数超过数量或包含零贡献面时严格拒绝；
- 技术重试恢复首次完整排空计划，重启后继续原货架/货架面位置，不重新排序或改选；
- 目标点位当前不可执行时，是否创建 Transport 按现有业务准入与 RCS 合同判断；已接纳动作不换 identity，结果未知只阻塞直接依赖；
- inbound BIN_MOVE 和成员全部 `SUCCEEDED`、结果发布且精确到达 HANDOFF_POSITION 后，来源面才能 feed_complete；
- return BIN_MOVE 只有权威成功和精确最终位置后才退出 RETURN_BUFFER 活动 FIFO；
- 确定 `FAILED` 保持终态并结束对应业务明细；`RECONCILING/UNKNOWN` 的同 identity 高版本结果可以正常收敛；
- 历史或归档对象的旧失败不阻塞无关新任务，但当前对象的直接业务依赖仍按现有 CTU/FIFO/同架规则等待；
- 已归档 passage 不参与 RETURN_BUFFER 活动 FIFO；归档后无需清理数据库或 Redis 即可继续新任务；
- 同一任务后续 revision 新增已离场货架的新面时可以创建新的 CTU01 Decision，RCS 保证同架动作串行；
- SCAN1 继续校验计划来源、目标架、入口位置和原 BIN_MOVE 成功；来源架后续离位不反向否定该 Bin；
- DeviceCommand 确定 `FAILED` 保持终态；结果未知通过原 `command_code` 收敛，不形成历史全局 gate；
- 单个 batch WMS confirmation 进入 `RECONCILING` 时不阻塞无关 CTU、drain 或其他 PickingTask；reconciler 只使用原 identity 恢复；
- manual-picking 收到尚未支持的 `added_direct_picks` 时在计划准入阶段明确拒绝，任务中不产生无法执行的 `DirectPickExecution`；
- 五层来源货架所有面完成后先调用 departure decision；WMS 返回 `WAIT` 时不创建 CTU03，返回 `READY` 后使用原样 destination 和 `CTU03` 创建唯一离场 Transport；
- 来源货架和 drain 货架调用 departure decision 时 `task_id=null`；不得填入当前任务、最近完成任务或 drain 触发任务，READY 后仍使用 `CTU03`；
- 目标/转运货架 departure READY 后仍使用 `F01`；WMS 返回 `ZONE` 或 `RACK_POSITION` 均不能改变模板；
- 目标/转运货架调用 departure decision 时必须携带其当前 PickingTask 的真实 `task_id`；缺失、为 `null` 或与冻结任务不一致时拒绝创建 confirmation；
- 仓库中不存在来源架/drain 离场固定 `WH01`、默认离场目的地或绕过 departure READY 的生产创建入口；
- `target_preparation.mode=REPLACE` 不再接受或生成 `rack_destination`；当前目标架通过独立 departure operation 获得唯一目的地；
- 同一 departure READY 只能创建一个稳定 binding/Transport；重复结果幂等返回，目的地漂移或货架/当前位置/当前面不匹配时严格冲突；
- 离场 Transport `ACCEPTED` 后目的地保持冻结；结果未知沿原 identity 收敛，确定 `FAILED` 不改址、不重发替代任务；
- 上述场景均通过正常回调、同 identity 重试或管理恢复入口闭合，不执行 SQL/Redis 清理，不删除 Evidence，不伪造成功；
- 同一取消 identity 重试原样返回，范围漂移返回冲突；
- WMS 不读取 WES 实时进度也能完成取消指令提交，WES 仍保存全部迟到物理事实。

本方案是合同和实施计划，不授权在当前未批准 operation 上直接实现生产代码；实施需按 `wes-implementation` 和项目测试所有权规则另行进入 Execution Lock。

# 10. 实施前测试矩阵（2026-09-17）

```text
WMS issued(workline_code)
├─ [MODIFY] contracts/outbound_picking/test_wire.py
│  ├─ 缺失/额外/非法 workline_code
│  └─ OpenAPI 必填与封闭对象
├─ [ADD] integration/.../test_issued_postgresql.py
│  ├─ WorkLine 不存在/停用/plugin-task_type 不匹配 → 整事务拒绝
│  ├─ WorkLine 忙碌 → 仍入指定线 QUEUED
│  ├─ Evidence → PickingTask → Evidence.picking_task_id 同事务循环引用
│  └─ 并发重放只有一个任务与一个 Evidence
└─ [MODIFY] prepare PostgreSQL/FAST
   ├─ 只领取指定 WorkLine 的任务
   ├─ 其它空闲线不得抢占
   └─ queue_changed 不可改派

WMS cancel(TASK | PLAN_MEMBERS)
├─ [ADD] contracts/.../test_cancel_wire.py
│  ├─ 严格 discriminator 与条件字段
│  ├─ RackFaceSequence/直接取料选择器去重
│  └─ ACK/冲突/额外字段/identity 漂移
├─ [ADD] contracts/.../test_cancel_service.py
│  ├─ QUEUED/PREPARING → CANCELLED
│  ├─ EXECUTING 只能 PLAN_MEMBERS
│  ├─ 任务行锁 + 全量选择器验证 + 条件更新计数
│  └─ 已取消/不存在成员整笔 409，零部分写入
├─ [ADD] integration/.../test_cancel_postgresql.py [→INTEGRATION]
│  ├─ cancel 与 plan_delta 并发
│  ├─ 两个重叠 cancel 并发
│  ├─ cancelled_evidence_id 外键与不可改绑
│  └─ commit failure 无虚假 ACK
├─ [MODIFY] confirmation owner/dispatch
│  ├─ prepare-only CANCELLED owner 仍可派发/保存响应
│  └─ 迟到 plan_delta 拒绝且零激活
└─ [MODIFY] runtime/transport/test_transport_service.py
   ├─ 可证明未发送 → FAILED/TRANSPORT_WITHDRAWN_BEFORE_SEND
   ├─ send_started/claim/Evidence 任一存在 → 拒绝终止
   └─ debug wrapper 复用共享能力且原行为不变

PickingTask 结构化关联
├─ [MODIFY] integration/outbound_picking/test_schema.py [→INTEGRATION]
│  ├─ workline_id NOT NULL、CANCELLED 状态、队列索引
│  ├─ TransportDecisionBinding.picking_task_id + 两条索引
│  ├─ InboundEvidence.picking_task_id 时间线索引
│  └─ 两张成员表 cancelled_evidence_id
├─ [MODIFY] runtime/execution/test_inbound_evidence_service.py
│  └─ 重放任务关联一致；关联漂移 fail closed
└─ [MODIFY] runtime/execution/test_transport_decision_binding.py
   └─ 按任务 join，不再解析 correlation_id 或 JSON

RETURN_BUFFER drain
├─ [MODIFY] contracts/return_buffer_drain/test_contract.py
│  ├─ request 仅 workline_code + required_slot_count
│  ├─ READY.racks[].rack_faces[] 严格、有序、有界
│  ├─ WAIT 禁止 racks，旧单值结构拒绝
│  └─ RackFaceSequence 三处复用
├─ [MODIFY] plugin test_drain_flow/test_drain_repository
│  ├─ 每线唯一 reservation
│  ├─ 同架逐面、跨架 departure ACCEPTED 后进下一架
│  ├─ READY 后 NO_BATCH 推进；首批 NO_BATCH/末面不足进入对账
│  ├─ restart 恢复原 rack/face 顺序
│  └─ UNKNOWN 不释放 reservation
└─ [MODIFY] business_loop/rack_cycle PostgreSQL [→INTEGRATION]
   └─ 多货架、多面、多个 return_batch 与最终 drain 关闭

统一 rack departure
├─ [MODIFY] contracts/outbound_picking/test_departure.py
│  ├─ task_id 必填 nullable 联合
│  ├─ transfer 非空；source/drain 固定 null
│  └─ READY destination 冻结与重放
├─ [MODIFY] test_material_decide.py
│  └─ REPLACE 只保留 mode，rack_destination 旧字段拒绝
└─ [MODIFY] plugin source/drain/transfer progression
   ├─ 全部离场必须有 matched departure READY Evidence
   ├─ SOURCE/DRAIN → CTU03，TRANSFER → F01
   └─ 固定 WH01、猜测 destination、重复离场零路径
```

关键生产失败模式与验证：

| 失败模式 | 自动处理 | 测试 owner | 可见性 |
| --- | --- | --- | --- |
| issued 指向错误或停用 WorkLine | 整事务拒绝，不创建任务 | issued PostgreSQL | WMS 收到确定冲突 |
| cancel 与 plan_delta 同时到达 | PickingTask 行锁串行，只有一个完整结果 | cancel PostgreSQL | ACK 或 409，不静默 |
| 本地 Transport 正在被 worker 领取时取消 | claim/send_started 围栏拒绝本地终止，原 identity 收口 | Transport FAST + cancel integration | Evidence/状态可查 |
| PREPARING 取消后 prepare 迟到响应 | 允许保存 prepare 结果，禁止激活和计划应用 | confirmation owner + plan_delta | 取消和迟到 Evidence 均可查 |
| drain 返回总容量不足 | 首批/末面合同检查进入对账，不自行改选 | drain plugin + WMS fixture | 明确冲突/对账 |
| 跨架时旧架结果迟到 | departure ACCEPTED 后下一架可排队，旧架结果按原 identity 应用 | rack cycle PostgreSQL | 不阻塞独立新架 |
| Evidence/Binding 查询关联漂移 | FK/摘要/owner 校验 fail closed | schema + service tests | 冲突证据可查 |
| deployment migration 与旧进程混跑 | 原子发布、停旧进程、迁移、启动新进程；失败回滚制品并清理开发数据重建 | 部署演练 | 启动门禁失败而非静默运行 |

测试所有权保持分层：核心测试不导入具体插件；插件测试不证明 WMS wire、Transport 基础可靠性或 migration。真实 PostgreSQL、Redis/worker
只在 selector 命中且环境就绪时运行，skip 不算通过证据。

## GSTACK REVIEW REPORT

评审日期：2026-09-17
评审范围：`develop@df284a1` 当前全部 Markdown 变更，并核对对应生产模型、Service、Repository、SDK、Transport 状态机和测试 owner。
评审方式：本地 Architecture / Code Quality / Test / Performance 全量评审；Claude Code 未认证，外部覆盖降级为独立 Codex reviewer。

| Review | Trigger | Why | Runs | Status | Findings |
| --- | --- | --- | --- | --- | --- |
| Eng Review | `/plan-eng-review` | 架构、数据流、测试与性能 | 2 | CLEAR | 本轮 11 项问题全部折叠，0 unresolved |
| Outside Review | Codex in-host fallback | 独立查漏 | 1 | completed, outside unavailable | 7 项发现，全部采纳或以更小方案收敛 |
| CEO Review | prior gstack record | 范围与策略 | 1 | prior | 保持单一合同、分阶段实施 |
| Design Review | N/A | 后端合同与执行，无 UI | 0 | skipped | 不适用 |
| DX Review | N/A | 非开发者产品入口 | 0 | skipped | 不适用 |

**OUTSIDE COVERAGE:** Claude Code authentication unavailable；独立 Codex reviewer 完成只读挑战，不构成跨模型覆盖。
**VERDICT:** ENG CLEARED — 方案已统一真源、闭合高风险边界，可以进入实施。

### 本轮已折叠发现

1. 权威合同与方案中的 `issued.workline_code`、cancel union、drain request/response 相互冲突：已统一全部当前真源。
2. PLAN_MEMBERS 缺少可查询取消边界：两张计划成员表增加单向 `cancelled_evidence_id`。
3. PREPARING 取消后的 prepare Confirmation owner 不闭合：prepare-only 允许 `CANCELLED` 可靠派发/保存，业务激活仍 fail closed。
4. 货架离场存在固定 WH01、material response 与 departure 三个权威来源：全部统一到 `departure_decide`。
5. 本地未发送 Transport 无通用安全终止：提炼中立 finalize-unsent，使用 Transport 行锁、claim/send/Evidence 围栏。
6. drain 多货架计划缺容量关联和面结束信号：增加每线唯一 reservation，并以 READY 后 NO_BATCH 推进面。
7. drain 数组无规模边界：数量受 WorkLine RETURN_BUFFER 配置约束，总面数不超过 required count。
8. rack/face DTO 可能重复定义：新增纯 `RackFaceSequence`，不复用带 Evidence/revision 的业务计划对象。
9. PickingTask 排查依赖 JSON/correlation 前缀：增加 InboundEvidence 与 TransportDecisionBinding 的本地任务外键和精确索引。
10. 外部优化误把 FAILED 改为可恢复、删除直接业务门禁并弱化 SCAN1：已撤回；确定终态保持，只有 UNKNOWN/RECONCILING 收敛。
11. 旧任务图未覆盖统一离场、关联迁移和部署顺序：已重写实施任务与测试矩阵。

### NOT in scope

- 不增加旧 wire、别名、兼容 parser、双路径或旧数据迁移；联调数据可清理后在新 schema 重建。
- 不让 WMS 订阅 WES 实时执行进度；取消 ACK 只证明取消事实已可靠提交。
- 不主动撤销 RCS/ECS 已接纳动作，不换 identity，不伪造物理成功。
- 不把 PickingTask 外键复制到共享 `transport_tasks`、成员或位置投影。
- 不新增通用工作流、动态 operation registry、通用 owner 字符串或第二套 outbox。
- 不把确定 `FAILED` 改造成可恢复状态；RCS/ECS 仍会自动重试时不得提前报告 FAILED。
- 不使用 CI 部署；本次使用本地门禁和联调服务器直接发布、验证。

### What already exists

- `InboundEvidenceService.accept` 已提供先持久化、内容摘要、重复重放和冲突证据。
- `PickingTaskIssuedService`、`PickingTaskPrepareCoordinator`、`lock_task_identity` 和 skip-locked 查询可直接收窄到指定 WorkLine。
- `WmsConfirmation` 已支持 PickingTask/WorkLine owner、可靠派发、迟到响应和原 identity 重试。
- `TransportDecisionBinding` 已提供 Decision → client_request_id 稳定映射，只需补本地任务外键。
- Transport 已有 debug 专用“可证明未发送才终止”判定，可提炼为中立基础能力。
- `return_batch`、drain、departure、material decision 和 manual-picking rack cycle 已有完整骨架，实施是直接替换合同而非重造流程。

### 最终数据流

```text
WMS issued(task, workline)
        │
        ▼
Evidence ──same tx──> PickingTask(QUEUED, workline_id NOT NULL)
        │                         │
        │                    assigned-line prepare
        │                         ▼
        │                    PREPARING ──plan_delta──> EXECUTING
        │                         │                        │
        └── cancel(TASK) ─────────┘                        ├── cancel(PLAN_MEMBERS)
                                                         │      └─ cancelled_evidence_id
                                                         ├── rack/bin actions
                                                         └── RETURN_BUFFER
                                                                │
                                              drain(workline,count) → racks[].faces[]
                                                                │
                                              return_batch + departure_decide
                                                                ▼
                                                        authoritative physical facts

Task diagnostics:
PickingTask
├─ InboundEvidence.picking_task_id
├─ WmsConfirmation.picking_task_id
└─ TransportDecisionBinding.picking_task_id
   └─ TransportTask.client_request_id
      └─ Transport Evidence.transport_task_id
```

### Failure modes

| Failure | Required behavior | Verification |
| --- | --- | --- |
| issued WorkLine invalid | 整事务拒绝，零 PickingTask/成功 ACK | PostgreSQL integration |
| cancel 与 plan_delta 并发 | PickingTask 行锁串行，零部分更新 | concurrent integration |
| cancel 与 Transport claim 竞态 | Transport 行 CAS；已 claim/send/evidence 时不终止 | Transport FAST + integration |
| PREPARING 取消后 prepare 迟到 | 保存原 Confirmation 结果，任务不激活，plan_delta 409 | confirmation/plan tests |
| drain 返回容量计划不一致 | 首批 NO_BATCH、越序、末面不足进入对账 | drain contract/plugin tests |
| departure destination 漂移 | 原 READY/binding 冻结，冲突不改址 | departure tests |
| migration/代码混跑 | 原子停服、迁移、启动；失败则旧制品 + 清理开发数据重建 | deploy rehearsal |

### Worktree 与实施顺序

当前 worktree 已有同范围 staged 文档和外部优化代码，多个 Lane 会同时修改 models、migrations、outbound_picking 和 manual-picking；为保护现场，采用单 owner 顺序实施，不再拆分并行 worktree。

1. 固定变更面、GitNexus impact、测试 owner 和 migration head。
2. SDK/wire/模型/migration 原子切片。
3. issued/prepare/cancel 与结构化关联。
4. Transport finalize-unsent 基础能力。
5. drain 多架多面与 reservation。
6. departure 统一和 material REPLACE 收口。
7. manual-picking 调度、取消、诊断和恢复消费。
8. 聚焦 FAST → PostgreSQL/worker HEAVY → QUALITY → staged selector。
9. 联调服务器停旧进程、清理开发数据、迁移、发布、重启和真实接口验证。

### Implementation Tasks

- [ ] **T1 (P1)** — 固定 Execution Lock 清单并完成所有目标生产符号 upstream impact。
- [ ] **T2 (P1)** — 新增 `RackFaceSequence`，直接替换 issued/cancel/drain/departure/material wire 与 SDK 合同。
- [ ] **T3 (P1)** — 生成随机 Alembic revision，修改 PickingTask、计划成员、InboundEvidence、TransportDecisionBinding 及索引。
- [ ] **T4 (P1)** — 实现指定 WorkLine issued/prepare、统一 cancel、PREPARING owner 与迟到 plan fail-closed。
- [ ] **T5 (P1)** — 提炼 Transport finalize-unsent 中立能力并闭合取消/dispatcher 竞态。
- [ ] **T6 (P1)** — 实现 drain 多货架多面、reservation、NO_BATCH 面推进和重启恢复。
- [ ] **T7 (P1)** — 统一所有 rack departure，删除 WH01 和 material REPLACE destination 旧路径。
- [ ] **T8 (P1)** — 更新 manual-picking 调度、查询和完成聚合，保留现有 CTU/FIFO/SCAN1 直接业务门禁。
- [ ] **T9 (P1)** — 完成测试矩阵、迁移链、QUALITY、HEAVY selector 和无旧符号残留验证。
- [ ] **T10 (P1)** — 直接部署联调服务器并验证 migration、API、Celery、WMS/RCS/ECS Mock/真实联调入口。

### Completion Summary

- Step 0: scope accepted as a single reviewed contract with sequential implementation
- Architecture Review: 8 issues found, all resolved
- Code Quality Review: 2 issues found, all resolved
- Test Review: complete execution diagram produced, all gaps assigned to explicit test owners
- Performance Review: 2 issues found, indexes and bounded drain plan added
- NOT in scope: written
- What already exists: written
- TODOS.md updates: obsolete cancellation TODO removed
- Failure modes: 0 unresolved critical gaps in the plan
- Outside voice: Claude Code unavailable; independent Codex reviewer ran and 7 findings were folded
- Parallelization: sequential implementation, no safe parallel worktree split in current dirty workspace
- Lake Score: N/A
- Unresolved decisions: 0

NO UNRESOLVED DECISIONS
