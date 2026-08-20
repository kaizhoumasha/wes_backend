---
title: WMS / WES Phase 8 粗分逐盘入库合同
status: Approved
implementation_authorization: true
approved_at: 2026-08-16
contract_version: "1.0"
audience: WMS、WES、RCS、ECS 开发与联调人员
scope: Phase 8 粗分逐盘准入、设备执行、目标 Cell 晚绑定、单层货架更换、NG、事实确认与人工核验恢复
related:
  - docs/contracts/device-annexes/rough-sorter-device-contract.md
  - docs/contracts/wms-northbound-interaction-contract.md
  - docs/contracts/wms-async-callback-envelope-contract.md
  - docs/contracts/transport-fulfillment-contract.md
  - docs/integration/third_party_integration_whitepaper.md
  - docs/architecture/SRS.md
---

# WMS / WES Phase 8 粗分逐盘入库合同

## 1. 状态与边界

本文是 WMS、WES、RCS、ECS 联合批准的 Phase 8 粗分入库业务合同，也是 `rough_sorter` 插件的唯一业务合同真源。
本文批准 Phase 8 实施；不批准满箱交换、自动上架、自动/人工分拣或其它阶段能力。后续阶段仍以
[`wms-inbound-putaway-integration-requirements.md`](wms-inbound-putaway-integration-requirements.md) 的
`ReviewRequired` 合同为准。

系统尚未发布，实施直接使用本文合同，不保留旧 operation、字段别名、目标早绑定、双读写、shim 或兼容分支。
WMS 公共 HTTP Client、公共信封与 Transport wire 分别引用 related 中的既有真源；本文不复制这些公共定义。

## 2. 权威与拓扑

| 参与方 | 唯一权威 |
| --- | --- |
| WMS | GRN、业务资格、`pkg_id`、库存主账、目标 Cell、换架计划和业务终态 |
| WES | `MaterialExecution`、可靠本地执行、位置投影、DeviceCommand/TransportTask 创建与最小安全隔离 |
| ECS | 设备动作、扫码测量事实、设备终态和物理位置事实 |
| RCS | 货架路径、实际执行顺序、避让和共享工作位互锁 |

每个 WorkLine 只绑定一个 ECS。插件键固定为 `rough_sorter`，每个 WorkLine 分别绑定三个一对一业务角色：

- `MEASUREMENT_DEVICE`：入料、扫码与测量；
- `TRANSFER_DEVICE`：从流水线入口输送到出口；
- `PLACEMENT_DEVICE`：从流水线出口放入目标 Cell 或 WMS 指定 NG 位置。

`device_code` 全厂唯一。角色、设备实例、Endpoint、合同版本、ECS/网关版本、时限和 WorkLine 的绑定必须进入当前
`LineRunEpochDeviceBinding` 与 Epoch digest；活动 Epoch 内不得静默替换。供应商私有字段、坐标、错误和适配只存在于
ECS/网关，不进入本合同、WES 核心或插件。

## 3. 生命周期与并发

`MaterialExecution` 生命周期固定为：

```text
CREATED | RUNNING | HOLD | CLOSED | RECONCILING
```

- `CREATED`：已建立执行身份，尚未开始业务推进。
- `RUNNING`：至少一个获批业务步骤正在可靠推进。
- `HOLD`：仍可安全等待业务决定、资源或明确未接纳后的有界恢复。
- `CLOSED`：placement/NG 事实已被 WMS `RECORDED | DUPLICATE`，或人工决定终止且现场事实已闭合。
- `RECONCILING`：已接纳命令失败、交付未知、位置未知、身份冲突或事实冲突，需要人工核验恢复。

扫码、准入、输送、目标请求、PUT 和上报是插件业务步骤，不复制 DeviceCommand、WMS 或 Transport 状态。多个
`MaterialExecution` 可以并行；不建立通用调度器或 WorkLine 全局锁。每次动作只验证：拓扑步骤正确、目标设备没有活动命令、
`source` 是预期 `material_trace_id`、`target` 当前可接收。

## 4. 稳定身份

| 身份 | 生成方 | 规则 |
| --- | --- | --- |
| `material_trace_id` | ECS | 全局唯一且永久不复用；SCAN、命令与结果均回显 |
| `source_event_id` | ECS | 设备事件幂等身份；同一事件重传保持载荷不变 |
| `material_execution_id` | WES | 一盘实物本次粗分执行身份 |
| `operation_id` | 当前 WMS/WES 消息发起方 | 原消息技术重试保持不变；重新求值使用新身份 |
| `command_code` | WES DeviceCommand | 命令与 CALLBACK 关联身份；必须校验 device/trace |
| `rack_replacement_id` | WMS | 一次稳定换架计划身份；重试返回原计划 |
| 业务幂等键 | `rough_sorter` 插件 | 固定为 `(rack_replacement_id, leg)`，其中 `leg = OLD_OUT | NEW_IN`；同一键永久映射同一 Transport 调用 |
| `client_request_id` | `rough_sorter` 应用端 | 首次形成每条腿的确定 Transport 输入时生成全局唯一 UUIDv7，并与业务幂等键和完整输入原子持久化；崩溃重放不得换号 |

## 5. Operation 闭集

| operation | 方向 | 触发条件 | 业务结果 |
| --- | --- | --- | --- |
| `inbound.material.admission_decide@v1` | WES → WMS | `SCAN_COMPLETED` 已可靠持久化并校验 | `ACCEPT | REJECT | WAIT` |
| `inbound.material.target_decide@v1` | WES → WMS | 料盘可靠到达流水线出口 | `ASSIGNED | NO_AVAILABLE_CELL | REJECT | WAIT` |
| `inbound.material.placement_report@v1` | WES → WMS | 出料 PUT 成功且位置身份匹配 | `RECORDED | DUPLICATE` |
| `inbound.material.ng_placement_report@v1` | WES → WMS | WMS 业务拒绝且料盘可靠到达指定 NG | `RECORDED | DUPLICATE` |
| `inbound.source_rack.replacement_plan_decide@v1` | WES → WMS | 无可用 Cell 且没有活动的同货架计划请求 | `READY | WAIT` |
| `inbound.execution.recovery_decided@v1` | WMS → WES | 人工已核对业务主账与物理事实 | `RECEIVED | DUPLICATE` |

operation 专属 `data` 是严格闭集。未知字段、错误类型、枚举外值、同一稳定身份不同载荷均必须拒绝。业务 `WAIT` 是一次确定
决定；后续重求值使用新 `operation_id`。网络超时、暂时不可用或未得到确定响应不改业务身份。

## 6. SCAN 与业务准入

WES 只有在 `SCAN_COMPLETED` 包含并可靠保存以下完整事实后才请求准入：

- `material_trace_id`；
- 六合一码 `LotCode`、`DateCode`、`Qty`、`ProductNo`、`MfrPN`、`PONumber`；
- `diameter_mm`、`thickness_mm`；
- `shape_result = PASS | FAIL`；
- 当前可靠位置、`line_run_epoch_id` 与 `workline_code`。

`inbound.material.admission_decide@v1` 请求携带上述冻结证据和 `material_execution_id`。WMS 在准入中完成 GRN 绑定与业务校验，
但不得分配目标 Cell：

| `result` | 必填字段 | WES 行为 |
| --- | --- | --- |
| `ACCEPT` | `pkg_id`、`inbound_admission_id` | 进入正常设备链；不把准入当作目标授权 |
| `REJECT` | `reason_code`、`ng_destination` | 只有位置与设备均可靠时才执行指定 NG |
| `WAIT` | `reason_code`、`retry_after_ms` | 保持 `HOLD`，不创建设备命令 |

外形测量 `FAIL` 是 ECS 事实，不自动等于设备失败或业务 NG；业务结果仍由 WMS 决定。

## 7. 正常设备链与目标晚绑定

WMS `ACCEPT` 后，插件按以下顺序创建既有 DeviceCommand：

1. `MEASUREMENT_DEVICE` 执行 `PICK_AND_PUT`，把当前 trace 从入料/测量位送到流水线入口；
2. 成功 CALLBACK 已持久化并形成确定位置事实后，`TRANSFER_DEVICE` 执行 `MOVE_FORWARD` 到流水线出口；
3. 料盘可靠到达出口后，WES 才请求 `inbound.material.target_decide@v1`；
4. 只有 WMS 返回唯一精确 Cell 且本地物理门禁通过时，`PLACEMENT_DEVICE` 执行 `PICK_AND_PUT`；
5. PUT 成功后提交 `inbound.material.placement_report@v1`；只有 WMS `RECORDED | DUPLICATE` 后执行进入 `CLOSED`。

目标请求携带 `material_execution_id`、`material_trace_id`、`pkg_id`、`inbound_admission_id`、出口位置和当前单层货架身份。

| `result` | 必填字段 | WES 行为 |
| --- | --- | --- |
| `ASSIGNED` | `target_assignment_id`、精确 `target_position`、`placement_sequence`、`expected_height_mm` | 校验后创建唯一出料命令 |
| `NO_AVAILABLE_CELL` | `reason_code` | 不下发出料命令；请求稳定换架计划 |
| `REJECT` | `reason_code`、`ng_destination` | 只有确定业务拒绝才进入指定 NG |
| `WAIT` | `reason_code`、`retry_after_ms` | 料盘停留出口安全位；不自行选择 Cell |

`target_position` 必须是唯一 `rack_id + rack_slot_code + bin_id + bin_cell_id`。WES 不预建、替换或本地计算 Cell。

## 8. placement、NG 与人工核验恢复

以下三个 operation 的 `data` 都是严格对象：只允许表中字段，未知字段、缺少必填字段、错误类型、非法 `null`、重复数组成员或
条件字段不一致均拒绝。所有 ID、code 和枚举都是大小写敏感的非空 string；时间是大于 `0` 的 UTC Unix 毫秒 integer。

### 8.1 `inbound.material.placement_report@v1`

只有出料 `PICK_AND_PUT` 的成功 CALLBACK 已持久化，且命令、设备、trace 与 WMS 目标全部匹配时才可提交。

| `data` 字段 | JSON 类型 | 必填 | 可空 | 约束 |
| --- | --- | --- | --- | --- |
| `material_execution_id` | string | 是 | 否 | 当前 `MaterialExecution` |
| `material_trace_id` | string | 是 | 否 | 必须与 SCAN、命令和 CALLBACK 原值一致 |
| `pkg_id` | string | 是 | 否 | admission `ACCEPT` 返回原值 |
| `inbound_admission_id` | string | 是 | 否 | admission `ACCEPT` 返回原值 |
| `target_assignment_id` | string | 是 | 否 | target `ASSIGNED` 返回原值 |
| `target_position` | object | 是 | 否 | 第 8.4 节 `ONE_LAYER_BIN_CELL` 严格对象，必须等于目标决定和 CALLBACK 实际位置 |
| `placement_sequence` | integer | 是 | 否 | 大于 `0`，取 target `ASSIGNED` 返回原值 |
| `command_code` | string | 是 | 否 | 本次出料 `PICK_AND_PUT` 的原命令身份 |
| `placed_at` | integer | 是 | 否 | ECS 确定物理完成时间，UTC Unix 毫秒 |

WMS 在一个事务中校验准入、目标、序号、trace 和最终位置并完成入库记录。首次成功响应只允许
`200 / RECORDED` 且 `data={}`；相同 `operation_id`、相同 Payload 重放只允许 `200 / DUPLICATE` 且 `data={}`。身份、目标、
位置或幂等内容冲突使用公共 `409 / CONFLICT`，不得改字段或换身份绕过。只有 `RECORDED | DUPLICATE` 才能关闭该盘；ACK、
DeviceCommand 成功或 WES 位置投影均不能替代该业务完成事实。

### 8.2 `inbound.material.ng_placement_report@v1`

业务 `REJECT` 优先走 WMS 指定 NG。只有 NG 位置 READY、命令/结果身份一致且物料可靠到位，WES 才提交：

| `data` 字段 | JSON 类型 | 必填 | 可空 | 约束 |
| --- | --- | --- | --- | --- |
| `material_execution_id` | string | 是 | 否 | 原粗分执行 |
| `material_trace_id` | string | 是 | 否 | 必须与 SCAN 和 NG 到位证据一致 |
| `pkg_id` | string | 否 | 否 | 只有 WMS 在业务拒绝前已返回稳定 `pkg_id` 时携带；没有时省略 |
| `ng_evidence_id` | string | 是 | 否 | WES 已持久化的不可变 NG 到位证据身份 |
| `ng_position` | object | 是 | 否 | 第 8.4 节 `NG_POSITION` 严格对象，必须等于 WMS 指定目的地和 ECS 实际位置 |
| `reason_code` | string | 是 | 否 | WMS 当前 `REJECT` 返回原值 |
| `business_context` | string | 是 | 否 | 固定为 `ROUGH_SORT_INBOUND` |

首次成功响应只允许 `200 / RECORDED` 且 `data={}`；相同 `operation_id`、相同 Payload 重放只允许
`200 / DUPLICATE` 且 `data={}`。执行、trace、位置、原因或幂等内容冲突使用公共 `409 / CONFLICT`。物理未知、身份冲突、
CALLBACK `FAILED` 或结果 `UNKNOWN` 不自动转 NG，统一进入 `RECONCILING`。

### 8.3 `inbound.execution.recovery_decided@v1`

WMS 人工核对业务主账与现场事实后，通过公共 WMS→WES 异步回调信封发送：

| `data` 字段 | JSON 类型 | 必填 | 可空 | 约束 |
| --- | --- | --- | --- | --- |
| `recovery_id` | string | 是 | 否 | WMS 生成的稳定人工恢复决定身份 |
| `material_execution_id` | string | 是 | 否 | 本次已冻结的唯一 `MaterialExecution` |
| `material_trace_id` | string | 是 | 否 | 必须等于该 execution 的原 trace |
| `reconciling_evidence_id` | string | 是 | 否 | WES 公开的当前冻结 evidence 身份；解析后的 evidence 必须等于 execution 当前 `last_transition_evidence_id` |
| `decision` | string | 是 | 否 | `CONTINUE | ABORT`；`CONTINUE` 要求 `authoritative_position` 非 `null` |
| `authoritative_position` | object | 是 | 是 | 已知时为第 8.4 节严格位置对象；实物缺失时为 `null` 且只能 `ABORT` |
| `reason_code` | string | 是 | 否 | 本次人工决定的稳定原因 |

WES 不建立人工对账单、恢复任务或批量恢复聚合。`decision=CONTINUE` 时，`authoritative_position` 必须是非 `null` 的第 8.4 节
严格对象；`authoritative_position=null` 时只能使用 `decision=ABORT`。多个 execution 必须分别发送消息，并分别使用新的
`recovery_id` 和 `operation_id`。

首次可靠持久化只允许 `202 / RECEIVED` 且 `data={}`；相同 `operation + operation_id`、相同 Payload 重放只允许
`200 / DUPLICATE` 且 `data={}`。同身份异 Payload、execution/trace/evidence 围栏或不可变决定冲突使用公共 `409 / CONFLICT`。
WES 只在 execution 仍为 `RECONCILING`，且公开 `reconciling_evidence_id` 解析出的 evidence 等于当前
`last_transition_evidence_id` 时应用决定；否则保持冻结。`CONTINUE` 只按本次非空权威位置恢复后续步骤；`ABORT` 终止该 execution
的业务推进但不删除现场实物。人工决定后的迟到
回调只保留证据，不改写人工决定或历史 DeviceCommand/TransportTask 结果。

### 8.4 严格位置对象

位置 object 以 `type` 判别，只允许以下三个结构；各分支禁止出现其它分支字段或未知字段：

| `type` | 完整字段 | JSON 类型 | 可空 | 约束 |
| --- | --- | --- | --- | --- |
| `ONE_LAYER_BIN_CELL` | `type`、`rack_id`、`rack_slot_code`、`bin_id`、`bin_cell_id` | 全部 string | 否 | 单层货架唯一目标 Cell |
| `HANDOFF_POSITION` | `type`、`location_code` | 全部 string | 否 | 当前 WorkLine 冻结的流水线/交接逻辑位置 |
| `NG_POSITION` | `type`、`location_code` | 全部 string | 否 | WMS 指定且活动 Epoch 已批准的 NG 位置 |

## 9. 单层货架更换与两个 TransportTask

WES 请求稳定换架计划时，WMS 返回：

请求 `data` 只包含当前决定所需的稳定身份：

| 字段 | JSON 类型 | 约束 |
| --- | --- | --- |
| `material_execution_id` | string | 当前停留在流水线出口的粗分执行身份 |
| `material_trace_id` | string | 当前料盘的原始 trace |
| `current_rack_id` | string | 当前已到位且没有可用 Cell 的单层货架身份；不是搬运 `source` 位置 |

不携带 WorkLine、release snapshot 或通用 context；旧架 release gate 仍由 WES 在创建 Transport 前本地闭合。

```text
rack_replacement_id
old_loaded_rack: rack_id + source + target + target_face
new_empty_rack:  rack_id + source + target + target_face
```

同一 `rack_replacement_id` 重试必须返回完全相同的计划。旧架 Transport 创建前必须闭合 release gate：

- 没有指向旧架的活动 PUT；
- 所有成功 placement 均已被 WMS `RECORDED | DUPLICATE`；
- 旧架位置不存在 `UNKNOWN | RECONCILING`；
- 已冻结不可变释放快照。

Phase 8 真实消费既有 Transport Port，并创建两个互相独立的 `RACK_MOVE`：

| 任务 | 业务幂等键 | `client_request_id` | Transport data |
| --- | --- | --- | --- |
| 旧装载架移出 | `(rack_replacement_id, OLD_OUT)` | 应用端持久化映射的全局唯一 UUIDv7 | `old_loaded_rack` 的 `rack_id + source + target + target_face` |
| 新空架移入 | `(rack_replacement_id, NEW_IN)` | 应用端持久化映射的全局唯一 UUIDv7 | `new_empty_rack` 的 `rack_id + source + target + target_face` |

业务幂等键不是 wire 字段，也不能直接写入 `client_request_id`。应用端必须在首次调用 Transport 前原子保存
`(rack_replacement_id, leg) -> client_request_id` 一对一映射和完整冻结输入；同键重试复用原 UUIDv7，不同键生成不同 UUIDv7。

不新增 `RACK_EXCHANGE`。两个任务可以同时提交；实际顺序、路径、避让和共享工作位互锁完全由 RCS 负责。该顺序能力是外部未
验证前提，不是 Phase 8 新增合同条款，也不由 WES 测试代证。

新架的 `transport.task.resulted@v1` 搬运最终结果为 `SUCCEEDED`，且 `rack_id + final_position + arrival_face` 与计划一致后，WES 可以重新请求目标 Cell，不等待旧架结果。
旧架失败或未知只隔离旧 rack；新架失败或未知阻止目标请求和出料。两个 TransportTask 状态不得级联或互相改写。

## 10. 失败与恢复

- 同步明确拒绝表示动作未接纳；可以保持等待、有界安全恢复或重新请求 WMS 业务决定。
- ACK 后 `FAILED`、交付未知或位置未知时，不重放等价物理动作，进入 `RECONCILING`。
- 相同回调幂等；冲突回调保留证据并进入人工核验恢复。
- 重启只从已持久化事实继续，不重发已接纳或结果未知的命令。
- 任一步骤都不能用状态查询、HTTP 成功或超时推断物理完成。

## 11. 验收所有权与未验证边界

| 主要 owner | 唯一验收内容 |
| --- | --- |
| Phase 7 核心 | 固定 device wire、DeviceCommand 可靠性、ACK/CALLBACK 与幂等 |
| 供应商 ECS/网关 | 粗分设备附录、供应商字段映射、状态真实性与物理后置条件 |
| `src/app/wms_adapter/` | 本文 operation、严格 DTO、幂等与 HTTP 错误语义 |
| `rough_sorter` 插件包 | 五态生命周期和本文业务 Decision |
| 现场联合验收 | 真实设备、WMS、RCS、ECS 的成功与失败关闭闭环 |

RCS 实际顺序与共享工作位互锁能力仍是外部未验证前提。它不阻断按获批合同开始 Phase 8 实施，但在供应商/现场证据形成前，
不得声称两个 `RACK_MOVE` 已完成真实 RCS 集成或业务验收。
