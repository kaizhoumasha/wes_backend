# 粗分机扫码到准入决策窄闭环合同

contract_version: rough-sorter-scan-decision.v1
status: Approved
owner: 业务 Owner（待明确）
approved_by: kaizhou
approved_at: 2026-07-16T19:39:04+08:00

> 本文是该切片的单一权威业务规格。已由 kaizhou 于 2026-07-16T19:39:04+08:00 明确批准，当前状态 `Approved`。配套 fixture 仅用于测试与评审取证，不是 Runtime 配置，也不是通用 DSL。

## 切片边界

起点是已经完成输入归一化、并被 RuntimeInbox 接受的 `SCAN_COMPLETED`。本切片有四类合法终点：下一设备命令已持久化、稳定原因码 Hold、late/unknown callback 的 evidence-only 归档、replay no-op；后两类均不得推进当前 Session。`MOVE_FORWARD`、`MOVE_TO_NG` 的执行结果不属于本切片。

明确排除：输送线命令的实际执行、格位预约、货架补给、出料、入库记账、满箱交换和 SMT 流程。本合同不设计 provider wiring、数据库表、API、Celery 任务或通用能力接口。

## 输入身份与归一化

- 输入只接受 RuntimeInbox 中的 canonical payload；扫码业务字段只从 `payload.data` 读取，现场别名在归一化阶段转换，顶层同名字段不得覆盖 canonical data。
- 业务键以归一化后的 `PkgID` 派生；缺失 `PkgID` 时不得创建 MaterialUnit 或 DeviceCommand。
- correlation key 绑定 `Session + 当前等待命令 code + callback source_event_id`，命令结果必须命中当前等待锚点。
- scan-decision idempotency key 由切片、业务键和 attempt 身份组成；同 key 不允许代表两个不同输入。
- payload digest 是 canonical JSON 的稳定 SHA-256；同 key 同 digest 才能 replay，同 key 不同 digest 必须冲突 Hold。

## 状态与决策表

| 输入/判定 | Material/Context 目标状态 | Intent | Outcome / reason code |
| --- | --- | --- | --- |
| 条码 OK | `IN_TRANSIT` / `PICK_TO_PIPELINE` | `CREATE_MATERIAL_UNIT`、`UPDATE_CONTEXT`、`COMMAND:PICK_AND_PUT` | `PICK_AND_PUT_PERSISTED` |
| 条码业务 NG | `NG` / `NG_MOVING` | `CREATE_MATERIAL_UNIT`、`UPDATE_CONTEXT`、`MARK_NG`、`COMMAND:MOVE_TO_NG` | `SCAN_NG_BY_RULE` |
| 缺 PkgID | 不创建物料/命令，Material Hold | `BLOCK:MATERIAL` | `ROUGH_SORTER_CONTEXT_MISSING` |
| 入料成功、有效测量、WMS 准入 | `IN_TRANSIT` / `MOVING_FORWARD` | `UPDATE_CONTEXT`、`COMMAND:MOVE_FORWARD` | `MOVE_FORWARD_PERSISTED` |
| 测量业务 NG | `NG` / `NG_MOVING` | `UPDATE_CONTEXT`、`MARK_NG`、`COMMAND:MOVE_TO_NG` | `MEASUREMENT_NG` |
| WMS 拒绝或无匹配 | `NG` / `NG_MOVING` | `UPDATE_CONTEXT`、`MARK_NG`、`COMMAND:MOVE_TO_NG` | `WMS_REJECTED` |
| 测量合同无效 | Material Hold | `BLOCK:MATERIAL` | `ROUGH_SORTER_MEASUREMENT_INVALID` |
| 入料设备失败 | Command Hold | `BLOCK:COMMAND` | 设备稳定错误码 |
| 入料结果超时 | Command Hold | `BLOCK:COMMAND` | `ROUGH_SORTER_PICK_RESULT_TIMEOUT` |
| WMS timeout/unavailable | 保留物料与 QUERY evidence，Material Hold | `BLOCK:MATERIAL` | `WMS_TIMEOUT` |
| 同键同 digest replay | 状态不变 | 无新 Intent | `REPLAY_ACCEPTED_NOOP` |
| 同键不同 digest | Material Hold | `BLOCK:MATERIAL` | `IDEMPOTENCY_CONFLICT` |
| late/unknown callback | Material、Command、当前 Session 均不变；只归档 mismatch evidence | 无 RuntimeIntent | `ARCHIVED_EVIDENCE / COMMAND_RESULT_CORRELATION_MISMATCH` |

Outcome 是本切片终点的业务结果，不表示后续设备动作已成功。Intent 使用现有稳定 kind/action 名；规格不复制完整设备协议 JSON。

## 能力与 Evidence 所有权

| 能力需求 | 类型 | 业务语义 | 首次 attempt 必须拥有的 evidence |
| --- | --- | --- | --- |
| 本地条码纯决策 | PURE | 对归一化六码做 OK/NG/缺字段判定，不产生外部副作用 | 输入快照、业务键、规则版本、判定与原因 |
| WMS 准入 | QUERY | 以业务键和有效测量摘要查询是否允许进入下一段 | QUERY 身份、请求摘要、响应摘要或 timeout/unavailable 摘要 |
| MaterialUnit / Context 写入 | EFFECT | 持久化物料身份、业务上下文、阶段及 NG/Hold 事实 | EFFECT/Intent 身份、目标状态、写入结果 |
| DeviceCommand 写入 | EFFECT | 持久化 `PICK_AND_PUT`、`MOVE_FORWARD` 或 `MOVE_TO_NG` 命令 | Intent 身份、action、correlation key、等待 deadline |

首次 attempt 是输入快照、测量、WMS 响应摘要、最终决策、Intent 身份和 payload digest 的所有者。Runtime/业务域必须能由这些 evidence 解释为何生成命令或 Hold，而不是依赖 provider 临时状态。

## 异常矩阵

| 场景 | 决策 | 是否 QUERY WMS | 是否创建新 EFFECT | 稳定结果 |
| --- | --- | --- | --- | --- |
| 条码 OK | 入料 | 否 | 是 | `PICK_AND_PUT` |
| 条码业务 NG | NG 搬运 | 否 | 是 | `MOVE_TO_NG` |
| 缺 PkgID | Hold | 否 | 否 | `ROUGH_SORTER_CONTEXT_MISSING` |
| 入料成功 + 测量 OK + WMS 准入 | 前进 | 首次一次 | 是 | `MOVE_FORWARD` |
| 测量业务 NG | NG 搬运 | 否 | 是 | `MEASUREMENT_NG` |
| WMS 拒绝/无匹配 | NG 搬运 | 首次一次 | 是 | `WMS_REJECTED` |
| 测量合同错误 | Hold | 否 | 否 | `ROUGH_SORTER_MEASUREMENT_INVALID` |
| 设备失败 | Command Hold | 否 | 否 | 设备稳定错误码 |
| 命令结果超时 | Command Hold | 否 | 否 | `ROUGH_SORTER_PICK_RESULT_TIMEOUT` |
| WMS 超时/不可用 | Material Hold | 首次一次且无成功响应 | 否 | `WMS_TIMEOUT` |
| 同键同 digest | 原决策 replay | 否 | 否 | no-op |
| 同键不同 digest | 冲突 Hold | 否 | 否 | `IDEMPOTENCY_CONFLICT` |
| late/unknown callback | 只归档 mismatch evidence，不推进或改变当前 Session | 否 | 否 | `ARCHIVED_EVIDENCE / COMMAND_RESULT_CORRELATION_MISMATCH` |

## Replay 契约

Replay 只读取首次 attempt 已持久化的输入、测量、WMS 响应摘要、决策、Intent 身份和 digest。同 key 同 digest 返回原始决策/evidence，不重新实时查询 WMS，也不重复 Material、Context 或 DeviceCommand EFFECT。

同 key 不同 digest 必须产生 `IDEMPOTENCY_CONFLICT` 审计并 Hold。首次 WMS QUERY timeout/unavailable 时，只保存失败摘要和“无成功 evidence”事实；replay 不得把之后的实时查询结果伪装成首次成功 evidence。需要重新尝试外部查询时，应建立新的受控 attempt，而不是修改原 attempt。

## 原因码决策记录

- `SCAN_NG_BY_RULE`：复用条码领域服务对 `PkgID` 命中 `SIZENG` / `THICKNESSNG` 的现有稳定码；规格不再使用 `BARCODE_RULE_NG`，禁止双码。
- `ROUGH_SORTER_MEASUREMENT_INVALID`：粗分机测量合同无效，当前没有发现全局稳定同义码，保留为目标码。
- `ROUGH_SORTER_PICK_RESULT_TIMEOUT`：表示已下发入料命令后等待终态结果超时；现有 `COMMAND_ACK_TIMEOUT` 仅表示 ACK 阶段，不是同义码，禁止互换。
- `ROUGH_SORTER_WMS_ADMISSION_UNAVAILABLE` 是业务概念名。仓库已有全局稳定 `WMS_TIMEOUT`，因此实际 outcome 统一使用 `WMS_TIMEOUT`，不再发出前者，避免双码。
- `IDEMPOTENCY_CONFLICT`：复用现有全局稳定码，不新增别名。
- `COMMAND_RESULT_CORRELATION_MISMATCH`：命令结果未命中当前等待锚点，作为 `ARCHIVED_EVIDENCE` 的稳定归档分类；不生成 `BLOCK` 或其他 RuntimeIntent，也不改变当前 Session。

## 当前实现对照

| 能力 | 状态 | 依据与缺口 |
| --- | --- | --- |
| 扫码 OK 创建 MaterialUnit、Context、`PICK_AND_PUT` | covered | `orchestrator_bridge.py` 已生成三个 Intent，并有 runtime 测试覆盖 |
| 条码业务 NG 生成 `MOVE_TO_NG` | covered | 已生成 Material/Context/NG/Command Intent |
| 缺 PkgID Hold | gap | 目标是 `BLOCK:MATERIAL / ROUGH_SORTER_CONTEXT_MISSING`，但当前条码服务先判定 `BARCODE_INCOMPLETE`，随后生成 `UPDATE_CONTEXT`、`MARK_NG` 与 `MOVE_TO_NG`，并未 Hold |
| 入料设备失败 Command Hold | covered | 失败 command result 已转 `BLOCK:COMMAND` |
| 入料结果超时 | partial | Runtime 已有 command result deadline/对账骨架，尚未形成本切片目标码与完整决策 |
| late/unknown callback | partial | 已有 correlation/reconciliation evidence 能力，目标为 `ARCHIVED_EVIDENCE` 且不生成 RuntimeIntent、不推进或改变当前 Session |
| 测量合同校验、WMS 准入、成功/NG 分支 | gap | 成功 `PICK_AND_PUT` callback 当前仅产生 `CONTINUE_NEXT`，未消费测量并执行 WMS QUERY，也未生成 `MOVE_FORWARD` / `MOVE_TO_NG` 决策 |
| scan-decision replay/冲突闭环 | gap | 平台存在通用幂等构件，但未形成该切片首次 evidence 与 QUERY/EFFECT no-op 合同 |

## 验收标准

1. fixture 的 `schema_version`、`slice_id` 和 `RS-SD-001` 至 `RS-SD-013` 固定且可由测试判定。
2. 13 个 case 都记录输入摘要、首次/replay evidence、目标状态、稳定 Intent/outcome、覆盖状态和精确源码引用。
3. covered/partial/gap 与当前实现对照一致；不得把成功 callback 的 `CONTINUE_NEXT` 描述为测量/WMS 已完成。
4. replay 同 digest 不重新 QUERY、不重复 EFFECT；不同 digest 使用 `IDEMPOTENCY_CONFLICT` Hold。
5. WMS timeout 只记录失败 evidence，reason code 统一为 `WMS_TIMEOUT`；不得同时发出业务概念别名。
6. 文档批准状态在业务 Owner 明确确认前保持 `Review`，`approved_by` 与 `approved_at` 为空。
