# ADR: WES/WMS/RCS 运行时资源边界

## 状态

Accepted - 2026-05-13

## 背景

`docs/superpowers/specs/2026-05-13-smt-execution-resource-model-design.md` 要求 WES 建立运行时资源事实层，用于追踪 WorkLine、Device、Rack、Bin、Material、Location 和 ExchangeTask 的执行证据。

现有 `docs/architecture/SRS.md` 同时存在两个口径：

- WES 采用纯代理模式，不维护库存主账，库存数量、预留、扣减和账务由 WMS 负责。
- SMT 混合入库章节曾写到 WES 锁定五层空箱、交换两个容器库存属性、Pick_Fail 后自动扣减库存。

这两个口径不能同时进入实现。否则资源模块会变成影子 WMS，满箱交换也会在 WES 与 WMS/RCS 之间形成双真相源。

## 决策

1. WMS 是库存、预留、扣减、账务、SAP 同步和空箱资源授权的唯一权威。
2. WES 可以持久化执行事实、过程快照、资源关系投影、回写证据和对账证据，但这些事实不能作为库存可用性、库存属性交换、库存扣减或资源授权的本地主账。
3. 满箱交换 v1 中，WES 不锁定五层货架空箱，不本地判断交换区空位，不交换两个容器库存属性。WES 只提交 `FULL_BIN_EXCHANGE` 外部请求，等待 WMS/RCS 回调。
4. 生产发料、Pick_Fail、退料和入库确认中，WES 不自动扣减库存。WES 记录设备失败、创建 RuntimeHold 或诊断，并等待 WMS 确认、拒绝或人工授权。
5. WMS/RCS 执行类回调统一走 `/api/v1/callback/external`。`/api/v1/callback/event` 保留给设备事件、历史 WMS 业务通知或非运行时等待事件；同一个运行时任务不得同时使用两个入口。
6. WMS/RCS 回调必须携带稳定业务键、来源事件 ID、来源版本、发生时间和签名证据。WES 只在字段完整且版本可信时推进当前资源投影。
7. `ResourceStateEvent` 是 append-only 事实账本；`RackPlacement`、`RackBinMount`、`RackMaterialMount` 是当前关系投影。冲突、乱序、迟到或缺字段只能追加 evidence，并进入 `RECONCILING` / RuntimeHold / 资源对账，不能静默覆盖。

## WMS/RCS 执行回调最小合同

适用于满箱交换、货架到达、搬运完成和后续需要恢复 `WAITING_EXTERNAL` Session 的外部执行结果。

| 字段 | 要求 |
| --- | --- |
| `callback_type` | 必填，例如 `WMS_FULL_BOX_EXCHANGE_RESULT`。 |
| `trace_id` | 必填，恢复 WES Runtime trace。 |
| `exchange_request_code` / `dispatch_key` | 至少一个必填，用于定位外部请求。满箱交换两者都应携带。 |
| `rack_release_id` | 满箱交换必填，必须与 Session context 一致。 |
| `wms_rcs_task_id` | WMS/RCS 侧任务 ID。 |
| `source_system` | `WMS` 或 `RCS`。 |
| `source_event_id` | 必填，来源侧稳定事件 ID，优先用于幂等。 |
| `source_version` | 必填，来源侧单调版本或业务版本。旧版本只能入 evidence。 |
| `occurred_at` | 必填，来源事实发生时间。 |
| `received_at` | WES 接收时间，由 WES 记录。 |
| `exchange_status` | 满箱交换状态。见下方状态语义。 |
| `post_exchange_relations` | `PHYSICAL_COMPLETED` / `RESOURCE_PROJECTED` 前必填；缺失时进入对账，不更新 active mount。 |
| `wms_confirmation` | `WMS_CONFIRMED` 必填，包含 WMS 单据、库存版本或确认引用。 |
| `signature` / `timestamp` / `request_id` | 必填，用于签名、时间窗和重放防护。 |

满箱交换状态必须拆分：

| 状态 | WES 处理 |
| --- | --- |
| `ACCEPTED` / `QUEUED` / `IN_PROGRESS` | 更新任务证据和 context，保持 `WAITING_EXTERNAL`。 |
| `PHYSICAL_COMPLETED` | 只表示外部物理动作完成；若缺 `post_exchange_relations`，进入 `RECONCILING`。 |
| `RESOURCE_PROJECTED` | WES 已根据可信关系事实更新当前投影。 |
| `WMS_CONFIRMED` | WMS 已确认库存、单据或业务版本。 |
| `BUSINESS_COMPLETED` | 物理动作、资源投影和 WMS 确认均完成，Session 才能业务闭环。 |
| `WMS_REJECTED` / `REJECTED` / `FAILED` / `CANCELLED` | 阻断并创建诊断或 RuntimeHold。 |

## Runtime 外部请求规则

1. 插件通过 `RuntimeIntent.external_request(...)` 表达通用外部系统请求，Runtime 统一创建 `WorklineOutbox(EXTERNAL_HTTP)`、Timeline 和等待状态。
2. 插件也可以通过领域化 `RuntimeIntent.rack_operation_request(...)` 表达 WES 单层货架搬运、交换、旋转或补给需求；该入口是 rack operation 领域包装，不是绕过外部请求机制的新通道。
3. rack operation 由 runtime/gateway 统一关联 `EXTERNAL_HTTP` outbox、Timeline、wait context 和 `WAITING_EXTERNAL` 状态；task dispatch key 用于 WMS/RCS 回调恢复，wait token 使用 rack operation key。
4. 外部请求必须有 `dispatch_key`、`target_code`、`payload`、`timeout_seconds` 和可追溯业务键。
5. `WAITING_EXTERNAL` 没有设备 ACK 阶段，`deadline_at = now + timeout_seconds` 必须立即生效。
6. 通用 `external_request` 的 wait token 使用 `dispatch_key`，满箱交换同时写入 `exchange_request_code`；rack operation 的 wait token 使用 operation key。
7. 超时、派发 ACK 耗尽或迟到 terminal 回调不得自动完成 Session，必须进入 RuntimeHold 或资源对账。

## 后果

- 首版资源模型可以覆盖 WES-wide 运行时资源事实，但执行顺序必须先落 Runtime 外部请求、WMS/RCS 合同、资源事实账本、当前投影和最小对账。
- SRS 中所有“WES 锁空箱”“WES 交换库存属性”“WES 自动扣减库存”的旧口径作废。
- 任何实现计划若需要 WES 本地判断库存可用性、空箱授权或库存扣减，必须先提出新的 ADR。

## 验收

- `docs/architecture/SRS.md` 已引用本 ADR，并移除冲突口径。
- `docs/business/wms_rcs_interface_requirements.md` 已明确 `/callback/external` 是运行时执行回调入口。
- 满箱交换计划和资源模型 spec 引用本 ADR，不再把第零阶段留成未解决门禁。
