---
title: WES-WMS Transport DTO 收敛设计
status: Approved
created_at: 2026-08-14
updated_at: 2026-08-31
scope: WES 与 WMS 之间 Transport 搬运提交/容器中间位置事件/搬运最终结果接口契约、WMS C# DTO 和 WMS-RCS 映射边界
system_stage: pre_release
migration_strategy: direct_replacement
implementation_alignment: ALIGNMENT_REQUIRED
related:
  - docs/integration/wes-wms-interface-requirements.md
  - docs/contracts/transport-fulfillment-contract.md
  - docs/hardware/CTU&AGV对接流程（完成80%）.pdf
  - docs/hardware/容器上报API接口文档.pdf
---

# WES-WMS Transport DTO 收敛设计

## 1. 文档定位

本文定义尚未发布系统的 WES-WMS Transport接口契约目标设计，重点减少 WMS 在 .NET Framework 4.6 中解析和组装
`RACK_MOVE`、`RACK_ROTATE`、`BIN_MOVE`、`BIN_EXCHANGE` 四种请求的重复工作，同时守住 WES、WMS 与 RCS 的职责边界。

本文是已完成联合设计评审的目标基线。两份现行合同已经更新，WES 代码、运行时 OpenAPI、独立 OpenAPI 3.0.3 文件和行为测试
尚未按本设计调整，因此 `implementation_alignment=ALIGNMENT_REQUIRED`。实施时直接替换旧合同，不保留兼容入口。

两份 `docs/hardware/` PDF 是厂商原始输入，保持原貌。厂商字段只用于说明 WMS ACL 的映射依据，不升级为 WES 核心合同。

系统尚未发布，不保留旧业务载荷、旧 DTO、字段别名、双读、双写或版本兼容。开发和测试数据可以清理。

## 2. 目标与非目标

### 2.1 目标

- 将搬运提交的四套 JSON/C# `data` DTO 收敛为货架、料箱两族。
- 统一四种操作中可统一的字段名称和结构，不用可选字段拼成万能 DTO。
- 让物理料箱在整个接口契约中统一使用 `container_id`，消除与厂商 `binId` 的同名异义。
- 删除搬运最终结果的含糊 `object_id`，按货架和料箱结果分别使用 `rack_id`、`container_id`。
- 保留 WES 稳定业务语义，使 WMS 到厂商 RCS 的转换集中为货架、料箱两个清晰的映射职责。
- 删除搬运提交 `429 / BUSY`、`BUSY` 和 `retry_after_ms`，让 WMS 可靠接纳后自行排队。

### 2.2 非目标

- 除三方明确约定的 `rcs_template_id` 外，不让 WES 发送厂商 `taskTyp`、`ctnrTyp`、`positionCodePath.type`、`podDir`、
  `ctuSide` 或 `sideA/sideB`。
- 不让 WES 规划 AGV/CTU 车辆、路径、交通管制、动作顺序或供应商子任务。
- 不复用同一个 C# 类同时表示 WMS 面向 WES 的协议 DTO 和 WMS 面向 RCS 的出站 DTO。
- 不建立 DTO 注册表、反射式处理框架、通用工作流或自定义多态 JSON 转换器。
- 不用厂商原始协议测试证明 WES Transport 核心能力，也不用 WES 核心测试证明供应商一致性。

## 3. 核心设计裁决

1. 搬运提交保持一个 endpoint、一个公共信封和一个 `kind` 闭集，但 `data` 只分为 `RackTransportData` 与
   `BinTransportData` 两族。
2. `RACK_MOVE` 与 `RACK_ROTATE` 共用 `rcs_template_id + source + target + target_face`。`RACK_MOVE` 的位置允许
   `RACK | ZONE | RACK_POSITION`，原地换面只使用精确 `RACK_POSITION` 并通过 `source == target` 表达。
3. `BIN_MOVE` 与 `BIN_EXCHANGE` 共用 `moves[] { container_id + source + target }`。交换通过闭环约束表达，不使用
   `exchange_pairs` 或 left/right 角色。
4. 接口契约中的物理料箱统一叫 `container_id`；`slot_id` 继续表示 WES 结构化货架槽位。WMS 根据主数据把槽位映射为厂商
   `binId`，不得靠字符串拼接或从容器编码反推。
5. 搬运最终结果货架结果直接展开，料箱结果保留 `results[]`。不再使用单元素货架数组和通用 `object_id`。
6. Position接口契约保持严格可判别联合；WMS C# 可以用一个自行命名的普通位置类型承载其实现属性，再按 `kind` 校验条件必填并省略
   不适用的 `null` 字段。
7. 公共接口契约继续使用 `snake_case`。WMS 为 WES 边界和厂商边界使用独立 Json.NET 配置及 DTO。

## 4. 公共信封与位置类型

搬运提交/容器中间位置事件/搬运最终结果继续使用固定请求信封：

```text
operation_id
operation
timestamp
data
```

`data` 是 JSON object，不是二次转义后的 JSON string。顶层和专属 `data` 都是严格闭集；未知字段、重复 key、隐式类型转换、
错误大小写和显式 `null` 均拒绝。

Position接口契约固定为：

| `kind` | 完整字段 | 用途 |
| --- | --- | --- |
| `RACK` | `kind + location_code` | `RACK_MOVE` 来源或目标；`location_code` 必须等于外层 `rack_id`，由 RCS 解析位置 |
| `ZONE` | `kind + location_code` | `RACK_MOVE` 来源或目标；`location_code` 表示区域编号，不指定精确地码 |
| `RACK_POSITION` | `kind + location_code` | `RACK_MOVE` 精确来源或目标、`RACK_ROTATE` 位置和货架最终位置 |
| `RACK_BIN_SLOT` | `kind + rack_id + rack_face + slot_id` | 指定货架、当前工作面和槽位 |
| `HANDOFF_POSITION` | `kind + location_code` | CTU、输送线或工作线交接位 |

`RACK` 出现在 `source` 或 `target` 时，`location_code` 必须等于同一 `RackTransportData.rack_id`；不一致的请求无效。

不适用字段不得出现在 JSON 中。`rack_face`、`target_face`、`arrival_face` 都只允许 JSON 整数 `90 | 270`。WES/WMS/RCS
将其作为约定面向值原样传递和精确比较，不接受 A/B 字符串、数字字符串、角度换算或容差。

## 5. 搬运提交请求设计

### 5.1 货架族

`RACK_MOVE` 与 `RACK_ROTATE` 的 `data` 完整字段相同：

```text
transport_task_id
kind: RACK_MOVE | RACK_ROTATE
rcs_template_id: CTU01 | CTU02 | CTU03 | F01
rack_id
source: RACK | ZONE | RACK_POSITION
target: RACK | ZONE | RACK_POSITION
target_face: 90 | 270
```

| `kind` | 结构约束 | 完成条件 |
| --- | --- | --- |
| `RACK_MOVE` | `source/target` 属于位置闭集且不同；`target_face` 必填 | 回调精确 `RACK_POSITION`；精确目标还须地码相等，实际面等于 `target_face` |
| `RACK_ROTATE` | `source == target` 且均为精确 `RACK_POSITION`；`target_face` 不同于可信当前面 | 保持在精确位置，实际面等于 `target_face` |

`RACK_MOVE` 可以由 WMS 分解为直接搬运、先换面后搬运或搬运后换面等一个或多个厂商任务。WES 不接收分解步骤，也不根据
RCS 中间步骤提前完成 TransportTask；WMS 必须以最终位置和最终工作面闭合一个 WES 运输义务。

`target_face` 是业务调用方在创建任务时给出的目标义务。WMS 将整数面向值原样传给 RCS，最终回调 `arrival_face` 必须与其相等。
`rcs_template_id` 使用 RCS 真实模板标识：库位到工作位为 `CTU01`，工作位原地旋转为 `CTU02`，工作位返回库位为 `CTU03`，
未指定时在形成不可变请求前规范化为 `F01`。Wire 始终携带明确模板；WES 不根据位置编码推断模板，也不建立模板配置映射。

`move_rack()`、`rotate_rack()` 及其请求对象必须把 `rcs_template_id`、位置和 `target_face` 写入 TransportTask 的不可变请求快照及
`request_body_digest`，后续重提只能读取冻结值。`RACK_POSITION` 目标要求最终地码相等；`RACK` 目标要求最终位置是 WMS/RCS 按
冻结货架编号和模板解析出的结果；`ZONE` 目标要求最终位置属于冻结区域。回调统一返回精确 `RACK_POSITION`，WES 不自行解析
宽泛目标。

### 5.2 料箱族

`BIN_MOVE` 与 `BIN_EXCHANGE` 的 `data` 完整字段相同：

```text
transport_task_id
kind: BIN_MOVE | BIN_EXCHANGE
moves[] {
  container_id
  source: RACK_BIN_SLOT | HANDOFF_POSITION
  target: RACK_BIN_SLOT | HANDOFF_POSITION
}
```

数组顺序不是物理执行顺序。WES 形成搬运提交快照时按 `container_id` 升序输出，保证同一不可变请求具有稳定 JSON。WMS/RCS 可以在
不改变冻结来源、目标和最终结果的前提下自行选择物理动作顺序。

#### `BIN_MOVE`

- `moves` 数量为 `1..4`。
- `container_id` 唯一；每个成员 `source != target`。
- 至少一端是 `RACK_BIN_SLOT`。
- 精确 `RACK_BIN_SLOT(rack_id + rack_face + slot_id)` 在全部成员的 `source/target` 中不得重复出现；同一
  `HANDOFF_POSITION` 可以由多个成员共享。

#### `BIN_EXCHANGE`

- `moves` 数量只能为 `2` 或 `4`。
- 所有端点都是 `RACK_BIN_SLOT`，不允许 `HANDOFF_POSITION`。
- 所有端点只允许涉及一个或两个 `rack_id + rack_face` 端点组；涉及两个组时，每个成员必须跨组移动；只有一个组时，允许在
  该货架当前面的不同精确储位之间交换。
- `container_id`、来源精确储位和目标精确储位分别唯一。
- 目标精确储位集合必须等于来源精确储位集合，形成一个不可拆分的闭环交换。
- 每个 `source=S, target=T` 的成员必须且只能存在一个 `source=T, target=S` 的反向成员。四个成员必须形成两个互不重叠的
  二元闭环，不允许形成三元、四元或其它环形置换。
- WMS/RCS 必须具备把整个请求作为一个协调任务可靠接纳的能力；不得拆成两个独立 WES TransportTask。

### 5.3 当前工作面约束

一个 Bin 批次可以涉及不同货架，但同一 `rack_id` 在整个请求中只能出现一个 `rack_face`。不同货架可以分别使用 `90` 面或 `270` 面。

- WES 在创建 TransportTask 前使用可靠本地位置/工作面投影校验。
- WMS 在返回 `RECEIVED` 前使用自身权威主数据及可信 RCS 状态再次校验。
- 请求工作面与已知当前面不一致时返回 `409 / CONFLICT`，不调用 RCS。
- 无法取得可信当前面时返回 `503 / UNAVAILABLE`，不调用 RCS。
- 需要操作另一面时，业务 owner 先完成独立 `RACK_ROTATE`，再基于新事实创建新的 Bin 任务。

同一 Bin 批次禁止先操作 `90` 面部分容器、再换面操作 `270` 面剩余容器。

## 6. 容器中间位置事件（可选）

容器中间位置事件对应 `transport.task.member_position_changed@v1`。只有 `BIN_MOVE` 与 `BIN_EXCHANGE` 在上游确有权威中间事实时才可使用；它不是
必经步骤，不能由搬运提交或最终结果倒推伪造。当前自动上架业务的 CTU/RCS只返回 `transport.task.resulted@v1` 完整最终结果，因此目标
Bin供退和满箱交换不产生容器中间位置事件。上游能够提供权威中间位置事实时，`data`固定为：

```text
transport_task_id
container_id
milestone: SOURCE_PICKED | TARGET_PLACED | POSITION_UNKNOWN
final_position?
```

`TARGET_PLACED` 必须携带等于冻结目标的 `final_position`；`SOURCE_PICKED` 与 `POSITION_UNKNOWN` 禁止携带 `final_position`。
容器中间位置事件不再使用 `bin_id`。

## 7. 搬运最终结果设计

### 7.1 货架结果

`RACK_MOVE` 与 `RACK_ROTATE` 的搬运最终结果 `data` 直接表达唯一货架：

```text
transport_task_id
kind: RACK_MOVE | RACK_ROTATE
outcome_revision
rack_id
status: SUCCEEDED | FAILED
final_position? | position_unknown: true
failure_code?
arrival_face?
```

- `SUCCEEDED` 必须携带精确 `RACK_POSITION final_position + arrival_face`；`arrival_face` 等于冻结 `target_face`。精确目标还须
  地码相等；`RACK | ZONE` 目标以 WMS/RCS 返回的实际精确地码更新位置事实。不得携带 `failure_code` 或 `position_unknown`。
- `FAILED` 且位置明确时必须携带 `final_position + arrival_face + failure_code`，其中 `failure_code` 只允许
  `RCS_TASK_REJECTED | RCS_EXECUTION_FAILED | MANUAL_ABORTED`。
- `FAILED` 且位置未知时必须携带 `position_unknown=true + failure_code=POSITION_UNKNOWN`，不得携带位置或到达面。
- 不使用只有一个成员的 `results[]`。

### 7.2 料箱结果

`BIN_MOVE` 与 `BIN_EXCHANGE` 的搬运最终结果 `data` 为：

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

- `results` 必须完整且仅覆盖冻结 `moves` 中的全部 `container_id`。
- WMS 按 `container_id` 升序输出，数组顺序不是 CTU 动作顺序。
- `SUCCEEDED` 必须携带等于冻结目标的 `final_position`，不得携带 `failure_code`。
- `FAILED` 必须携带稳定 `failure_code`，并按证据在明确位置和 `position_unknown=true` 中严格二选一。位置未知时
  `failure_code` 必须为 `POSITION_UNKNOWN`；其它失败码只能与明确位置组合。
- `BIN_EXCHANGE` 部分完成时仍完整报告每个容器的已知位置；不得伪造整体回滚。

搬运最终结果接口契约中不再存在 `object_id`。WES 内部 `TransportMember.object_id` 和位置投影的通用标识可以保留，由 WMS ACL 在接口契约
边界转换，不要求数据库字段跟随重命名。

### 7.3 回调可靠性不变量

本次 DTO 收敛不改变容器中间位置事件/搬运最终结果的统一回调信封、消息幂等和结果单调性：

- 容器中间位置事件/搬运最终结果继续以 `operation + operation_id` 作为消息身份；WES 在返回接纳 ACK 前原子保存消息身份、`message_digest` 和
  原始 evidence。首次 `RECEIVED` 后，相同消息身份、相同消息信封返回 `DUPLICATE`；首次 `REJECTED/CONFLICT` 原样稳定重放；
  相同消息身份、不同消息信封返回 `409 / CONFLICT`。`503` 不建立幂等记录。
- `outcome_revision` 范围为 `1..Int64.MaxValue`。同一 `transport_task_id` 从 `1` 开始连续递增；同一权威结果的技术重试保持
  原版本号和原 `operation_id`。
- 搬运最终结果在上述消息幂等之外，还必须原子登记 `transport_task_id + outcome_revision + 版本内容摘要`。同一任务、同一版本的摘要
  不同，或使用新的 `operation_id` 复用该版本，均返回 `409 / CONFLICT`。
- 低于已应用版本的合法迟到消息可以可靠保存并 ACK，但不得回退任务、成员或位置投影；只有更高版本可以推进尚未确定的结果。
  `UNKNOWN` 可以由更高版本收敛，已经确定的 `SUCCEEDED/FAILED` 不得由后续普通搬运最终结果改写。
- `failure_code` 是闭集，只允许 `RCS_TASK_REJECTED | RCS_EXECUTION_FAILED | POSITION_UNKNOWN | MANUAL_ABORTED`。厂商私有码必须
  在 WMS 边界归一化；未映射私有码不能透传或默认归类。

## 8. 搬运提交 ACK 与重试

搬运提交同步响应闭集为：

| HTTP / `code` | 使用条件 | WES 动作 |
| --- | --- | --- |
| `202 / RECEIVED` | WMS 已可靠保存请求、幂等身份、首次响应和后续 RCS 义务 | 等待容器中间位置事件/搬运最终结果 |
| `200 / DUPLICATE` | 相同身份和相同不可变请求已经接纳 | 收敛到首次接纳事实 |
| `409 / CONFLICT` | 身份内容冲突、活动资源冲突或已知当前工作面不匹配 | 进入对账，不自动重提 |
| `422 / REJECTED` | 信封、DTO、闭集枚举、组约束或固定能力确定非法 | 确定拒绝，不重用原任务 |
| `503 / UNAVAILABLE` | WMS 无法可靠持久化，或无法取得必须的可信当前面 | 原完整消息受控重提 |
| `400` 空响应体 | Content-Type、UTF-8/JSON、任意层级重复 key、number 超出合同规范化域，或消息身份无法解析 | 确定未接纳，不建立幂等记录 |
| `413` 空响应体 | 原始请求正文超限 | 确定未接纳 |

除预关联失败的空响应体 `400 | 413` 外，ACK 固定使用 `operation_id + code + timestamp + data` 信封，`operation_id` 原样回显。
`data` 是严格联合：

- `RECEIVED | DUPLICATE | CONFLICT | UNAVAILABLE`：完整且仅包含 `transport_task_id`。
- `REJECTED`：完整且仅包含 `reason_code`，或 `transport_task_id + reason_code`；只有请求中的任务 ID 缺失或非法时才能省略
  `transport_task_id`。
- `reason_code` 只允许 `INVALID_ENVELOPE | UNSUPPORTED_OPERATION | INVALID_DATA |
  COORDINATED_BIN_EXCHANGE_UNSUPPORTED`。

同一请求的幂等重放复用首次可靠保存的 `timestamp + data`。即使活动资源被另一任务占用，`409` 也回显当前请求中已解析的
`transport_task_id`，不能返回占用资源的旧任务 ID。

活动资源围栏固定为：每个未闭合任务绑定其全部 `container_id`、货架任务的 `rack_id`，以及 Bin 任务所有
`RACK_BIN_SLOT` 中出现的 `rack_id`。精确槽位 `(rack_id, rack_face, slot_id)` 用于请求内位置唯一性、成员目标校验和结果匹配，
不另建活动资源绑定；其所在 `rack_id` 已被任务整体互斥。`HANDOFF_POSITION` 不得仅因 `location_code` 相同而全局互斥。
资源只有在任务取得确定终态或经人工对账关闭后才释放。

删除 `429 / BUSY`、`BUSY`、`retry_after_ms` 及所有相关分支。RCS 或内部调度容量不足不是拒绝 WES 义务的理由：WMS 应先
可靠接纳，再在内部排队。`503` 固定等待 2000 毫秒，单任务发送预算仍为最多 3 次；请求可能已经送达但响应未知时继续进入
`RECONCILING`，不得自动重提。只有确认未送达的 `NOT_SENT` 和明确未接纳的 `503` 可以使用原始不可变请求受控重提；发送预算
耗尽后形成 WES 内部终态 `REJECTED / TRANSPORT_SUBMIT_RETRY_EXHAUSTED`，该诊断码不进入 WMS ACK 接口契约。

## 9. WMS .NET Framework 4.6 实现边界

WMS 只需要一个公共信封、一个 Position DTO、两个搬运提交 data DTO、一个货架搬运结果 data DTO 和一个料箱搬运结果 data DTO。

最小解析流程：

```text
解析并严格校验公共信封
→ 读取 data.kind
→ RACK_MOVE / RACK_ROTATE：按 RackTransportData Schema 解析
→ BIN_MOVE / BIN_EXCHANGE：按 BinTransportData Schema 解析
→ 执行所属族的结构校验和 kind 专属不变量
→ 可靠保存 WMS 义务
→ 进入货架或料箱映射职责，具体类和方法名由 WMS 自定
```

实现约束：

- 使用普通 POCO 和一次显式 `switch`；不编写自定义 JsonConverter、继承树或类型注册表。
- WMS 面向 WES 的协议边界统一使用 Json.NET `SnakeCaseNamingStrategy`，未知成员按错误处理；WMS 面向厂商 RCS 的边界使用
  独立的 camelCase DTO 和序列化设置。
- WMS 自行命名的位置类型可以包含可空实现属性，但接口契约省略不适用字段，校验器按 `kind` 强制严格联合。
- WES DTO 只表达跨系统运输义务；RCS DTO 只表达厂商调用。建议分别集中为货架、料箱两个明确映射职责，具体类和方法名由 WMS
  自定；两侧 DTO 不能互相继承或共用同一类。

## 10. WMS 到厂商协议的最小映射

| WES 字段/语义 | WMS/RCS 处理 |
| --- | --- |
| `transport_task_id` | WMS 为每个厂商子任务生成符合厂商限制的 `taskCode`，并保存一个 WES 任务到一个或多个厂商任务的关联 |
| `rack_id` | 通过 WMS 主数据解析为厂商 `podCode`，并校验厂商字段限制 |
| `container_id` | 通过 WMS 主数据解析为厂商 `containerId`，并校验厂商字段限制 |
| `RACK.location_code` | 必须等于外层 `rack_id`，作为货架编号交给 RCS 解析位置 |
| `ZONE.location_code` | 作为区域编号交给 RCS 选址 |
| `RACK_POSITION.location_code` | 作为精确地码映射 `positionCodePath[].positionCode` |
| `RACK_BIN_SLOT` | 通过 WMS 主数据映射厂商仓位 `binId`，不得从字符串格式猜测 |
| `target_face` | 整数 `90 | 270`，WMS 原样传给 RCS |
| `arrival_face` | RCS 回传的整数 `90 | 270`，成功时与冻结 `target_face` 精确相等 |
| `rcs_template_id` | 直接调用同名 RCS 模板；只允许 `CTU01 | CTU02 | CTU03 | F01` |

厂商 `sideA/sideB` 是 WMS 基于自身库存和货架主数据形成的整架容器上报，不进入 WES 搬运提交。WES 不复制 WMS 已拥有的两面容器
全集，也不参与厂商短时 `taskCode + podCode` 幂等规则。

WES 目标接口契约的 `transport_task_id` 长度为 `1..80`，`rack_id`、`container_id` 为 `1..100`；厂商文档中的 `taskCode`、
`podCode`、`containerId`、`binId` 上限均为 64。两侧身份语义可以一致，但 WMS 必须经过主数据解析并按出站合同校验长度，不能
在设计上承诺字符串直接复用。若现场主数据恰好返回相同值，也只是映射结果相同，不改变该边界。

厂商资料存在两个不能由 WES 合同猜测解决的问题：

- AGV/CTU 流程示例中的 `podDir` 为空，未形成可依赖的字段语义；WMS 必须按现场联调结果维护映射。
- 容器上报文档的列表参数表写“最大 50 条”，全局校验又写“≤10”；该矛盾属于厂商接口确认事项，不改变 WES Bin 批次 `1..4`
  的业务合同。

## 11. 分层、测试与实施范围

### 11.1 分层边界

- Transport 核心继续保留 `move_rack()`、`rotate_rack()`、`move_bins()`、`exchange_bins()` 四个领域方法；只收敛 接口契约，不把四种
  领域行为合并为一个万能方法。`move_rack()` 与 `rotate_rack()` 的签名及请求对象都显式接收 `target_face`，货架任务快照和
  摘要都冻结该字段。
- `src/app/wms_adapter/` 负责 WES接口契约编解码和 WMS ACL 转换；基础 HTTP 传输层不解释 Rack、Container、Face 或 RCS 任务类型。
- WMS 私有 RCS 映射、库存查询、`sideA/sideB` 组装和供应商子任务表不进入 WES 仓库。

### 11.2 TDD 所有权

本设计会改变生产代码行为，实施阶段必须先修改或新增受影响行为测试，再修改实现：

- Transport 核心测试验证四个领域方法、位置/工作面不变量、不可变快照和任务聚合。
- `tests/contracts/wms_adapter/` 验证两族搬运提交接口契约、容器中间位置事件/搬运最终结果、ACK 闭集、严格 JSON 和 ACL 转换。
- `tests/integration/wms_adapter/` 只在需要真实持久化或事务时验证 WMS Adapter，不证明基础 HTTP 能力。
- `rcs_template_id` 的闭集与默认值由 WES 合同测试负责；模板实际行为和原始错误码仍由 WMS/RCS 交付边界验收。
- 两份人类阅读文档不增加正文断言测试；纯文档阶段只做格式、引用和 diff 检查。

### 11.3 直接替换

- 原地重写未发布的 `transport.task.submit@v1`、容器中间位置事件和搬运最终结果，不创建 v2。
- 删除旧四族 submit DTO、`exchange_pairs` 接口契约、容器中间位置事件 `bin_id`、搬运最终结果 `object_id`、`BUSY` 和 `retry_after_ms`。
- 不保留旧字段 alias、兼容解析、deprecated wrapper 或双路径测试。
- 实施完成后清理开发/测试数据，使用新合同 fixture 重建验证基线；不编写历史数据迁移。

## 12. 验收标准

1. WMS 面向搬运提交只需要 `RackTransportData` 和 `BinTransportData` 两种 `data` Schema；WMS 私有 C# 类名不属于本合同。
2. 四种 `kind` 的 JSON 都能通过一次 rack/bin 分流完成严格解析，不需要自定义多态转换器。
3. `RACK_MOVE` 与 `RACK_ROTATE` 都携带明确 `rcs_template_id` 和整数 `target_face=90|270`；成功回调返回精确
   `RACK_POSITION`，并校验 `arrival_face` 与冻结面向值一致。
4. `BIN_MOVE` 与 `BIN_EXCHANGE` 都使用 `moves[].container_id + source + target`，且严格执行单面、端点组和闭环规则。
5. 容器中间位置事件/搬运最终结果接口契约不再出现 `bin_id` 或 `object_id`；货架和料箱结果身份分别明确为 `rack_id`、`container_id`。
6. 搬运提交 ACK、实现和测试中不存在 `429 / BUSY`、`BUSY` 或 `retry_after_ms`。
7. 搬运提交 ACK `data`、活动资源围栏以及容器中间位置事件/搬运最终结果幂等、修订和确定终态规则均有唯一、可执行的闭集定义。
8. `target_face` 由业务调用方提供并冻结，WMS 原样传给 RCS；搬运最终结果 `arrival_face` 表达确认后的实际工作面并与其精确比较。
9. WMS 为厂商子任务生成合规 `taskCode`，所有跨协议身份经过主数据解析和长度校验，不依赖字符串直接复用。
10. 除批准的 `rcs_template_id` 外，WMS/RCS 私有字段不进入 WES 公共合同；两份厂商 PDF 未被修改。
11. 两份当前态合同、OpenAPI、生产代码和行为测试使用同一目标接口契约，不存在旧格式兼容入口。
12. 基础能力、WMS ACL、Transport 业务不变量和供应商一致性由各自测试所有者独立验证。
13. 初级 WMS 开发人员只阅读 WMS 对接指南即可得到完整 DTO、映射表、成功路径、错误分类和失败处理，不需要反查 WES 内部代码。
