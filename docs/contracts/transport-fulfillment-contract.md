---
title: WES Transport 履约合同
status: ReviewRequired
created_at: 2026-08-07
updated_at: 2026-08-08
scope: Phase 4 AGV 整架搬运、CTU 料箱搬运、Transport Port、WMS 转发 RCS Adapter 与异步运输结果
system_stage: pre_release
migration_strategy: direct_replacement
related:
  - docs/architecture/SRS.md
  - docs/architecture/adr/2026-05-13-wes-wms-rcs-resource-boundary.md
  - docs/architecture/authority-matrix.md
  - docs/contracts/wms-northbound-interaction-contract.md
  - docs/contracts/wms-outbound-picking-task-integration-requirements.md
  - docs/integration/callback_event_validation_principles.md
  - docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md
  - docs/superpowers/specs/2026-08-06-wes-outbound-operation-top-level-design.md
  - docs/superpowers/plans/2026-08-03-wes-architecture-convergence-master-plan.md
---

# WES Transport 履约合同

## 1. 文档定位

本文是 Phase 4 运输履约（transport fulfillment）的唯一评审基线，定义待 WMS/WES 双方冻结的：

- `TransportTask` 与 Transport Port 的职责和首版状态；
- WES 经 WMS 请求 AGV 整架搬运、CTU 架内料箱搬运的提交、同步应答（ACK）、逐箱位置事实和异步终态；
- WMS 事件入口与 Transport evidence 应用端口的分工；
- 幂等、未知结果、冲突、对账和测试所有权。

本文不定义 PickingTask、库存、来源分配、目标分配、业务取消或设备内部动作。WMS 出库业务合同继续拥有这些业务语义；
Phase 3 `WmsClient` 只提供 HTTP/JSON 访问，不拥有 Transport 生命周期。

系统尚未发布，首版直接实现本文目标合同，不保留旧 Effect、status query、callback hint、别名、兼容路径或数据迁移。

## 2. 核心裁决

1. WMS 是搬运对象、来源、目标、优先级和业务授权的唯一权威。WES 只从已经闭合的授权事实创建 `TransportTask`。
2. AGV 的搬运对象只能是完整货架；CTU 的搬运对象只能是货架内料箱。一个 `TransportTask` 只能属于其中一种请求类型，
   不得同时包含 Rack 和 Bin。
3. WES 只表达供应商无关的履约需求，不选择 RCS、具体 AGV/CTU、车辆、路径、交通策略或设备内部步骤。
4. 当前产品只实现 WMS 转发 RCS Adapter。WES 核心不关心 WMS 后方由 WMS、RCS、MCS 或其他系统实际承接。
5. WES 先持久化 `TransportTask`，再通过 Transport Port 提交。同步 ACK 只表示 WMS 已可靠接纳，不表示开始或完成。
6. CTU 每个冻结成员只接收 `SOURCE_PICKED` 和 `TARGET_PLACED` 两个会改变料箱位置的标准里程碑；它们更新位置投影，
   但不把批次任务推进为终态。导航、升降、接近目标等 CTU 内部阶段不进入 WES Transport 合同。
7. 运输执行终态只由匹配的权威异步 `TransportResult` 推进。首版不提供主动状态查询、callback hint 或轮询 worker。
8. Transport evidence 复用 `/api/v1/wms/events` 的持久化后应答能力，但必须分发给独立的 Transport evidence 应用端口。普通 WMS
   业务事件不能终结 `TransportTask`。
9. 结果超过合同截止时间仍未到达时进入 `RECONCILING`。WES 不自动判失败、不换幂等身份重提，也不重放物理动作。
10. 首版没有已批准的取消 wire，因此不提供 `cancel()`、`CANCEL_REQUESTED` 或 `CANCELLED`。真实合同获批后另行扩展。
11. 首版同一运输资源不允许存在重叠的非终态 TransportTask。Rack 请求绑定其 `rack_id`；BinBatch 除绑定全部 `bin_id`，
   还绑定所有来源/目标 `RACK_BIN_SLOT.rack_id`。WES 构造带资源类型的唯一资源键集合，去重后按稳定顺序取得数据库活动
   绑定并失败关闭，避免同批次共享货架时自冲突、AGV 搬架与 CTU 操作同架料箱并发，以及晚到旧结果覆盖新任务位置。

## 3. 权威与边界

| 事实或动作 | 唯一 owner | WES 边界 |
| --- | --- | --- |
| 搬运对象、来源、目标、业务资格和优先级 | WMS | 只消费已批准且可关联的封闭事实 |
| 运输需求身份与等待状态 | `TransportTask` | 持久化请求快照、ACK、Transport evidence 和对账状态 |
| 货架物理搬运、车辆、路径、排队和交通 | RCS/AGV | WES 只提交 `RackTransportRequest`，不读取或调度具体车辆 |
| 货架内料箱物理搬运、路径和设备内部步骤 | RCS/CTU | WES 只提交 `BinBatchTransportRequest`，不控制 CTU 内部动作 |
| HTTP/JSON 单次访问 | Phase 3 `WmsClient` | 不持久化、不重试、不解释业务结果 |
| WMS 转发 wire 翻译 | WMS 转发 RCS Adapter | 固定 operation、DTO 和错误映射，不拥有任务生命周期 |
| 运输事实接收与推进 | `TransportEvidence` + `TransportEvidenceService` + `TransportTask` owner | ACK 前可靠持久化；按闭集 operation 校验后由唯一 owner 应用 |
| 位置与对象投影 | Transport projection writer | 按匹配的成员位置事实或终态更新；位置未知时显式标记 unknown |

架构基础能力与业务能力必须分开：出站 HTTP、入站持久化和幂等是基础能力；货架补给、料箱投放/回收、换面和具体
PickingTask 场景是业务消费者。任何一方的测试都不能替代另一方。

## 4. 首版 Transport Port

### 4.1 端口操作

首版只提供：

```text
submit(request) -> TransportSubmitAck
```

每次调用只执行一次有界发送。Transport Port 不打开数据库事务，不拥有 retry/backoff、轮询、状态查询、任务领取或业务
决策。`TransportTask` owner 在提交前完成持久化，并根据调用事实和 ACK 更新自身状态。

### 4.2 请求类型

首版只有两个类型化请求：

| 请求 | 用途 | 最小冻结事实 |
| --- | --- | --- |
| `RackTransportRequest` | AGV 搬运完整货架；适用于五层货架、单层货架、目标转运货架、退料货架或空架的整体搬运/换面 | `transport_task_id`、不可变版本、WMS `authority_refs`、`action=MOVE|ROTATE`、`rack_id`、WMS 给出的货架类型、来源、目标；`ROTATE` 额外冻结 `target_face=A|B` |
| `BinBatchTransportRequest` | CTU 搬运货架内料箱；一个批次冻结一个或多个料箱，完成货架与约定交接位置之间的投放、回收或搬运 | `transport_task_id`、不可变版本、WMS `authority_refs`、`action=MOVE`、冻结成员、每个成员的 `bin_id`、来源和目标 |

空架、五层货架、单层货架和目标架是请求属性，不增加具名 Port 方法。料箱投放和回收由来源与目标方向表达，不增加
`put_bin()`、`return_bin()` 等业务方法。业务所谓“满箱交换”在 Transport 合同中只是一个
`BinBatchTransportRequest`：两个或多个成员中至少一对具有相反的来源/目标，仍是普通 `MOVE` 批次；Transport 不接收
满箱/空箱分类，也不增加 `EXCHANGE` action、独立 Port 方法或成员子任务。
`ROTATE` 的来源和目标业务位置相同，`target_face` 必须与当前已确认工作面不同。

请求联合类型本身就是搬运能力判别：Rack 请求只进入 AGV 搬运链，BinBatch 请求只进入 CTU 搬运链。首版不再增加
`device_type`、`vehicle_type` 或供应商字段；Adapter 必须拒绝 Rack/Bin 混装、AGV 搬运 Bin 或 CTU 搬运 Rack 的 Payload。

### 4.3 Transport locator

首版只允许三个闭集 locator：

| locator | 必填事实 | 允许用途 |
| --- | --- | --- |
| `RACK_POSITION` | `location_code` | Rack 请求的来源或目标，例如存储位、`STATION_A`、`STATION_B`、`FIVE_STATION` |
| `RACK_BIN_SLOT` | `rack_id + slot_id` | BinBatch 成员位于货架内的来源或目标；这里是整箱储位，不是箱内物料料格 |
| `HANDOFF_POSITION` | `location_code` | BinBatch 成员与滚筒线等消费者交接的入口或退料位置 |

Rack 请求的来源和目标只能是 `RACK_POSITION`。BinBatch 每个成员的来源和目标只能是 `RACK_BIN_SLOT` 或
`HANDOFF_POSITION`，且必须至少一端是 `RACK_BIN_SLOT`。Transport 核心不计算缓存容量、CTU 背篓容量、可用料箱数或空储位；
业务消费者/WMS 先完成 `min(...)` 准入和目标授权，再提交完整冻结成员。开放位置字典、拼接字符串 locator 和从 `bin_id`
反推货架储位均失败关闭。

请求不携带车辆、路径、供应商、RCS Endpoint、设备内部步骤、任意扩展字典或可由 WES 自行决定的替代目标。
`authority_refs` 是非空且无重复的 WMS 决定/事件身份列表，必须共同覆盖本次来源、目标和 action。普通 MOVE 通常只引用
启动决定或来源恢复事件；目标架 ROTATE 同时引用启动决定和本次逐盘目标面决定。WES 不从对象 ID 反推运输目标。

创建 BinBatch 时，WES 在取得全部活动绑定的同一事务内，把每个成员由 WMS 授权的冻结来源保存为该任务不可变的
`AT_SOURCE` 基线及 authority evidence。`SOURCE_PICKED` 只校验这个任务级基线，不要求它等于旧 Transport 全局投影；
这样滚筒线等外部 owner 移动料箱后，反向 CTU 任务仍可从新的授权交接位置开始。WorkLine/Device 不写
`TransportPositionProjection`，Transport 也不猜测交接后的内部位置。

### 4.4 提交 ACK

WES 使用固定 operation 向 WMS 提交：

| 方向 | 方法与路径 | operation | 模式 |
| --- | --- | --- | --- |
| WES → WMS | `POST {{WMS_BASE_URL}}/api/v1/wes/transport-requests` | `transport.task.submit@v1` | 不可变请求 + 同步 ACK |

请求沿用 WMS 北向统一信封：`request_id`、`operation`、UTC Unix 毫秒 `timestamp` 和闭集 `data`。业务幂等身份是
`transport_task_id + request_version`；`request_id` 只用于 HTTP 关联。

ACK 结果是闭集：

| HTTP / `code` | 含义 | `TransportTask` 处理 |
| --- | --- | --- |
| `202 / RECEIVED` | WMS 首次可靠接纳不可变请求 | `PENDING` 或仅因提交交付未知进入的 `RECONCILING` → `ACCEPTED` |
| `200 / DUPLICATE` | 相同身份和相同 Payload 已接纳 | 返回首次接纳事实；`PENDING` 或仅因提交交付未知进入的 `RECONCILING` → `ACCEPTED` |
| `409 / CONFLICT` | 相同身份对应不同 Payload | 保存冲突并进入 `RECONCILING` |
| `400|422 / REJECTED` | 合同或业务准入拒绝，确认未接纳 | `PENDING` 或仅因提交交付未知进入的 `RECONCILING` → `REJECTED` |
| `429 / BUSY`、`503 / UNAVAILABLE` | 本次确认未接纳 | 保留原身份；是否同 Payload 重提只由本合同批准的提交策略执行 |
| 响应未知或传输结果为 `DELIVERY_UNKNOWN` | 可能已接纳 | 保留原身份并进入 `RECONCILING`，不得换身份重提 |

同身份重提只允许完全相同的信封语义和 Payload。WMS 必须原子保存幂等身份、规范化摘要和首次 ACK；相同身份不同 Payload
必须稳定返回冲突。Adapter 不根据自由文本、远端内部状态或未登记响应推断接纳结果。
仅因提交交付未知进入 `RECONCILING` 时，可靠 owner 使用相同 `transport_task_id + request_version` 和相同 Payload 重提，
以取得确定 ACK；这不会重放已知已接纳的物理动作。回调超期、结果冲突或物理结果不确定形成的 `RECONCILING` 不得由提交 ACK 关闭。

## 5. 运输事实回调

### 5.1 固定入口

WMS/RCS 把会改变搬运对象位置的事实和最终结果归一化后，通过现有 WMS Event 入口发送：

| 方向 | 方法与路径 | operation | 模式 |
| --- | --- | --- | --- |
| WMS → WES | `POST {{WES_BASE_URL}}/api/v1/wms/events` | `transport.task.member_position_changed@v1` | CTU 逐箱位置事实 + 同步 ACK |
| WMS → WES | `POST {{WES_BASE_URL}}/api/v1/wms/events` | `transport.task.resulted@v1` | 可靠 Event + 同步 ACK |

这不是普通 PickingTask 事件。唯一 WMS event route 只负责统一信封和按固定 operation 静态分发；Transport 专用
`TransportEventHandler` 负责 DTO 转换并调用 `TransportEvidenceService` 完成幂等和持久化后应答。Phase 4 不注册第二条
同 method/path route；只有 `TransportTask` owner 可以推进任务状态和相关投影。

### 5.2 CTU 成员位置事实

`TransportMemberPositionChanged` 至少冻结：部署级唯一 `event_id`、`transport_task_id`、`request_version`、`bin_id`、
发生时间、闭集里程碑 `SOURCE_PICKED | TARGET_PLACED`，以及闭集结果 `CONFIRMED | POSITION_UNKNOWN`。

- `SOURCE_PICKED + CONFIRMED`：只允许把成员从当前任务的不可变 `AT_SOURCE` 基线推进为 `ON_CARRIER`；不得携带 locator。
- `TARGET_PLACED + CONFIRMED`：必须携带闭集 `TransportLocator`，且必须等于请求冻结目标，才能推进为 `AT_TARGET`。
- `POSITION_UNKNOWN`：不得携带成功位置，不应用成功里程碑；位置投影标记为 `UNKNOWN`，任务进入
  `RECONCILING + POSITION_UNKNOWN`。

确认里程碑按 `SOURCE_PICKED < TARGET_PLACED` 单调应用：重复事实幂等；倒序迟到事实只留 evidence，不回退投影；目标
locator 与冻结目标不同或成员不属于冻结批次时进入冲突对账。批次终态先到时，后到成员位置事实同样只留 evidence，
不覆盖终态投影。
确认里程碑应用后 TransportTask 保持 `ACCEPTED`；`POSITION_UNKNOWN` 按前述规则进入 `RECONCILING`。两者都不为成员
增加第二套状态机，也不增加任务级 `ACTIVE`。

CTU 导航、升降、到达区域、机械状态等不改变料箱位置的内部阶段由 WMS/RCS 消化，不转发为 WES Transport operation。
这不是丢失现场证据，而是只保留 WES 能据此安全改变对象投影的跨系统事实。

CTU 把料箱可靠放到 `HANDOFF_POSITION` 后，Transport 只保留最后 Transport 权威事实。滚筒线继续把料箱移动到 SCAN、
工作位、NG 或退料位置时，由 WorkLine/Device 位置 owner 维护运行期投影；Transport 不猜测这些内部位置。新的 CTU 退箱任务
以 WMS 已授权的 `HANDOFF_POSITION` 为冻结来源，在创建事务中建立新的任务级 `AT_SOURCE` 基线并重新取得该成员的
Transport 写入权，避免两个 owner 同时写同一投影。

### 5.3 最小终态事实

`TransportResult` 至少冻结：

- 部署级唯一 `event_id`；
- `transport_task_id` 与 `request_version`；
- 闭集终态 `SUCCEEDED | FAILED`；
- 结果发生时间；
- 请求类型对应的对象身份；
- `RackTransportRequest` 成功时的最终位置，以及 `ROTATE` 成功时的最终目标面；失败时携带稳定 `failure_code`，并携带
  已确认的最终位置/工作面或 `position_unknown=true`；
- `BinBatchTransportRequest` 与请求完全一致的冻结成员结果；每个成员结果只能是 `SUCCEEDED | FAILED`，成功成员必须携带
  最终位置，失败成员必须携带稳定 `failure_code`，并携带已确认的最终位置或 `position_unknown=true`；
- 失败时的稳定 `failure_code` 和可选诊断摘要。

结果必须与提交时的 action、对象、来源、目标和冻结成员可关联。批次终态必须覆盖全部冻结成员，不能添加、删除或替换成员，
也不能用 WMS/RCS 内部阶段冒充终态；缺少成员结果时不接受为终态，`TransportTask` 进入 `RECONCILING`。全部成员成功时
批次为 `SUCCEEDED`；至少一个成员明确失败、全部成员结果完整且位置均已确认时，批次为 `FAILED`。任何成员位置未知时，
对应位置投影标记为 unknown，任务进入 `RECONCILING`。已确认的最终位置按成员事实更新，不能因批次整体失败而忽略已搬运
成员，也不能把失败解释为仍在原位置。成员结果只描述本批次最终事实，不建立成员级第二套 Transport 生命周期。

终态可以把 `ON_CARRIER` 成员推进到已确认最终位置，但不能否定已经接受的 `TARGET_PLACED`。例如先确认成员已放到冻结目标，
随后终态却报告成员仍在来源，这不是普通失败，而是 `EVIDENCE_CONFLICT`；WES 保留双方 evidence、进入 `RECONCILING`，
不选择任一事实覆盖位置。

具有相反来源/目标成员的普通 `MOVE` 批次，只有在全部冻结成员到达各自目标时才为 `SUCCEEDED`。结果完整且位置明确的
部分失败为 `FAILED`；任一成员位置未知时进入 `RECONCILING`。`ROTATE` 失败且最终工作面未知时同样进入
`RECONCILING`，不能假定货架仍保持原工作面。

### 5.4 持久化后应答

固定处理顺序：

1. 校验统一信封、operation、`event_id` 和闭集 DTO；
2. 原子保存幂等身份、operation、规范化摘要和原始 `TransportEvidence`；
3. 持久化成功后返回 `202 / RECEIVED`；相同身份与相同 Payload 返回 `200 / DUPLICATE`；
4. 异步把类型化位置事实或终态交给 Transport evidence 应用端口；
5. `TransportTask` owner 校验任务身份、请求版本、对象和冻结成员；
6. 若任务仍为 `PENDING` 或 `RECONCILING + SUBMIT_DELIVERY_UNKNOWN`，匹配的权威位置/终态 evidence 先在同一事务证明请求
   已被接纳并收敛到 `ACCEPTED`；
7. 唯一 reducer 根据匹配的成员位置事实单调更新投影，或根据匹配的 `TransportResult` 推进执行终态。

WMS 必须可靠保存未获成功 ACK 的结果。无响应、`429 / BUSY` 或 `503 / UNAVAILABLE` 时，使用相同 `event_id` 和相同
Payload 受控重试，直到收到 `RECEIVED` 或 `DUPLICATE`；`400|422 / REJECTED` 停止重试原 Payload，修正后使用新
`event_id` 提交；`409 / CONFLICT` 停止自动重试并进入合同对账。同一 `event_id` 不同 Payload、同一任务不同结果身份产生的
矛盾终态、未知任务或版本不匹配必须失败关闭并保留冲突 evidence，不得覆盖已接受终态。迟到或经
WMS/RCS 核验后重新签发的结果，仍必须作为匹配的 `TransportResult` 经固定入口提交，才能关闭 `RECONCILING`。

位置或终态 evidence 可以先于 submit ACK、提交调用结果写回到达；权威 evidence 本身证明远端已接纳该不可变请求。后到的
`RECEIVED/DUPLICATE` 只补充接纳留痕，不得回退位置或终态；后到且与已接受 evidence 矛盾的拒绝/冲突只记录
`EVIDENCE_CONFLICT` 并告警，不得把已发生的物理事实改写为未接纳。

## 6. TransportTask 状态

| 状态 | 含义 | 允许迁移 |
| --- | --- | --- |
| `PENDING` | 请求快照已持久化，尚未取得确定接纳 ACK 或权威 evidence | `ACCEPTED`、`REJECTED`、`RECONCILING` |
| `ACCEPTED` | WMS 已可靠接纳，等待唯一异步终态 | `SUCCEEDED`、`FAILED`、`RECONCILING` |
| `REJECTED` | 不可变提交已被 `400|422 / REJECTED` 明确拒绝，确认未接纳 | 无 |
| `SUCCEEDED` | 匹配的成功结果已接受 | 无 |
| `FAILED` | 接纳后匹配的权威失败 `TransportResult` 已接受 | 无 |
| `RECONCILING` | 提交结果未知、回调超期或证据冲突，需要消歧；必须记录闭集 `reconciliation_cause` | 仅 `SUBMIT_DELIVERY_UNKNOWN` 可由相同身份/Payload 的确定 ACK 进入 `ACCEPTED` 或 `REJECTED`；`RESULT_DEADLINE_EXCEEDED`、`EVIDENCE_CONFLICT`、`POSITION_UNKNOWN` 仅可由匹配的迟到或重新签发 `TransportResult` 进入 `SUCCEEDED` 或 `FAILED`；人工核验不直接迁移状态 |

首版没有任务级 `ACTIVE`：WES 只消费会改变对象位置的闭集成员里程碑，不消费外部内部进度。也没有
`CANCEL_REQUESTED`、`CANCELLED` 或查询中间态。
`SUCCEEDED` 和 `FAILED` 仅由匹配的权威 `TransportResult` 推进；准入拒绝不得解释为执行失败。

## 7. 超时、恢复与对账

- 提交超时必须区分确认未发送、确认未接纳和可能已接纳。确认未发送，或 `429 / BUSY`、`503 / UNAVAILABLE` 明确本次
  未接纳且合同批准安全重提时，可用原身份、原版本、原 Payload 受控重提；仅因提交交付未知进入 `RECONCILING` 时也用
  同一不可变提交重提，但只用于取得确定 ACK。`400|422 / REJECTED` 已关闭准入，原 Payload 不得重提。
- 已取得 `RECEIVED/DUPLICATE` 后只等待异步结果，不主动查询、不重复提交。
- Transport submit claim 只选择 `PENDING` 的安全 due 项及 `RECONCILING + SUBMIT_DELIVERY_UNKNOWN`；其他对账原因永不进入
  submit claim。内部 claim lease 必须严格大于 WMS Client 最大总耗时与结果写回事务预算之和，不能把普通 lease 到期解释为
  外部未接纳。
- 超过结果 deadline 时进入 `RECONCILING` 并告警；沉默不能解释为成功、失败、取消或未执行。
- 人工核验只能识别现场物理真相，并促使 WMS/RCS 形成或更正匹配的权威 `TransportResult`，再由 WMS 经固定入口补发；
  人工核验结果本身不直接迁移 `TransportTask`。对账不读取旧 Effect 状态、不猜测现场、不自动创建替代任务。
- 晚到或重新签发的匹配 `TransportResult` 可以关闭 `RECONCILING`；已接受终态后的重复结果只幂等留痕，矛盾结果只作为冲突
  evidence 保留，不得覆盖已接受终态。

## 8. 与其他合同的关系

- WMS 出库合同负责 PickingTask、业务决定和事实报告；不得把这些 operation 放入 Transport Adapter。
- WMS 普通业务事件与两个 Transport operation 可以复用同一 HTTP ingress 和基础持久化能力，但必须使用静态 operation
  分发到不同应用 owner。
- 设备统一接口只负责 ECS 设备命令和事件。TransportResult 不使用设备 `task_type`、`command_code` 或设备合同附录。
- 旧 `wms_effect.status_query`、callback hint、operation registry 和轮询 worker 是 Phase 5 删除对象，不是本合同的实现参考。

## 9. 测试所有权与验收

| 测试 | 唯一所有者 | 必须证明 |
| --- | --- | --- |
| `TransportTask` 核心生命周期 | 核心 runtime/transport 测试 | 六态、单 reducer、提交交付未知的确定 ACK 收敛、重复、冲突、deadline 和结果对账；只使用测试内 fake |
| Transport Port 与 WMS 转发 wire | WMS Adapter 合同测试 | 固定路径、operation、请求联合类型、ACK、错误映射和单次发送 |
| Transport evidence ingress | WMS Adapter/入站合同测试 | 两个固定 operation、ACK-after-persist、幂等、冲突、静态 dispatch 和唯一 owner |
| 成员位置 reducer | 核心 runtime/transport + PostgreSQL integration | pick/place 单调、重复、倒序、终态先到、unknown 和投影不回退 |
| 真实 RCS/AGV/CTU 行为 | WMS/RCS 联调验收 | WMS 转发、标准里程碑归一化、可靠回调、真实超时和成员结果 |
| 五层货架、单层货架、空架补给、货架换面、料箱投放/回收/交换 | 对应业务模块或 WorkLine 插件 | 已批准业务事实到 Transport 请求的映射 |

基础 HTTP、WMS Adapter、Transport 核心和业务插件不得相互代测。核心测试不得使用 PickingTask 或具体货架业务成功路径
证明 Transport 生命周期；业务插件测试不得替代 ACK-after-persist、幂等和可靠性不变量。

Phase 4 退出门禁：

1. 本合同的 submit、member position 与 result wire 已由 WMS/WES 双方确认；
2. `TransportTask`、Transport Port、WMS 转发 Adapter 和 Transport evidence 应用端口职责无重叠；
3. 生产代码不存在 Transport 状态查询、callback hint、动态 operation registry、直连 RCS SDK 或取消预留；
4. 核心、Adapter、入站和业务测试所有权互不替代；
5. Phase 4 不接入旧生产 Composition Root；生产切换与旧 owner 删除留在 Phase 5。

## 10. 明确非目标

- Transport 状态查询、进度订阅、轮询、任意内部阶段镜像和通用回调平台；
- 取消、暂停、恢复、改派、换车、改目标或通用补偿；
- WES 直连 RCS/AGV/CTU；
- 车辆、路线、交通、充电、任务拆分和设备内部步骤；
- PickingTask、库存、来源选择、目标分配和 WMS 业务终态；
- 通用 Intent/Effect/Capability、动态 Provider、Service Locator 或配置驱动 operation；
- 兼容旧字段、旧表、旧 API、旧数据或双写/双读。
