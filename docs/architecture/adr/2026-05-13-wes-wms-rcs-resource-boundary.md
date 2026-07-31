# ADR: WES/WMS/RCS 运行时资源边界

## 状态

Accepted - 2026-05-13

## 背景

`docs/superpowers/archive/specs/2026-05-13-smt-execution-resource-model-design.md` 要求 WES 建立运行时资源事实层，用于追踪 WorkLine、Device、Rack、Bin、Material、Location 和 ExchangeTask 的执行证据。

现有 `docs/architecture/SRS.md` 同时存在两个口径：

- WES 采用纯代理模式，不维护库存主账，库存数量、预留、扣减和账务由 WMS 负责。
- SMT 混合入库章节曾写到 WES 锁定五层空箱、交换两个容器库存属性、Pick_Fail 后自动扣减库存。

这两个口径不能同时进入实现。否则资源模块会变成影子 WMS，满箱交换也会在 WES 与 WMS/RCS 之间形成双真相源。

## 决策

1. WMS 是库存、预留、扣减、账务、SAP 同步和空箱资源授权的唯一权威。
2. WES 可以持久化执行事实、过程快照、资源关系投影、回写证据和对账证据，但这些事实不能作为库存可用性、库存属性交换、库存扣减或资源授权的本地主账。
3. 满箱交换 v1 中，WES 不锁定五层货架空箱，不本地判断交换区空位，不交换两个容器库存属性。WES 只提交 `wms.fulfillment.full_box_exchange@v1`，并以 typed ACK、status query 与 typed terminal result 收敛结果。
4. 生产发料、Pick_Fail、退料和入库确认中，WES 不自动扣减库存。WES 记录设备失败、创建 RuntimeHold 或诊断，并等待 WMS 确认、拒绝或人工授权。
5. E08–E14 的提交响应只产生 typed ACK，WES 通过 status query 取得 typed terminal result；可选 `WMS_EFFECT_STATUS_HINT` 只唤醒查询，不携带终态，也不直接推进资源投影。
6. WMS 四类普通事件与 `WMS_EFFECT_STATUS_HINT` 统一走 `/api/v1/callback/external`。`/api/v1/callback/event` 保留给设备事件；同一个运行时任务不得建立平行终态入口。
7. `ResourceStateEvent` 是 append-only 事实账本；`RackPlacement`、`RackBinMount`、`RackMaterialMount` 是当前关系投影。冲突、乱序、迟到或缺字段只能追加 evidence，并进入 `RECONCILING` / RuntimeHold / 资源对账，不能静默覆盖。

## WMS 异步 EFFECT 证据合同

E08–E14 的终态权威链固定为 typed ACK → status query → typed terminal result。CTU 入线、退线使用 E12/E13 批次级结果，WES 校验 ACK 冻结成员、`provider_reference`、版本与 terminal result 的成员集合，不接收逐箱或分阶段终态。

| 证据 | 要求 |
| --- | --- |
| typed ACK | 冻结 `operation_identity`、`idempotency_key`、`provider_reference` 与批次接纳范围；不是完成事实。 |
| status snapshot | 仅接受已注册 E08–E14 identity，状态和版本必须单调；`NOT_FOUND` 不携带可见结果字段。 |
| typed terminal result | 与原请求和 ACK 同 identity/reference/version；完成后才允许 owner 校验并更新作业期投影。 |
| `WMS_EFFECT_STATUS_HINT` | 仅携带 `operation_identity`、`idempotency_key`、`dispatch_key`，只唤醒同一 status query。 |

## Runtime 外部请求规则

1. 插件通过 `RuntimeIntent.external_request(...)` 表达通用外部系统请求，Runtime 统一创建 `WorklineOutbox(EXTERNAL_HTTP)`、Timeline 和等待状态。
2. 插件也可以通过领域化 `RuntimeIntent.rack_operation_request(...)` 表达 WES 单层货架搬运、交换、旋转或补给需求；该入口是 rack operation 领域包装，不是绕过外部请求机制的新通道。
3. rack operation 由 runtime/gateway 统一关联 `EXTERNAL_HTTP` outbox、Timeline、wait context 和 `WAITING_EXTERNAL` 状态；task dispatch key 用于关联 E08–E14 status 查询，wait token 使用 rack operation key。
4. 外部请求必须有 `dispatch_key`、`target_code`、`payload`、`timeout_seconds` 和可追溯业务键。
5. `WAITING_EXTERNAL` 没有设备 ACK 阶段，`deadline_at = now + timeout_seconds` 必须立即生效。
6. 通用 `external_request` 的 wait token 使用 `dispatch_key`，满箱交换同时写入 `exchange_request_code`；rack operation 的 wait token 使用 operation key。
7. 超时、派发 ACK 耗尽或迟到 typed terminal result 不得绕过 owner 校验自动完成 Session，必须进入 RuntimeHold 或资源对账。

## 后果

- 首版资源模型可以覆盖 WES-wide 运行时资源事实，但执行顺序必须先落 Runtime 外部请求、typed ACK/status/terminal result、资源事实账本、当前投影和最小对账。
- SRS 中所有“WES 锁空箱”“WES 交换库存属性”“WES 自动扣减库存”的旧口径作废。
- 任何实现计划若需要 WES 本地判断库存可用性、空箱授权或库存扣减，必须先提出新的 ADR。

## 验收

- `docs/architecture/SRS.md` 已引用本 ADR，并移除冲突口径。
- `docs/business/wms_rcs_interface_requirements.md` 已明确 E08–E14 的 typed ACK/status/terminal result 权威链和可选 status hint 边界。
- 满箱交换计划和资源模型 spec 引用本 ADR，不再把第零阶段留成未解决门禁。
