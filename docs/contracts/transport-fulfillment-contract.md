---
title: WES AGV/CTU 通用搬运能力合同
status: Approved
created_at: 2026-08-07
updated_at: 2026-08-09
scope: Phase 4 AGV 整架搬运、货架原地换面、CTU 料箱搬运与协调交换
system_stage: pre_release
migration_strategy: direct_replacement
related:
  - docs/architecture/SRS.md
  - docs/architecture/adr/2026-05-13-wes-wms-rcs-resource-boundary.md
  - docs/architecture/authority-matrix.md
  - docs/contracts/wms-northbound-interaction-contract.md
  - docs/contracts/wms-async-callback-envelope-contract.md
  - docs/superpowers/plans/2026-08-08-wes-minimal-platform-capabilities.md
---

# WES AGV/CTU 通用搬运能力合同

## 1. 文档定位

本文是 Phase 4 搬运能力的唯一线上接口评审基线。它定义工作线插件如何调用四个通用搬运方法，以及 WES 如何经 WMS
提交 RCS 搬运请求、接收位置事实和异步最终结果。

Phase 4 的目标不是建立通用执行平台，而是让后续工作线插件用简单方法完成：

- 搬运一个指定货架；
- 指定货架原地换面；
- 搬运一个或多个指定料箱；
- 在一个协调任务内交换 1～2 对指定料箱。

本文不定义空货架、空料箱、可用储位或业务资格的选择。WMS 或工作线插件先完成选择，再把确定对象、来源和目标交给
Phase 4。

术语约定：调用方（caller）是发起搬运的工作线/工作站；搬运句柄（handle）是创建任务后立即返回的标识；搬运结果
（outcome）是异步通知插件的统一结果；适配器（Adapter）只负责内部对象与 WMS 线上接口合同（wire contract）的转换。

系统尚未发布，首版直接实现本文目标合同，不保留旧 Effect、状态查询、回调提示、别名、兼容路径或数据迁移。

## 2. 权威与职责

| 事实或动作 | 唯一责任方 | Phase 4 边界 |
| --- | --- | --- |
| 空货架、可用料箱、空储位、业务资格和优先级 | WMS/工作线插件 | 只接收已确定结果，不自行查询或选择 |
| 业务步骤顺序和并行关系 | 工作线插件 | 只发布搬运结果，不推进插件业务状态 |
| 搬运调用、可靠任务和本地位置投影 | `TransportService` / `TransportTask` | 持久化并收敛搬运事实 |
| AGV/CTU 调度、车辆、路径和内部动作 | WMS/RCS | WES 不读取或干预 |
| HTTP/JSON 单次访问 | Phase 3 `WmsClient` | 不持久化、不重试、不解释搬运状态 |
| WMS DTO 转换 | `WmsTransportAdapter` | 不访问数据库、不拥有任务生命周期 |
| WMS/RCS 位置与结果证据 | `TransportService.record_evidence()` / `process_pending_evidence()` | 前者只持久化并应答，后者异步幂等应用 |
| 已接纳任务结果超时 | `TransportService.reconcile_overdue_tasks()` | 有界领取超期任务并形成 `UNKNOWN`，不查询或补偿物理动作 |
| 工作线结果通知 | `TransportOutcomePublisher` | 发布统一结果，不动态发现插件 |

WES 当前只经 WMS 转发 RCS，不直连 RCS、AGV、CTU 或 ECS。未来替换接入方时只能替换内部适配器，不改变工作线插件的四个方法。

## 3. 工作线插件公共合同

### 3.1 调用方和幂等

`TransportCaller` 包含：

- `workline_id`：必填；
- `station_id`：可选，用于区分同一工作线的 STATION A/B 等工作站；
- `correlation_id`：可选，用于关联同一次业务流程中的多个搬运任务。

每个方法必须携带唯一 `client_request_id`：

- 相同 `client_request_id` 和相同规范化请求，返回原 `transport_task_id`；
- 相同 `client_request_id` 和不同请求，返回幂等冲突；
- `client_request_id` 是 WES 调用幂等号，不等于每次 HTTP 尝试的 WMS 信封 `request_id`。

方法返回 `TransportHandle(transport_task_id, client_request_id)`。它只证明可靠任务已创建，不证明 WMS 已接纳或物理搬运已完成。

### 3.2 四个方法

```text
move_rack(client_request_id, caller, rack_id, source, target) -> TransportHandle
rotate_rack(client_request_id, caller, rack_id, position, target_face) -> TransportHandle
move_bins(client_request_id, caller, moves) -> TransportHandle
exchange_bins(client_request_id, caller, exchange_pairs) -> TransportHandle
```

#### 搬运货架 `move_rack()`

一次只搬一个确定货架。来源和目标均为 `RACK_POSITION`。货架是单层、五层、空架或目标架不改变方法。

#### 货架原地换面 `rotate_rack()`

一次只处理一个确定货架，位置为 `RACK_POSITION`，目标面为闭集 `A | B`。当前位置或 WMS 最近一次权威结果回传的当前工作面
未知时失败关闭；WES 不从旧数据、目标面或业务流程推断当前面。

#### 批量搬运料箱 `move_bins()`

一次包含一个或多个 `BinMove`：

```text
BinMove = bin_id + source + target
```

来源和目标只能是 `RACK_BIN_SLOT` 或 `HANDOFF_POSITION`，且至少一端是 `RACK_BIN_SLOT`。单次成员数固定为 `1..4`，对应 CTU
背篓最多承载 4 个料箱。调用方提交前按 `min(4, 目标位当前可承接容量, 可搬运料箱数量)` 冻结本批成员；Phase 4 不查询、
计算或预占滚筒线容量，也不选择可用料箱或空储位。

#### 协调交换料箱 `exchange_bins()`

一次包含 1～2 个 `BinExchangePair`：

```text
BinExchangePair = left_bin_id + left_location + right_bin_id + right_location
```

每个交换对的结果是 left bin 到 right location、right bin 到 left location。必须满足：

- 两个料箱不同、两个位置不同；
- 每个位置均为 `RACK_BIN_SLOT`；首版不支持交接位参与交换；
- 同一料箱或位置不得在同一请求中重复；
- 一次调用只生成一个 `TransportTask`、一个 WMS 请求和一个 RCS 协调任务；
- 不携带“满箱/空箱”字段，不由 WES 判断交换资格；
- WES 不拆分请求、不安排 CTU 取放顺序、不创建临时储位。

“一个协调任务”不代表物理动作可以事务回滚。若 CTU 只完成部分动作，最终结果必须逐箱报告已确认位置；任一位置未知时，
任务结果为 `UNKNOWN`，不得伪造整体成功或回到原位。

### 3.3 最小结构校验

Phase 4 只校验搬运合同，不判断空箱、满箱、容量、业务资格、工作站占用或业务顺序：

| 方法 | 失败关闭条件 |
| --- | --- |
| 全部方法 | 标识为空、位置类型或必填字段不符合闭集 |
| `move_rack()` | 来源与目标相同，或来源/目标不是 `RACK_POSITION` |
| `rotate_rack()` | 目标面不在 `A/B`、当前面未知，或目标面等于当前面 |
| `move_bins()` | 成员数不在 `1..4`、重复 `bin_id`、单成员来源与目标相同、重复使用 `RACK_BIN_SLOT` |
| `exchange_bins()` | 交换对数量不是 1～2、料箱或储位重复、位置不是 `RACK_BIN_SLOT` |

多个成员可以使用同一个 `HANDOFF_POSITION`；其容量和排队规则仍由 WMS/工作线插件决定。

### 3.4 位置类型

| 位置类型 | 必填字段 | 用途 |
| --- | --- | --- |
| `RACK_POSITION` | `location_code` | 货架来源、目标和换面位置 |
| `RACK_BIN_SLOT` | `rack_id + slot_id` | 料箱所在货架储位 |
| `HANDOFF_POSITION` | `location_code` | 滚筒线入料口、出料口等 CTU 交接位置 |

不得使用任意字符串、供应商 DTO 或从 `bin_id` 反推位置。

## 4. WMS 提交合同

### 4.1 固定入口

| 方向 | 方法与路径 | operation | 模式 |
| --- | --- | --- | --- |
| WES → WMS | `POST {{WMS_BASE_URL}}/api/v1/wes/transport-requests` | `transport.task.submit@v1` | 不可变请求 + 同步 ACK |

请求使用 Transport 自有的 `request_id + operation + timestamp + data` 固定闭集信封。`request_id` 只关联单次 HTTP；不可变搬运请求的
业务幂等身份是 `transport_task_id`。首版没有请求更新能力，因此不增加永远只能为 `1` 的 `request_version`。请求和响应均为
UTF-8 JSON，Body 上限固定为 `256 KiB`；超限返回 `413 / PAYLOAD_TOO_LARGE`，不得部分处理。

`WmsTransportAdapter` 调用 `WmsClient.post()` 时必须传入 `max_request_body_bytes=256 KiB` 和
`max_response_body_bytes=256 KiB`。`WmsClient` 在统一 JSON 编码后、调用 Phase 2 Transport 前校验请求体字节数，并在内部把
响应上限映射为 `max_wire_bytes=256 KiB`、`max_decoded_bytes=256 KiB` 的 `OutboundHttpResponseLimits`；Phase 4 Adapter
不导入 Phase 2 合同、不复制 JSON 编码，也不绕过 `WmsClient`。

`data` 公共字段固定为：

```text
transport_task_id
kind
caller { workline_id, station_id?, correlation_id? }
```

四种可判别请求的专属字段固定为：

| `kind` | 来源方法 | 关键内容 |
| --- | --- | --- |
| `RACK_MOVE` | `move_rack()` | `rack_id + source + target` |
| `RACK_ROTATE` | `rotate_rack()` | `rack_id + position + target_face` |
| `BIN_MOVE` | `move_bins()` | `moves[1..4] { bin_id + source + target }` |
| `BIN_EXCHANGE` | `exchange_bins()` | `exchange_pairs[1..2] { left_bin_id + left_location + right_bin_id + right_location }` |

公共 `TransportCaller` 可以作为追踪元数据随请求发送，但 WMS/RCS 不得根据它改变已经冻结的对象、来源或目标。
请求不得携带货架类型、空/满箱、容量、车辆、路径、RCS 内部动作顺序或供应商私有字段。

### 4.2 同步 ACK

| HTTP / `code` | 含义 | 内部处理 |
| --- | --- | --- |
| `202 / RECEIVED` | WMS 首次可靠接纳 | `PENDING → ACCEPTED` |
| `200 / DUPLICATE` | 相同身份和 Payload 已接纳 | 收敛到原接纳事实 |
| `409 / CONFLICT` | 相同身份对应不同 Payload | `RECONCILING` 并告警 |
| `400|422 / REJECTED` | 合同、资源或能力拒绝，确认未接纳 | `REJECTED` |
| `429 / BUSY`、`503 / UNAVAILABLE` | 本次确认未接纳 | 原身份按合同受控重提 |
| `DELIVERY_UNKNOWN` | 请求可能已送达 | 原身份进入 `RECONCILING`，不得换身份 |

同步 ACK 只表示接纳，不表示 AGV/CTU 已开始或完成。`TransportHandle` 在本地任务提交后即可返回，因此插件也不依赖同步 ACK。

ACK 使用 Transport 自有的 `request_id + code + message + timestamp + data` 固定响应信封。`data` 只允许：

```text
transport_task_id
reason_code?       # REJECTED 时必填
retry_after_ms?    # BUSY 时必填
```

`BIN_EXCHANGE` 只有在 WMS 确认 RCS 能将 1～2 个交换对作为一个协调任务整体接纳时才返回 `RECEIVED`。不支持时固定返回
`422 / REJECTED / COORDINATED_BIN_EXCHANGE_UNSUPPORTED`；WES 不拆分或降级。

WMS 必须对同一 `transport_task_id` 原子保存规范化 Payload 摘要和首次 ACK。相同身份不同 Payload 必须稳定冲突。

### 4.3 提交可靠性

- `TransportTask.submit_attempt_count` 创建时为 `0`，是单任务发送预算的唯一持久化计数；不从日志、租约或时间戳推算次数。
- 领取任务后、调用 HTTP 前，使用独立短事务原子递增 `submit_attempt_count` 并写入发送开始事实 `send_started_at`；HTTP 在
  事务外执行，结果使用新事务保存。只有尚无 `send_started_at` 的过期领取可以重新领取。
- 已有 `send_started_at` 后 worker 退出或租约过期表示请求可能已送达，任务收敛为 `RECONCILING/UNKNOWN`，不得自动重提；
  不新增任务状态或尝试表。
- 本地领取令牌只隔离 worker 执行权，不作为 WMS 权威准入结论身份。
- worker 写回确定性 ACK 时必须携带实际发送请求的 `transport_task_id + payload_digest`；任一不匹配均失败关闭。
- 已被新尝试替代的旧 worker 不得覆盖新租约，但匹配身份和摘要的确定性 WMS 准入结论仍可单调收敛。
- 已取得 `RECEIVED / DUPLICATE` 后不得再次提交。
- 单次 HTTP 访问硬超时为 10 秒。每个任务最多实际发送 3 次，即 `submit_attempt_count` 只允许从 `0 → 1 → 2 → 3`；达到
  `3` 后不得再次进入发送开始事务。
- 只有确认未送达的 `NOT_SENT` 和明确未接纳的 `429/503` 可以使用原 `transport_task_id + payload_digest` 重提；
  `NOT_SENT/503` 固定等待 2 秒，`429` 只使用 ACK 的正整数 `data.retry_after_ms`，缺失或不是正整数时固定等待 2 秒；
  Transport 合同不使用 HTTP `Retry-After`。
- 只有保存 `NOT_SENT` 或明确未接纳的 `429/503` 时，才在同一事务清除本次 `send_started_at` 并安排下一次固定重提；
  其他结果或进程崩溃不得清除该事实。
- `DELIVERY_UNKNOWN` 永不自动重提；3 次发送预算耗尽后形成
  `REJECTED / TRANSPORT_SUBMIT_RETRY_EXHAUSTED`。
- 不实现指数退避、通用重试框架或可配置策略表。
- 首版只在任务上保留 `submit_attempt_count`，不增加独立提交尝试表、heartbeat、状态查询或通用重试平台。

## 5. WMS 位置与结果回调

### 5.1 固定入口

| 方向 | 方法与路径 | operation | 模式 |
| --- | --- | --- | --- |
| WMS → WES | `POST {{WES_BASE_URL}}/api/v1/wms/events` | `transport.task.member_position_changed@v1` | 逐箱位置事实 + 持久化后 ACK |
| WMS → WES | `POST {{WES_BASE_URL}}/api/v1/wms/events` | `transport.task.resulted@v1` | 最终结果 + 持久化后 ACK |

两类回调均复用 `docs/contracts/wms-async-callback-envelope-contract.md` 定义的 WMS 异步回调统一信封；本 Transport
operation 另外固定 `256 KiB` Body 上限。
`TransportEventHandler.handle(raw_body: bytes)` 在 JSON 解码和 DTO 校验前检查原始请求体长度；超限返回
`413 / PAYLOAD_TOO_LARGE`，不得保存部分 evidence。Phase 4 只交付 Handler，不注册第二条同路径 FastAPI route；未来唯一
WMS Event route 只把原始 bytes 交给 Handler，生产路由接线不属于本阶段，也不构成 Phase 4 的入口或退出条件。

### 5.2 逐箱位置事实

首版只接收两个位置变化里程碑和一个位置未知事实。三个枚举值本身均表示 WMS/RCS 已确认的权威事实，不增加永远只能为
`true` 的 `confirmed` 字段：

- `SOURCE_PICKED`：来源已取出，位置变为 `ON_CARRIER`；
- `TARGET_PLACED`：已放入冻结目标，位置变为 `AT_TARGET`；
- `POSITION_UNKNOWN`：最终位置未知，投影变为 `UNKNOWN` 并进入对账。

`transport.task.member_position_changed@v1` 的 `data` 固定为：

```text
event_id
transport_task_id
bin_id
milestone: SOURCE_PICKED | TARGET_PLACED | POSITION_UNKNOWN
final_position?   # TARGET_PLACED 时必填，且必须等于冻结目标
```

重复事实幂等；倒序事实不得让位置回退。导航、升降、到达区域和机械状态等 CTU 内部阶段不进入 WES Transport 合同。

### 5.3 最终结果

`transport.task.resulted@v1` 的 `data` 固定为：

```text
event_id
transport_task_id
kind
results[] {
  object_id
  status: SUCCEEDED | FAILED
  final_position?
  position_unknown?: true
  failure_code?    # FAILED 时必填
  arrival_face?    # 位置明确的货架结果必填，A | B
}
```

`final_position` 与字面量 `position_unknown=true` 必须严格二选一：位置明确时必须提供 `final_position` 且不得提供
`position_unknown`；位置未知时必须提供 `position_unknown=true` 且不得提供 `final_position`。`position_unknown=false`、两者同时
提供或两者都缺少均为无效 DTO。`SUCCEEDED` 只能使用明确位置；`FAILED` 必须按实际证据在两种位置表达中二选一。

`TransportResult` 必须关联 `transport_task_id` 并完整覆盖请求对象：

- `RACK_MOVE`、`RACK_ROTATE` 的 `SUCCEEDED` 结果必须携带最终位置和到达面 `arrival_face`；
- `RACK_MOVE`、`RACK_ROTATE` 的 `FAILED` 结果在位置已确认时也必须携带该位置的 `arrival_face`；只有
  `position_unknown=true` 时可以不携带；
- `BIN_MOVE` 完整覆盖全部冻结成员及各自最终位置；
- `BIN_EXCHANGE` 完整覆盖全部交换成员及各自最终位置。

每个对象结果只能是 `SUCCEEDED | FAILED`；`SUCCEEDED` 不得携带 `failure_code`，`FAILED` 必须携带稳定 `failure_code`。缺少成员、增加成员、
目标不一致或事实互相矛盾时不得接受为确定终态。

任务结果只按成员事实聚合：全部对象成功且位置明确才是 `SUCCEEDED`；至少一个对象失败、但全部对象位置均明确时是 `FAILED`；
任一对象位置未知时是 `UNKNOWN/RECONCILING`。Phase 4 不把部分成功包装成整体成功，也不根据业务价值修改聚合规则。
WMS 不回传可由 `results[]` 推导的任务总状态，避免总状态和逐对象事实产生双真源。

`arrival_face` 为闭集 `A | B`，是 WMS/RCS 对货架当前到位姿态的权威事实。缺少应有 `arrival_face` 的货架结果不得接受为
确定结果；WES 接受后同步更新本地面向投影，后续 `rotate_rack()` 只能使用该投影校验目标面不同于当前面。

### 5.4 持久化后应答

1. 校验信封、operation、`event_id`、Payload 上限和闭集 DTO；
2. 原子保存 `operation + event_id`、规范化摘要和原始 Transport evidence；
3. 保存成功后返回 `202 / RECEIVED`，同身份同 Payload 返回 `200 / DUPLICATE`；
4. 异步锁定 `TransportTask`，校验不可变任务身份、对象和冻结成员；
5. 在同一事务更新任务、成员、位置投影、evidence 处理状态和待发布 `outcome_version`；
6. 后台有界领取未发布版本，在事务外交给 `TransportOutcomePublisher`，成功后记录已发布版本。

同一 `operation + event_id` 不同 Payload、未知任务、对象/冻结成员不匹配和矛盾终态必须失败关闭并保留冲突证据。

原始 evidence 使用最小处理状态 `PENDING | APPLIED | CONFLICT`。`TransportRepository` 按稳定顺序小批量领取
`PENDING` evidence，并记录领取令牌和租约截止时间；租约过期后允许重新领取，旧令牌不得写回。应用成功标记 `APPLIED`，
无法与冻结任务单调收敛的证据标记 `CONFLICT` 并保留诊断事实。首版只增加待处理索引，不建立通用队列或额外 Service。

`record_evidence()` 只执行上面第 1～3 步，不在 WMS 回调请求中推进任务或发布结果；
`process_pending_evidence(limit)` 只执行第 4～5 步；`publish_pending_outcomes(limit)` 单独执行第 6 步。这样即使进程在应答后退出，
已经持久化的 evidence 仍可由后续批次重领，两个后台入口也不会重复发布结果。

## 6. 插件统一结果

| `TransportOutcome.status` | 形成条件 | 插件语义 |
| --- | --- | --- |
| `SUCCEEDED` | 匹配权威成功结果且最终位置完整 | 可以继续依赖该搬运的业务步骤 |
| `FAILED` | 匹配权威失败结果且相关位置完整 | 搬运失败，但资源位置可判断 |
| `REJECTED` | WMS/RCS 明确未接纳 | 可以修正请求或重新分配 |
| `UNKNOWN` | 交付、结果或任一对象位置不确定 | 停止依赖动作，等待核验 |

结果携带 `transport_task_id`、`client_request_id`、单调递增的 `outcome_version`、`TransportCaller`、稳定结果码和最终位置。
只有 `SUCCEEDED` 可以触发依赖动作。

任务首次进入 `ACCEPTED` 时写入唯一截止事实 `result_deadline_at = 当前时间 + 10 分钟`。无论由同步 ACK 还是先到的位置证据
首次证明远端已接纳，都执行相同写入；重复 ACK、成员位置事实和其他更新不得刷新该字段。若最终结果先到并直接形成确定终态，
无须设置截止时间。到期仍无匹配权威结果时发布 `UNKNOWN / TRANSPORT_RESULT_TIMEOUT` 并保持相关资源绑定；超时只是结果
不确定，不代表物理失败，也不触发自动补偿。

`reconcile_overdue_tasks(limit)` 只按 `result_deadline_at` 和稳定顺序有界领取超过结果截止时间的 `ACCEPTED` 任务，在一个事务内转为
`RECONCILING`、递增 `outcome_version` 并形成待发布结果；它不查询 WMS/RCS、不释放资源，也不直接调用 Publisher。

`UNKNOWN` 对应内部 `RECONCILING`，不是伪造终态。后续 WMS/RCS 提交匹配的权威结果完成消歧时，可以用更高
`outcome_version` 再次发布同一任务的 `SUCCEEDED` 或 `FAILED`；插件必须按 `transport_task_id + outcome_version` 幂等处理，
并允许版本号跳跃。

形成 `UNKNOWN` 或确定结果的事务必须同时递增 `outcome_version`。Transport 后台只领取
`published_outcome_version < outcome_version` 的任务，领取时冻结当前版本、结果快照、领取令牌和租约，在事务外调用
`TransportOutcomePublisher.publish(outcome)`。方法正常返回才表示发布成功；异常或取消均不记账。成功后只有匹配领取令牌才能
推进 `published_outcome_version`，租约过期后允许重新领取。

尚未发布的低版本可以被更高版本合并，系统只保证最新权威结果最终送达，不保证逐个发布中间版本。若低版本已经发布，更高版本
仍会继续发布。发布后、记账前崩溃允许重复通知；首版不建立结果历史表或独立 Outbox 表。

## 7. 内部状态与资源互斥

内部状态仅供 Phase 4 实现使用：

| 状态 | 含义 |
| --- | --- |
| `PENDING` | 可靠任务已创建，尚未取得确定接纳事实 |
| `ACCEPTED` | WMS 已接纳，等待最终结果 |
| `REJECTED` | WMS/RCS 明确未接纳 |
| `SUCCEEDED` | 权威成功结果已接受 |
| `FAILED` | 权威失败结果已接受且位置明确 |
| `RECONCILING` | 交付、结果或位置未知，或证据冲突 |

同一货架或料箱最多属于一个非终态任务。Bin 任务必须绑定每个成员来源和目标 `RACK_BIN_SLOT` 中出现的全部不同 `rack_id`，
同时绑定被搬运的每个 `bin_id`，防止 AGV 搬架与 CTU 在该架取箱或放箱并发。资源键先去重、稳定排序后在一个事务中取得；
只有 `REJECTED / SUCCEEDED / FAILED` 的确定终态事务释放绑定；`RECONCILING` 即使已向插件发布 `UNKNOWN` 也必须继续保持绑定，
直到匹配的权威确定结果完成消歧。资源冲突在创建阶段失败关闭，不等待 RCS 再拒绝。

位置或终态证据可以先于 submit ACK 到达；匹配证据本身可以证明远端已接纳。后到 ACK 只补充接纳事实，不得回退位置或终态。

## 8. 分拣机流程映射

| 业务步骤 | WMS/插件先确定 | Phase 4 调用 |
| --- | --- | --- |
| 补充 1～2 个粗分完成的单层货架 | rack、来源、STATION A/B | 每个货架一次 `move_rack()`，可并行 |
| 补充有可用料箱/料格的五层货架 | rack、来源、FIVE_STATION | `move_rack()` |
| 五层货架到位后投入料箱 | bin、来源储位、滚筒线入料位置 | `move_bins()` |
| 从滚筒线出料口退箱 | bin、出料位置、五层货架空储位 | `move_bins()` |
| 满箱与空箱交换 | 1～2 个确定交换对 | 一次 `exchange_bins()` |
| 货架原地换面 | rack、当前位置、目标面 | `rotate_rack()` |

五层货架只有收到 `SUCCEEDED` 后才能触发投箱。ACK、`ACCEPTED` 或 AGV“已派发”均不能作为到位条件。

## 9. 测试所有权与验收

| 测试 | 唯一所有者 | 必须证明 |
| --- | --- | --- |
| 四个公共方法 | 核心 runtime/transport | 参数、幂等、一个调用一个任务、统一 handle/outcome |
| 任务和资源可靠性 | PostgreSQL integration/transport | 唯一约束、资源互斥、领取和原子结果应用 |
| WMS 提交与回调 DTO | WMS Adapter contract | 固定 path/operation、四种请求、ACK 和事件转换 |
| 协调交换真实行为 | WMS/RCS 联调 | 1～2 对交换、内部顺序、部分失败和最终位置 |
| 分拣机开工顺序 | 分拣机插件测试 | WMS 分配、并行调架、五层货架成功后投箱 |

Phase 4 核心测试不得使用“粗分完成、空货架、满箱、空箱、可用储位”等业务分类证明搬运能力。工作线插件测试使用假的
`TransportService`，不得替代 Phase 4 的数据库、幂等和 WMS 合同测试。

## 10. 明确非目标

- 空货架、空料箱、可用储位和业务资格选择；
- 分拣机、滚筒线、机械臂、扫码和 NG 业务编排；
- WES 直连 RCS/AGV/CTU；
- ECS/DeviceCommand、设备状态和供应商私有协议；
- 车辆、路径、交通、充电、CTU 取放顺序和临时位规划；
- 状态轮询、取消、暂停、恢复、改派和自动补偿；
- 通用 Runtime/Effect、动态 Provider、Service Locator、插件 SDK 或工作流引擎；
- 旧字段、旧表、旧 API、旧数据和兼容路径。
