# WMS 全量工厂接口合同

> 当前业务 wire contract 以 `docs/contracts/wms-northbound-interaction-contract.md` 为真源；
> WES 侧所有权与执行架构以
> `docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md` 为真源。
> 35 项业务视图见 [WMS 全量工厂 operation blueprint](./wms_full_factory_operation_blueprint.md)。

## 1. 边界

WES 面向一个工厂和一个明确配置的目标 WMS 连接。WMS/RCS 内部任务拆分、机器人/PLC 防呆、CTU 逐箱阶段和
货架到位状态不进入 WES callback 合同。WES 只观测类型化提交结果、状态查询结果和状态提示。

本文只描述业务 wire 视图，不定义 Provider、Catalog、registry、conformance、lane 或运行时发现机制。禁止保留旧
transport facade、兼容 operation identity、terminal callback 或按旧路径推断业务成功。异步搬运最终状态必须
由 `TransportTask` 通过类型化 status query 收敛。

## 2. 35 项 operation blueprint

固定 wire contract 共 35 项：

- Q01–Q19：19 项 QUERY，其中 Q19 是无副作用 POST，其余为 GET。
- E01–E07、E15：8 项同步业务确认，提交响应就是 typed terminal result。
- E08–E14：7 项 `ASYNC_TASK` EFFECT，提交返回 ACK，随后通过统一 status query 收敛。
- E16：1 项搬运取消操作，单次 HTTP 返回 typed cancel disposition，生命周期仍归 `TransportTask`。

QUERY：

1. `wms.master_data.get_material@v1`
2. `wms.master_data.list_materials@v1`
3. `wms.master_data.list_zones@v1`
4. `wms.master_data.list_locations@v1`
5. `wms.master_data.get_rack@v1`
6. `wms.master_data.list_racks@v1`
7. `wms.master_data.get_bin@v1`
8. `wms.document.get_grn@v1`
9. `wms.document.list_grn_packages@v1`
10. `wms.document.get_pick_order@v1`
11. `wms.document.get_outbound_order@v1`
12. `wms.document.get_wave@v1`
13. `wms.document.get_task_snapshot@v1`
14. `wms.inventory.query_inventory@v1`
15. `wms.inventory.get_reservation@v1`
16. `wms.reconciliation.check_bin_drift@v1`
17. `wms.reconciliation.check_rack_drift@v1`
18. `wms.reconciliation.check_full_drift@v1`
19. `wms.document.validate_rough_sorter_admission@v1`

同步 HTTP 写操作：

1. `wms.inventory.reserve_inventory@v1`
2. `wms.inventory.release_reservation@v1`
3. `wms.inventory.confirm_inbound@v1`
4. `wms.inventory.confirm_outbound@v1`
5. `wms.inventory.transfer_inventory@v1`
6. `wms.inventory.confirm_return_putaway@v1`
7. `wms.fulfillment.notify_pkg_binding@v1`
8. `wms.fulfillment.publish_manual_task@v1`
9. `wms.fulfillment.cancel_request@v1`

异步搬运提交：

1. `wms.fulfillment.request_rack_supply@v1`
2. `wms.fulfillment.request_rack_transport@v1`
3. `wms.fulfillment.change_rack_face@v1`
4. `wms.fulfillment.full_box_exchange@v1`
5. `wms.fulfillment.move_bins_to_conveyor_entry@v1`
6. `wms.fulfillment.move_bins_from_conveyor_exit@v1`
7. `wms.fulfillment.request_load_unit_transport@v1`

HTTP method、path template、请求/结果 model 和稳定拒绝码由北向合同冻结。WES 实现中每项能力以一个垂直模块
内聚这些 wire 事实及 `WmsCallSpec`；不存在生产运行时 registry、manifest 或动态发现。预算属于 WES 调用策略，
不改变 wire contract。

## 3. 入站普通事件

普通业务事件进入 callback event 入口，持久化为 `InboundEvidence` 后立即 ACK，不能直接完成业务确认或搬运任务：

- `WMS_GRN_RECEIVED`
- `WMS_PALLET_ARRIVED`
- `WMS_INVENTORY_UPDATED`
- `WMS_PDA_OPERATION_RECORDED`

共享包络必须包含 `source_system`、`source_event_id`、`source_version`、`occurred_at`、`request_id`；能关联既有
业务链路时还必须包含 `correlation_id`。幂等身份为 `source_system + source_event_id`。

## 4. 异步状态提示

`WMS_EFFECT_STATUS_HINT` 只适用于 E08–E14。提示只携带 `operation_identity`、`dispatch_key`、
`source_event_id` 和 `occurred_at`。`dispatch_key` 是该操作从 submit、ACK、status、terminal、cancel 到 hint
全链路唯一 wire 幂等键；合同不定义 `idempotency_key` 别名或双键映射。提示只负责持久化 evidence 并唤醒
对应 `TransportTask`；不能
携带 terminal 结果，也不能直接推进执行对象、资源投影或库存结论。

不同逻辑提示使用不同 `source_event_id`；同一次网络重试复用同一 ID 和同一 payload。同键异 hash 进入冲突审计。

## 5. 联调验收

- 测试态 capability conformance harness 必须精确识别 35 个垂直能力模块；生产运行时不得为此建立 registry。
- 8 项同步业务确认分别验证直接 terminal result，E16 单独验证 typed cancel disposition。
- E08–E14 分别验证 ACK、status 状态序列和 hint。
- 普通事件允许集之外的 WMS/RCS callback 返回 4xx，且不得创建 `InboundEvidence` 或调用领域生命周期。
- 未迁移业务入口在所属领域明确 fail closed，不得回退到兼容 transport facade。
