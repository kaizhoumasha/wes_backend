# WMS 业务 ACL 交互合同

> 状态：`DRAFT_CONSUMER_MATRIX`，Phase 3 Task 1 已重新打开，当前不得进入代码实施。
> 需求真源：`docs/architecture/SRS.md`。
> 架构真源：`docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md`。
> 出库业务真源：`docs/superpowers/specs/2026-08-06-wes-outbound-operation-top-level-design.md`。
> 外部输入：`docs/hardware/wms_rcs_interface_requirements.md`，原文只读保留，不自动成为实施合同。

## 1. 目的

本文只定义当前交付业务真实消费者所需的 WMS Anti-Corruption Layer（ACL）边界。Phase 3 的目标是无状态、
消费者驱动的 WMS 业务接入，不以旧代码、厂商初稿接口数量或历史 operation 编号维持固定 surface。

一项能力只有同时满足以下条件，才可进入 Phase 3：

1. 能追溯到当前 SRS 范围内的具名业务场景。
2. 能指出唯一或明确共享的调用消费者。
3. 交互对象是 WMS 拥有的业务、库存、单据或主数据事实。
4. method、path、request/result、必填性、错误码和幂等语义已经由 WMS 确认。

仅仅出现在硬件初稿、旧实现、旧测试或历史文档中的接口，不满足准入条件。

## 2. 边界

### 2.1 Phase 3 拥有

- WMS 权威业务事实的类型化查询 DTO。
- WES 向 WMS 请求 `PickingTask` 来源绑定及提交业务事实通知的类型化 DTO。
- WMS 向 WES 下发业务命令的类型化 DTO 和无状态校验/标准化。
- 固定 method/path、请求编码、响应解析和单次调用结果翻译。
- 一个明确配置的 WMS origin 和当前 `NONE` 认证决定。

### 2.2 Phase 3 不拥有

- 任何 RCS、AGV、CTU 搬运、补给、交换、旋转、状态查询或取消操作。
- `Transport Port`、`TransportTask`、搬运批次、队列位置、FIFO 窗口或位置投影。
- `WmsConfirmation`、`InboundEvidence` 的持久化、领取、重试、恢复或终态推进。
- 数据库表、Migration、Repository、调用 evidence、Circuit Breaker 或清理任务。
- WorkLine Decision、库存分配算法、库存锁、料格冻结、跨任务来源分配、设备命令和厂商 Payload。
- 通用 `call`、动态 registry、Provider 切换、兼容 alias、fallback、缓存或自动续页。

Phase 2 只提供单次 HTTP 传输事实。Phase 3 只做 WMS 业务 wire 翻译。可靠对象和 WMS 转发 RCS 的
Transport Adapter 均由 Phase 4 拥有。

## 3. 当前消费者矩阵

### 3.1 WMS 权威事实查询

| 能力候选 | 当前消费者 | 业务依据 | 厂商初稿 | 当前裁决 |
| --- | --- | --- | --- | --- |
| `get_material` | 粗分机准入、装箱算法 | SRS §3.1.1、§3.3.1 | `GET /api/wms/materials/{material_id}` | 保留；完整 DTO/错误码待 WMS 确认 |
| `get_rack` | 当前作业货架身份与权威位置校验 | SRS §3.1.1、§3.3.1 | `GET /api/wms/racks/{rack_id}` | 保留；只允许点查，不同步全局货架主账 |
| `get_bin` | 当前料箱、料格和顶部物料校验 | SRS §3.1.1、§3.3.1 | `GET /api/wms/bins/{bin_id}` | 保留；只返回当前执行所需事实 |
| `get_grn` | 粗分机到货/GRN 归属校验 | SRS §3.2、§3.3.1 | `GET /api/wms/grn/{grn_id}` | 保留；完整 DTO/错误码待确认 |
| `list_grn_packages` | 当前 GRN 的料盘归属校验 | SRS §3.3.1 | `GET /api/wms/grn/{grn_id}/packages` | 保留；单次有界结果，不预建分页 |
| `query_inventory` | SRS 库存协同场景 | SRS §3.4.1 | `GET /api/wms/inventory/query` | 条件保留；不得用于 `PickingTask` 来源选择、锁定或重新分配 |
| `get_reservation` | 预留恢复、释放确认和人工对账 | SRS §3.4.1 | 初稿未定义 | 条件保留；只有 reserve/release wire 获批后才实施 |

粗分准入由 WES 使用上述 WMS 权威事实作出本地判定，不再建设复合
`validate_rough_sorter_admission` WMS 决策接口。WMS 提供事实，WES 负责工作线准入规则。

### 3.2 WES → WMS 业务请求与事实通知

| 能力候选 | 当前消费者 | 业务依据 | 厂商初稿 | 当前裁决 |
| --- | --- | --- | --- | --- |
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

除来源绑定请求外，其他同步通知只表达已经发生的物理事实或明确的 WMS 业务义务。Adapter 的返回值不直接推进
WorkLine；Phase 4 可靠对象保存结果后，由对应业务对象重新裁决。逐项位置事实与整单完成事实各自拥有独立可靠义务，
不得复用 `transfer_inventory` 形成双写。

### 3.3 WMS → WES 业务输入

| 业务命令 | 当前消费者 | 业务依据 | 当前裁决 |
| --- | --- | --- | --- |
| 创建 `PickingTask` | 自动出库插件 | SRS §3.3.3、出库设计 §5.1 | 保留；只含目标任务项，不含来源、SixInOne、`PkgID` 或具体目标架；wire 待 WMS 确认 |
| 更新排队任务优先级 | 自动出库队列 | 出库设计 §6 | 保留；只允许 `QUEUED` |
| 替代来源裁决 | 未完成任务项 | 出库设计 §5.3、§6、§9 | 保留；封闭结果为原子替代绑定批次或明确“无完整替代方案”；不得覆盖历史来源 |
| 恢复指定任务 | `PAUSED` / `WAITING_STOCK` 任务 | 出库设计 §6 | 保留；不得全局恢复 |
| 取消指定任务 | 非终态 `PickingTask` | 出库设计 §10 | 保留；不伪造已发出的物理动作终态 |
| 通知人工作业完成 | 人工分拣线 SCAN2 工作位 | 最小架构 SPEC §12.4 | 保留；WMS 是人工任务和扫码业务 owner |

Phase 3 只定义 DTO 与无状态校验/标准化。先持久化 `InboundEvidence` 再 ACK、幂等、业务对象推进和恢复均由
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
| `validate_rough_sorter_admission` | WMS 权威事实查询 + WES 本地准入 Decision |
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
→ 类型化 WMS Query / Picking Source Binding / Confirmation Port
→ 无状态 WMS ACL
→ Phase 2 OutboundHttpTransport（恰好一次 send）
→ 类型化单次调用结果
→ Phase 4 可靠对象决定持久化、暂停、恢复或重提
```

Adapter 不打开数据库事务，不保存调用 evidence，不申请 breaker permit，不自动 retry，不拥有业务终态。传输层已经提供的
超时、响应上限和交付事实不得在 Phase 3 复制。

## 7. Phase 4 Transport handoff

以下仅登记 Phase 4 业务需求，不在本文冻结其 wire：

- 空架补给、货架搬运和货架换面。
- 满箱交换。
- 五层货架料箱批次投入流水线和从退料口返库。
- 搬运 submit、status、cancel 及 typed terminal result。
- `TransportTask` 的持久化、批次成员、轮询、恢复和位置投影。

即使当前网络请求由 WMS 转发 RCS，这些能力仍按 Transport 语义归 Phase 4，不进入 Phase 3 WMS ACL。

## 8. Phase 3 Task 1 退出门禁

- 每个保留能力都有具名当前消费者和 SRS/顶层设计依据。
- 每个排除能力都登记 successor 或 `NONE`。
- WMS 已批准全部保留能力的 method/path/DTO/错误码/幂等语义。
- Phase 3 与 Phase 4 Transport 合同没有重复 method 或生命周期 owner。
- SRS 只保留用户需求和权威边界，不复制 operation 编号或实现参数。
- 厂商原始文档保持原貌，项目内不再保留 33 项旧蓝图副本。

当前结论：`BLOCKED`。PickingTask 两阶段来源绑定、逐项位置事实和整单完成的业务语义已经冻结，但保留能力的 WMS
wire 批准及条件候选的消费者确认尚未完成，Phase 3 代码实施不得开始。
