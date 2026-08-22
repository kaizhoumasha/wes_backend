---
title: WMS / WES Phase 9 人工 Bin 处理集成合同
status: ApprovedForImplementation
implementation_authorization: true
contract_version: "1.0"
audience: WMS、WES、ECS、RCS 开发与联调人员
scope: Phase 9 第一阶段人工入库与人工出库共用的任务调度、Bin 流转和可靠交互
related:
  - docs/contracts/openapi/wes-wms-manual-bin-processing.openapi.json
  - docs/contracts/device-annexes/manual-bin-processing-device-contract.md
  - docs/contracts/wms-outbound-picking-task-integration-requirements.md
  - docs/contracts/wms-northbound-interaction-contract.md
  - docs/contracts/wms-async-callback-envelope-contract.md
  - docs/contracts/transport-fulfillment-contract.md
  - docs/superpowers/specs/2026-08-21-phase9-continuous-business-delivery-resequence-design.md
---

# WMS / WES Phase 9 人工 Bin 处理集成合同

## 1. 批准边界

本文与同级 OpenAPI、设备附录共同批准 Phase 9 第一阶段 `manual_bin_processing` 实施。它统一承载人工入库和人工出库；
`task_kind` 只声明 `MANUAL_BIN_PROCESSING`，不为尚未实施的自动业务预留枚举。

本合同不包含 PDA 页面、物料子任务、库存事务、自动上架/拣货、供应商私有协议、停线排空新协议或历史数据迁移。真实供应商一致性、
现场联调和业务验收仍是独立门禁，不能由本合同批准或本机测试代替。

系统尚未发布。实施直接替换草案和旧 operation，不保留别名、shim、双路径、兼容字段或旧数据迁移。

## 2. 系统所有权与复用边界

| 系统 | 唯一权威 |
| --- | --- |
| WMS | 业务 Task、队列顺序、五层来源货架面、预期 Bin 集合、物料/PDA、精确回库储位、业务释放、业务终态和 PDA 操作员移除确认 |
| WES | WorkLine/Epoch 选择、本地 ManualTask、BinExecution、缓存计数、滚筒线推进、FIFO、Transport/DeviceCommand 组织 |
| ECS | 扫码结果、设备命令 ACK/CALLBACK 和滚筒线物理动作结果 |
| RCS | CTU/货架路径、搬运执行和最终 Transport 结果；Phase 9 只要求整批最终结果 |

Phase 9 必须复用现有基础能力：

- WMS 入站消息使用 `InboundEvidence` 的 `(operation, operation_id)` 去重、冲突和先持久化后 ACK；不新增 Inbox 表；
- WES 出站消息使用 `WmsConfirmation` 的冻结请求、投递、响应和 `RECONCILING`；仅允许其现有
  `material_execution_id` 关联为空，不新增 `WmsExchange`、Outbox 或可靠状态机；
- 搬运复用 `TransportTask` 与 `RACK_MOVE | RACK_ROTATE | BIN_MOVE`，设备动作复用 `DeviceCommand`；
- 运行隔离复用 `LineRunEpoch`，已识别 Bin 的当前位置复用并直接收敛现有 `TransportPositionProjection` 为唯一
  `PositionProjection`；不并行保留两套位置投影；
- `src/app/manual_bin_processing/` 是依赖基础端口的业务应用模块，拥有最小 `ManualTask` 和人工流程 Service；
  `workline_plugins/manual_bin_processing/` 仅拥有类型化 Fact 到封闭 Decision 的纯逻辑映射。基础模块不反向导入两者，插件不访问数据库；
  不新增通用任务基类、工作流 DSL、动态 registry、`ManualBinFlow`、`ManualInboundBatch`、`ManualCtuActionClaim` 或逐 Bin `INGRESS` 实体。

## 3. 公共信封和可靠语义

复用现有公共端点：

| 方向 | 端点 | 用途 |
| --- | --- | --- |
| WMS → WES | `POST /api/v1/wms/events` | Task、计划增量、释放决定、NGZone 操作员移除事实 |
| WES → WMS | `POST /api/v1/wes/decisions` | 准备、批次、路由、回库、货架离场、完成确认 |
| WES → WMS | `POST /api/v1/wes/facts` | 人工工作位到达、回库完成 |

请求顶层严格且仅含 `operation_id + operation + timestamp + data`；响应顶层严格且仅含
`operation_id + code + timestamp + data`。稳定消息身份是 `(operation, operation_id)`，`operation_id` 为 canonical UUIDv7。

- 技术重试必须重发首次冻结的完整消息；不得换号、刷新 `timestamp` 或改字段；
- WMS `WAIT`、`NO_BATCH`、`RACK_FACE_DONE`、`BUSINESS_IN_PROGRESS` 是一次确定业务结果；再次求值必须使用新身份；
- 网络失败、响应未知或 `UNAVAILABLE` 才重试原消息；确定收到但合同不合法时进入 `RECONCILING`；
- Event 首次持久化返回 `202 / RECEIVED`，同身份同消息返回 `200 / DUPLICATE`；
- Decision 首次与重放均返回首次冻结的 `200 / DECIDED`；Fact 首次返回 `RECORDED`，重放返回 `DUPLICATE`。
- 三个端点都使用严格 `REJECTED | CONFLICT | UNAVAILABLE` 错误响应；原因只放在 `data.reason_code`，不返回自由文本
  `message`。无法提取合法 `operation_id` 的 `400` 和请求体超限的 `413` 使用空响应体；未可靠保存时返回
  `503 / UNAVAILABLE`。

## 4. Operation 闭集

| operation | 方向 | 结果 |
| --- | --- | --- |
| `workline.task.issued@v1` | WMS → WES | `RECEIVED | DUPLICATE` |
| `workline.task.queue_changed@v1` | WMS → WES | `RECEIVED | DUPLICATE` |
| `manual.task.plan_delta@v1` | WMS → WES | `RECEIVED | DUPLICATE` |
| `manual.bin.release_decided@v1` | WMS → WES | `RECEIVED | DUPLICATE` |
| `workline.bin.ng_removed@v1` | WMS → WES | `RECEIVED | DUPLICATE` |
| `workline.task.prepare@v1` | WES → WMS | `DECIDED` |
| `workline.bin.inbound_batch_decide@v1` | WES → WMS | `READY | NO_BATCH | RACK_FACE_DONE` |
| `workline.bin.route_decide@v1` | WES → WMS | `MOVE_TOP | MOVE_RIGHT` |
| `workline.bin.return_batch_decide@v1` | WES → WMS | `READY | NO_BATCH` |
| `workline.rack.departure_decide@v1` | WES → WMS | `READY | WAIT` |
| `workline.task.completion_confirm@v1` | WES → WMS | `COMPLETED | PLAN_REVISION_STALE | BUSINESS_IN_PROGRESS` |
| `manual.bin.workstation_arrived@v1` | WES → WMS | `RECORDED | DUPLICATE` |
| `workline.bin.returned@v1` | WES → WMS | `RECORDED | DUPLICATE` |

`transport.task.resulted@v1` 继续由 Transport 合同拥有，本文只消费它，不重新定义。不得新增批次完成、身份不匹配、恢复、
通用状态变更或停线排空 operation。

## 5. ManualTask 与任务调度

`ManualTask` 是人工业务应用模块私有的最小持久对象，只保存：

```text
task_id, queue_revision, dispatch_sequence, not_before,
workline_code, last_applied_plan_revision, state,
source_rack_faces, current_rack_face_progress
```

`state = QUEUED | PREPARING | ACTIVE | COMPLETED`。它不得保存库存、物料、PDA、Bin 当前位置、BinExecution 状态或 Transport 内部状态。
一个人工 WorkLine 同时最多存在一个已准备且未完成的 ManualTask。

### 5.1 `workline.task.issued@v1`

`data` 严格包含 `task_id + task_kind + queue_revision + dispatch_sequence`，可选 `not_before`。约束：

- `task_kind` 固定 `MANUAL_BIN_PROCESSING`；未知值返回 `422`；
- 首次 `queue_revision=1`，`dispatch_sequence>0`；`not_before` 是 UTC Unix 毫秒；
- 不携带 `workline_code`、货架、Bin、物料、容量或 Transport 数据。

### 5.2 `workline.task.queue_changed@v1`

`data` 严格包含 `task_id + queue_revision + dispatch_sequence`，可选 `not_before`。不重复 `task_kind`。`queue_revision` 必须连续加一；
只允许修改尚未准备 Task 的排队字段，已进入 `PREPARING` 的 Task 返回状态冲突。

### 5.3 `workline.task.prepare@v1`

WES 从已到期 Task 中按 `dispatch_sequence ASC, task_id ASC` 选择，并从已激活的 3/4 号人工线中按
`available_since ASC, workline_code ASC` 选线。请求严格包含 `task_id + workline_code`。WMS 成功决定后，WES 原子冻结
`workline_code + 当前 LineRunEpoch`，Task 进入 `ACTIVE`；WMS 不改派 WorkLine。

### 5.4 `manual.task.plan_delta@v1`

`data` 严格包含 `task_id + plan_revision + rack_faces[]`。`plan_revision` 从 1 开始连续加一；每项只含
`rack_id + rack_face`，表示五层来源货架面。不得携带 Bin、储位、物料、工作位或 Transport 计划，也不得修改已接收面。

WES 固定按“当前未完成面 → 同架另一面 → `plan_revision` 和数组顺序”选择下一面。同架换面调用 `RACK_ROTATE`；不同架必须先让
旧架离场，再 `RACK_MOVE` 新架，禁止并行。Transport 回调的实际 `rack_id + rack_face` 才能更新当前位置；位置未知时等待，
不得猜测或增加 WMS 位置查询。

## 6. 货架到位后的两步入站

货架实际到达后，WES 执行两步，不把 WMS 选 Bin 和 Transport 搬 Bin 合并：

1. 计算 `max_bin_count = min(4, configured_ingress_capacity - occupied_count)`；值小于 1 时不请求；
2. 调用 `workline.bin.inbound_batch_decide@v1`，取得同一 `rack_id + rack_face` 的 Bin 后，再创建一次批量 `BIN_MOVE`。

请求 `data` 严格包含 `task_id + workline_code + rack_id + rack_face + max_bin_count`。

| 结果 | 严格字段 | 行为 |
| --- | --- | --- |
| `READY` | `bins[1..max_bin_count]` | 每项含 `bin_id + source`；同一面、Bin/slot 不重复；WES 发起一次 `BIN_MOVE` |
| `NO_BATCH` | 无附加字段 | 当前无可取 Bin；以后以新身份重求值 |
| `RACK_FACE_DONE` | 无附加字段 | 当前面业务完成；WES按固定规则换面或换架 |

`source` 严格为 `{kind:"RACK_BIN_SLOT", rack_id, rack_face, slot_id}`。WMS `READY` 不授权 WES 预建 BinExecution、
逐 Bin INGRESS 位置或业务流对象。

### 6.1 Transport 整批结果与 INGRESS 计数

Phase 9 只要求 RCS 整批完成，WMS 按现有 Transport 合同发送一次 `transport.task.resulted@v1`。WES 只从最终成员结果计算
`SUCCEEDED` 数量：

- 冻结结果成员和状态作为审计参考，不把它们当实际 SCAN1 身份；
- `occupied_count += succeeded_count`；INGRESS 只保存不可变日志和持久计数，不保存成员队列、队首、序号或 PositionProjection；
- SCAN1 指令 `SUCCESS` 后 `occupied_count -= 1`，不在扫码时提前减少；
- SCAN1 先于最终 Transport 结果到达时，可以保存读码并创建可识别 Bin 的 BinExecution，但必须等待结果和计数入账后再路由，
  防止负数；计数不足属于执行冲突，只暂停该 Bin，不停其它线。

## 7. SCAN1：以现场读码创建执行并决定路由

可靠可读的 SCAN1 扫码是正常 `BinExecution` 创建点。`BinExecution` 只拥有 Bin 物理生命周期，不含 `task_id`、物料、PDA、库存或
重复当前位置字段；当前物理位置只写 `PositionProjection`。同一 `bin_id` 已存在其它活动执行时，只围栏受影响 Bin，其他 WorkLine 继续。

处理顺序：

1. 已有单调 `ng_reason_code`：不再访问 WMS，直接 `MOVE_RIGHT`；
2. 当前 WorkLine 有活动 ManualTask：调用 `workline.bin.route_decide@v1`；
3. 当前 WorkLine 无活动 Task：不访问 WMS，按正常 `MOVE_RIGHT → SCAN3 → MOVE_DOWN → SCAN4` 回流。

路由请求严格包含 `task_id + workline_code + bin_id + scan_evidence_id`。WMS 只返回：

- `MOVE_TOP`：当前任务接受该 Bin，进入 SCAN2；
- `MOVE_RIGHT`：不属于当前任务预期集合，但不是 NG，跳过 SCAN2，经 SCAN3/SCAN4 进入本线 RETURN_BUFFER。

WMS 超时、不可用、响应未知或非法属于软硬件故障：受影响 Bin 留在 SCAN1，INGRESS 占用不释放；有明确
`MOVE_TOP | MOVE_RIGHT` 业务结果时分拣机继续运行。ECS 收到 ACK 后自行负责等待和设备内重试，WES 只等待 CALLBACK；仅在确定
未接纳前安全重提同一 DeviceCommand。

## 8. 不可读码与 NG

不可读码本身是可靠物理路由事实，不得用计划身份、Transport 成员或 INGRESS 顺序猜测 `bin_id`：

- SCAN1 不可读：不创建 BinExecution，不访问 WMS，等待 Transport 计数已入账后下发 `MOVE_RIGHT`；首次 SCAN1 指令成功只减少来源线
  INGRESS 计数；
- SCAN3 不可读：继续 `MOVE_RIGHT`；料箱按 1 → 2 → 3 → 4 线向右流转，第四线 SCAN3 再右移进入物理 NGZone；
- 后续任一扫码点首次可靠读出 `bin_id`：在该点创建 BinExecution，设置唯一 `ng_reason_code=UPSTREAM_SCAN_UNREADABLE`，不访问 WMS，
  继续右移；
- 全程不可读：始终不创建 BinExecution，只保留扫码、DeviceCommand 和 NGZone 日志，交人工盘库；不得发明身份。

`BinExecution.ng_reason_code` 可空但单调：首次设置后不能清除或改写；不得新增 NG 表。已标记 NG 的 Bin 到达下游 SCAN1 时直接右移，
不重复 WMS 认证，也不修改下游线 INGRESS 计数。第一次 SCAN3 `MOVE_RIGHT` 成功即确认 NG 路由，后续线只负责向右传递；
`PositionProjection` 仍按后续扫码事实记录实际工位，不能把尚未物理到达的共享 NGZone 写成当前位置。

Bin 到达共享 NGZone 只表示实物已进入人工接管区，不关闭 BinExecution。操作员使用 WMS PDA 扫码并实际取走 Bin 后，WMS 才发送
`workline.bin.ng_removed@v1`；`data` 严格包含 `bin_id + ng_zone_code + scan_evidence_id + removed_at`，不含
`workline_code`。WES 以该不可变操作员事实关闭唯一匹配的活动 NG BinExecution 并清除位置投影；无唯一匹配时记录冲突并交人工盘库，
不停线。匿名不可读流程没有可关闭执行，只记录移除事实。

## 9. SCAN2 人工工作位

只有 `MOVE_TOP` 的 Bin 进入 SCAN2。可靠到位后 WES 发送 `manual.bin.workstation_arrived@v1`，`data` 严格包含
`task_id + workline_code + bin_id + scan_evidence_id`。WMS/PDA 完成物料业务后发送 `manual.bin.release_decided@v1`：

```text
data = task_id + workline_code + bin_id + decision
decision = RETURN | NG
```

WES 根据决定向 SCAN2 下发 DeviceCommand；WMS/PDA 不直接控制 ECS。`RETURN` 进入正常 SCAN3/SCAN4，`NG` 首次设置
`ng_reason_code=BUSINESS_NG` 并向右流转。SCAN2 命令成功表示本 Task 对该 Bin 的本地工作结束，后续滚筒线和回库属于独立物理尾巴。

## 10. SCAN3、SCAN4 与 RETURN_BUFFER

- SCAN3：有 `ng_reason_code` 或不可读时 `MOVE_RIGHT`；否则 `MOVE_DOWN`；
- SCAN4：可靠读码后写入本线 `PositionProjection` 并追加 Epoch 级 RETURN FIFO；若没有活动 BinExecution，则以现场 `bin_id`
  创建普通活动执行、告警并进入同一 FIFO，不新增“恢复执行”子类型；
- RETURN FIFO 按每条 WorkLine 独立，允许跨 Task，成员保留 `bin_id + PositionProjection + entered_at`；回库优先于新入站；
- RETURN 与 INGRESS 不同：RETURN 必须保存逐 Bin 身份和顺序，INGRESS 只保存日志与计数。

回库候选固定取可执行连续前缀：

```text
candidate_count = min(4, actionable_continuous_fifo_prefix_count)
```

不查询 CTU 空闲容量，不跨 WorkLine 组批。`workline.bin.return_batch_decide@v1` 请求包含
`workline_code + return_candidates[]`；每项含 `bin_id + source`。WMS 返回：

- `READY`：只允许候选连续前缀，每项给出精确 `{kind:"RACK_BIN_SLOT", rack_id, rack_face, slot_id}`；WES 创建一次 `BIN_MOVE`；
- `NO_BATCH`：FIFO 保持不变，后续以新身份重求值。

可靠的本线出料口 `BIN_DEPARTED` 立即释放 RETURN_BUFFER 位置；BinExecution 继续活动并由 Transport 管辖。最终 Transport 成功且
WMS 对 `workline.bin.returned@v1` 返回 `RECORDED | DUPLICATE` 后关闭执行。离开前失败保留缓存；离开后失败或位置未知保持执行活动、
缓存已释放，复用 Transport 高 revision 结果和 `RECONCILING`，不新增 WMS 对账接口。

## 11. 货架离场与任务完成

当前货架已无本地未完成依赖且没有未决搬运时，WES 可调用 `workline.rack.departure_decide@v1`。请求包含
`task_id + workline_code + rack_id + current_face + current_location`；`READY` 返回唯一 `rack_destination`，`WAIT` 返回
`retry_after_ms`。WES 只在 `READY` 后创建 `RACK_MOVE`。

WES 发送 `workline.task.completion_confirm@v1`：

```text
data = task_id + workline_code + last_applied_plan_revision
```

| 结果 | 行为 |
| --- | --- |
| `COMPLETED` | ManualTask 进入 `COMPLETED`，本线可准备下一任务 |
| `PLAN_REVISION_STALE` | WMS 重放缺失 plan delta；WES 应用后用新 operation_id 再确认 |
| `BUSINESS_IN_PROGRESS` | 携带 `retry_after_ms`；到期或业务变化后用新 operation_id 再确认 |

任务完成不等待 SCAN3/SCAN4、RETURN Transport 或共享 NGZone 人工接收。新任务开始后，既有 RETURN FIFO 仍保持最高优先级。

## 12. STOP 与最小失败策略

Phase 9 不新增 drain-rack 合同。STOP 只阻止新 Task 准备和新入站批次，不删除活动 BinExecution、FIFO、Transport、DeviceCommand 或可靠 WMS 义务。
当前面无法承接 RETURN 且没有未来货架时，WMS 返回 `NO_BATCH`，FIFO 保持，Epoch 继续 `ACTIVE`。后续确有现场排空需求时另行评审，
不得复用或扭曲 `return_batch`、`rack.departure`。

| 场景 | 最小行为 |
| --- | --- |
| WMS 路由超时/未知 | 当前 Bin 停在 SCAN1；其他线继续 |
| 明确 `MOVE_RIGHT` | 正常旁路，不标 NG，不停线 |
| 可读 Bin 已有其它活动执行 | 围栏该 Bin并告警，不停其它线 |
| Transport 最终结果迟于 SCAN1 | 保存扫码；等待计数入账后再路由 |
| SCAN1/SCAN3 不可读 | 按向右链路进入 NGZone；不猜身份 |
| RETURN 离口后 Transport 未知 | 缓存已释放，执行保持活动并复用 Transport 对账 |
| WMS NG 移除事实无唯一执行 | 保存冲突，人工盘库，不停线 |

## 13. 验收责任分离

- 核心基础测试：BinExecution、PositionProjection、WmsConfirmation 放宽、Transport/Device/Epoch 可靠性；不使用人工业务判定基础能力；
- 业务应用测试：ManualTask、计数、可靠编排和持久化；不替代基础能力测试；
- 插件测试：类型化 Fact 下的路由、不可读链、FIFO 和任务完成 Decision；不访问数据库，也不复制 Transport/Device 内部测试；
- WMS Adapter 测试：本文严格 DTO、operation、响应联合、幂等和冲突；
- 供应商一致性：设备附录的 SCAN1～SCAN4、方向映射、ACK/CALLBACK 和实际事件；
- E2E：只在上述 owner 各自通过后证明人工纵向切片装配；本机 Mock 不等于现场验收。

任何单层绿灯都不能替代其它层，也不能将 Phase 9 后端候选宣称为供应商或业务 RC。
