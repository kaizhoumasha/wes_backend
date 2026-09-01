---
title: WES AGV/CTU 通用搬运能力合同
status: Approved
created_at: 2026-08-07
updated_at: 2026-09-01
contract_version: 0.3.0
implementation_alignment: ALIGNED
scope: Phase 4 AGV 整架搬运、货架原地换面、CTU 料箱搬运与协调交换
system_stage: pre_release
migration_strategy: direct_replacement
related:
  - docs/architecture/SRS.md
  - docs/architecture/adr/2026-05-13-wes-wms-rcs-resource-boundary.md
  - docs/architecture/authority-matrix.md
  - docs/contracts/wms-northbound-interaction-contract.md
  - docs/contracts/wms-async-callback-envelope-contract.md
  - docs/contracts/wms-outbound-picking-task-integration-requirements.md
  - docs/contracts/wms-inbound-putaway-integration-requirements.md
  - docs/superpowers/specs/2026-08-14-wes-wms-transport-dto-design.md
  - ../archive_docs/wes_backend/docs/superpowers/plans/2026-08-08-wes-minimal-platform-capabilities.md
---

# WES AGV/CTU 通用搬运能力合同

## 1. 文档定位

本文是 Phase 4 搬运能力的唯一线上接口评审基线。它定义工作线插件如何调用四个通用搬运方法，以及 WES 如何经 WMS
提交 RCS 搬运请求、接收位置事实和异步最终结果。

本合同生命周期为 `Approved`，内容是已评审的目标接口契约。WES 代码、运行时 OpenAPI、独立 OpenAPI 3.0.3 文件和行为测试
已与本合同对齐；backend `develop@fdfa4725` 与联调部署 revision `e7e3d6af` 具有相同 tree `46d568d1`，因此
`implementation_alignment=ALIGNED`。该状态只证明 WES 实现与已部署软件版本；WMS 实现、双方真实联调、供应商一致性、
设备物理和业务验收仍未完成。

Phase 4 的目标不是建立通用执行平台，而是让后续工作线插件用简单方法完成：

- 搬运一个指定货架；
- 指定货架原地换面；
- 搬运一个或多个指定料箱；
- 在一个协调任务内交换 1～2 对指定料箱。

本文不定义空货架、空料箱、可用储位或业务资格的选择。WMS 或工作线插件先完成选择，再把确定对象、来源和目标交给
Phase 4。

术语约定：调用方（caller）是发起搬运的工作线/工作站；搬运句柄（handle）是创建任务后立即返回的标识；搬运结果
（outcome）是异步通知插件的统一结果；适配器（Adapter）只负责内部对象与跨系统线上接口契约（wire contract）的转换。

系统尚未发布，首版直接实现本文目标合同，不保留旧 Effect、WMS/RCS 状态查询、回调提示、别名、兼容路径或数据迁移。
WES 可以提供本地 TransportTask 运维观察接口；该接口不进入 WMS/RCS 对接合同，也不能驱动轮询、取消、重试、状态修改或
业务完成判定。唯一写入例外是数据可丢弃联调环境中的定向清理：操作员仅需指定 `transport_task_id`，即可删除该任务的完整本地
Transport 链路，不以任务状态、`TransportEvidence` 或 outcome 作为阻断条件。删除范围包括 Callback Receipt、Evidence、由该任务
Evidence 产生的位置投影、资源绑定、成员和任务；不扩展到库存、业务单据或其它 TransportTask。该动作不是远端取消或重试，
不得向 WMS/RCS 发送请求，也不能撤销已经发生的物理动作。

## 2. 权威与职责

| 事实或动作 | 唯一责任方 | Phase 4 边界 |
| --- | --- | --- |
| 空货架、可用料箱、空储位、业务资格和优先级 | WMS/工作线插件 | 只接收已确定结果，不自行查询或选择 |
| 业务步骤顺序和并行关系 | 工作线插件 | 只发布搬运结果，不推进插件业务状态 |
| 搬运调用、可靠任务和本地位置投影 | `TransportService` / `TransportTask` | 持久化并收敛搬运事实 |
| AGV/CTU 调度、车辆、路径和内部动作 | WMS/RCS | WES 不读取或干预 |
| HTTP/JSON 单次访问 | Phase 3 `WmsClient` | 不持久化、不重试、不解释搬运状态 |
| WMS DTO 转换 | `WmsTransportAdapter` | 不访问数据库、不拥有任务生命周期 |
| WMS/RCS 位置与结果证据 | `TransportService.record_callback()` / `process_pending_evidence()` | 前者在同一事务持久化 callback receipt 与合法 evidence，后者异步幂等应用 |
| 已接纳任务结果超时 | `TransportService.reconcile_overdue_tasks()` | 有界领取超期任务并形成 `UNKNOWN`，不查询或补偿物理动作 |
| 工作线结果通知 | `TransportOutcomePublisher` | 发布统一结果，不动态发现插件 |

WES 当前只经 WMS 转发 RCS，不直连 RCS、AGV、CTU 或 ECS。未来替换接入方时只能替换内部适配器，不改变工作线插件的四个方法。

## 3. 工作线插件公共合同

### 3.1 调用方和幂等

`TransportCaller` 包含：

- `workline_id`：必填；
- `station_id`：可选，用于区分同一工作线的 STATION A/B 等工作站。

该对象只用于 WES 本地结果路由和运行诊断，不进入 WMS submit 接口契约。

每个方法必须携带唯一 `client_request_id`：

- 相同 `client_request_id` 和相同规范化请求，返回原 `transport_task_id`；
- 相同 `client_request_id` 和不同请求，返回幂等冲突；
- `client_request_id` 是 WES 内部业务调用幂等号，不替代 WMS接口契约的 `operation_id`。

方法返回 `TransportHandle(transport_task_id, client_request_id)`。它只证明可靠任务已创建，不证明 WMS 已接纳或物理搬运已完成。

### 3.2 四个方法

```text
move_rack(client_request_id, caller, rack_id, source, target, target_face, rcs_template_id=F01) -> TransportHandle
rotate_rack(client_request_id, caller, rack_id, position, target_face, rcs_template_id=CTU02) -> TransportHandle
move_bins(client_request_id, caller, moves) -> TransportHandle
exchange_bins(client_request_id, caller, exchange_pairs) -> TransportHandle
```

#### 搬运货架 `move_rack()`

一次只搬一个确定货架。来源和目标均使用 `kind + location_code`，`kind` 只能为 `RACK | ZONE | RACK_POSITION`，且两个位置不能
完全相同。`RACK` 的 `location_code` 表示货架编号，必须等于外层 `rack_id`；`ZONE` 表示区域编号，`RACK_POSITION` 表示精确地码。
`target_face` 是业务调用方冻结的不透明目标面 string；该必填字段拒绝空字符串和 NUL，不限制其它字符内容或长度。货架类型和 string 内容不改变
调用方法，WES 不解释其业务或设备语义。

`RACK_POSITION` 目标要求最终地码等于冻结目标。`RACK` 目标由 WMS/RCS 按冻结的货架编号和 `rcs_template_id` 解析位置；`ZONE`
目标由 WMS/RCS 选址，并确认最终地码属于冻结区域。满足对应目标后，WMS 才能回调 `SUCCEEDED`，并返回实际的精确
`RACK_POSITION(location_code)`。WES 不自行解析宽泛目标，只校验回调结构和 `arrival_face`。

#### 货架原地换面 `rotate_rack()`

一次只处理一个确定货架，位置为精确 `RACK_POSITION`，目标面使用与 `RACK_MOVE` 相同的不透明 string token。内部请求仍使用单个
`position`；Adapter 形成接口契约时
把它同时写入 `source + target`。当前位置或 WMS 最近一次权威结果回传的当前工作面未知时失败关闭；WES 不从旧数据、目标面或
业务流程推断当前面。

货架任务携带真实 `rcs_template_id`。库位到工作位使用 `CTU01`，工作位原地旋转使用 `CTU02`，工作位返回库位使用 `CTU03`；
调用方未指定时，WES 在形成不可变请求前规范化为 `F01`。Wire 始终发送明确值，WES 不根据位置编码反推模板，也不建立模板配置映射。

#### 批量搬运料箱 `move_bins()`

一次包含一个或多个 `BinMove`：

```text
BinMove = bin_id + source + target
```

来源和目标只能是 `RACK_BIN_SLOT` 或 `HANDOFF_POSITION`，且至少一端是 `RACK_BIN_SLOT`。单次成员数固定为 `1..4`，对应 CTU
背篓最多承载 4 个料箱。调用方必须在提交前取得业务 owner 冻结的完整成员和具体来源、目标；Phase 4 不凑批，不查询或
计算 CTU 背篓、滚筒线和货架容量，也不选择可用料箱或空储位。

同一批次可以包含不同端点组，但同一 `rack_id` 在全部来源和目标中只能出现一个 `rack_face`，且该面必须等于 WES 的可信当前面。
不同货架可以使用不同的面 token。需要操作同一货架另一面时，业务 owner 必须先完成独立 `RACK_ROTATE`，再创建新的 Bin 任务。

自动出库中，WES 先提供来源货架实际到达面和已预留的具体 `HANDOFF_POSITION`，由 WMS 形成入站 moves；退箱则只以
`RETURN_BUFFER` 的实际候选触发，由 WMS 在当前工作货架面选择并预留目标 `RACK_BIN_SLOT`。两类决定都在业务层形成后一次
调用 `move_bins()`，不属于 Transport 核心的批次规划职责。

#### 协调交换料箱 `exchange_bins()`

一次包含 1～2 个 `BinExchangePair`：

```text
BinExchangePair = left_bin_id + left_location + right_bin_id + right_location
```

每个交换对的结果是 left bin 到 right location、right bin 到 left location。必须满足：

- 两个料箱不同、两个位置不同；
- 每个位置均为 `RACK_BIN_SLOT`；首版不支持交接位参与交换；
- 同一料箱或位置不得在同一请求中重复；
- 全部位置只允许涉及一个或两个 `rack_id + rack_face` 组；涉及两个组时，每个成员必须跨组移动；只有一个组时，允许在该货架
  当前面的不同精确储位之间交换；
- 展开为成员移动后，每个 `source=S, target=T` 必须且只能存在一个 `source=T, target=S` 的反向成员；两个交换对必须形成
  两个互不重叠的二元闭环，不允许三元、四元或其它环形置换；
- 一次调用只生成一个 `TransportTask`、一个 WMS 请求和一个 RCS 协调任务；
- 不携带“满箱/空箱”字段，不由 WES 判断交换资格；
- WES 不拆分请求、不安排 CTU 取放顺序、不创建临时储位。

“一个协调任务”不代表物理动作可以事务回滚。若 CTU 只完成部分动作，最终结果必须逐箱报告已确认位置；任一位置未知时，
任务结果为 `UNKNOWN`，不得伪造整体成功或回到原位。若业务上两面都需要交换，业务 owner 必须在当前面交换及业务记账全部闭环后，
分别完成所需货架的 `RACK_ROTATE`，再重新决定下一面的交换批次；Transport 核心不提前创建跨面任务。

### 3.3 最小结构校验

Phase 4 只校验搬运合同，不判断空箱、满箱、容量、业务资格、工作站占用或业务顺序：

| 方法 | 失败关闭条件 |
| --- | --- |
| 全部方法 | 标识为空、位置类型或必填字段不符合闭集 |
| `move_rack()` | 来源与目标相同、来源/目标不属于 `RACK \| ZONE \| RACK_POSITION`、`RACK.location_code` 与外层 `rack_id` 不同、`target_face` 不是非空 string，或模板不在闭集 |
| `rotate_rack()` | 位置不是精确 `RACK_POSITION`、`target_face` 不是非空 string、当前面未知、目标面等于当前面，或模板不在闭集 |
| `move_bins()` | 成员数不在 `1..4`、重复 `bin_id`、单成员来源与目标相同、重复使用 `RACK_BIN_SLOT`，或同一 `rack_id` 混用不同面 token |
| `exchange_bins()` | 交换对数量不是 1～2、料箱或储位重复、位置不是 `RACK_BIN_SLOT`、涉及超过两个工作面组、同一货架混面，或不能展开为 1～2 个互不重叠的二元闭环 |

多个成员可以使用同一个 `HANDOFF_POSITION`；其容量和排队规则仍由 WMS/工作线插件决定。

### 3.4 位置类型

| 位置类型 | 必填字段 | 权威来源 | 用途 |
| --- | --- | --- | --- |
| `RACK` | `location_code` | WMS/RCS 货架主数据 | `RACK_MOVE` 来源或目标；值必须等于外层 `rack_id`，由 RCS 解析位置 |
| `ZONE` | `location_code` | WMS/RCS 区域主数据 | `RACK_MOVE` 来源或目标；值表示区域编号，不指定精确地码 |
| `RACK_POSITION` | `location_code` | WMS/RCS 全局货架位置主数据 | `RACK_MOVE` 精确来源或目标、`RACK_ROTATE` 位置和货架最终位置 |
| `RACK_BIN_SLOT` | `rack_id + rack_face + slot_id` | WMS 货架、货架面与储位主数据 | 料箱所在货架储位；`rack_face` 为不透明 string token |
| `HANDOFF_POSITION` | `location_code` | WES 静态工作线拓扑和位置投影 | 滚筒线入料口、出料口等 CTU 交接位置 |

位置联合不得退化为未声明的任意字符串，不得引入供应商 DTO，也不得从 `bin_id` 反推位置。

## 4. WMS 提交合同

### 4.1 部署入口

| 方向 | 方法与路径 | operation | 模式 |
| --- | --- | --- | --- |
| WES → WMS | `POST {{WMS_BASE_URL}}{{TRANSPORT_SUBMIT_PATH}}` | `transport.task.submit@v1` | 不可变请求 + 同步 ACK |

`WMS_BASE_URL` 是不带尾部 `/` 的 HTTP(S) origin；`TRANSPORT_SUBMIT_PATH` 是由部署 profile 提供、最长 2048 个字符、以 `/`
开头且不含 origin、query、fragment、反斜杠或 `.`/`..` 路径段的相对路径。WES 启动时校验并冻结两者，路径大小写保持原样，
修改后必须重启。
该配置只改变 WMS 服务端的部署路由，不改变 HTTP `POST`、`operation`、请求/响应 DTO、ACK 或认证语义。

#### 4.1.1 Transport ID 所有权

| ID | 生成方 | 用途 | 规则 |
| --- | --- | --- | --- |
| `client_request_id` | Transport 的 WES 业务调用方 | 标识一次不可变的本地 Transport 调用 | 首次形成确定 Transport 输入时生成全局唯一 UUIDv7，并与调用方的业务唯一键和完整输入原子持久化；同 ID 同请求返回原任务，同 ID 不同请求冲突；崩溃重放不得换号 |
| `transport_task_id` | WES Transport 服务 | 标识可靠 TransportTask | 首次接纳 `client_request_id` 时生成并持久化；后续提交、ACK、evidence 和 outcome 始终引用原值 |
| `operation_id` | 当前接口契约交互的发起方 | 标识一次 Transport submit 或 WMS evidence 回调交互，并承担协议幂等 | submit 由 WES 在首次形成不可变请求时生成 UUIDv7；每个 evidence 回调由 WMS 生成自己的 UUIDv7；各自重试保持原 ID 和原消息信封，通过 `transport_task_id` 关联同一任务 |

自动出库业务 owner 以 WMS 业务决定 `operation_id + 执行阶段 + 确定成员和方向` 作为业务唯一键，保存它与
`client_request_id`、`transport_task_id` 的一对一执行映射。Transport 只保证可靠搬运对象，不解释 WMS 业务决定，也不复制
业务决定 ID。其他调用方同样按自身业务义务生成全局唯一 `client_request_id`。

Transport 不定义专用 `correlation_id`。单任务诊断使用 `transport_task_id`，单次接口契约交互使用 `operation_id`；一个业务流程与多个
TransportTask 的关系由业务 owner 的执行映射维护。项目中的 `ExecutionCorrelation.correlation_id` 属于运行时跨域关联对象，
不是 Transport DTO 字段。

请求复用 WMS/WES 统一的 `operation_id + operation + timestamp + data` 固定闭集信封。WES Transport 在首次形成不可变提交时
生成并可靠保存 `operation_id`；相同 TransportTask 的安全重提保持原 `operation_id` 和原冻结请求体。请求 `timestamp` 同时写入 UTC Unix
毫秒时间，后续 HTTP 重提保持原值；它只用于审计和诊断，不参与搬运顺序、fencing 或超时判断。首版没有请求更新能力，因此
不增加永远只能为 `1` 的 `request_version`。请求和响应均为 UTF-8 JSON，Body 上限固定为 `256 KiB`；请求 Body 超限时在解码前
返回空响应体 `413`，不得部分处理或猜测 `operation_id`。

HTTP `Content-Type` 必须是 `application/json`；`charset` 可以省略，存在时只能是 UTF-8。`Content-Encoding` 只能缺省或为
`identity`。其它媒体类型、charset 或压缩编码返回空响应体 `400`。JSON 顶层和各专属 `data` 都是严格闭集：拒绝重复 key、
未知字段、大小写近似字段、注释、尾逗号、BOM、`NaN/Infinity` 和隐式类型转换。`operation_id` 只接受小写 canonical UUIDv7；
`timestamp` 只接受 `0..Int64.MaxValue` 的 UTC Unix 毫秒整数，但不以时钟偏差拒绝消息。

任意层级重复 key 都无法形成唯一规范化消息，统一按预关联失败返回空响应体 `400` 且不建立幂等记录；响应分类不得因
`operation/operation_id` 在 object 中位于重复成员之前或之后而变化。

线上合法字段不使用 Float。接收方在身份查询前只需保留可由 .NET `System.Decimal` 无损表示的有限 Float，用于识别 `1.0/1e0`
这类更换消息内容的常见类型错误；`NaN/Infinity` 或超出 Decimal 表示域的 Float 无法形成双方稳定规范化值，统一按预关联空响应体
`400` 且不建立幂等记录。无损必须按原始 number lexeme 与 `decimal.GetBits` 的十进制系数/指数精确比较，不能只依赖
`Decimal.TryParse` 成功；任何舍入或下溢同样返回预关联 `400`。不得以 `double` 的舍入值作为 `message_digest` 真源。

WMS 使用 `operation + operation_id` 作为接口契约幂等身份，并保证一个已接纳的 `transport_task_id` 只绑定一个 submit
`operation_id`。同身份同请求体返回原接纳结果；同身份不同请求体，或同一已接纳 `transport_task_id` 换另一个
`operation_id` 再提交，稳定返回 `409 / CONFLICT`。`400 | 413 | 422` 确认原请求未被 WMS 接纳时，当前 TransportTask 仍按
第 4.2 节进入不可变终态 `REJECTED`，不得修改其提交快照、替换 `operation_id` 或继续引用原 `transport_task_id`。业务仍需搬运
时，调用方修正输入后必须使用新的 `client_request_id` 创建新的 TransportTask。自动出库业务 owner 必须先确认原 WMS 决定
仍然有效；如果修正改变了 WMS 决定的来源、非固定目标或成员，必须先取得新的业务决定 `operation_id`。

无法取得合法消息身份的 `400 | 413` 不建立 WMS 幂等记录。已取得合法 `operation + operation_id` 的 `422` 必须保存规范化摘要和
首次 `REJECTED` 响应；同身份同非法消息信封稳定重放首次拒绝，同身份换内容返回 `409 / CONFLICT`。`503` 是尚未接纳的临时响应，
不建立业务绑定，也不冻结为首次终局响应。WES 已形成的原任务和诊断事实不得被覆盖。

`WmsTransportAdapter` 调用 `WmsClient.post_json_bytes()` 发送 TransportTask 已冻结的最终请求体字节，并传入
`max_request_body_bytes=256 KiB` 和 `max_response_body_bytes=256 KiB`。`WmsClient` 不重新编码该请求体，只在调用 Phase 2
Transport 前校验请求体字节数，并把响应上限映射为 `max_wire_bytes=256 KiB`、`max_decoded_bytes=256 KiB` 的
`OutboundHttpResponseLimits`；Phase 4 Adapter 不导入 Phase 2 合同，也不绕过 `WmsClient`。

`data` 公共字段固定为：

```text
transport_task_id
kind
```

四种 `kind` 只使用两族可判别 DTO：

| DTO 族 | `kind` | 来源方法 | `data` 专属字段 |
| --- | --- | --- | --- |
| `RackTransportData` | `RACK_MOVE` | `move_rack()` | `rcs_template_id + rack_id + source + target + target_face`；`source != target` |
| `RackTransportData` | `RACK_ROTATE` | `rotate_rack()` | `rcs_template_id + rack_id + source + target + target_face`；`source == target` |
| `BinTransportData` | `BIN_MOVE` | `move_bins()` | `moves[1..4] { container_id + source + target }` |
| `BinTransportData` | `BIN_EXCHANGE` | `exchange_bins()` | `moves[2\|4] { container_id + source + target }`，且为 1～2 个二元闭环 |

`RackTransportData.target_face` 由业务调用方传入并随请求冻结，使用不透明 string token。WMS 将该值原样传给 RCS；成功回调中的
`arrival_face` 必须按大小写敏感的 Unicode code point 序列与冻结值精确相等。`RACK_POSITION` 目标还要求最终位置相等。对于 `RACK`
目标，WMS/RCS 必须确认最终位置是按冻结
货架编号和模板解析出的结果；对于 `ZONE` 目标，最终位置必须属于冻结区域。两种回调都返回精确 `RACK_POSITION`。

`rcs_template_id` 只允许 `CTU01 | CTU02 | CTU03 | F01`，分别表示库位到工作位、工作位原地旋转、工作位返回库位和默认模板。
调用方省略与显式 `F01` 在规范化后形成相同请求；模板、位置和面向值全部进入不可变请求快照及摘要。

货架搬运场景固定如下。`RACK` 和 `ZONE` 是宽泛位置选择器；成功结果一律返回实际到达的精确
`RACK_POSITION/location_code`。

| 场景 | `rcs_template_id` | `source.kind` | `target.kind` | 成功结果约束 |
| --- | --- | --- | --- | --- |
| 区域内货架到工作位 | `CTU01` | `ZONE` | `RACK_POSITION` | 等于请求目标 |
| 指定货架到工作位 | `CTU01` | `RACK` | `RACK_POSITION` | 等于请求目标 |
| 精确库位货架到工作位 | `CTU01` | `RACK_POSITION` | `RACK_POSITION` | 等于请求目标 |
| 工作位原地换面 | `CTU02` | `RACK_POSITION` | `RACK_POSITION` | 位置不变，面向等于 `target_face` |
| 工作位按货架编号返回库位 | `CTU03` | `RACK_POSITION` | `RACK` | WMS/RCS 选定的精确库位 |
| 工作位返回指定区域 | `CTU03` | `RACK_POSITION` | `ZONE` | 指定区域内的精确库位 |
| 工作位返回精确库位 | `CTU03` | `RACK_POSITION` | `RACK_POSITION` | 等于请求目标 |
| 其它精确位置搬运 | `F01` | `RACK_POSITION` | `RACK_POSITION` | 等于请求目标 |

上述八种货架场景以及 `BIN_MOVE`、`BIN_EXCHANGE` 的完整提交与结果 JSON，见
[WES 与 WMS 接口需求说明](../integration/wes-wms-interface-requirements.md) 第 3.1 节。

`RACK_ROTATE` 创建任务前，WES 必须确认 `target_face` 不同于可信当前面；WMS 返回 `RECEIVED` 前使用自身权威主数据和可信 RCS
状态再次校验。WMS 无法取得可信当前面时返回 `503 / UNAVAILABLE`，确认 `target_face` 等于当前面时返回
`409 / CONFLICT`，两种情况都不得调用 RCS。

`BinMove.bin_id` 与 `BinExchangePair` 是 WES 内部领域结构；Adapter 形成接口契约时统一输出 `container_id` 和显式 `source + target`。
`BIN_EXCHANGE` 不发送 `exchange_pairs`、left/right 角色或执行顺序。料箱业务载荷数组按 `container_id` 升序输出，该顺序不代表
CTU 物理动作顺序。

所有料箱业务载荷中，同一 `rack_id` 只能出现一个 `rack_face`。WES 在创建任务前使用可信本地面投影校验；WMS 在返回 `RECEIVED`
前以自身权威主数据和可信 RCS 状态再次校验。已知当前面不匹配返回 `409 / CONFLICT`，当前面无法可靠确认返回
`503 / UNAVAILABLE`，两种情况都不得调用 RCS。

`TransportCaller` 不随请求发送。WMS/RCS 只接收已冻结的对象、来源和目标，跨系统诊断使用 `transport_task_id` 和当前
`operation_id`，不复制本地工作线归属字段。
除批准的 `rcs_template_id` 外，请求不得携带货架类型、空/满箱、容量、车辆、路径、RCS 内部动作顺序或供应商私有字段。

### 4.2 同步 ACK

| HTTP / `code` | 含义 | 内部处理 |
| --- | --- | --- |
| `202 / RECEIVED` | WMS 首次可靠接纳 | `PENDING → ACCEPTED` |
| `200 / DUPLICATE` | 相同身份和请求体已接纳 | 收敛到原接纳事实 |
| `409 / CONFLICT` | 相同身份对应不同请求体，或与已接纳不可变状态/活动资源冲突 | `RECONCILING` 并告警 |
| `400`，空响应体 | 不是合法 JSON 或无法提取合法 UUIDv7 `operation_id`，确认未接纳 | `REJECTED` |
| `413`，空响应体 | 原始请求 Body 超限，确认未接纳 | `REJECTED` |
| `422 / REJECTED` | 已有关联身份，但信封、DTO、闭集枚举或固定能力不符合合同 | `REJECTED` |
| `503 / UNAVAILABLE` | 当前无法可靠持久化，或无法取得必须的可信当前面，本次确认未接纳 | 原身份按合同受控重提 |
| `DELIVERY_UNKNOWN` | 请求可能已送达 | 原身份进入 `RECONCILING`，不得换身份 |

同步 ACK 只表示接纳，不表示 AGV/CTU 已开始或完成。`TransportHandle` 在本地任务提交后即可返回，因此插件也不依赖同步 ACK。
搬运提交不提供 `429 / BUSY`，也不定义 `retry_after_ms`。RCS 或 WMS 内部调度容量不足时，WMS 必须先可靠接纳 WES 搬运义务，再在
内部排队；只有无法可靠持久化请求或无法取得必须的可信当前面时才返回 `503 / UNAVAILABLE`。

除预关联的空响应体 `400 | 413` 外，ACK 使用统一的 `operation_id + code + timestamp + data` 固定响应信封。
`operation_id` 必须原样回显当前 submit 的值，包括 `DUPLICATE`。ACK `timestamp` 由 WMS 在首次形成并可靠保存完整应答时
写入 UTC Unix 毫秒时间。首次响应为 `RECEIVED` 后，同一 `operation + operation_id + 请求体` 的幂等重放返回
`DUPLICATE`，同时复用首次应答的 `timestamp + data`，不能刷新业务应答时间或改写业务数据。首次 `REJECTED/CONFLICT` 的
同身份同请求体重试必须原样重放首次响应；`503` 不建立幂等记录。
除 `REJECTED` 在请求中的任务 ID 缺失或非法时可以省略 `transport_task_id` 外，所有带 Body 的搬运提交 ACK 都必须回显本次请求中已解析的
合法 `transport_task_id`，包括活动资源与另一任务冲突的 `409`；不得改为返回占用资源的旧任务 ID。`data` 是严格联合：

- `RECEIVED | DUPLICATE | CONFLICT | UNAVAILABLE`：完整且仅包含 `transport_task_id`；
- `REJECTED`：完整且仅包含 `reason_code`，或 `transport_task_id + reason_code`；只有请求中的任务 ID 缺失或非法时才能省略
  `transport_task_id`。

搬运提交 `REJECTED.reason_code` 只允许 `INVALID_ENVELOPE | UNSUPPORTED_OPERATION | INVALID_DATA |
COORDINATED_BIN_EXCHANGE_UNSUPPORTED`。WES 对这些诊断码执行相同的确定拒绝处理，不按码自动重试或补偿。

`BIN_EXCHANGE` 只有在 WMS 确认 RCS 能将 1～2 个二元闭环作为一个协调任务整体接纳时才返回 `RECEIVED`。不支持时固定返回
`422 / REJECTED / COORDINATED_BIN_EXCHANGE_UNSUPPORTED`；WES 不拆分或降级。

WMS 必须原子保存 `operation + operation_id`、`transport_task_id`、首次接纳语义、业务 `data` 和完整请求消息摘要。
WES 固定保存实际发送的完整 UTF-8 JSON 请求体及其 `request_body_digest`，后续重提必须发送同一字节串，不得重新序列化，
也不得加入 HTTP Header、连接信息等单次访问元数据。相同身份不同消息必须稳定冲突。

活动资源冲突的最小范围为：同一 `rack_id` 或同一内部 `bin_id` 已绑定另一未闭合任务。接口契约 `container_id` 在 Adapter 边界映射为
对应内部 `bin_id`。`RACK_BIN_SLOT` 的精确身份 `rack_id + rack_face + slot_id` 用于请求内位置唯一性、成员目标校验和结果匹配，
不另建活动资源绑定；其所在 `rack_id` 已整体互斥。`HANDOFF_POSITION` 允许由多个任务引用，不能仅因 `location_code` 相同就冲突。
资源只在前一任务取得确定终态或经人工对账关闭后解除。

### 4.3 提交可靠性

- `TransportTask.submit_attempt_count` 创建时为 `0`，是单任务发送预算的唯一持久化计数；不从日志、租约或时间戳推算次数。
- 首次提交前，WES Transport 必须把 submit `operation_id`、`timestamp`、完整 UTF-8 JSON 请求体和
  `request_body_digest` 与 TransportTask 原子持久化；后续领取和重提只读取该不可变快照，不能在 worker 内重新生成线上身份或重新序列化。
- 领取任务后、调用 HTTP 前，使用独立短事务原子递增 `submit_attempt_count` 并写入发送开始事实 `send_started_at`；HTTP 在
  事务外执行，结果使用新事务保存。只有尚无 `send_started_at` 的过期领取可以重新领取。
- 已有 `send_started_at` 后 worker 退出或租约过期表示请求可能已送达，任务收敛为 `RECONCILING/UNKNOWN`，不得自动重提；
  不新增任务状态或尝试表。
- 本地领取令牌只隔离 worker 执行权，不作为 WMS 权威准入结论身份。
- worker 写回确定性 ACK 时必须携带实际发送请求的 `operation_id + transport_task_id + request_body_digest`；任一不匹配均失败关闭。
- 已被新尝试替代的旧 worker 不得覆盖新租约，但匹配身份和摘要的确定性 WMS 准入结论仍可单调收敛。
- 已取得 `RECEIVED / DUPLICATE` 后不得再次提交。
- 单次 HTTP 访问硬超时为 10 秒。每个任务最多实际发送 3 次，即 `submit_attempt_count` 只允许从 `0 → 1 → 2 → 3`；达到
  `3` 后不得再次进入发送开始事务。
- 只有确认未送达的 `NOT_SENT` 和明确未接纳的 `503` 可以使用原 `operation_id + transport_task_id + request_body_digest` 重提；
  两者固定等待 2 秒。Transport 合同不使用 HTTP `Retry-After`。
- 只有保存 `NOT_SENT` 或明确未接纳的 `503` 时，才在同一事务清除本次 `send_started_at` 并安排下一次固定重提；
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
空响应体 `413`，不得保存部分 evidence 或猜测 `operation_id`。Phase 6 已注册唯一 WMS Event 生产 route；route 只负责有界读取、
冻结认证策略检查，并把原始 bytes 交给 Handler，不保留第二入口或旁路持久化。

两类 operation 只允许公共信封中的 `202 / RECEIVED`、`200 / DUPLICATE`、`400` 空响应体、`413` 空响应体、
`422 / REJECTED`、`409 / CONFLICT` 和 `503 / UNAVAILABLE`。专属分类如下：

| operation | 同步 `422 / REJECTED` | 同步 `409 / CONFLICT` | 接纳后异步 `evidence=CONFLICT` |
| --- | --- | --- | --- |
| `transport.task.member_position_changed@v1` | 已知 operation 的信封或 DTO 结构非法，`reason_code=INVALID_EVIDENCE`；未知 operation 使用 `UNSUPPORTED_OPERATION` | 同身份不同消息信封 | 未知任务、`container_id` 不属于冻结成员、位置与冻结任务或已接纳事实矛盾 |
| `transport.task.resulted@v1` | 已知 operation 的信封或 DTO 结构非法，`reason_code=INVALID_EVIDENCE`；未知 operation 使用 `UNSUPPORTED_OPERATION` | 同身份不同消息信封，或同任务同 `outcome_revision` 内容不同 | 未知任务、`rack_id/container_id` 与冻结对象不匹配、与已接纳终态矛盾 |

`202 | 200` 只用于可靠接纳；`400 | 413` 是预关联失败；`503` 只表示当前无法可靠持久化且未接纳。ACK 前只做
信封、DTO、消息幂等校验，以及搬运最终结果的 `transport_task_id + outcome_revision` 版本身份登记；不在 HTTP 请求内应用任务状态。
未知任务或与既有任务事实冲突的结构合法 evidence 先返回 `202`，再异步标记 `CONFLICT` 并保留
诊断事实；不能把它伪装成可修正 DTO 的 `422`，也不能以 `503` 要求自动重提。

容器中间位置事件/搬运最终结果同样不提供 `429 / BUSY`。部署认证固定为 `NONE`，正常业务响应不包含 `401`；`401` 只作为部署策略错误的防御性运维响应。
WMS 收到 `401` 时必须保留原消息、停止热重试并告警，待配置修复后再恢复发送；HTML 或其它未定义组合仍按未知响应处理。
响应 `operation_id` 必须等于请求，带任务 ID 的 ACK 还必须回显冻结消息中的
`transport_task_id`，否则不得结束发送义务。
`409 / CONFLICT` 的 `data` 仅在首次冻结消息已解析出合法 `transport_task_id` 时包含该字段；首次消息缺失或使用非法任务 ID 时
固定为空对象，不得从发生冲突的后续消息猜测关联任务。

首版不规定 WMS 在取得权威证据后多少毫秒内形成容器中间位置事件/搬运最终结果，只验收消息最终可靠形成和送达。现场 SOP 必须填写 RCS 无结果告警阈值、
责任人和通知渠道；该运维阈值不是 DTO 字段，也不能把普通 timeout 转换为权威位置或终态。

### 5.2 可选的逐容器中间位置事件

首版只接收两个位置变化里程碑和一个位置未知事实。三个枚举值本身均表示 WMS/RCS 已确认的权威事实，不增加永远只能为
`true` 的 `confirmed` 字段：

- `SOURCE_PICKED`：来源已取出，位置变为 `ON_CARRIER`；
- `TARGET_PLACED`：已放入冻结目标，位置变为 `AT_TARGET`；
- `POSITION_UNKNOWN`：最终位置未知，投影变为 `UNKNOWN` 并进入对账。

`transport.task.member_position_changed@v1` 的 `data` 固定为：

```text
transport_task_id
container_id
milestone: SOURCE_PICKED | TARGET_PLACED | POSITION_UNKNOWN
final_position?   # TARGET_PLACED 时必填，且必须等于冻结目标；只允许 RACK_BIN_SLOT | HANDOFF_POSITION
```

`SOURCE_PICKED` 与 `POSITION_UNKNOWN` 禁止携带 `final_position`。接口契约 `container_id` 在 Adapter 边界映射到冻结成员的内部
`bin_id`；`transport.task.member_position_changed@v1` 不接受 `bin_id` 别名。

每个回调顶层 `operation_id` 遵循 WMS 异步回调统一信封，由 WMS 为该位置事实首次生成 UUIDv7，重试时保持原值，并通过
`transport_task_id` 关联原 TransportTask。位置事实是独立交互，不沿用 submit `operation_id`。

重复事实幂等；倒序事实不得让位置回退。导航、升降、到达区域和机械状态等 CTU 内部阶段不进入 WES Transport 合同。

`transport.task.member_position_changed@v1` 是有权威中间事实时才发送的可选证据，不是 TransportTask 或业务流程的必经步骤。
当前自动上架业务使用的 CTU/RCS 只能返回 `transport.task.resulted@v1` 完整最终结果，因此目标 Bin供给、退回和满箱交换不发送也不
伪造逐容器中间位置事件。搬运任务 `RECEIVED` 后到最终结果到达之前是否已经离开来源无法判断；调用方必须保持对象和资源围栏，
不能把既有来源投影当作当前确定位置。

### 5.3 搬运最终结果

`transport.task.resulted@v1` 按货架、料箱分为两族严格 DTO。

货架族 `RACK_MOVE | RACK_ROTATE` 的 `data` 直接表达唯一货架：

```text
transport_task_id
kind: RACK_MOVE | RACK_ROTATE
outcome_revision  # 1..Int64.MaxValue；同一 transport_task_id 从 1 开始连续递增；技术重试保持不变
rack_id
status: SUCCEEDED | FAILED
final_position? | position_unknown: true
failure_code?
arrival_face?
```

- `SUCCEEDED` 必须携带精确 `RACK_POSITION final_position + arrival_face`；`arrival_face` 等于冻结 `target_face`。对于
  `RACK_POSITION` 目标，最终地码必须等于冻结目标；对于 `RACK` 目标，最终位置必须是 WMS/RCS 按冻结货架编号和模板解析出的
  位置；对于 `ZONE` 目标，最终位置必须属于冻结区域。不得携带 `failure_code` 或 `position_unknown`。
- `FAILED` 且位置明确时必须携带 `final_position + arrival_face + failure_code`。
- `FAILED` 且位置未知时必须携带 `position_unknown=true + failure_code=POSITION_UNKNOWN`，不得携带位置或到达面。
- 货架结果不使用只有一个成员的 `results[]`。

料箱族 `BIN_MOVE | BIN_EXCHANGE` 的 `data` 为：

```text
transport_task_id
kind: BIN_MOVE | BIN_EXCHANGE
outcome_revision
results[] {
  container_id
  status: SUCCEEDED | FAILED
  final_position? | position_unknown: true
  failure_code?
}
```

- `results` 必须完整且仅覆盖冻结成员，并按 `container_id` 升序输出；数组顺序不表示 CTU 动作顺序。
- `SUCCEEDED` 必须携带等于冻结目标的 `final_position`，不得携带 `failure_code` 或 `position_unknown`。
- `FAILED` 必须携带稳定 `failure_code`，并按证据在明确位置和 `position_unknown=true` 中严格二选一。
- `BIN_EXCHANGE` 部分完成时仍完整报告全部容器的已知位置，不得伪造整体回滚。

所有搬运最终结果 DTO 中，`final_position` 与字面量 `position_unknown=true` 必须严格二选一。`position_unknown=false`、两者同时提供或两者
都缺少均无效。缺少成员、增加成员、目标不一致或事实互相矛盾时不得接受为确定终态。接口契约 不再存在 `object_id`；内部
`TransportMember.object_id` 继续作为统一持久化身份，由 Adapter 按任务 `kind` 转换为 `rack_id` 或 `container_id`。

`failure_code` 只允许 `RCS_TASK_REJECTED | RCS_EXECUTION_FAILED | POSITION_UNKNOWN | MANUAL_ABORTED`。WMS/RCS 私有码必须在 WMS
边界完成归一化；未映射私有码不得透传或默认归入 `RCS_EXECUTION_FAILED`，而应告警并等待核对。RCS timeout 本身不能形成搬运最终结果
失败或 `POSITION_UNKNOWN`；只有 RCS 明确结论或人工实物核对才能形成相应权威结果。

任务结果只按冻结对象事实聚合：全部对象成功且位置明确才是 `SUCCEEDED`；至少一个对象失败、但全部对象位置均明确时是
`FAILED`；任一对象位置未知时是 `UNKNOWN/RECONCILING`。Phase 4 不把部分成功包装成整体成功，也不根据业务价值修改聚合规则。
料箱任务不回传可由 `results[]` 推导的任务总状态；货架任务的顶层 `status` 就是唯一对象结果，不形成第二份聚合状态。

`rack_face`、`target_face`、`arrival_face` 按各自上下文可为 `null`；一旦提供，JSON value 必须是非空且不含 NUL 的 UTF-8 string。
除 NUL 外不定义字符内容或长度限制；该边界保证值可进入 PostgreSQL `TEXT`，HTTP Body 仍须符合公共 UTF-8/JSON 信封规则。
WES/WMS/RCS 对解析后的 string 原样传递，不做 trim、case folding、
Unicode normalization、A/B 转换、角度计算或容差处理。成功结果的 `arrival_face` 必须
与冻结 `target_face` 精确相等。缺少应有
`arrival_face` 的货架结果不得接受为确定结果；WES 接受后同步更新本地面向投影，后续货架和 Bin 任务都使用该投影校验工作面。

WMS 为同一 `transport_task_id` 的首条完整搬运最终结果使用 `outcome_revision=1`，每次形成新的完整权威结果时连续加一，技术重试不得改号。
WES 可靠保存每个合法版本：更高版本可以推进未确定结果；低于已应用版本的迟到消息仍按幂等规则 ACK，但不得回退结果或位置；
同一任务、同一版本绑定首个消息身份，新 `operation_id` 复用该版本一律返回 `409 / CONFLICT`。`timestamp` 不参与版本排序。
`UNKNOWN` 可在取得权威完整位置后由更高版本收敛；已经确定的 `SUCCEEDED/FAILED` 不允许通过后续搬运最终结果自动改写，即使版本更高也按
证据冲突处理。人工对账只形成独立审计和现场处置，不伪装成普通搬运最终结果改写已释放资源的确定终态。

### 5.4 持久化后应答

1. 检查请求体上限和严格 JSON 语法；任意层级重复 key 按预关联 `400` 处理。随后提取合法
   `operation + operation_id` 并保留可规范化的完整消息；无法取得合法身份的 `400 | 413` 不建立幂等记录；
2. 查询既有消息身份并比较完整消息：同身份不同消息信封在 DTO 校验前返回 `409 / CONFLICT`；同身份同消息信封按首次响应稳定
   重放，只有首次 `RECEIVED` 转为 `200 / DUPLICATE`；
3. 只有首次出现的消息才校验信封其余字段、operation 和闭集 DTO。失败时原子保存消息身份、规范化摘要和首次
   `422 / REJECTED`，不保存 Transport evidence；`503` 不建立幂等记录；
4. DTO 合法时，原子保存消息身份、规范化摘要和原始 Transport evidence；搬运最终结果同时登记
   `transport_task_id + outcome_revision + 版本内容摘要`。同一版本已存在不同摘要时返回 `409 / CONFLICT`，保存当前消息身份、摘要和
   首次冲突响应以便稳定重放，但不保存第二份 evidence；
5. 首次 evidence 保存成功后返回 `202 / RECEIVED`；
6. 异步锁定 `TransportTask`，校验不可变任务身份、对象和冻结成员；
7. 搬运最终结果只在 `outcome_revision` 高于已应用版本时，在同一事务更新任务、成员、位置投影、已应用接口契约版本、evidence 处理状态和待发布
   的内部 `outcome_version`；低版本标记已处理但不得回退投影；
8. 后台有界领取未发布版本，在事务外交给 `TransportOutcomePublisher`，成功后记录已发布版本。

搬运最终结果版本登记必须使用数据库唯一约束或等价的原子并发控制，不能先查询再插入；同一版本、同一摘要但使用了新的消息身份属于发送方
违反冻结身份要求，返回 `409 / CONFLICT`，不能伪装成技术重试；接收方保存该新消息身份、摘要和首次冲突响应，但不保存第二份
evidence，后续同身份同消息信封稳定重放该冲突响应。低于已登记最高版本的合法迟到消息仍可保存并可靠 ACK，版本登记只防止同一版本
出现两个内容，不在 ACK 路径判断任务状态。

同一 `operation + operation_id` 对应不同消息信封时在 ACK 前返回 `409`，不保存为新的原始 evidence，可以另存诊断审计。未知任务、
对象/冻结成员不匹配和矛盾终态已经可靠接纳，在异步应用阶段失败关闭并把原始 evidence 标记为 `CONFLICT`。

原始 evidence 使用最小处理状态 `PENDING | APPLIED | CONFLICT`。`TransportRepository` 按稳定顺序小批量领取
`PENDING` evidence，并记录领取令牌和租约截止时间；租约过期后允许重新领取，旧令牌不得写回。应用成功标记 `APPLIED`，
无法与冻结任务单调收敛的证据标记 `CONFLICT` 并保留诊断事实。首版只增加待处理索引，不建立通用队列或额外 Service。

`record_callback()` 只执行上面第 1～5 步，不在 WMS 回调请求中推进任务或发布结果；
`process_pending_evidence(limit)` 只执行第 6～7 步；`publish_pending_outcomes(limit)` 单独执行第 8 步。这样即使进程在应答后退出，
已经持久化的 evidence 仍可由后续批次重领，两个后台入口也不会重复发布结果。

## 6. 插件统一结果

| `TransportOutcome.status` | 形成条件 | 插件语义 |
| --- | --- | --- |
| `SUCCEEDED` | 匹配权威成功结果且最终位置完整 | 可以继续依赖该搬运的业务步骤 |
| `FAILED` | 匹配权威失败结果且相关位置完整 | 搬运失败，但资源位置可判断 |
| `REJECTED` | WMS/RCS 明确未接纳 | 原任务不可修改；修正业务输入或重新分配后，以新的 `client_request_id` 创建新任务 |
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

仅当任务仍为 `UNKNOWN/RECONCILING` 时，WMS 作为全局位置事实 owner 才可以在有审计记录的人工核对后，通过同一个
`transport.task.resulted@v1` 提交下一连续 `outcome_revision` 的完整权威结果 evidence；WES 接纳后为同一 TransportTask 形成
更高的内部 `outcome_version`，不增加人工修正专用 operation。确认动作已经完成时，`SUCCEEDED` 必须携带完整最终位置；确认动作
未完成时，`FAILED` 同样必须携带所有对象的已知位置。只确认“操作员已检查”而没有完整位置，不能把 `UNKNOWN` 提升为确定结果，
也不能直接改写 WES 位置投影。任务已是确定 `SUCCEEDED/FAILED` 时，人工核对只形成独立审计和现场处置，不再发送用于改写终态的
搬运最终结果。

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

WES 本地运维观察接口可以按 `transport_task_id` 返回任务当前状态、原因、submit 身份和最近一条已可靠持久化的
`TransportEvidence` 摘要。最近 evidence 按本地 `(received_at DESC, id DESC)` 确定，包含 `PENDING / APPLIED / CONFLICT`，
不得按 WMS 事件时间或最大 `outcome_revision` 推断顺序，也不得返回原始 callback payload。该投影只用于开发和现场诊断，
不属于 WMS/RCS 状态查询、物理完成证明或业务验收。

同一货架或料箱最多属于一个非终态任务。Bin 任务必须绑定每个成员来源和目标 `RACK_BIN_SLOT` 中出现的全部不同 `rack_id`，
同时绑定被搬运的每个内部 `bin_id`；Adapter 接收的接口契约`container_id` 必须先解析为该冻结成员身份。这样可以防止 AGV 搬架与
CTU 在该架取箱或放箱并发。资源键先去重、稳定排序后在一个事务中取得；
只有 `REJECTED / SUCCEEDED / FAILED` 的确定终态事务释放绑定；`RECONCILING` 即使已向插件发布 `UNKNOWN` 也必须继续保持绑定，
直到匹配的权威确定结果完成消歧。唯一例外是第 1 节定义的联调定向清理：事务锁定任务后，按 `transport_task_id` 删除完整本地链路，
包括随任务聚合删除其绑定；晚到 callback 仍按既有 missing-task Evidence 合同保留为 `CONFLICT`，不得静默丢弃。资源冲突在创建
阶段失败关闭，不等待 RCS 再拒绝。

精确储位身份使用 `RACK_BIN_SLOT(rack_id + rack_face + slot_id)`，只承担请求内位置唯一性、成员目标校验和结果匹配；活动任务通过
其所在 `rack_id` 整体互斥，不重复建立精确储位资源绑定。`HANDOFF_POSITION` 可以由多个任务引用，其瞬时容量属于 WMS/RCS 或
业务 owner，不因 `location_code` 相同就在 Transport 核心互斥。

位置或终态证据可以先于 submit ACK 到达；匹配证据本身可以证明远端已接纳。后到 ACK 只补充接纳事实，不得回退位置或终态。

## 8. 分拣机流程映射

| 业务步骤 | WMS/插件先确定 | Phase 4 调用 |
| --- | --- | --- |
| 补充 1～2 个粗分完成的单层货架 | rack、来源、目标位置、目标可用工作面 | 每个货架一次 `move_rack()`，可并行 |
| 补充有可用料箱/料格的五层货架 | rack、来源、FIVE_STATION、目标可用工作面 | `move_rack()` |
| 五层货架到位后投入料箱 | bin、来源储位、滚筒线入料位置 | `move_bins()` |
| 从滚筒线出料口退箱 | bin、出料位置、五层货架空储位 | `move_bins()` |
| 当前面内料箱交换 | 1～2 个确定交换对 | 一次 `exchange_bins()` |
| 货架原地换面 | rack、当前位置、目标面 | `rotate_rack()` |

五层货架只有收到 `SUCCEEDED` 后才能触发投箱。ACK、`ACCEPTED` 或 AGV“已派发”均不能作为到位条件。
一次 `exchange_bins()` 只处理每个相关货架的当前面，可以在同一货架面内交换，也可以在两个货架当前面之间交换；需要处理任一
货架另一面时，必须由业务层在当前面业务闭环后完成独立换面，再形成下一批，不能把同一货架的跨面成员塞进一个 TransportTask。

## 9. 测试所有权与验收

| 测试 | 唯一所有者 | 必须证明 |
| --- | --- | --- |
| 四个公共方法 | 核心 runtime/transport | 参数、`move_rack.target_face`、幂等、一个调用一个任务、统一 handle/outcome |
| 任务和资源可靠性 | PostgreSQL integration/transport | 唯一约束、资源互斥、领取和原子结果应用 |
| WES → WMS 搬运提交请求体与 ACK | WMS Adapter contract | 冻结请求体、profile path、固定 operation、两族搬运提交、ACK 和 WMS 所有的服务端 OpenAPI 一致性 |
| WMS → WES 容器中间位置事件/搬运最终结果 DTO | WMS Adapter contract | WES 所有的 OpenAPI 3.0.3、固定 path/operation、容器中间位置事件、两族搬运最终结果、ACK、严格 JSON 和事件转换 |
| 协调交换真实行为 | WMS/RCS 联调 | 同面 1～2 个二元闭环、内部顺序、部分失败和最终位置 |
| 分拣机开工顺序 | 分拣机插件测试 | WMS 分配、并行调架、五层货架成功后投箱 |

Phase 4 核心测试不得使用“粗分完成、空货架、满箱、空箱、可用储位”等业务分类证明搬运能力。工作线插件测试使用假的
`TransportService`，不得替代 Phase 4 的数据库、幂等和 WMS 合同测试。

Transport 按服务端所有权分别使用两份 OpenAPI 3.0.3 权威文件：WMS 提供搬运提交 `POST {{TRANSPORT_SUBMIT_PATH}}`，表达四种
`kind` 对应的两族 DTO、位置判别联合和 ACK；WES 提供容器中间位置事件与搬运最终结果共用的 `POST /api/v1/wms/events`，表达条件字段、搬运最终结果
`outcome_revision` 和完整响应联合。任一系统不得为对方服务端接口另建第二份权威定义。Swagger 2.0 只能作为旧工具的非权威
导出，不能替代 `oneOf` 等严格联合。

## 10. 明确非目标

- 空货架、空料箱、可用储位和业务资格选择；
- 分拣机、滚筒线、机械臂、扫码和 NG 业务编排；
- WES 直连 RCS/AGV/CTU；
- ECS/DeviceCommand、设备状态和供应商私有协议；
- 车辆、路径、交通、充电、CTU 取放顺序和临时位规划；
- WMS/RCS 状态轮询，以及取消、暂停、恢复、改派和自动补偿；
- 通用 Runtime/Effect、动态 Provider、Service Locator、插件 SDK 或工作流引擎；
- 旧字段、旧表、旧 API、旧数据和兼容路径。
