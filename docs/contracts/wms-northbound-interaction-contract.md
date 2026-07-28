# WMS 北向 35 项 Operation 合同

> 状态：Frozen
> 主真源：`src/app/wms_integration/operation_registry.py`
> 适用范围：单工厂、单 active WMS Provider 的 WES 北向交互

## 不变量

- identity、method、typed request/result、稳定错误/拒绝码、预算、分页、完成模式和 lane 只由静态 Definition
  声明；部署配置不得覆盖。
- 19 项 QUERY 无副作用；Q01–Q18 使用 GET，Q19 使用无副作用 POST JSON body。
- 16 项 EFFECT 全部使用 POST。E01–E07/E15/E16 返回同步 typed terminal result；E08–E14 只通过 ACK +
  status query 得到终态。
- 同步 EFFECT 不建立 status binding、scanner 或 `WMS_EFFECT_STATUS_HINT` 路由。
- Provider manifest、conformance manifest、业务场景覆盖和 Mock fixture 必须与 registry 键集合完全一致；缺失
  任一项即 fail closed。
- 不提供旧 transport facade、alias、双写或 fallback。

## Operation Catalog

### QUERY

| # | Identity | Method |
| --- | --- | --- |
| Q01 | `wms.master_data.get_material@v1` | GET |
| Q02 | `wms.master_data.list_materials@v1` | GET |
| Q03 | `wms.master_data.list_zones@v1` | GET |
| Q04 | `wms.master_data.list_locations@v1` | GET |
| Q05 | `wms.master_data.get_rack@v1` | GET |
| Q06 | `wms.master_data.list_racks@v1` | GET |
| Q07 | `wms.master_data.get_bin@v1` | GET |
| Q08 | `wms.document.get_grn@v1` | GET |
| Q09 | `wms.document.list_grn_packages@v1` | GET |
| Q10 | `wms.document.get_pick_order@v1` | GET |
| Q11 | `wms.document.get_outbound_order@v1` | GET |
| Q12 | `wms.document.get_wave@v1` | GET |
| Q13 | `wms.document.get_task_snapshot@v1` | GET |
| Q14 | `wms.inventory.query_inventory@v1` | GET |
| Q15 | `wms.inventory.get_reservation@v1` | GET |
| Q16 | `wms.reconciliation.check_bin_drift@v1` | GET |
| Q17 | `wms.reconciliation.check_rack_drift@v1` | GET |
| Q18 | `wms.reconciliation.check_full_drift@v1` | GET |
| Q19 | `wms.document.validate_rough_sorter_admission@v1` | POST |

列表 QUERY 使用 `items + next_cursor` 并冻结最大页数、单页行数和总行数。cursor 绑定同一 Provider authority
snapshot，禁止跨版本拼页。总 deadline 覆盖 attempts、分页和 backoff；基线为 10 秒、最多 3 次。

### EFFECT

| # | Identity | Completion / lane |
| --- | --- | --- |
| E01 | `wms.inventory.reserve_inventory@v1` | `SYNC_RESULT / wms-data` |
| E02 | `wms.inventory.release_reservation@v1` | `SYNC_RESULT / wms-data` |
| E03 | `wms.inventory.confirm_inbound@v1` | `SYNC_RESULT / wms-data` |
| E04 | `wms.inventory.confirm_outbound@v1` | `SYNC_RESULT / wms-data` |
| E05 | `wms.inventory.transfer_inventory@v1` | `SYNC_RESULT / wms-data` |
| E06 | `wms.inventory.confirm_return_putaway@v1` | `SYNC_RESULT / wms-data` |
| E07 | `wms.fulfillment.notify_pkg_binding@v1` | `SYNC_RESULT / wms-data` |
| E08 | `wms.fulfillment.request_rack_supply@v1` | `ASYNC_TASK / wms-fulfillment` |
| E09 | `wms.fulfillment.request_rack_transport@v1` | `ASYNC_TASK / wms-fulfillment` |
| E10 | `wms.fulfillment.change_rack_face@v1` | `ASYNC_TASK / wms-fulfillment` |
| E11 | `wms.fulfillment.full_box_exchange@v1` | `ASYNC_TASK / wms-fulfillment` |
| E12 | `wms.fulfillment.move_bins_to_conveyor_entry@v1` | `ASYNC_TASK / wms-fulfillment` |
| E13 | `wms.fulfillment.move_bins_from_conveyor_exit@v1` | `ASYNC_TASK / wms-fulfillment` |
| E14 | `wms.fulfillment.request_load_unit_transport@v1` | `ASYNC_TASK / wms-fulfillment` |
| E15 | `wms.fulfillment.publish_manual_task@v1` | `SYNC_RESULT / wms-data` |
| E16 | `wms.fulfillment.cancel_request@v1` | `SYNC_RESULT / wms-fulfillment` |

EFFECT request 包含 `dispatch_key` 与 operation-specific 业务身份。typed result 回显 `dispatch_key`、业务身份、
`provider_reference` 与 `source_version`。WMS 以 `operation_identity + idempotency_key` 原子幂等；同键不同
fingerprint 返回 `IDEMPOTENCY_CONFLICT`。

## GRN 与 Q19

GRN 是 PO 行级记录，直接包含 `grn_id / po_number / po_item / material_code`、计划/已收/剩余数量、批次和质检
状态。一个 GRN 可关联多个实收料盘，Q09 保留。

Q19 request 冻结 raw code、canonical `HHPN / MfrPN / Qty / DateCode / LotCode / PkgID`、卷盘直径/厚度和
`station_code / workline_id / session_id / correlation_id`。result 冻结 `ADMIT|REJECT`、匹配身份、测量校对、
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
- E11 只在货架到达 manifest 固定交换位/货架面并取得 flow owner 后创建。WES 提交满箱、原储位、冻结 occupancy
  与约束；空箱和两侧目标储位由 WMS 选择。
- E12 冻结精确批次成员，整批接受或在物理动作前整批拒绝。
- E13 提交 SCAN3 退料队列的有界 FIFO 候选窗口。ACK 的 accepted scope 必须是候选有序前缀；零接纳返回
  `NO_DESTINATION_CAPACITY`。部分失败和未知位置逐成员返回最终事实。

## 状态、callback 与南向机械臂

E08–E14 共用 `ACCEPTED / PROCESSING / COMPLETED / REJECTED / NOT_FOUND` status snapshot。
callback 只是关联 hint：保留 evidence 并唤醒 status query，不携带、不决定、不覆盖终态。

南向因果链固定为：

`PICK ACK → 下一北向 PICK → PICK result → SCAN → SCAN result → WES typed 决策 → PUT result → 最终事实`

`PICK / SCAN / PUT` 仅为语义槽位。厂商 `task_type` 只能存在版本化
`WorklinePluginBinding.typed_config_json`；禁止配置表达式/归约 DSL、扫码平台传感器/空闲状态和 WES 防呆推断。
