> **拆分说明**: 本文档从 `workline-and-plugin-restructuring.md` v7 拆分（2026-07-10）。
> 完整设计目标与 frontmatter 仍以原文件为准, 详见同目录 index。
> 章节对应: interface = 原文件 §5 接口设计。

---

## 5. 接口设计

### 5.1 wms_integration 能力面 Port 详细

`wms_integration` 是 WMS 能力面 port 的统一入口。可复用现有 `wms_integration` 中已验证的 ACL 能力，但允许破坏性整理包结构、import 路径和 API。现有 `WmsInventoryPort` 必须破坏性拆分为只读 `WmsInventoryQueryPort` 和事务型 `WmsInventoryTransactionPort`。

| Port | 职责 | 现状 | 关键方法 |
| --- | --- | --- | --- |
| `WmsMasterDataPort` | 查询/校验货架、料箱、库位、物料、区域、地码等元数据 | **新增** | `get_material` / `list_materials` / `get_zone` / `list_locations` / `get_rack` / `get_bin` / `validate_rack_bin` |
| `WmsDocumentPort` | 只读查询 GRN、入库单、出库单、批次单、波次、业务任务快照 | **新增** | `get_grn` / `list_grn_packages` / `get_inbound_order` / `get_outbound_order` / `get_batch_order` / `get_task_snapshot` |
| `WmsInventoryQueryPort` | 查询库存、箱位、货架占用、可用容器等外部事实 | **由现有 `WmsInventoryPort` 只读能力迁出** | `query_inventory` / `query_empty_bins` |
| `WmsInventoryTransactionPort` | 库存预留、释放、转移确认等会改变 WMS 事务状态的能力 | **由现有 `WmsInventoryPort` mutation 能力迁出** | `reserve_inventory` / `release_reservation` / `confirm_transfer` |
| `WmsFulfillmentPort` | 请求外部系统执行搬运、补给、移出、换面、投箱、取箱和满箱交换 | **新增** | `request_rack_supply` / `request_rack_transport` / `change_rack_face` / `full_box_exchange` / `notify_pkg_binding` / `move_bin_to_conveyor_entry` / `move_bin_from_conveyor_exit` |
| `WmsEventPort` | 接收 WMS 状态变化、RCS 结果、任务结果、异常通知 | **新增**（部分实现于 callback_normalizer） | `WMS_GRN_RECEIVED` / `WMS_PALLET_ARRIVED` / `WMS_RACK_ARRIVED` / `WMS_TRANSPORT_COMPLETED` / `WMS_EXCHANGE_COMPLETED` |
| `WmsReconciliationQueryPort` | 只读拉取 WMS 权威事实、版本和 drift snapshot，用于对账 WES 作业期投影 | **新增** | `check_bin_drift` / `check_rack_drift` / `check_workline_drift` / `check_full_drift` |

**任务变化边界**：WMS 任务变化推送（如 `WMS_TASK_CHANGE`）属于 `WmsEventPort` / `InboundNormalizerProfile`，只能经 callback API 写 `RuntimeInbox`；`WmsDocumentPort` 只提供当前单据/任务快照查询，不提供订阅、push 或入站事件入口。

**对账端口边界**：`WmsReconciliationQueryPort` 是只读 QueryPort，不是 EffectPort。它只返回 WMS 权威事实、`source_version` 和 drift snapshot，不创建 `RuntimeIntentLog`，不向 WMS 写入确认/修正，不直接写 `ReconciliationRecord`。本地冲突登记、`RuntimeHold`、`resolution_decision` 和 audit log 归 `reconciliation` 域的 `ReconciliationManager`。若未来确需向 WMS 发起对账确认、库存更正或履约补偿，必须归入明确的 `WmsInventoryTransactionPort` 或 `WmsFulfillmentPort` action，并走 `RuntimeIntentLog + EffectPort`，不得把只读对账查询端口升级成通用副作用端口。

**`wms_rcs_interface_requirements.md` 到 port 的映射**：

| 来源接口/事件 | 目标 port | 目标态说明 |
| --- | --- | --- |
| `GET /api/wms/materials/{id}` / `GET /api/wms/materials?ids=...` | `WmsMasterDataPort` | 物料主数据按需查询；结果可 30s TTL 缓存 |
| `GET /api/wms/zones` / `GET /api/wms/locations?zone=...` | `WmsMasterDataPort` | 区域/地码用于设备归属、资源边界和履约目标校验 |
| `GET /api/wms/racks/{id}` / `GET /api/wms/bins/{id}` / `GET /api/wms/racks?type=...` | `WmsMasterDataPort` | 货架/料箱主数据与状态按需引用，不复制为 WES 主数据 |
| `GET /api/wms/grn/{id}` / `GET /api/wms/grn/{id}/packages` | `WmsDocumentPort` | GRN 与料盘归属用于作业上下文和 PKG 校验 |
| `GET /api/wms/inventory/query` | `WmsInventoryQueryPort` | 库存查询实时透传 WMS；WES 只可短 TTL 缓存 |
| `POST /api/wes/rack-supply-request` / `POST /api/wes/transport-request` | `WmsFulfillmentPort` | WES 生成搬运需求；WMS 统一调度 RCS |
| `POST /api/wms/kitting/pkg-binding` | `WmsFulfillmentPort` | WES 作业结果通知 WMS；属于出站 effect，必须走 `RuntimeIntentLog` + EffectPort，不允许进入只读 `WmsDocumentPort` |
| `POST /api/wms/inventory/reserve` / `DELETE /api/wms/inventory/reserve/{id}` / `POST /api/wms/inventory/transfer` | `WmsInventoryTransactionPort` | 库存预留、释放、转移确认必须以 WMS 事务结果为准；必须走 `RuntimeIntentLog` + EffectPort，不允许作为查询能力直调 |
| `POST /api/v1/callback/event` / `POST /api/v1/callback/result` | `WmsEventPort` / `DeviceEventPort` → `RuntimeInbox` | 统一回调入口；按 source 路由到 WMS/RCS/ECS/device normalizer；ACK 后写 inbox，不直接改 session |

**ExternalContractProfile（外部合同 profile）**：

| 字段 | 语义 |
| --- | --- |
| `provider_code` | WMS / ECS / RCS / provider-specific code |
| `contract_version` | 外部合同版本；必须写入 evidence、callback envelope 和 trace attributes |
| `runtime_capabilities` | provider 支持且可注入 `RuntimeCapabilityContext` 的 query/effect 能力集合 |
| `inbound_normalizers` | provider 支持的 callback/event/result normalizer 能力集合；只允许写 `RuntimeInbox` |
| `field_mapping` | 外部字段到 WES typed port DTO 的声明式映射；只存在于 adapter/normalizer |
| `unsupported_actions` | 明确不支持动作；admission 必须 fail fast，不得运行中隐式降级 |
| `timeout_policy` | provider 级 timeout、retry、breaker、backoff 约束 |
| `fixture_set` | contract tests 与 simulator 使用的 fixture 集 |

**合同版本规则**：

- WES 内部域只识别 typed port contract；外部合同变化只能落在 `ExternalContractProfile` 和 adapter/normalizer。
- 合同 profile 可破坏性替换，不保留旧中台兼容入口；但同一 `ExecutionSession` 固定 `provider_code + contract_version`，不在 RUNNING 期间热切。
- 每个 provider profile 必须配套 contract tests、sample callback、error fixture 和 replay scenario。
- 未声明的 `runtime_capabilities` 不得进入 `RuntimeCapabilityContext`；未声明的 `inbound_normalizers` 不得被 callback API 接收。
- profile 未声明的字段不得进入 runtime capability；必须被 normalizer 丢弃或写入诊断 evidence。
- `field_mapping` 不承载业务分支、计算规则或流程决策；复杂转换必须在 adapter/normalizer 代码中实现，并由 contract tests 覆盖。

**标准履约意图**：

| 意图 | 当前执行方式 | 说明 |
| --- | --- | --- |
| `SUPPLY_EMPTY_SINGLE_LAYER_RACK` | 请求 WMS 履约接口 | 为粗分机补充带空料箱的单层货架；对应 `/api/wes/rack-supply-request` |
| `REMOVE_LOADED_SINGLE_LAYER_RACK` | 请求 WMS 履约接口 | 将粗分机上已载有料盘/物料的单层货架移出到满箱交换区、交换决策点、分拣机 STATION 或排队区；对应 `/api/wes/transport-request` |
| `POSITION_FIVE_LAYER_RACK` | 请求 WMS 履约接口 | 将五层货架从原料仓移动到分拣机工作位，或从工作位移出 |
| `CHANGE_RACK_FACE` | 请求 WMS 履约接口 | 请求货架原地换面 |
| `FULL_BOX_EXCHANGE` | 请求 WMS 履约接口 | 满箱交换区内由 WMS 调度 CTU 执行箱级入库交换；WES 只提交履约意图和接收 evidence |
| `MOVE_BIN_TO_CONVEYOR_ENTRY` | 请求 WMS 履约接口 | 从工作位货架取指定料箱，送入分拣机入口 |
| `MOVE_BIN_FROM_CONVEYOR_EXIT` | 请求 WMS 履约接口 | 从分拣机出口取料箱，送回工作位货架指定位置 |
| `NOTIFY_PKG_BINDING` | 通知 WMS 作业结果 | 将 PKG 与料箱/料格/货架绑定结果通知 WMS；对应 `/api/wms/kitting/pkg-binding` |

**WES 只定义本系统侧的履约意图、请求字段、幂等证据、回调接收和状态处理**。外部 WMS 如何选择货架、计算箱位、规划库位或调度 AGV/CTU，不在本系统规划内。

**物理事实与 WMS 业务确认顺序**：

设备/ECS callback `SUCCESS` 是现场物理事实，WES 必须先基于该 evidence 消费预约、写入 `RuntimeLocationEvent`、更新 `BinMaterialMount / BinCellOccupancy / MaterialUnit.location_summary` 等作业期投影，再通过 `RuntimeIntentLog + WmsFulfillmentPort / WmsInventoryTransactionPort` 通知 WMS PKG 绑定或库存事务。WMS effect 失败不允许抹掉已确认的本地物理位置事实；必须进入 WMS 同步 `RuntimeHold` / `ReconciliationRecord`，等待 WMS 重试、人工 reconcile 或外部更正。

**批量履约语义**：

CTU 入线、退线和货架补给这类外部动作可能表现为“一次请求、多对象、多阶段回调”。目标态只建一个 `WmsFulfillmentRequest`
父请求，但必须为每个被搬运对象生成可追踪的子 evidence，并把子 evidence 投影为 `RuntimeLocationEvent`：

- 父请求记录批次级约束、幂等键、请求 hash、WMS/RCS 接收状态和批次完成结果。
- 子 evidence 的 owner 是对应对象的 `ExecutionWorkItem`；不另建与 runtime 并列的子履约状态源。每个 CTU 子 work item 必须记录批次序号、placeholder_key、resolved_bin_id、阶段状态和 evidence。
- 子 evidence 记录单个料箱/货架/料盘的阶段结果，例如从五层货架取出、进入 CTU 背篓、移动到入口、投入滚筒线、从退料线取出、放回货架。
- 批次完成 callback 只能证明外部系统声明批次完成；Runtime 仍必须校验所有子对象 evidence、active projection 和队列 membership 已收敛后，才能关闭本地 `HandlingOperation`。
- 子对象 evidence 缺失、重复、乱序或与 active projection 冲突时，父请求不得被本地标记为业务完成，必须进入 `RECONCILING` 或 `RuntimeHold`。
- 批次约束由 WES 按作业期投影和 WMS 查询结果计算，例如 `min(入口线空位, CTU 背篓容量, 五层货架可用料箱数)`；WMS/RCS 如何规划路径与调度车辆仍归外部系统。

### 5.2 plane 接口（运营敏感数据入口）

**适用前端范围**：P0 plane 接口只面向操作员终端、工程调试台和只读可视化大屏；不面向公开报表、客户门户或 WMS 全局库存查询。前端不得绕过 `PlaneSceneView + PlaneSnapshot + plane/events` 直接拼接 resource/material/device/runtime 散表。

**接口设计**（首版**禁止**聚合接口）：

```text
GET /worklines/{id}/plane/scene
  -> PlaneSceneView
  鉴权: biz:workline:view-plane-scene
  频率: 1 Hz 轮询足够（manifest 派生，变化慢）

GET /worklines/{id}/plane/snapshot
  -> PlaneSnapshot
  鉴权: biz:workline:view-plane-snapshot
  频率: 实时刷新（SSE/WebSocket 或 250ms 轮询）

GET /worklines/{id}/plane/events  (后续, 不强制)
  -> SSE/WebSocket, 基于 object_transition_events + device event/result + handling request status changes
```

**实时性分级决策（M1 回归）**：

- `plane/scene`：manifest 派生，首版只支持 1 Hz 轮询；不提供 SSE/WebSocket。
- `plane/snapshot`：active projection 派生，首选 SSE 单向流；断线或客户端不支持 SSE 时 fallback 到 250ms 轮询。
- `plane/events`：增量事件流，只使用 SSE；不引入 WebSocket，避免双向通道把前端动作混入读模型。
- 首版不实现完整数字孪生，只保证平面展示可按上述接口得到稳定 scene、当前 snapshot 和后续增量事件。

**安全门禁**：

| 维度 | 要求 |
| --- | --- |
| 鉴权 | 拆 `biz:workline:view-plane-scene` / `biz:workline:view-plane-snapshot` 两套权限 |
| 行级过滤 | 默认用户只能读自己 WorkLine 域内的 WorkLine；跨域读需 `wes.observer` 角色 |
| 脱敏 | `evidence_json` 默认脱敏（`pkg_code` 后 4 位掩码、`bin_code` 前缀掩码），仅 `wes.engineer` 角色可见全量 |
| Audit log | 每次 plane 读取写 `audit_logs`：`viewer_user_id, viewer_ip, snapshot_version, snapshot_status, result_size, read_at` |

**PlaneSceneView schema**（实施细节在 Phase 3 SPEC 展开）：

```text
PlaneSceneView
  schema_version          (string, 当前 "1.0")
  generated_at            (ISO timestamp)
  workline_id
  workline_code
  nodes[]
    node_id
    node_type = CONVEYOR | QUEUE | ENTRY_POINT | EXIT_POINT | DEVICE | RACK_POSITION
    ref_code               (稳定 identifier)
    label                  (i18n 字符串)
    role
    layout
    capacity
    order_policy
  edges[]
    from_node_id
    to_node_id
    edge_type = MATERIAL_FLOW | OPERATION | QUEUE_FLOW | EXTERNAL_TRANSFER
  warnings[]
    code
    message
    evidence
```

**PlaneSnapshot schema**（实施细节在 Phase 3 SPEC 展开）：

```text
PlaneSnapshot
  workline_id
  schema_version          (string, 当前 "1.0")
  generated_at            (ISO timestamp)
  stale_threshold_seconds (int, 默认 30)
  snapshot_status         (enum: OK | EMPTY | CONFLICTS_ONLY | STALE | RECONCILING)
  active_material_units[] (限 active 30 天内; 超限 -> truncated=true)
  active_bins[]           (presence_type 目标态枚举)
  queue_memberships[]     (上限 200, top by entered_at desc)
  devices[]               (上限 50, by last_event_at desc)
  resource_projections[]  (上限 200)
  in_transfer[]           (限 active 30 天内, 限 100 条)
  conflicts[]             (top 50 by detected_at desc)
  warnings[]
  truncated               (bool)
  total_counts            (Map<list_name, int>)
```

**目标态枚举值**：

- `presence_type ∈ {ON_CONVEYOR, AT_WORK_POSITION, IN_TRANSFER, UNKNOWN}`
- `queue_role ∈ {BUFFER, INFEED, SCAN, WORKSTATION, EXIT, NG_REJECT, RETURN}`
- `snapshot_status ∈ {OK, EMPTY, CONFLICTS_ONLY, STALE, RECONCILING}`

### 5.3 External callback 鉴权

**入口**：统一 `src/app/callback/v1/callback.py:91`（不引入新 callback 路径）

**目标态约束**：

- `docs/integration/wms_rcs_interface_requirements.md` 的 WMS/RCS 回调与 `docs/integration/third_party_integration_whitepaper.md` 的 ECS/device 回调共用入口，但必须按 `source_system + provider_code + callback_type` 路由到不同 normalizer。
- 联调白皮书中的 Bearer Token 可选口径只作为历史输入；目标态中任何外部 callback 都必须通过 HMAC body 签名、timestamp、nonce、source allow-list 和幂等校验。
- Callback API 不承载业务决策；只做鉴权、schema normalize、原始日志、幂等校验、ACK、写 `RuntimeInbox`。
- `DeviceEventPort` 处理 ECS/device 的 `/api/v1/callback/event` 与 `/api/v1/callback/result`；`WmsEventPort` 处理 WMS/RCS 事件；二者不得互相复用 DTO 或 provider exception。

**Raw body 与 EvidenceEnvelope 分层**：

| 层 | 输入/输出 | 规则 |
| --- | --- | --- |
| Raw callback body | 外部系统原始 JSON | 保持供应商合同原样，用于 HMAC、原始日志和重放；不要求外部系统提交内部 `EvidenceEnvelope` |
| Callback auth envelope | method/path/header/timestamp/nonce/body_hash/app_id | 只做身份、签名、防重放、allow-list 和幂等预检 |
| Provider normalizer | raw body -> normalized callback DTO | WMS/RCS/ECS/device 各自 normalizer 负责字段映射、缺字段诊断和 provider error code 转换 |
| EvidenceEnvelope | normalized DTO -> 内部 evidence | 只在 WES 内部生成；写入 RuntimeInbox、diagnostic、timeline 或 projection evidence |

外部 body 签名覆盖的是 raw body；`EvidenceEnvelope` 的 schema 校验发生在 normalizer 之后。供应商 DTO、HTTP client、provider exception 仍只能存在于对应 ACL/normalizer 层。

**签名 canonical string**：

```text
canonical = method + "\n" + path + "\n" + timestamp + "\n" + nonce + "\n" + sha256(body) + "\n" + app_id
signature = HMAC-SHA256(secret, canonical)
```

**鉴权矩阵**：

| 字段 | 要求 |
| --- | --- |
| `provider_code` | 必填，`WMS` / `RCS` / `ECS` / `AGV` / `CTU` / provider-specific code |
| `source_system` | 必填，必须命中启用状态的外部系统 allow-list |
| `app_id` | 必填，绑定 secret、IP allow-list、provider_code 和 callback_type allow-list |
| `signature` | 必填，HMAC-SHA256 覆盖 method/path/timestamp/nonce/body/app_id |
| `timestamp` | 必填，与 WES 时钟偏差 > 30s 拒绝 |
| `nonce` | 必填，按 `app_id` 做 5 分钟 TTL 去重 |
| `callback_type` | 必填，必须匹配 provider + 未终结 request/command/session allow-list |
| `source_event_id` | 必填；ECS/device event 使用 `data.event_id`，result 使用 `command_code`，WMS/RCS 使用 `request_id` 或业务事件 id |
| `body` | 必填，外部原始 JSON；通过 normalizer 后必须生成合法 typed `EvidenceEnvelope` |

**Body 完整性**：signature 校验失败立即返回 401，**不触发**业务处理；防重放窗口 5 分钟。

**callback_type allow-list**（实施细节在 Phase 3 SPEC 展开）：

- WMS/RCS：`WMS_GRN_RECEIVED` / `WMS_PALLET_ARRIVED` / `WMS_RACK_ARRIVED` / `WMS_TRANSPORT_COMPLETED` / `WMS_EXCHANGE_COMPLETED` / `WMS_INVENTORY_UPDATED` / `WMS_TASK_CHANGE` / `WMS_REJECTED` / `WMS_FAILED`
- ECS/device：`DEVICE_RESULT` / `DEVICE_EVENT` / `DEVICE_STATUS_CHANGED` / `MATERIAL_ARRIVED` / `SCAN_COMPLETED` / `ESTOP_PRESSED` / `DEVICE_ERROR` / `DEVICE_ONLINE` / `DEVICE_OFFLINE`

**统一入口约束**：WMS/RCS 事件沿用 `docs/integration/wms_rcs_interface_requirements.md` 的统一 callback 语义；ECS/device 事件沿用 `docs/integration/third_party_integration_whitepaper.md` 的 Command-Ack-Callback 语义。目标态中 callback API 只做签名校验、幂等校验、原始日志、normalizer 调用、ACK、写 `RuntimeInbox`，不得直接修改 `ExecutionSession`、`DeviceRuntime`、`MaterialUnit` 或投影表。

### 5.4 idempotency_key 规范

**复合主键**：

```text
idempotency_keys:
  PRIMARY KEY (provider_code, operation_kind, idempotency_key)
  request_hash          (immutable)
  execution_correlation_id  (correlation key)
  business_owner_key
  created_at            (TTL 30 天)
```

**provider_code**：`"WES" / "WMS" / "ECS" / "RCS" / "AGV" / "CTU"` 及 provider-specific code，跨域跨 provider 隔离。

**operation_kind**：`"fulfillment" / "callback" / "device_command" / "device_event" / "reconciliation"`。

**Phase 3 审计矩阵**：

| canonical operation_kind | domain | aliases / legacy inputs |
| --- | --- | --- |
| `callback` | `callback` | `external_callback`, `wms_callback`, `rcs_callback` |
| `fulfillment` | `wms_integration` | `FULFILLMENT`, `wms_fulfillment` |
| `device_command` | `device` | `DISPATCH_COMMAND`, `DEVICE_DISPATCH`, `device_dispatch` |
| `device_event` | `device` | `command_result`, `event_push` |
| `reconciliation` | `reconciliation` | `runtime_reconciliation`, `resource_reconciliation` |

审计 payload 必须保留原始 `operation_kind`，同时输出 `normalized_operation_kind`、`domain`、`status_code=409` 和 `security_control=idempotency_key_request_hash`，避免跨域调用点用临时日志字段代替安全审计。

**idempotency_key**：调用方提供的业务键，跨域跨 provider 唯一。

**WES 内部 key 命名空间（AUTHORITY_METADATA_BOUNDARY 回归）**：

- WES 内部生成的 key 不得使用裸 `callback-timeout:{id}` / `dispatch-ack-exhausted:{id}` / `safety-estop:{id}` 格式。
- 内部 key 统一格式：`WES-{OPERATION_KIND}-{DETERMINISTIC_HASH(source_id, source_event_id, correlation_id)}`。
- 内部 `operation_kind` 必须细分为 `wes-callback-timeout` / `wes-dispatch-ack-exhausted` / `wes-safety-estop` / `wes-resource-reconciliation` / `wes-manual-replay` 等，不得全部塞入通用 `reconciliation`。
- 外部 provider 传入的 `idempotency_key` 保持原样存储，但必须与 `provider_code + operation_kind` 共同组成命名空间；WES 生成 key 必须使用 `provider_code=WES` 且 operation_kind 细分。
- 同一 `provider_code + operation_kind + idempotency_key` 下不同 `request_hash` 必须 409；不同 provider 或不同 operation_kind 下同名 key 不视为冲突。

**行为**：

- 同 key 不同 `request_hash` → `409 Conflict` + 安全审计事件（**不静默**返回旧 record）
- 同 key 同 `request_hash` → 直接返回旧 record（不重新走状态机）
- 30 天 TTL 后允许同 key 不同 hash 覆盖

**现有实现迁移**：`runtime_hold` 的 `UniqueConstraint("source_idempotency_key")` 需迁移到复合主键。

### 5.5 域内 API 边界

**域间 API 调用规则**：

- 域间通过 port 接口调用（`WmsFulfillmentPort` / `EffectPort` 等）
- 域间不直接 import 对方模型类（避免强耦合）
- 域间返回值通过 typed Pydantic 模型
- 域间不直接访问对方数据库（必须通过对方 repository）

**域内 API 规则**：

- 域内 Service 通过 repository 访问数据库
- 域内 repository 不跨域访问
- 域内 Service 负责业务逻辑、跨 repository 协调
- 域内 Model 由 ModelFactory 派生 Schema

---

