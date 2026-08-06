# WMS 全量工厂接口合同

> 当前业务 wire contract 以 `docs/contracts/wms-northbound-interaction-contract.md` 为真源；
> WES 侧所有权与执行架构以
> `docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md` 为真源。
> 33 项业务视图见 [WMS 全量工厂 operation blueprint](./wms_full_factory_operation_blueprint.md)。
> `docs/hardware/wms_rcs_interface_requirements.md` 是与 WMS 交互约定的初稿，只读保留。Decision A 已采用其覆盖
> 16 项的 path/业务字段和当前批准 method，E02 为 `POST`；旧 MCS 生命周期、认证、重试、缓存和分页语义不进入目标合同。

## 1. 边界

WES 面向一个工厂和一个明确配置的目标 WMS 连接。WMS/RCS 内部任务拆分、机器人/PLC 防呆、CTU 逐箱阶段和
货架到位状态不进入 WES callback 合同。WES 只观测类型化提交结果、状态查询结果和状态提示。

本文只描述业务 wire 视图，不定义 Provider、Catalog、registry、conformance、lane 或运行时发现机制。禁止保留旧
transport facade、兼容 operation identity、terminal callback 或按旧路径推断业务成功。异步搬运最终状态必须
由 `TransportTask` 通过类型化 status query 收敛。

## 2. 33 项 operation blueprint

目标 operation surface 共 33 项；其中初稿覆盖 16 项已采用 wire 基线，其余 17 项仍待 WMS/业务方批准：

- Q01–Q15：15 项 QUERY，其中 Q15 是无副作用 POST，其余为 GET。
- E01–E07、E15、E17–E18：10 项同步业务确认，提交响应就是 typed terminal result。
- E08–E14：7 项 `ASYNC_TASK` EFFECT，提交返回 ACK，随后通过统一 status query 收敛。
- E16：1 项搬运取消操作，单次 HTTP 返回 typed cancel disposition，生命周期仍归 `TransportTask`。

QUERY：

1. `wms.master_data.get_material@v1`
2. `wms.master_data.get_materials@v1`
3. `wms.master_data.list_zones@v1`
4. `wms.master_data.list_locations@v1`
5. `wms.master_data.get_rack@v1`
6. `wms.master_data.list_racks@v1`
7. `wms.master_data.get_bin@v1`
8. `wms.document.get_grn@v1`
9. `wms.document.list_grn_packages@v1`
10. `wms.inventory.query_inventory@v1`
11. `wms.inventory.get_reservation@v1`
12. `wms.reconciliation.check_bin_drift@v1`
13. `wms.reconciliation.check_rack_drift@v1`
14. `wms.reconciliation.check_full_drift@v1`
15. `wms.document.validate_rough_sorter_admission@v1`

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
10. `wms.fulfillment.report_picking_source_ng@v1`
11. `wms.fulfillment.confirm_picking_completed@v1`

异步搬运提交：

1. `wms.fulfillment.request_rack_supply@v1`
2. `wms.fulfillment.request_rack_transport@v1`
3. `wms.fulfillment.change_rack_face@v1`
4. `wms.fulfillment.full_box_exchange@v1`
5. `wms.fulfillment.move_bins_to_conveyor_entry@v1`
6. `wms.fulfillment.move_bins_from_conveyor_exit@v1`
7. `wms.fulfillment.request_load_unit_transport@v1`

初稿覆盖 16 项的完整 path 和已明确业务字段已由北向合同冻结；E02 method 已批准改为 `POST`。33 项完整请求/结果字段
矩阵、其余 17 项，以及 E08–E14 status method/path、状态闭集、`request_id`/`task_id` 关联和幂等承诺仍是 Phase 3
编码前置门禁。
WES 实现中每项能力以一个垂直模块内聚这些 wire 事实及 `WmsCallSpec`；不存在生产运行时 registry、manifest 或动态发现。
单响应资源预算属于 Phase 2 Transport，不改变 wire contract。

## 3. 入站普通事件

普通业务事件进入 `/api/v1/callback/event`，持久化为 `InboundEvidence` 后立即 ACK，不能直接完成业务确认或搬运任务：

- `WMS_GRN_RECEIVED`
- `WMS_PALLET_ARRIVED`
- `WMS_INVENTORY_UPDATED`
- `WMS_PDA_OPERATION_RECORDED`

共享包络必须包含 `source_system`、`source_event_id`、`source_version`、`occurred_at`、`request_id`；能关联既有
业务链路时还必须包含 `correlation_id`。幂等身份为 `source_system + source_event_id`。

## 4. 异步状态提示

`WMS_EFFECT_STATUS_HINT` 只有在 WMS inbound 合同完整批准且 Phase 4 唯一 hint successor 已验收时，才通过
`/api/v1/callback/external` 接收并适用于 E08–E14。提示只负责持久化 evidence 并唤醒经获批关联字段定位的
`TransportTask`；不能携带 terminal 结果，也不能直接推进执行对象、资源投影或库存结论。WES 内部 `dispatch_key` 不自动
成为 hint wire 字段。Decision A 下 E08/E09 submit wire 字段为 `request_id`，但 hint 是否使用 `request_id`、响应
`task_id` 或其他字段仍必须由 WMS 合同明确，不能从旧实现推断。

hint 的完整 payload、关联字段、事件幂等身份、网络重试复用和同键异 payload/hash 冲突规则均待 WMS inbound 合同批准；
合同未完整批准或 Phase 4 successor 未验收时，hint successor 为 `NONE`，Phase 5 只删除旧 route、payload、OpenAPI 和测试，
不得实现、联调或从普通事件包络复制这些语义。

## 5. 联调验收

- 测试态 capability conformance harness 必须精确识别 33 个垂直能力模块；生产运行时不得为此建立 registry。
- 10 项同步业务确认分别验证直接 terminal result，E16 单独验证 typed cancel disposition。
- E08–E14 在逐项合同获批后分别验证 ACK 与 status 状态序列；可选 hint 只有在 WMS inbound 合同完整批准且 Phase 4 唯一
  successor 已验收后才进入联调，否则按 `NONE` 验证旧 hint 资产全部缺席。
- 普通事件允许集之外的 WMS/RCS callback 返回 4xx，且不得创建 `InboundEvidence` 或调用领域生命周期。
- 未迁移业务入口在所属领域明确 fail closed，不得回退到兼容 transport facade。
