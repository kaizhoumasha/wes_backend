# WMS 北向 35 项 Operation 合同

> 状态：Decision A 已批准并修订；初稿覆盖 16 项的 path/业务字段作为 wire 基线，E02 method 已批准改为 `POST`；完整
> 字段矩阵、其余 19 项，以及 E08–E14 status/状态闭集/关联键/幂等承诺仍待完成
> wire 主真源：本文
> 架构主真源：`docs/superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md`
> 适用范围：单工厂、单目标 WMS 连接的 WES 北向交互

## 不变量

- 35 项 identity 和 completion owner 作为当前目标 surface 保留。初稿覆盖的 Q01–Q09、Q14、
  E01/E02/E05/E07/E08/E09 共 16 项以初稿 path 和业务字段为 wire 基线；method 采用本文当前矩阵，其中 E02 已批准改为
  `POST`。当前架构同时剔除旧认证、重试、缓存、分页和旧生命周期语义，其余 19 项仍待 WMS/业务方补齐。
- proposed surface 中 19 项 QUERY 无副作用：Q01–Q18 使用 GET，Q19 使用无副作用 POST JSON body。
- 初稿覆盖的写操作统一使用 POST；其中 E02 的 method 由当前批准合同覆盖初稿 `DELETE`。未被初稿覆盖的写操作 method
  仍待确认。E01–E07/E15 返回同步 typed terminal result；E08–E14 通过 ACK + status query
  得到终态；E16 同步返回取消裁决，但仍归搬运端口所有。
- 同步业务确认不建立 status binding、scanner 或 `WMS_EFFECT_STATUS_HINT` 路由。
- 每项能力由一个垂直模块内聚 request/result、固定 method/path、拒绝码和 `WmsCallSpec`。Protocol 与 Gateway
  使用显式窄方法；禁止生产运行时 registry、通用公开 `call`、动态发现或 codegen。
- 测试态 conformance harness 必须核对 35 个能力模块、端口方法、Gateway 绑定和共享错误映射；该 harness
  不得进入生产装配。
- 当前 outbound 认证方式固定为 `NONE`。合同不定义 auth scheme、credential、HMAC、认证 Header 配置/动态注入、fallback
  或扩展点；与认证无关且 wire 必需的固定协议 Header 由各 operation 的 `WmsCallSpec` 显式持有，不进入部署配置。
- 每个公开方法只发出一次 HTTP 请求；列表 QUERY 一次返回有界 `items`，不定义 cursor、page size、next cursor 或自动续页。
- HTTP 结果采用封闭解释：只有 2xx + 有效 DTO 为成功，合同明确列出的业务拒绝按业务拒绝处理，其余全部非 2xx
  （包括 3xx）均为远端/依赖失败；Adapter 不跟随重定向、不把 3xx 当成功。
- Phase 2 `base_url` 只允许 HTTP origin，公共 `/api/wms` 或 `/api/MCS` 前缀必须由每项 operation 的完整 path 持有；
  不提供 prefix 配置、运行时拼接策略或兼容分支。
- 不提供旧 transport facade、alias、双写或 fallback。

## Phase 3 实施入口门禁

`docs/hardware/wms_rcs_interface_requirements.md` 是与 WMS 交互约定的 2026-03 初稿，现作为本合同的业务输入保留，原文不修改。
它概念性覆盖 Q01–Q09、Q14 和 E01/E02/E05/E07/E08/E09，但不是完整 35 项合同，也不是当前架构真源。

| 初稿事实 | 当前裁决 |
| --- | --- |
| `MCS`、`WorklineInbox`、旧 Session/Outbox 编排 | 仅作历史上下文，不进入 Phase 3 Adapter |
| “基础数据均采用异步回调”说明与同步 GET 示例并存 | 采用各 operation 明示的同步 GET method/path；概括性说明不引入 callback 查询模型 |
| `Authorization: Bearer` 示例 | 被当前 `NONE` 认证决定覆盖，不进入合同 |
| 指数退避三次、查询缓存 TTL | 被当前无 retry、无 Adapter cache 决定覆盖 |
| 货架列表响应中的 `page/page_size` | 只是一份返回样例，未定义请求/续页/一致性；不据此实现分页 |
| `/api/wms/materials`、`/api/wms/grn`、`/api/MCS/*` 等路径 | Decision A：初稿覆盖的 16 项采用这些完整 path；`base_url` 只保存 origin |
| 部分查询和业务请求字段/样例 | Decision A：作为对应 16 项的业务字段基线；只删除与当前架构硬约束冲突的字段和语义 |
| `WMS_RACK_ARRIVED`、搬运/交换完成等入站事件 | 不属于 Phase 3 outbound；由 Phase 4/5 按 `InboundEvidence`/status hint 边界另行对照 |
| 未覆盖的 19 项 operation | 必须由 WMS/业务方补充或明确删除，不得从旧代码猜测 |

### 初稿 operation 覆盖对照

下表登记 Decision A 后的裁决；初稿示例中出现且未被删除线废止的业务字段名均作为对应 16 项的 DTO 基线。
“尚待补齐”主要指必填性、精确类型、枚举、精度、时间格式、上限、错误响应和初稿未提供的 result，不代表可以重新发明
另一套字段名：

| Identity | 采用的 method/path | 当前裁决 | 尚待补齐 |
| --- | --- | --- | --- |
| Q01 | `GET /api/wms/materials/{material_id}` | 保留；身份字段为 `material_id` | 完整 result 字段、枚举和拒绝码 |
| Q02 | `GET /api/wms/materials?ids=...` | 保留；语义为按 `ids` 批量获取，不是通用列表 | `ids` 上限、result 字段和拒绝码 |
| Q03 | `GET /api/wms/zones` | 保留 | 完整 result 字段、枚举和拒绝码 |
| Q04 | `GET /api/wms/locations?zone=...` | 保留；query 闭集以初稿为基线 | 完整 query/result 字段和拒绝码 |
| Q05 | `GET /api/wms/racks/{rack_id}` | 保留 | 完整 result 字段、枚举和拒绝码 |
| Q06 | `GET /api/wms/racks?type/status/zone` | 保留 query 基线；删除分页语义 | 单响应上限、result 字段和拒绝码 |
| Q07 | `GET /api/wms/bins/{bin_id}` | 保留 | 完整 result 字段、枚举和拒绝码 |
| Q08 | `GET /api/wms/grn/{grn_id}` | 保留 | 完整 result 字段、枚举和拒绝码 |
| Q09 | `GET /api/wms/grn/{grn_id}/packages` | 保留 | 完整 result 字段、枚举和拒绝码 |
| Q14 | `GET /api/wms/inventory/query` | 保留 | 完整 query/result 字段和拒绝码 |
| E01 | `POST /api/wms/inventory/reserve` | 保留；删除旧 retry/auth 语义 | request/result 字段、幂等键和拒绝码 |
| E02 | `POST /api/wms/inventory/reserve/{id}` | 当前批准合同覆盖初稿 `DELETE`；path 保持不变 | request/result 字段和拒绝码 |
| E05 | `POST /api/wms/inventory/transfer` | 保留 | request/result 字段、幂等键和拒绝码 |
| E07 | `POST /api/wms/kitting/pkg-binding` | 保留 `pkg_code/bin_id/slot_id/rack_id/grn_id/material_id/qty/vendor/lc/dc/thickness` | result、幂等承诺和拒绝码 |
| E08 | `POST /api/MCS/rack-supply-request` | 保留；wire 使用 `request_id/area/rack_type/urgency/reason`，任务生命周期仍归 `TransportTask` | `request_id` 幂等承诺、ACK/status/result 和拒绝码 |
| E09 | `POST /api/MCS/transport-request` | 保留；wire 使用 `request_id/rack_id/rack_type/from_location/to_location/priority` | `request_id` 幂等承诺、ACK/status/result 和拒绝码 |

初稿未覆盖 Q10–Q13、Q15–Q19、E03–E04、E06、E10–E16，共 19 项。它们的 operation identity、completion owner
和业务目标暂保留，但 method/path/DTO/拒绝码仍是待批准提案，不得进入实现。

Phase 2 已裁决 prefix 所有权：`build_outbound_http_transport(...)` 要求 `base_url` 只含 HTTP origin，
`OutboundHttpRequest.path` 持有以 `/` 开头的完整请求 path。因此本文所有 path 均显式包含 `/api/wms` 或 `/api/MCS`，
Gateway 不再做公共 prefix 拼接。

2026-08-06 当前合同已批准 E02 使用 `POST`，并保持 `/api/wms/inventory/reserve/{id}` path 不变。硬件初稿中的 `DELETE`
作为原始业务输入保留，不再约束实现；这不是兼容 fallback，也不需要扩展 Phase 2 method 合同。

Phase 3 Task 1 必须建立 35 项逐项差异矩阵，记录初稿 method/path/字段/错误码、本文 proposed 合同、最终裁决和缺失输入，
并补齐 request/result 字段、必填性、类型、枚举、精度、时间格式、JSON body 所需固定协议 Header 和业务拒绝码闭集。旧
`src/app/wms_integration/ports/*.py` 只可用于发现遗漏，不得覆盖初稿或批准后的本文。

初稿覆盖 16 项的 path/业务字段及当前批准 method 已按 Decision A 冻结；完整字段矩阵、其余 19 项裁决，以及 E08–E14
status method/path、状态闭集、`request_id`/`task_id` 关联和幂等承诺任一未获批准时，不得声称 typed wire 合同已冻结，
也不得启动 Phase 3 Task 2–12。

## 目标 wire operation 清单

下表中 Q01–Q09、Q14、E01/E02/E05/E07/E08/E09 已采用初稿 path/业务字段及当前批准 method；其余 19 项仍是待批准提案。
所有 path 均按 Phase 2 的 origin-only `base_url` 合同写成完整请求 path。

### QUERY

| # | Identity | Method | Path | Capability module |
| --- | --- | --- | --- | --- |
| Q01 | `wms.master_data.get_material@v1` | GET | `/api/wms/materials/{material_id}` | `get_material.py` |
| Q02 | `wms.master_data.get_materials@v1` | GET | `/api/wms/materials` | `get_materials.py` |
| Q03 | `wms.master_data.list_zones@v1` | GET | `/api/wms/zones` | `list_zones.py` |
| Q04 | `wms.master_data.list_locations@v1` | GET | `/api/wms/locations` | `list_locations.py` |
| Q05 | `wms.master_data.get_rack@v1` | GET | `/api/wms/racks/{rack_id}` | `get_rack.py` |
| Q06 | `wms.master_data.list_racks@v1` | GET | `/api/wms/racks` | `list_racks.py` |
| Q07 | `wms.master_data.get_bin@v1` | GET | `/api/wms/bins/{bin_id}` | `get_bin.py` |
| Q08 | `wms.document.get_grn@v1` | GET | `/api/wms/grn/{grn_id}` | `get_grn.py` |
| Q09 | `wms.document.list_grn_packages@v1` | GET | `/api/wms/grn/{grn_id}/packages` | `list_grn_packages.py` |
| Q10 | `wms.document.get_pick_order@v1` | GET | `/api/wms/documents/pick-orders/{pick_order_id}` | `get_pick_order.py` |
| Q11 | `wms.document.get_outbound_order@v1` | GET | `/api/wms/documents/outbound-orders/{outbound_order_id}` | `get_outbound_order.py` |
| Q12 | `wms.document.get_wave@v1` | GET | `/api/wms/documents/waves/{wave_id}` | `get_wave.py` |
| Q13 | `wms.document.get_task_snapshot@v1` | GET | `/api/wms/documents/tasks/{task_id}` | `get_task_snapshot.py` |
| Q14 | `wms.inventory.query_inventory@v1` | GET | `/api/wms/inventory/query` | `query_inventory.py` |
| Q15 | `wms.inventory.get_reservation@v1` | GET | `/api/wms/inventory/reservations/{reservation_id}` | `get_reservation.py` |
| Q16 | `wms.reconciliation.check_bin_drift@v1` | GET | `/api/wms/reconciliation/bin-drift` | `check_bin_drift.py` |
| Q17 | `wms.reconciliation.check_rack_drift@v1` | GET | `/api/wms/reconciliation/rack-drift` | `check_rack_drift.py` |
| Q18 | `wms.reconciliation.check_full_drift@v1` | GET | `/api/wms/reconciliation/full-drift` | `check_full_drift.py` |
| Q19 | `wms.document.validate_rough_sorter_admission@v1` | POST | `/api/wms/documents/rough-sorter-admission/validate` | `validate_rough_sorter_admission.py` |

列表 QUERY 的结果只包含本次响应的有界 `items`；需要版本事实的 operation 可返回该响应的 `source_version`，但不提供
跨页一致性语义。单响应 deadline、wire/decoded bytes 和解码资源上限由 Phase 2 Transport 统一拥有。未来只有真实 WMS
合同明确分页后，才能同时修订本文、Adapter DTO 和 Phase 2 能力；当前不预留分页 seam。

### 写操作

| # | Identity | Method | Path | HTTP completion | WES 目标所有者 | Capability module |
| --- | --- | --- | --- | --- | --- | --- |
| E01 | `wms.inventory.reserve_inventory@v1` | POST | `/api/wms/inventory/reserve` | `SYNC_RESULT` | `WmsConfirmation` | `reserve_inventory.py` |
| E02 | `wms.inventory.release_reservation@v1` | POST | `/api/wms/inventory/reserve/{id}` | `SYNC_RESULT` | `WmsConfirmation` | `release_reservation.py` |
| E03 | `wms.inventory.confirm_inbound@v1` | POST | `/api/wms/inventory/confirm-inbound` | `SYNC_RESULT` | `WmsConfirmation` | `confirm_inbound.py` |
| E04 | `wms.inventory.confirm_outbound@v1` | POST | `/api/wms/inventory/confirm-outbound` | `SYNC_RESULT` | `WmsConfirmation` | `confirm_outbound.py` |
| E05 | `wms.inventory.transfer_inventory@v1` | POST | `/api/wms/inventory/transfer` | `SYNC_RESULT` | `WmsConfirmation` | `transfer_inventory.py` |
| E06 | `wms.inventory.confirm_return_putaway@v1` | POST | `/api/wms/inventory/confirm-return-putaway` | `SYNC_RESULT` | `WmsConfirmation` | `confirm_return_putaway.py` |
| E07 | `wms.fulfillment.notify_pkg_binding@v1` | POST | `/api/wms/kitting/pkg-binding` | `SYNC_RESULT` | `WmsConfirmation` | `notify_pkg_binding.py` |
| E08 | `wms.fulfillment.request_rack_supply@v1` | POST | `/api/MCS/rack-supply-request` | `ASYNC_TASK` | `TransportTask` | `request_rack_supply.py` |
| E09 | `wms.fulfillment.request_rack_transport@v1` | POST | `/api/MCS/transport-request` | `ASYNC_TASK` | `TransportTask` | `request_rack_transport.py` |
| E10 | `wms.fulfillment.change_rack_face@v1` | POST | `/api/wms/fulfillment/rack-face-change` | `ASYNC_TASK` | `TransportTask` | `change_rack_face.py` |
| E11 | `wms.fulfillment.full_box_exchange@v1` | POST | `/api/wms/fulfillment/full-box-exchange` | `ASYNC_TASK` | `TransportTask` | `full_box_exchange.py` |
| E12 | `wms.fulfillment.move_bins_to_conveyor_entry@v1` | POST | `/api/wms/fulfillment/conveyor-entry-batches` | `ASYNC_TASK` | `TransportTask` | `move_bins_to_conveyor_entry.py` |
| E13 | `wms.fulfillment.move_bins_from_conveyor_exit@v1` | POST | `/api/wms/fulfillment/conveyor-exit-batches` | `ASYNC_TASK` | `TransportTask` | `move_bins_from_conveyor_exit.py` |
| E14 | `wms.fulfillment.request_load_unit_transport@v1` | POST | `/api/wms/fulfillment/load-unit-transport` | `ASYNC_TASK` | `TransportTask` | `request_load_unit_transport.py` |
| E15 | `wms.fulfillment.publish_manual_task@v1` | POST | `/api/wms/fulfillment/manual-tasks` | `SYNC_RESULT` | `WmsConfirmation` | `publish_manual_task.py` |
| E16 | `wms.fulfillment.cancel_request@v1` | POST | `/api/wms/fulfillment/requests/cancel` | `SYNC_RESULT` | `TransportTask` | `cancel_request.py` |

`dispatch_key` 是 WES 可靠对象内部的稳定调度身份，不自动成为 WMS wire 字段。Decision A 下，每项 operation 只发送
WMS 已批准的一个 wire 幂等/关联字段：E08/E09 当前为初稿 `request_id`，Adapter 可由内部 `dispatch_key` 生成该值，
但不得同时发送 `dispatch_key`、`request_id` 或 `idempotency_key` 多套别名。初稿只展示响应 `task_id`，尚未承诺
`request_id` 原子幂等、回显、status/cancel 关联或同键异 payload 的拒绝码；这些是 Phase 3 编码前必须补齐的合同事实，
不能由 WES 单方面冻结。

不建立通用 `provider_reference`。初稿仅展示响应 `task_id`；其是否作为对端业务引用以及如何关联 submit/status/cancel
仍待 WMS 批准。`source_version` 只是 reconciliation 结果版本的待批准提案，未裁决前不得实现。

## GRN 与 Q19

Decision A 下 Q08 采用初稿的 GRN header + `items[]` 结构：header 包含 `grn_id/po_number/po_item/status/`
`dock_location/arrival_date/vendor/qc_status/metadata`；每个 item 包含 `material_id/material_name/ordered_qty/received_qty/`
`remaining_qty/unit/lc/dc`。初稿中已删除线标记的混托字段不进入基线。一个 GRN 可包含多条物料明细，并关联多个实收料盘；
Q09 返回 `grn_id/total_packages/packages[]/summary`，package 以 `pkg_code/material_id/qty/vendor/lc/dc/status/`
`current_location/bound_at/kitted_at` 为字段基线。完整必填性、类型、枚举和缺失值规则仍须写入字段矩阵。

Q19 未被初稿覆盖。当前业务目标提案要求 request 包含 raw code、canonical
`HHPN / MfrPN / Qty / DateCode / LotCode / PkgID`、卷盘直径/厚度和 `station_code / workline_id / correlation_id`，且任何
WES 内部 Session 或执行对象数据库主键都不得进入 wire。result 目标为 `ADMIT|REJECT`、匹配身份、测量校对、标准值/容差、
`rule_version` 与 `source_version`；以下拒绝码也是待 WMS 批准的闭集提案：

- `GRN_NOT_FOUND`
- `PACKAGE_NOT_FOUND`
- `PACKAGE_GRN_MISMATCH`
- `MATERIAL_MISMATCH`
- `QUANTITY_MISMATCH`
- `MEASUREMENT_OUT_OF_TOLERANCE`
- `PACKAGE_NOT_ADMISSIBLE`

Q19 的 WES 业务边界不创建绑定、预留、扣减或收货进度。合同获批后，首次有效结论在设备下发前成为 WES admission fact，
replay 不重新查询改写首次决定；`ADMIT` 只允许继续设备流程，物理投格后仍须 E07 + E03。获批前不得实现该 wire。

## 履约业务目标与 wire 成熟度

- E08 wire 业务字段以初稿 `area + rack_type + urgency + reason` 为基线。WES 内部“同工作位/货架类型最多一个 active
  demand”仍可由 `TransportTask` 管理，但 `station_code/demand_generation` 如何映射为 WMS wire 必须在字段矩阵中批准。
- E11–E13 未被初稿覆盖。当前 WES 业务目标分别是：交换任务提交满箱、原储位、冻结 occupancy 与约束；入线批次冻结
  精确成员并整批接受/拒绝；出线请求提交有界 FIFO 候选窗口且 ACK 只可接受有序前缀。这些不是已批准的 WMS wire 字段，
  method/path/DTO/拒绝码必须在其余 19 项裁决中确认后才能实现。

## 状态与 callback

目标可靠性模型要求 E08–E14 可获得 `ACCEPTED / PROCESSING / COMPLETED / REJECTED / NOT_FOUND` typed status snapshot，
但初稿没有定义 status endpoint、关联字段或这些状态。`GET /api/wms/operations/status` 仍只是待 WMS 批准的提案，不能进入
Phase 3 实现。callback 只允许作为关联 hint：保留 evidence 并唤醒已批准的 status query，不携带、不决定、不覆盖终态；
若 WMS 不提供查询能力，必须先重新裁决可靠性合同，不能退回旧 callback 直接终结路径。

具体工作线的 PICK/SCAN/PUT 因果链不属于 WMS 北向合同。WorkLine 插件只决定业务动作及逻辑参数；厂商
`task_type`、wire DTO 和命令映射由对应 Adapter 版本拥有，不写入 `WorklinePluginBinding.typed_config_json`。
插件业务顺序以业务蓝图为输入，Adapter 边界以 `docs/architecture/device-command-contract.md` 为准。
