---
status: Approved
implementation_authorization: true
annex_key: rough-sorter-device-contract
contract_version: "1.0"
approved_at: 2026-09-05
scope: Phase 8 粗分机测量、输送和出料设备统一合同
owners: [ECS, WES, 业务负责人, 项目交付负责人]
---

# 粗分机设备合同附录

## 1. 状态与真源

本文定义 Phase 8 粗分设备的 WES 侧合同；2026-09-05 按用户批准的“约定大于配置”收敛，供应商实现仍需独立验证。
固定路径、公共包络、身份、ACK/CALLBACK、幂等、状态查询、
HTTP 错误和投递未知语义全部引用
[`third_party_integration_whitepaper.md`](../../integration/third_party_integration_whitepaper.md)，本文只收窄粗分实际使用的设备角色、
`task_type`、`event_type`、严格载荷、结果、稳定错误类型和绑定规则。

粗分业务语义以
[`wms-rough-sorter-inbound-integration-requirements.md`](../wms-rough-sorter-inbound-integration-requirements.md) 为唯一真源。
[`SMT粗分机接口调用说明书20260321-v1.md`](../../hardware/SMT粗分机接口调用说明书20260321-v1.md) 是只读供应商输入，不是
WES wire；供应商私有路径、坐标、字段、错误和适配只留在 ECS/网关。

## 2. WorkLine、角色与合同身份

`device_code` 全厂唯一；每个 WorkLine 各绑定一个以下角色：

| `device_role` | `contract_key` | 允许能力 |
| --- | --- | --- |
| `MEASUREMENT_DEVICE` | `rough_sorter.measurement_device` | `SCAN_COMPLETED`；`PICK_AND_PUT` |
| `TRANSFER_DEVICE` | `rough_sorter.transfer_device` | `MOVE_FORWARD` |
| `PLACEMENT_DEVICE` | `rough_sorter.placement_device` | `PICK_AND_PUT` |

三个角色都是独立命令资源，每个 `device_code` 最多有一个已接纳未终态命令。不同 `device_code` 可以并行；插件不建立 WorkLine
全局锁。`ESTOP_PRESSED` 由 Phase 7 基础能力处理，不进入本附录的 Phase 8 自动业务事件闭集。

## 3. Endpoint、设备与 Epoch 绑定

`Device.endpoint_base_url` 是可派发物理设备的 Endpoint 主数据；纯上报、人工或逻辑设备允许为空。多个 Device 可以共享同一
Endpoint，同一 WorkLine 内的 Device 也可以分别指向不同 Endpoint。Endpoint 不属于插件配置，不新增 `DeviceEndpoint` 实体。
Endpoint 主机接受 RFC1918、IPv6 ULA、回环地址、内部单段服务名或带点完整域名；公网 IP 及 unspecified、广播、multicast、link-local、
reserved、文档网段和 legacy numeric host 均失败关闭。域名在配置校验阶段只做语法与 canonicalization，不把 DNS 解析结果冻结为设备身份。

每个部署实例必须把派发链实际读取的不可变值写入现有 `LineRunEpochDeviceBinding`。`topology_digest` 只摘要创建前即可形成的稳定
topology input，不摘要数据库生成的 `line_run_epoch_id`、`device_id`、binding 主键、审计字段或时间戳；父 Epoch 关联仍必须持久化，
但它不是 topology 内容。设备绑定的稳定摘要输入包含 `device_code`、插件 `device_role`、Endpoint、合同身份和派发策略，
Position 的稳定摘要输入包含角色、`location_id` 和固定 `location_type`；Device 主数据本身不保存业务角色：

| 绑定项 | 规则 |
| --- | --- |
| `line_run_epoch_id` | 指向父 Epoch；WorkLine 归属由 `LineRunEpoch.workline_id` 唯一确定，不重复保存 `workline_id` |
| `device_role` | 每个 WorkLine 三个角色各一个绑定 |
| `device_code` | 全厂唯一；不能用 Endpoint 数量替代设备身份 |
| Endpoint Base URL | 从可派发 `Device` 复制；必须为非空局域网 HTTP origin，固定路径不进入配置 |
| `contract_key`、`contract_version=1.0` | 插件声明并冻结；实时状态接口验证命令能力，测量角色还必须支持 `SCAN_COMPLETED` |
| `status_max_age_ms`、`command_timeout_ms` | 插件固定为 10,000 ms 和 30,000 ms；冻结供基础派发读取，不进入命令 `params` |

工作线只保存通用 `config.device_bindings`，角色键为上述三个设备角色，值为本线实际 `device_code`。
START 使用宿主通用绑定校验，冻结该配置快照，并将 Endpoint、合同与执行策略写入现有 Epoch binding。
不再保存 `rough_sorter` 私有配置子树、版本登记、位置映射或未被执行逻辑消费的参数。
`configuration_digest` 仍由插件身份、运行模式和 canonical JSON 快照生成；基础层不解释粗分业务。

粗分插件使用以下固定逻辑位置参数；`location_id` 与 `location_type` 均取该角色字面量：

| 逻辑参数 | 业务含义 |
| --- | --- |
| `MEASUREMENT_POSITION` | 测量和准入位置 |
| `PIPELINE_INLET` | 流水线入口 |
| `PIPELINE_OUTLET` | 流水线出口 |
| `NG_POSITION` | 业务拒绝放置位置 |

这些参数不是设备编码或厂商坐标。ECS 根据收到命令的设备及约定解释实际位置；WMS 交互使用相同逻辑位置合同。
绑定和投影沿用工作线/Epoch 范围，不新增点位主数据、映射表或全局位置别名。
状态年龄阈值 10 秒是 WES 的执行准入策略，使用状态响应后的观察时间；未来或过期状态继续失败关闭。
插件决定下一步命令前，通过基础 ECS 适配器按 Epoch 冻结的 Endpoint 和设备身份读取实时状态；暂不可用时沿用业务等待重试。
历史状态记录不能作为持续等待的唯一依据。基础派发层在发送前再次校验实时状态，防止预检后状态变化；两次读取均有界，不新增轮询器。
固定参数的本地验证不表示供应商已实现对应解释。

`plugin_key="rough_sorter"`、`plugin_version="1.0.0"`、`flow_mode="ROUGH_SORT_INBOUND"`、三组 `contract_key` 和
`contract_version="1.0"` 来自静态部署组合，不由数据库配置选择。Endpoint 不进入该业务配置。

粗分纯 Decision 层只声明角色、合同和业务处理逻辑，不读取或保存 Endpoint。插件应用层的 START 对三个可派发角色逐一要求 Device Endpoint 非空，并复制到
Epoch binding；活动 Epoch 和派发链只读取冻结 binding，不回读可变 Device 主数据。业务诊断读取 Epoch 快照，不把当前
`WorkLine.config` 伪装成历史运行配置。活动 Epoch 内任何绑定或配置变化都必须停止新接纳、闭合或人工清理活动对象并使用新的
`request_id` 创建新 Epoch；不得静默替换，也不新增数据库插件注册表或 Endpoint 注册表。

## 4. 命令闭集

Phase 8 只使用两个 `task_type`：

| `task_type` | 允许角色 | 成功后置条件 |
| --- | --- | --- |
| `PICK_AND_PUT` | `MEASUREMENT_DEVICE`、`PLACEMENT_DEVICE` | 当前 trace 已离开 source，并稳定到达命令 target |
| `MOVE_FORWARD` | `TRANSFER_DEVICE` | 当前 trace 已稳定到达流水线出口 target |

两类命令的 `params` 都是严格闭集：

| 字段 | JSON 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `material_trace_id` | string | 是 | ECS 生成、全局唯一且永久不复用；命令和结果原样回显 |
| `source` | object | 是 | 第 5 节严格位置对象；必须含当前 trace |
| `target` | object | 是 | 第 5 节严格位置对象；下发时可接收当前 trace |

禁止 `priority`、`timeout`、供应商动作名、坐标或额外控制参数。供应商的扫码、测量、抓取路径和 PLC 内部步骤由 ECS/网关完成，
不能扩展本闭集或要求 WES 重放内部动作。

## 5. 位置对象

`source` 与 `target` 固定包含：

| 字段 | JSON 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `location_id` | string | 是 | 活动 Epoch 冻结的稳定逻辑位置 |
| `location_type` | string | 是 | `MEASUREMENT_POSITION \| PIPELINE_INLET \| PIPELINE_OUTLET \| RACK_CELL \| NG_POSITION` |
| `material_trace_id` | string | 是 | 必须与命令顶层 `params.material_trace_id` 一致 |

`RACK_CELL` 还必须含 `rack_id + rack_slot_code + bin_id + bin_cell_id`，且与 WMS 当前目标决定一致。`NG_POSITION` 必须是 WMS
当前业务拒绝指定且活动 Epoch 已批准的位置。设备坐标、供应商 `location_id` 别名和机械参数不得进入 WES 合同。

## 6. 自动事件闭集

Phase 8 自动业务输入只有 `SCAN_COMPLETED`。事件公共包络中的 `source_event_id` 做幂等；同一事实重传时 `timestamp`、合同身份和
`data` 不得变化。

`SCAN_COMPLETED.data` 为严格闭集：

| 字段 | JSON 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `material_trace_id` | string | 是 | ECS 全局唯一且永久不复用 |
| `LotCode`、`DateCode`、`Qty`、`ProductNo`、`MfrPN`、`PONumber` | string | 是 | 六合一码设备原文；WES 不拼接或换算 |
| `diameter_mm`、`thickness_mm` | string | 是 | 规范十进制毫米值；毫米是唯一单位 |
| `shape_result` | string | 是 | `PASS \| FAIL`；这是测量事实，不直接等于业务 NG |
| `position` | object | 是 | 第 5 节 `MEASUREMENT_POSITION`，trace 必须一致 |

供应商 `SCAN_COMPLETED` 缺少任一必填字段时，ECS/网关不得发送半成品事件或用默认值补齐；应在供应商边界告警并失败关闭。

## 7. CALLBACK 结果闭集

结果必须回显原 `command_code`、`device_code`、`contract_key`、`contract_version`、`task_type` 和 `material_trace_id`，并携带部署级
永久唯一 `source_event_id`。WES 必须同时校验命令、设备和 trace；任一不一致保存冲突证据并进入对账，不推进位置。

| `result` | `data` | `error_detail` |
| --- | --- | --- |
| `SUCCESS` | `material_trace_id + actual_position`；`actual_position` 符合第 5 节且等于命令 target | 必须为 `null` |
| `FAILED` | `material_trace_id`；位置已确定时可带符合第 5 节的 `last_known_position`，未知时省略 | 必须使用第 8 节稳定错误类型 |

ACK 只代表 ECS 接纳命令。只有 `SUCCESS` CALLBACK 已持久化且 `command_code + device_code + material_trace_id + target` 全部匹配，
才能形成确定位置事实并推进拓扑。状态查询、HTTP `200` 或超时不得替代 CALLBACK。

## 8. 稳定错误类型

ECS/网关把供应商私有码映射为以下稳定闭集，并把原码保存在仅供诊断的 `supplier_raw_data`：

| `error_detail.code` | WES 处理 |
| --- | --- |
| `ACTION_FAILED` | 动作已失败；不得自动重放等价命令 |
| `SAFETY_INTERLOCK` | 保持最小安全隔离，等待现场处置 |
| `MATERIAL_IDENTITY_CONFLICT` | 冻结当前 trace、设备与相关位置并对账 |
| `POSITION_UNKNOWN` | 进入 `RECONCILING`，禁止推断 NG 或完成 |
| `DEVICE_FAULT` | 隔离该 `device_code`，保留原始诊断 |
| `INTERNAL_ERROR` | 失败关闭并人工诊断 |

测量 `shape_result=FAIL` 不是设备错误；`BIN_FULL` 等库存/容量结论由 WMS 决定，也不是设备错误。未知供应商错误只能映射为
`INTERNAL_ERROR` 并保留原码，不能临时映射为成功、NG 或可重试。

## 9. ACK、Retry-After、未知与不可逆点

- 命令 ACK、回调 ACK、固定 HTTP 状态和公共错误响应直接遵循统一白皮书。
- HTTP `429` 必须返回合法 `Retry-After`；调用方到期后只可在明确未接纳前提下使用原身份、原载荷有界重提。
- HTTP `400/404/405/413/422/429/503` 表示明确未接纳；网络中断、HTTP 超时和 `500/502/504` 是 delivery unknown。
- 命令 ACK 后只能等待原命令 CALLBACK 或进入对账；不得更换 `command_code`、target 或重放等价动作。
- 回调瞬态失败按统一白皮书使用原 `source_event_id` 和原载荷有界重传；HTTP `409` 立即停止并对账。

不可逆点按可观察物理事实定义：

| 动作 | 不可逆点 |
| --- | --- |
| `PICK_AND_PUT` | 设备已取得并控制当前 trace，物料不再可靠位于 source |
| `MOVE_FORWARD` | 当前 trace 已离开流水线入口，不能在不执行反向物理动作的情况下恢复 source |

不可逆点后的失败、未知或身份冲突均进入 `RECONCILING`；不能用 HTTP 已发送、状态 `RUNNING` 或 WES 推测代替物理信号。

## 10. 原始供应商字段映射

| 供应商输入 | 统一 wire |
| --- | --- |
| `SCAN_COMPLETED` 原始六合一码 | 保持原六字段进入同名严格字段 |
| 英寸、层数或类型化尺寸 | ECS/网关换算并验证为 `diameter_mm`、`thickness_mm` 规范毫米字符串，同时保留原始证据 |
| 供应商位置/坐标 | 映射为活动 Epoch 冻结 `location_id` 和第 5 节类型；私有值不外泄 |
| `INSPECTION_SIZE_NG`、`INSPECTION_THICKNESS_NG` | 映射为 `shape_result=FAIL` 测量事实，不产生 `FAILED` CALLBACK |
| `PICK_AND_PUT_FAILED` 等动作码 | 映射为第 8 节稳定错误类型，原码留在 `supplier_raw_data` |
| 成功时 `error_detail.code=NONE` | 统一为 `error_detail=null` |

## 11. 验收所有权

| 主要 owner | 唯一验收内容 |
| --- | --- |
| Phase 7 核心 | 固定路径、公共包络、身份、ACK/CALLBACK、幂等和 DeviceCommand 可靠性 |
| 供应商 ECS/网关 | 本附录闭集、原始字段映射、状态真实性、不可逆点、时限和重传 |
| `src/app/wms_adapter/` | 获批粗分 WMS operation、DTO、幂等和错误语义 |
| `rough_sorter` 插件 | 业务 Decision、设备动作创建时机、目标晚绑定和对账边界 |
| 现场联合验收 | 一个真实料盘的成功与失败关闭闭环 |

WES/WMS/ECS 联合批准只冻结合同。具体部署仍必须保存 Endpoint、设备、版本、时限和一致性验收证据；在真实供应商/现场证据
形成前，不得把本文 `Approved` 夸大为供应商一致性通过或现场业务验收完成。
