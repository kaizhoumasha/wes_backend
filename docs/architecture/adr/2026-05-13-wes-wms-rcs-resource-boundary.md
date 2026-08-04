# ADR: WES/WMS/RCS 运行时资源边界

## 状态

Accepted - 2026-05-13

## 背景

当前顶层 SPEC 要求 WES 保留作业期执行事实与资源投影，但不得将本地投影升级为 WMS 主账或全局规划真源。

当前 SRS 的早期版本曾同时存在两个口径：

- WES 采用纯代理模式，不维护库存主账，库存数量、预留、扣减和账务由 WMS 负责。
- SMT 混合入库章节曾写到 WES 锁定五层空箱、交换两个容器库存属性、Pick_Fail 后自动扣减库存。

这两个口径不能同时进入实现。否则资源模块会变成影子 WMS，满箱交换也会在 WES 与 WMS/RCS 之间形成双真相源。

## 决策

1. WMS 是库存、预留、扣减、账务、SAP 同步和空箱资源授权的唯一权威。
2. WES 可以持久化执行事实、过程快照、资源关系投影、回写证据和对账证据，但这些事实不能作为库存可用性、库存属性交换、库存扣减或资源授权的本地主账。
3. 满箱交换 v1 中，WES 不锁定五层货架空箱，不本地判断交换区空位，不交换两个容器库存属性。WES 只提交 `wms.fulfillment.full_box_exchange@v1`，并以 typed ACK、status query 与 typed terminal result 收敛结果。
4. 生产发料、Pick_Fail、退料和入库确认中，WES 不自动扣减库存。WES 记录设备失败和诊断，将当前具体执行对象标记为硬件故障或依赖停顿，并等待 WMS 确认、拒绝或人工授权。
5. E08–E14 的提交响应只产生 typed ACK，WES 通过 status query 取得 typed terminal result；可选 `WMS_EFFECT_STATUS_HINT` 只唤醒查询，不携带终态，也不直接推进资源投影。
6. WMS 四类普通事件与 `WMS_EFFECT_STATUS_HINT` 统一走 `/api/v1/callback/external`。`/api/v1/callback/event` 保留给设备事件；同一个运行时任务不得建立平行终态入口。
7. `ResourceStateEvent` 是 append-only 事实账本；`RackPlacement`、`RackBinMount`、`RackMaterialMount` 是当前关系投影。冲突、乱序、迟到或缺字段只能追加 evidence，并将具体执行对象进入安全停顿或人工清线，不能静默覆盖。

## WMS 异步 EFFECT 证据合同

E08–E14 的终态权威链固定为 typed ACK → status query → typed terminal result。CTU 入线、退线使用 E12/E13 批次级结果，WES 校验 ACK 冻结成员、`provider_reference`、版本与 terminal result 的成员集合，不接收逐箱或分阶段终态。

| 证据 | 要求 |
| --- | --- |
| typed ACK | 冻结 `operation_identity`、`dispatch_key`、`provider_reference` 与批次接纳范围；不是完成事实。 |
| status snapshot | 仅接受已注册 E08–E14 identity，状态和版本必须单调；`NOT_FOUND` 不携带可见结果字段。 |
| typed terminal result | 与原请求和 ACK 同 identity/reference/version；完成后才允许 owner 校验并更新作业期投影。 |
| `WMS_EFFECT_STATUS_HINT` | 仅携带 `operation_identity`、`dispatch_key`，只唤醒同一 status query。 |

## 目标外部请求规则

1. 插件需要同步 WMS 事实时，只通过注入的 `WmsCapabilities` 发起类型化查询，不暴露 HTTP 或 Provider DTO。
2. 改变 WMS 业务状态的确认由封闭 Decision 创建 `WmsConfirmation`；其持久化、幂等、重试和证据由确认发送器拥有。
3. AGV/CTU 搬运、交换、旋转或补给需求由 Decision 创建 `TransportTask`；任务自身拥有 `dispatch_key`、业务目标、超时、ACK/status 和终态证据。
4. ECS 设备动作只通过 `DeviceCommand` 执行，不与 `TransportTask` 或 `WmsConfirmation` 共用通用 Intent/Effect/Outbox 状态机。
5. 超时、ACK 耗尽、迟到终态或 payload hash 冲突不得绕过具体对象 owner 推进投影；必须保留 evidence 并进入硬件故障、依赖停顿或人工清线。

## 后果

- 资源模型只覆盖 WES 作业期资源事实；执行链分别落到 `DeviceCommand`、`TransportTask`、`WmsConfirmation`、资源事实账本和当前投影。
- SRS 已同步明确“WES 不锁空箱、不交换库存属性、不自动扣减库存”，需求与本 ADR 保持一致。
- 任何实现计划若需要 WES 本地判断库存可用性、空箱授权或库存扣减，必须先提出新的 ADR。

## 验收

- `docs/architecture/SRS.md` 作为当前需求真源保留，并引用顶层 SPEC、当前 WMS 合同与本 ADR 落实架构边界。
- `docs/business/wms_rcs_interface_requirements.md` 已明确 E08–E14 的 typed ACK/status/terminal result 权威链和可选 status hint 边界。
- 满箱交换计划和资源模型 spec 引用本 ADR，不再把第零阶段留成未解决门禁。
