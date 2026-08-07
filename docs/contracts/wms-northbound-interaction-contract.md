# WMS 业务 ACL 交互合同

> 状态：`DRAFT_CONSUMER_MATRIX`，Phase 3 Task 1 的跨能力门禁仍未关闭，当前不得进入代码实施。
> 需求真源：`docs/architecture/SRS.md`。
> 架构真源：`docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md`。
> 出库业务真源：`docs/superpowers/specs/2026-08-06-wes-outbound-operation-top-level-design.md`。
> 外部输入：`docs/hardware/wms_rcs_interface_requirements.md`，原文只读保留，不自动成为实施合同。

## 1. 目的

本文只定义当前交付业务真实消费者所需的 WMS Anti-Corruption Layer（ACL）边界。Phase 3 的目标是无状态、
消费者驱动的 WMS 业务接入，不以旧代码、厂商初稿接口数量或历史 operation 编号维持固定 surface。

一项能力只有同时满足以下条件，才可进入 Phase 3：

1. 能追溯到当前 SRS 范围内的具名业务场景。
2. 能指出目标阶段、具体组件、调用时机以及唯一或明确共享的调用消费者。
3. 交互对象是 WMS 拥有的业务决策结果、库存、单据、主数据事实或 WES 提交的物理执行事实。
4. method、path、request/result、必填性、错误码和幂等语义已经由 WMS 确认。

仅仅出现在硬件初稿、旧实现、旧测试或历史文档中的接口，不满足准入条件。

WMS 是全部业务决策 owner。WES 只校验合同、关联、版本、时效和物理可执行性，并把有效结果映射为等待、发送、暂停、
隔离或对账等执行动作；不得重算或替换来源、目标、优先级、路线、NG/等待/替代、取消、恢复或业务终态。

## 2. 边界

### 2.1 Phase 3 拥有

- WMS 权威业务事实的类型化查询 DTO。
- WES 请求、WMS 返回的 operation-specific 封闭业务决策 DTO。
- WES 向 WMS 请求 `PickingTask` 来源绑定及提交业务事实通知的类型化 DTO。
- WMS 向 WES 下发业务命令的类型化 DTO 和无状态校验/标准化。
- 固定 method/path、请求编码、响应解析和单次调用结果翻译。
- 一个明确配置的 WMS origin 和 outbound `NONE` 认证决定。

### 2.2 Phase 3 不拥有

- 任何 RCS、AGV、CTU 搬运、补给、交换、旋转、状态查询或取消操作。
- `Transport Port`、`TransportTask`、搬运批次、队列位置、FIFO 窗口或位置投影。
- `WmsConfirmation`、`InboundEvidence` 的持久化、领取、重试、恢复或终态推进。
- 数据库表、Migration、Repository、调用 evidence、Circuit Breaker 或清理任务。
- WES 本地业务规则或业务 Decision、库存分配算法、来源/目标/优先级/路线/NG/等待/替代裁决、库存锁、料格冻结、
  跨任务来源分配、设备命令和厂商 Payload。
- 通用 `call`、动态 registry、Provider 切换、兼容 alias、fallback、缓存或自动续页。

Phase 2 只提供单次 HTTP 传输事实。Phase 3 只做 WMS 业务 wire 翻译。可靠对象和 WMS 转发 RCS 的
Transport Adapter 均由 Phase 4 拥有。

系统部署在隔离局域网，WMS inbound 固定使用 `NONE`。该接线及原始 body 有界读取由 Phase 4 API ingress 拥有；Phase 3
normalizer 不接收认证 header、principal 或认证策略。目标态不建设 HMAC、nonce、clock、凭据、IP allowlist 或认证扩展 seam。

## 3. 目标态消费者矩阵

### 3.1 WMS 权威事实查询

| 能力候选 | 目标态消费者 | 业务依据 | 厂商初稿 | 当前裁决 |
| --- | --- | --- | --- | --- |
| `get_material` | 粗分机准入、装箱算法 | SRS §3.1.1、§3.3.1 | `GET /api/wms/materials/{material_id}` | 保留；完整 DTO/错误码待 WMS 确认 |
| `get_rack` | 当前作业货架身份与权威位置校验 | SRS §3.1.1、§3.3.1 | `GET /api/wms/racks/{rack_id}` | 保留；只允许点查，不同步全局货架主账 |
| `get_bin` | 当前料箱、料格和顶部物料校验 | SRS §3.1.1、§3.3.1 | `GET /api/wms/bins/{bin_id}` | 保留；只返回当前执行所需事实 |
| `get_grn` | 粗分机到货/GRN 归属校验 | SRS §3.2、§3.3.1 | `GET /api/wms/grn/{grn_id}` | 保留；完整 DTO/错误码待确认 |
| `list_grn_packages` | 当前 GRN 的料盘归属校验 | SRS §3.3.1 | `GET /api/wms/grn/{grn_id}/packages` | 保留；单次有界结果，不预建分页 |
| `query_inventory` | SRS 库存协同场景 | SRS §3.4.1 | `GET /api/wms/inventory/query` | 条件保留；不得用于 `PickingTask` 来源选择、锁定或重新分配 |
| `get_reservation` | 预留恢复、释放确认和人工对账 | SRS §3.4.1 | 初稿未定义 | 条件保留；只有 reserve/release wire 获批后才实施 |

上述查询只允许服务于展示、追溯或纯执行校验，不能由 WES 组合成业务结论。粗分准入必须由 WMS 返回一个封闭业务结果；
Phase 3 Task 1 需按真实消费者冻结 operation-specific 语义、request/result 和 wire，不复用旧
`validate_rough_sorter_admission` 名称、旧字段或旧实现作为合同。

### 3.2 WES → WMS 业务决策请求与事实通知

| 能力候选 | 目标态消费者 | 业务依据 | 厂商初稿 | 当前裁决 |
| --- | --- | --- | --- | --- |
| 粗分准入决策（业务名待合同冻结） | 粗分机当前料盘 | SRS §3.3.1 | 旧初稿曾提出复合校验 | 保留语义；WMS 返回允许/拒绝及业务处置，禁止 WES 组合原始事实自行判定 |
| 粗分目标料格决策（业务名待合同冻结） | 粗分机出料投放 | SRS §3.3.1、§3.4.2 | 初稿未定义 | 保留语义；WMS 返回唯一目标或等待/拒绝，WES 只校验物理可执行性 |
| `request_picking_source_bindings` | `STARTING` 的自动出库 `PickingTask` | SRS §3.3.3、出库设计 §5.2、§12 | 初稿未定义 | 保留业务名；method/path/wire 待 WMS 确认 |
| `reserve_inventory` | SRS 库存预留场景 | SRS §3.4.1 | `POST /api/wms/inventory/reserve` | 条件保留；必须明确非 `PickingTask` 的实际消费者 |
| `release_reservation` | 取消、失败和预留恢复 | SRS §3.4.1 | `DELETE /api/wms/inventory/reserve/{id}` | 条件保留；method 与幂等合同待 WMS 确认，不自行改成 POST |
| `confirm_inbound` | 粗分/自动入库物理完成 | SRS §3.3.1、§3.4.1 | 初稿未定义 | 保留；wire 待 WMS 确认 |
| `notify_picking_item_location_completed` | 自动出库单个任务项成功 PICK+PUT | SRS §3.3.3、出库设计 §8、§11 | 初稿未定义 | 保留；每个 `PkgID` 独立通知，不得由整单完成替代 |
| `transfer_inventory` | 自动分拣中已发生的非 PickingTask 库存位置变化 | SRS §3.3.2、§3.4.1 | `POST /api/wms/inventory/transfer` | 条件保留；不得与逐项出库位置事实双写 |
| `notify_pkg_binding` | 粗分机出料机械臂成功投放 | SRS §3.3.1 | `POST /api/wms/kitting/pkg-binding` | 保留；不得替代入库确认 |
| `report_picking_source_ng` | 自动出库来源 NG/身份异常 | SRS §3.3.3、出库设计 §9 | 初稿未定义 | 保留；只报告事实，不选择替代来源 |
| `confirm_picking_completed` | 自动出库任务全部物理完成 | SRS §3.3.3、出库设计 §11 | 初稿未定义 | 保留；独立于逐项位置事实，二者不得合并或双写 |
| `notify_manual_station_ready` | 人工分拣线 SCAN2 到位 | 最小架构 SPEC §12.4 | 初稿未定义 | 保留业务需求；wire 待 WMS 确认 |

`request_picking_source_bindings` 只允许返回覆盖全部目标任务项的原子来源绑定，或明确的“无完整方案”结果。后者不申请
目标架，由业务 owner 进入无目标架的 `WAITING_STOCK`；Phase 3 只翻译结果，不决定状态。完整绑定的
每一项都必须包含完整 SixInOne 与全局唯一 `PkgID`；部分绑定、缺项、重复项或字段不完整均不生效。
WMS 独立拥有库存资格、库存锁、料格冻结和跨任务来源分配，WES 不得通过 `query_inventory + reserve_inventory` 自行拼装方案。

除显式业务决策请求外，其他同步通知只表达已经发生的物理事实或明确的 WMS 业务义务。Adapter 的返回值不直接推进
WorkLine；Phase 4 可靠对象保存结果后，由对应执行对象校验关联并严格执行 WMS 结果，不得重新裁决其业务语义。逐项位置
事实与整单完成事实各自拥有独立可靠义务，不得复用 `transfer_inventory` 形成双写。

### 3.3 WMS → WES 业务输入

| 业务命令 | 目标态消费者 | 业务依据 | 当前裁决 |
| --- | --- | --- | --- |
| 创建 `PickingTask` | 自动出库插件 | SRS §3.3.3、出库设计 §5.1 | 保留；只含目标任务项，不含来源、SixInOne、`PkgID` 或具体目标架；wire 待 WMS 确认 |
| 更新排队任务优先级 | 自动出库队列 | 出库设计 §6 | 保留；只允许 `QUEUED` |
| 替代来源裁决 | 未完成任务项 | 出库设计 §5.3、§6、§9 | 保留；封闭结果为原子替代绑定批次或明确“无完整替代方案”；不得覆盖历史来源 |
| 恢复指定任务 | `PAUSED` / `WAITING_STOCK` 任务 | 出库设计 §6 | 保留；不得全局恢复 |
| 取消指定任务 | 非终态 `PickingTask` | 出库设计 §10 | 保留；不伪造已发出的物理动作终态 |
| 通知人工作业完成 | 人工分拣线 SCAN2 工作位 | 最小架构 SPEC §12.4 | 保留；WMS 是人工任务和扫码业务 owner |

Phase 3 只定义 operation 专属 DTO、显式 normalizer 方法与无状态校验/标准化，不提供字符串 selector 或 inbound registry。
先持久化 `InboundEvidence` 再 ACK、幂等、业务对象推进和恢复均由
Phase 4 负责；Phase 3 不单独建立可绕过可靠边界的 FastAPI 业务处理器。

“无完整替代方案”只有在与当前有效替代请求和绑定基线匹配时才是库存权威结果；未收到消息、传输未知或 WMS 仍在
处理不得映射为该结果。原子替代绑定批次还必须满足顶层设计的未完成、位置已知、无在途和无未知结果资格；任一项
不合格则整批不生效。请求关联、绑定代际、method/path 和 wire DTO 仍须由 WMS 批准。

## 4. 明确排除的旧候选

| 旧候选 | Successor / 裁决 |
| --- | --- |
| `get_materials` | `get_material`；没有真实批量消费者前不为减少往返预建接口 |
| `list_zones` / `list_locations` / `list_racks` | `NONE`；禁止把 WMS 全局主数据同步为 WES 本地能力 |
| `check_bin_drift` / `check_rack_drift` / `check_full_drift` | 普通权威点查 + Phase 4 对账；WMS 不接收 WES `workline_code` 做漂移判断 |
| 旧 `validate_rough_sorter_admission` wire | `NONE`；按 §3.2 重新批准 operation-specific 粗分业务决策，不兼容旧名称或字段 |
| `confirm_return_putaway` | `NONE`（当前阶段）；SRS §3.6 明确为未来交付 |
| `publish_manual_task` | `notify_manual_station_ready` + WMS → WES 人工作业完成；WMS 拥有人工任务 |
| 搬运/补给/换面/交换/料箱移动 | Phase 4 `Transport Port` 和 WMS 转发 RCS Adapter |
| 搬运取消及全部搬运 status | Phase 4 `Transport Port` |
| 通用 `request_load_unit_transport` | `NONE`；由 Phase 4 按真实 RCS/WMS 合同定义最小搬运 surface |

## 5. Wire 成熟度

厂商初稿只证明其中部分 WMS 业务接口曾被提出，并不证明当前 method、字段、错误码或幂等已经获批。当前存在以下
实施阻断：

- `release_reservation` 的 HTTP method 尚未由当前 WMS 合同确认。
- `request_picking_source_bindings` 的业务名及完整原子/无完整方案语义已冻结，但 method/path/request/result、错误码和幂等
  wire 尚未由 WMS 批准。
- `confirm_inbound`、逐项位置通知、NG 报告、PickingTask 完成和人工工作位交互缺少 WMS 批准的完整 wire。
- `query_inventory`、reserve/release 的当前非出库消费者需要业务方确认。
- `PickingTask` 六类 inbound 命令及替代来源两类封闭结果缺少双方批准的 method/path/DTO/幂等和拒绝码。

未关闭的能力不得生成生产 DTO、占位 endpoint 或宽泛 `dict[str, Any]` 接口。已确认能力也只能实现 WMS 明确批准的
字段闭集；WES 内部数据库 ID、`dispatch_key`、队列位置、投影版本和设备身份不得自动进入 wire。

## 6. Phase 3 实现合同

目标 Adapter 必须保持无状态：

```text
业务消费者
→ 类型化 WMS Query / Business Decision / Confirmation Port
→ 无状态 WMS ACL
→ Phase 2 OutboundHttpTransport（恰好一次 send）
→ 类型化单次调用结果
→ Phase 4 可靠对象决定持久化、发送、暂停、隔离或重提；不改变 WMS 业务语义
```

Adapter 不打开数据库事务，不保存调用 evidence，不申请 breaker permit，不自动 retry，不拥有业务终态。传输层已经提供的
超时、响应上限和交付事实不得在 Phase 3 复制。

每条 Port 只在首项对应职责的 operation 获批时创建；没有获批能力时不得预建空 Protocol、空 normalizer、通用成功容器
或 package 骨架。

### 6.1 单项能力批准模板

每项 outbound 或 inbound 能力必须在进入 TDD 前独立补齐以下信息；缺一项时该能力的代码、DTO 和测试保持不存在：

| 批准项 | 必填内容 |
| --- | --- |
| 业务定位 | 目标阶段、具体消费者、调用时机、原子边界、WMS 权威事实或业务义务 |
| Wire | operation 名、方向、method、relative path、闭集 request/result、必填性、错误码与拒绝语义 |
| 可靠语义 | 幂等字段及来源、安全重提是否允许；未批准时按不可安全重提处理 |
| 权威元数据 | version/time/provenance 的真实 wire 来源；WMS 不提供的字段不得由 WES 伪造 |
| 尺寸预算 | request/result 最大项数、最大编码/wire/decoded 字节数和超限语义；outbound response 预算由 Phase 2 primitive 逐请求执行 |
| 独立真源 | WMS 批准、版本化且机器可读的 fixture/schema；人类 Markdown 与实现方 MockTransport 均不能代替 |

新增 API 使用固定最小流程：登记消费者 → 完成上表批准 → 选择职责匹配的窄端口 → 增加操作专属 DTO 和一个显式
Gateway/Normalizer 方法 → 以获批 fixture/schema 在 `tests/contracts/wms_adapter/` 做 TDD → 同步矩阵与公开导出。每个
operation 使用一个独立小 PR；只有首项 outbound operation 同时建立最小 Gateway/factory，不捆绑其他未获批能力。
首个获批 operation 只作为结构参考纵切片，不升级为生成器、配置驱动 API、production registry、generic `call`、动态发现
或公共 `WmsCallSpec`。只有至少三个获批能力出现相同语义和相同约束时才提取共享 helper。

若获批 HTTP method 不在 Phase 2 `OutboundHttpMethod` 中，必须先以独立基础层 TDD 变更扩展 Phase 2；Phase 3 不得直接使用
HTTPX 或把 method 歪曲为现有值。

### 6.2 尺寸预算执行所有权

| 边界 | Owner | 必须执行的限制 |
| --- | --- | --- |
| Outbound request | Phase 3 operation DTO/encoder | send 前校验最大 items 和最大编码 body bytes；超限为合同无效且 0 次 send |
| Outbound response 读取 | Phase 2 Transport，预算由 Phase 3 operation 提供 | 每次请求显式设置获批 `OutboundHttpResponseLimits`；2 MiB wire / 4 MiB decoded 是默认值而非硬上限或业务批准值 |
| Outbound response 解析 | Phase 3 operation decoder | 完整解码后校验最大 items 与闭集 DTO；超限 fail closed，不返回截断或部分结果 |
| Inbound raw body | Phase 4 API ingress | normalizer 前执行获批的原始字节上限与隔离局域网 `NONE` 接线 |
| Inbound DTO | Phase 3 operation normalizer | 通过显式方法校验闭集字段和最大 items；不接收认证或持久化上下文 |

## 7. Phase 4 handoff

以下仅登记 Phase 4 业务需求，不在本文冻结其 Transport wire 或 ingress 实现：

- 空架补给、货架搬运和货架换面。
- 满箱交换。
- 五层货架料箱批次投入流水线和从退料口返库。
- 搬运 submit、status、cancel 及 typed terminal result。
- `TransportTask` 的持久化、批次成员、轮询、恢复和位置投影。
- WMS inbound API ingress 的隔离局域网 `NONE` 接线、原始 body 有界读取、显式 operation 路由、证据持久化和 ACK。

即使当前网络请求由 WMS 转发 RCS，这些能力仍按 Transport 语义归 Phase 4，不进入 Phase 3 WMS ACL。

## 8. Phase 3 Task 1 跨能力退出门禁

- 当前态全部业务判定都已映射为 WMS operation-specific 封闭结果，并登记目标阶段、具体组件、调用时机和 SRS/顶层设计
  依据；条件候选无法定位真实消费者时删除。
- 每个排除能力都登记 successor 或 `NONE`。
- 业务判定不组合多项 WMS 原始事实；纯执行校验确需组合时，已有共同 snapshot/version，或已批准读取顺序、有效窗口和
  fail-closed 条件。
- 列表/批量 request/result 已有最大项数、最大编码/wire/decoded 字节数和超限语义；outbound response 预算可由 Phase 2
  primitive 执行，超过其默认值时已有 WMS/业务批准的数值和理由。
- WMS inbound 已按隔离局域网部署约束冻结为 API ingress 所有的 `NONE`；该决定不进入 Phase 2 outbound Transport 或
  Phase 3 normalizer。
- §6.1 单项批准模板已冻结；全部 operation 的 wire 不要求在 Task 1 同时获批。
- Phase 3 与 Phase 4 Transport 合同没有重复 method 或生命周期 owner。
- SRS 只保留用户需求和权威边界，不复制 operation 编号或实现参数。
- 厂商原始文档保持原貌，项目内不再保留 33 项旧蓝图副本。

当前结论：`BLOCKED`。WMS 全业务决策/WES 纯执行决策、批准模板和隔离局域网 inbound `NONE` 已经冻结，但完整业务决策
operation/消费者盘点、纯执行多事实一致性与尺寸预算三项跨能力外部决定尚未关闭，Phase 3 代码实施不得开始。

Task 1 关闭后，每项 operation 只有完整通过 §6.1 才能独立进入 TDD；未获批 operation 保持不存在，不阻断其他已获批
operation。Phase 3 最终退出仍要求所有保留 operation 已获批并实施；无法获批或无法定位消费者的候选项直接删除，
不得保留占位 DTO、兼容接口或未来 seam。
