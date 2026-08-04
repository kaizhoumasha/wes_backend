# WMS 北向 35 项 Operation 合同

> 状态：Frozen
> wire 主真源：本文
> 架构主真源：`docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md`
> 适用范围：单工厂、单目标 WMS 连接的 WES 北向交互

## 不变量

- identity、method、path、typed request/result、稳定错误/拒绝码和分页 wire 语义由本文冻结；部署配置不得覆盖。
- 19 项 QUERY 无副作用；Q01–Q18 使用 GET，Q19 使用无副作用 POST JSON body。
- 16 项写操作全部使用 POST。E01–E07/E15 返回同步 typed terminal result；E08–E14 通过 ACK + status query
  得到终态；E16 同步返回取消裁决，但仍归搬运端口所有。
- 同步业务确认不建立 status binding、scanner 或 `WMS_EFFECT_STATUS_HINT` 路由。
- 每项能力由一个垂直模块内聚 request/result、固定 method/path、拒绝码和 `WmsCallSpec`。Protocol 与 Gateway
  使用显式窄方法；禁止生产运行时 registry、通用公开 `call`、动态发现或 codegen。
- 测试态 conformance harness 必须核对 35 个能力模块、端口方法、Gateway 绑定和共享错误映射；该 harness
  不得进入生产装配。
- 不提供旧 transport facade、alias、双写或 fallback。

## Wire operation 清单

### QUERY

| # | Identity | Method | Path | Capability module |
| --- | --- | --- | --- | --- |
| Q01 | `wms.master_data.get_material@v1` | GET | `/master-data/materials/{material_code}` | `q01_get_material.py` |
| Q02 | `wms.master_data.list_materials@v1` | GET | `/master-data/materials` | `q02_list_materials.py` |
| Q03 | `wms.master_data.list_zones@v1` | GET | `/master-data/zones` | `q03_list_zones.py` |
| Q04 | `wms.master_data.list_locations@v1` | GET | `/master-data/locations` | `q04_list_locations.py` |
| Q05 | `wms.master_data.get_rack@v1` | GET | `/master-data/racks/{rack_id}` | `q05_get_rack.py` |
| Q06 | `wms.master_data.list_racks@v1` | GET | `/master-data/racks` | `q06_list_racks.py` |
| Q07 | `wms.master_data.get_bin@v1` | GET | `/master-data/bins/{bin_id}` | `q07_get_bin.py` |
| Q08 | `wms.document.get_grn@v1` | GET | `/documents/grns/{grn_id}` | `q08_get_grn.py` |
| Q09 | `wms.document.list_grn_packages@v1` | GET | `/documents/grns/{grn_id}/packages` | `q09_list_grn_packages.py` |
| Q10 | `wms.document.get_pick_order@v1` | GET | `/documents/pick-orders/{pick_order_id}` | `q10_get_pick_order.py` |
| Q11 | `wms.document.get_outbound_order@v1` | GET | `/documents/outbound-orders/{outbound_order_id}` | `q11_get_outbound_order.py` |
| Q12 | `wms.document.get_wave@v1` | GET | `/documents/waves/{wave_id}` | `q12_get_wave.py` |
| Q13 | `wms.document.get_task_snapshot@v1` | GET | `/documents/tasks/{task_id}` | `q13_get_task_snapshot.py` |
| Q14 | `wms.inventory.query_inventory@v1` | GET | `/inventory/query` | `q14_query_inventory.py` |
| Q15 | `wms.inventory.get_reservation@v1` | GET | `/inventory/reservations/{reservation_id}` | `q15_get_reservation.py` |
| Q16 | `wms.reconciliation.check_bin_drift@v1` | GET | `/reconciliation/bin-drift` | `q16_check_bin_drift.py` |
| Q17 | `wms.reconciliation.check_rack_drift@v1` | GET | `/reconciliation/rack-drift` | `q17_check_rack_drift.py` |
| Q18 | `wms.reconciliation.check_full_drift@v1` | GET | `/reconciliation/full-drift` | `q18_check_full_drift.py` |
| Q19 | `wms.document.validate_rough_sorter_admission@v1` | POST | `/documents/rough-sorter-admission/validate` | `q19_validate_rough_sorter_admission.py` |

列表 QUERY 使用 `items + next_cursor`，同一公开调用内的 cursor 必须保持 `source_version` 一致，禁止跨版本
拼页。WES 对一个公开分页调用只申请一次 breaker permit，并共享累计 deadline、wire bytes、decoded bytes、
最大页数和总行数预算；预算不属于 WMS 可配置 wire 字段。

### 写操作

| # | Identity | Method | Path | HTTP completion | WES 目标所有者 | Capability module |
| --- | --- | --- | --- | --- | --- | --- |
| E01 | `wms.inventory.reserve_inventory@v1` | POST | `/inventory/reservations` | `SYNC_RESULT` | `WmsConfirmation` | `e01_reserve_inventory.py` |
| E02 | `wms.inventory.release_reservation@v1` | POST | `/inventory/reservations/release` | `SYNC_RESULT` | `WmsConfirmation` | `e02_release_reservation.py` |
| E03 | `wms.inventory.confirm_inbound@v1` | POST | `/inventory/confirm-inbound` | `SYNC_RESULT` | `WmsConfirmation` | `e03_confirm_inbound.py` |
| E04 | `wms.inventory.confirm_outbound@v1` | POST | `/inventory/confirm-outbound` | `SYNC_RESULT` | `WmsConfirmation` | `e04_confirm_outbound.py` |
| E05 | `wms.inventory.transfer_inventory@v1` | POST | `/inventory/transfers` | `SYNC_RESULT` | `WmsConfirmation` | `e05_transfer_inventory.py` |
| E06 | `wms.inventory.confirm_return_putaway@v1` | POST | `/inventory/confirm-return-putaway` | `SYNC_RESULT` | `WmsConfirmation` | `e06_confirm_return_putaway.py` |
| E07 | `wms.fulfillment.notify_pkg_binding@v1` | POST | `/fulfillment/pkg-bindings` | `SYNC_RESULT` | `WmsConfirmation` | `e07_notify_pkg_binding.py` |
| E08 | `wms.fulfillment.request_rack_supply@v1` | POST | `/fulfillment/rack-supply` | `ASYNC_TASK` | `TransportTask` | `e08_request_rack_supply.py` |
| E09 | `wms.fulfillment.request_rack_transport@v1` | POST | `/fulfillment/rack-transport` | `ASYNC_TASK` | `TransportTask` | `e09_request_rack_transport.py` |
| E10 | `wms.fulfillment.change_rack_face@v1` | POST | `/fulfillment/rack-face-change` | `ASYNC_TASK` | `TransportTask` | `e10_change_rack_face.py` |
| E11 | `wms.fulfillment.full_box_exchange@v1` | POST | `/fulfillment/full-box-exchange` | `ASYNC_TASK` | `TransportTask` | `e11_full_box_exchange.py` |
| E12 | `wms.fulfillment.move_bins_to_conveyor_entry@v1` | POST | `/fulfillment/conveyor-entry-batches` | `ASYNC_TASK` | `TransportTask` | `e12_move_bins_to_conveyor_entry.py` |
| E13 | `wms.fulfillment.move_bins_from_conveyor_exit@v1` | POST | `/fulfillment/conveyor-exit-batches` | `ASYNC_TASK` | `TransportTask` | `e13_move_bins_from_conveyor_exit.py` |
| E14 | `wms.fulfillment.request_load_unit_transport@v1` | POST | `/fulfillment/load-unit-transport` | `ASYNC_TASK` | `TransportTask` | `e14_request_load_unit_transport.py` |
| E15 | `wms.fulfillment.publish_manual_task@v1` | POST | `/fulfillment/manual-tasks` | `SYNC_RESULT` | `WmsConfirmation` | `e15_publish_manual_task.py` |
| E16 | `wms.fulfillment.cancel_request@v1` | POST | `/fulfillment/requests/cancel` | `SYNC_RESULT` | `TransportTask` | `e16_cancel_request.py` |

写操作 request 包含 `dispatch_key` 与 operation-specific 业务身份。typed ACK、status、terminal result 和 cancel
裁决均回显同一个 `dispatch_key`；WMS 以 `operation_identity + dispatch_key` 原子幂等，同键不同 fingerprint 返回
`IDEMPOTENCY_CONFLICT`。最终合同不再存在独立 `idempotency_key` 字段、别名或双键映射。

`provider_reference` 是 WMS 接纳写操作后返回的业务引用，只用于关联对端任务，不构成 WES Provider 架构身份。
`source_version` 继续表示 WMS 结果版本。

## GRN 与 Q19

GRN 是 PO 行级记录，直接包含 `grn_id / po_number / po_item / material_code`、计划/已收/剩余数量、批次和质检
状态。一个 GRN 可关联多个实收料盘，Q09 保留。

Q19 request 冻结 raw code、canonical `HHPN / MfrPN / Qty / DateCode / LotCode / PkgID`、卷盘直径/厚度和
`station_code / workline_id / correlation_id`。任何 WES 内部 Session 或具体执行对象数据库主键均不进入 WMS wire；
admission fact 与内部执行对象的关联由 WES 在边界内完成，不属于本合同。result 冻结 `ADMIT|REJECT`、匹配身份、测量校对、
标准值/容差、`rule_version` 与 `source_version`。拒绝码闭集：

- `GRN_NOT_FOUND`
- `PACKAGE_NOT_FOUND`
- `PACKAGE_GRN_MISMATCH`
- `MATERIAL_MISMATCH`
- `QUANTITY_MISMATCH`
- `MEASUREMENT_OUT_OF_TOLERANCE`
- `PACKAGE_NOT_ADMISSIBLE`

Q19 不创建绑定、预留、扣减或收货进度。首次有效结论在设备下发前成为 WES admission fact，replay 不重新查询改写
首次决定。`ADMIT` 只允许继续设备流程，物理投格后仍须 E07 + E03。

## 关键履约冻结

- E08 身份为 `station_code + rack_type + demand_generation`；同工作位/货架类型最多一个 active demand。
- E11 只在货架到达工作线配置的交换位/货架面并取得执行 owner 后创建。WES 提交满箱、原储位、冻结 occupancy
  与约束；空箱和两侧目标储位由 WMS 选择。
- E12 冻结精确批次成员，整批接受或在物理动作前整批拒绝。
- E13 提交 SCAN3 退料队列的有界 FIFO 候选窗口。ACK 的 accepted scope 必须是候选有序前缀；零接纳返回
  `NO_DESTINATION_CAPACITY`。部分失败和未知位置逐成员返回最终事实。

## 状态与 callback

E08–E14 共用 `ACCEPTED / PROCESSING / COMPLETED / REJECTED / NOT_FOUND` status snapshot。
callback 只是关联 hint：保留 evidence 并唤醒 status query，不携带、不决定、不覆盖终态。

具体工作线的 PICK/SCAN/PUT 因果链不属于 WMS 北向合同。WorkLine 插件只决定业务动作及逻辑参数；厂商
`task_type`、wire DTO 和命令映射由对应 Adapter 版本拥有，不写入 `WorklinePluginBinding.typed_config_json`。
插件业务顺序以业务蓝图为输入，Adapter 边界以 `docs/architecture/device-command-contract.md` 为准。
