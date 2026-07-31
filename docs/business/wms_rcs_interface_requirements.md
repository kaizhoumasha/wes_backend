# WMS 全量工厂接口合同

> 当前合同以 `docs/superpowers/specs/2026-07-28-wms-full-factory-integration-design.md`
> 和 `src/app/wms_integration/operation_registry.py` 为真源。
> 35 项业务视图见 [WMS 全量工厂 operation blueprint](./wms_full_factory_operation_blueprint.md)。

## 1. 边界

WES 面向一个工厂、一个 active WMS Provider。WMS/RCS 内部任务拆分、机器人/PLC 防呆、CTU 逐箱阶段和货架到位
状态不进入 WES callback 合同。WES 只观测 typed submit、typed terminal result、status query 和 status hint。

禁止保留旧 transport facade、兼容 operation identity、terminal callback 或按旧路径推断业务成功。异步 EFFECT
最终状态必须由 typed status query 收敛。

## 2. 35 项 operation blueprint

静态 registry 共 35 项：

- Q01–Q19：19 项 QUERY，其中 Q19 是无副作用 POST，其余为 GET。
- E01–E07、E15、E16：9 项 `SYNC_RESULT` EFFECT，提交响应就是 typed terminal result。
- E08–E14：7 项 `ASYNC_TASK` EFFECT，提交返回 ACK，随后通过统一 status query 收敛。

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

同步 EFFECT：

1. `wms.inventory.reserve_inventory@v1`
2. `wms.inventory.release_reservation@v1`
3. `wms.inventory.confirm_inbound@v1`
4. `wms.inventory.confirm_outbound@v1`
5. `wms.inventory.transfer_inventory@v1`
6. `wms.inventory.confirm_return_putaway@v1`
7. `wms.fulfillment.notify_pkg_binding@v1`
8. `wms.fulfillment.publish_manual_task@v1`
9. `wms.fulfillment.cancel_request@v1`

异步 EFFECT：

1. `wms.fulfillment.request_rack_supply@v1`
2. `wms.fulfillment.request_rack_transport@v1`
3. `wms.fulfillment.change_rack_face@v1`
4. `wms.fulfillment.full_box_exchange@v1`
5. `wms.fulfillment.move_bins_to_conveyor_entry@v1`
6. `wms.fulfillment.move_bins_from_conveyor_exit@v1`
7. `wms.fulfillment.request_load_unit_transport@v1`

HTTP method、path template、请求/结果 model、预算、分页、拒绝码和 target code 不在本文复制，统一从静态 registry
生成并由 conformance manifest 校验。

## 3. 入站普通事件

普通业务事件进入 callback event 入口，落原始日志和 RuntimeInbox 后立即 ACK，不能直接完成 EFFECT：

- `WMS_GRN_RECEIVED`
- `WMS_PALLET_ARRIVED`
- `WMS_INVENTORY_UPDATED`
- `WMS_PDA_OPERATION_RECORDED`

共享包络必须包含 `source_system`、`source_event_id`、`source_version`、`occurred_at`、`request_id`；能关联既有
业务链路时还必须包含 `correlation_id`。幂等身份为 `source_system + source_event_id`。

## 4. 异步状态提示

`WMS_EFFECT_STATUS_HINT` 只适用于 E08–E14。提示只携带 `operation_identity`、`idempotency_key`、
`dispatch_key`、`source_event_id` 和 `occurred_at`。它只负责持久化 evidence 并唤醒统一 status claim；不能携带
terminal 结果，也不能直接推进 Session、资源投影或库存结论。

不同逻辑提示使用不同 `source_event_id`；同一次网络重试复用同一 ID 和同一 payload。同键异 hash 进入冲突审计。

## 5. 联调验收

- registry 必须精确等于 35 项，Mock 路由不得多也不得少。
- 9 项同步 EFFECT 分别验证直接 terminal result。
- E08–E14 分别验证 ACK、status 状态序列和 hint。
- 普通事件允许集之外的 WMS/RCS callback 返回 4xx，且不得创建 RuntimeInbox 或调用领域 lifecycle。
- 未迁移业务入口在所属领域明确 fail closed，不得回退到兼容 transport facade。
