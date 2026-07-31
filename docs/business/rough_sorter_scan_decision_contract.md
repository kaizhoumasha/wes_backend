# 粗分机扫码到准入决策窄闭环合同

contract_version: rough-sorter-scan-decision.v1
status: Approved
owner: kaizhou
approved_by: kaizhou
approved_at: 2026-07-16T19:39:04+08:00

> 本文是该切片的单一权威业务规格。kaizhou 是本合同的业务批准责任人，并已于 2026-07-16T19:39:04+08:00 明确批准，当前状态 `Approved`。配套 fixture 仅用于测试与评审取证，不是 Runtime 配置，也不是通用 DSL。

## 切片边界

起点是已经完成输入归一化、并被 RuntimeInbox 接受的 `SCAN_COMPLETED`。扫码/测量事实必须先完成 Q19 准入并持久化 typed decision，之后才允许生成正常入料命令；禁止先 `PICK_AND_PUT`、再执行 Q19。本切片有四类合法终点：下一设备命令已持久化并等待终态结果、稳定原因码 Hold、late/unknown callback 的 evidence-only 归档、replay no-op；后两类均不得推进当前 Session。当前 Q19 provider failure 仅进入通用 fail-closed，虽记录 `HOLD` outcome 与 provider failure evidence，但尚未形成 Session Hold EFFECT，该缺口归入 T6。`PICK_AND_PUT`、`MOVE_FORWARD`、`MOVE_TO_NG` 均使用 `COMMAND_RESULT`，成功、失败和超时都必须经 RuntimeInbox 推进，禁止把命令持久化或派发成功解释为设备动作完成。

明确排除：格位预约、货架补给、出料、入库记账、满箱交换和 SMT 流程。未来切片新增的 `PUT_TO_BIN` 等设备动作同样必须等待完成回调，不得采用 `FIRE_AND_FORGET`。本合同不设计 provider wiring、数据库表、API、Celery 任务或通用能力接口。

## 输入身份与归一化

- 输入只接受 RuntimeInbox 中的 canonical payload；扫码业务字段只从 `payload.data` 读取，现场别名在归一化阶段转换，顶层同名字段不得覆盖 canonical data。
- 业务键以归一化后的 `PkgID` 派生；缺失 `PkgID` 时不得创建 MaterialUnit 或 DeviceCommand。
- correlation key 绑定 `Session + 当前等待命令 code + callback source_event_id`，命令结果必须命中当前等待锚点。
- scan-decision idempotency key 由切片、业务键和 attempt 身份组成；同 key 不允许代表两个不同输入。
- payload digest 是 canonical JSON 的稳定 SHA-256；同 key 同 digest 才能 replay，同 key 不同 digest 必须冲突 Hold。

## 状态与决策表

`BLOCK` 的 scope 用于写入 Session 的 `failure_domain`，普通 `BLOCK` 的 Hold 状态由 Session 持有；
callback deadline、资源对账和安全事件等专用途径会额外创建 RuntimeHold。两类路径都不会把
`MaterialUnit` 或 `DeviceCommand` 写成 `MANUAL_HOLD`，因此下表中的 Hold 分支必须保留实体在进入
Hold 前已经持久化的实际状态。

| 输入/判定 | Material/Context 目标状态 | Intent | Outcome / reason code |
| --- | --- | --- | --- |
| 扫码/测量事实有效且 Q19 `ADMIT` 已持久化 | `IN_TRANSIT` / `PICK_TO_PIPELINE` | `CREATE_MATERIAL_UNIT`、`UPDATE_CONTEXT`、`COMMAND:PICK_AND_PUT` | `PICK_AND_PUT_PERSISTED` |
| 条码业务 NG | `NG` / `NG_MOVING` | `CREATE_MATERIAL_UNIT`、`UPDATE_CONTEXT`、`MARK_NG`、`COMMAND:MOVE_TO_NG` | `SCAN_NG_BY_RULE` |
| 缺 PkgID | 不创建物料/命令；Session Hold | `BLOCK:MATERIAL` | `ROUGH_SORTER_CONTEXT_MISSING` |
| Q19 `REJECT` | `NG` / `NG_MOVING`；不得创建正常入料命令 | `CREATE_MATERIAL_UNIT`、`UPDATE_CONTEXT`、`MARK_NG`、`COMMAND:MOVE_TO_NG` | Q19 typed decision 的稳定 reason code |
| Q19 timeout/unavailable | 不创建物料/命令；Context 保持 `READY`；Session 保持 `RUNNING` | 无业务 Intent；通用 fail-closed 返回 `success=0/failed=1` | outcome reason 为空；provider evidence 保留稳定失败码（如 `WMS_PROVIDER_TIMEOUT`）；T6 blocked |
| 入料成功、有效测量且复用已持久化 Q19 `ADMIT` | `IN_TRANSIT` / `MOVING_FORWARD` | `UPDATE_CONTEXT`、`COMMAND:MOVE_FORWARD` | `MOVE_FORWARD_PERSISTED` |
| 测量业务 NG | `NG` / `NG_MOVING` | `UPDATE_CONTEXT`、`MARK_NG`、`COMMAND:MOVE_TO_NG` | `MEASUREMENT_NG` |
| 测量合同无效 | 物料保持 `IN_TRANSIT`、原命令保持 `COMPLETED`；Session Hold | `BLOCK:MATERIAL` | `ROUGH_SORTER_MEASUREMENT_INVALID` |
| 入料设备失败 | 命令保持 `FAILED`；Session Hold | `BLOCK:COMMAND` | 设备稳定错误码 |
| 入料结果超时 | 命令保持 `ACK_RECEIVED`；Session Hold；RuntimeHold `OPEN` | 无 RuntimeIntent；平台 `TIMER_TIMEOUT` reconciliation | `ROUGH_SORTER_PICK_RESULT_TIMEOUT` |
| `MOVE_FORWARD` / `MOVE_TO_NG` 成功 | 当前业务 phase 保持；清除命令等待锚点；Session `RUNNING` | `CONTINUE_NEXT` | `<ACTION>_COMPLETED` |
| `MOVE_FORWARD` / `MOVE_TO_NG` 失败 | 当前业务 phase 保持；Session Hold | `BLOCK:COMMAND` | 设备稳定错误码 |
| `MOVE_FORWARD` / `MOVE_TO_NG` 超时 | 当前业务 phase 保持；Session Hold；RuntimeHold `OPEN` | 无 RuntimeIntent；平台 `TIMER_TIMEOUT` reconciliation | `ROUGH_SORTER_COMMAND_RESULT_TIMEOUT` |
| 同键同 digest replay | 状态不变 | 无新 Intent | `REPLAY_ACCEPTED_NOOP` |
| 同键不同 digest | Material/Command 状态不变；Session Hold | `BLOCK:MATERIAL` | `IDEMPOTENCY_CONFLICT` |
| late/unknown callback | Material、Command、当前 Session 均不变；只归档 mismatch evidence | 无 RuntimeIntent | `ARCHIVED_EVIDENCE / COMMAND_RESULT_CORRELATION_MISMATCH` |

Outcome 是本切片终点的业务结果，不表示后续设备动作已成功。Intent 使用现有稳定 kind/action 名；规格不复制完整设备协议 JSON。

## 能力与 Evidence 所有权

| 能力需求 | 类型 | 业务语义 | 首次 attempt 必须拥有的 evidence |
| --- | --- | --- | --- |
| 本地条码纯决策 | PURE | 对归一化六码做 OK/NG/缺字段判定，不产生外部副作用 | 输入快照、业务键、规则版本、判定与原因 |
| Q19 准入 | QUERY | 在任何正常入料命令前，以业务键和扫码/测量事实判定 `ADMIT` / `REJECT`；不可用时通用 fail-closed | operation 身份、请求摘要、typed decision 或稳定 provider failure evidence |
| MaterialUnit / Context 写入 | EFFECT | 持久化物料身份、业务上下文、阶段及 NG 事实 | EFFECT/Intent 身份、目标状态、写入结果 |
| DeviceCommand 写入 | EFFECT | 持久化 `PICK_AND_PUT`、`MOVE_FORWARD` 或 `MOVE_TO_NG` 命令，并统一进入 `COMMAND_RESULT` 等待 | Intent 身份、action、correlation key、等待 deadline、终态 callback |
| Session Hold 写入 | EFFECT | 普通 `BLOCK` 持久化 Session Hold、`failure_domain` 与稳定原因码 | BLOCK Intent 身份、独立 EFFECT 身份、scope、原因码、Hold 写入结果 |
| 平台超时对账 | EFFECT | 保留事件 `TIMER_TIMEOUT` 直接进入 reconciliation，持久化 Session Hold 与 RuntimeHold，不创建 RuntimeIntent | reconciliation 身份/原因、RuntimeHold 身份/写入结果、Session Hold 写入结果 |

首次 attempt 是输入快照、测量、Q19 typed decision 或失败摘要、Intent 身份和 payload digest 的所有者。Runtime/业务域必须能由这些 evidence 解释为何生成命令或 Hold，而不是依赖 provider 临时状态。

## 异常矩阵

| 场景 | 决策 | 是否 QUERY WMS | 是否创建业务/设备 EFFECT | 是否创建 Session Hold EFFECT | 稳定结果 |
| --- | --- | --- | --- | --- | --- |
| 扫码/测量事实有效 + Q19 `ADMIT` | 入料 | 首次一次 | 是 | 否 | `PICK_AND_PUT` |
| 条码业务 NG | NG 搬运 | 否 | 是 | 否 | `MOVE_TO_NG` |
| 缺 PkgID | Hold | 否 | 否 | 首次一次 | `ROUGH_SORTER_CONTEXT_MISSING` |
| Q19 `REJECT` | 输入臂直接 NG 搬运 | 首次一次 | 是，但不得创建正常入料命令 | 否 | Q19 稳定 reason code |
| Q19 timeout/不可用 | 通用 fail-closed；T6 尚未形成 Session Hold | 首次一次且无成功 decision | 否 | 否 | outcome reason 为空；provider evidence 保留稳定失败码；`success=0/failed=1` |
| 入料成功 + 测量 OK + 已持久化 Q19 `ADMIT` | 前进 | 否，复用首次 decision | 是 | 否 | `MOVE_FORWARD` |
| 测量业务 NG | NG 搬运 | 否 | 是 | 否 | `MEASUREMENT_NG` |
| 测量合同错误 | Hold | 否 | 否 | 首次一次 | `ROUGH_SORTER_MEASUREMENT_INVALID` |
| 设备失败 | `BLOCK:COMMAND`，Session Hold | 否 | 否 | 首次一次 | 设备稳定错误码 |
| 命令结果超时 | 平台 `TIMER_TIMEOUT` reconciliation，Session Hold + RuntimeHold | 否 | 否 | 平台首次一次 | `ROUGH_SORTER_PICK_RESULT_TIMEOUT` |
| 同键同 digest | 原决策 replay | 否 | 否 | 否 | no-op |
| 同键不同 digest | 冲突 Hold | 否 | 否 | 首次检测冲突时一次 | `IDEMPOTENCY_CONFLICT` |
| late/unknown callback | 只归档 mismatch evidence，不推进或改变当前 Session | 否 | 否 | 否 | `ARCHIVED_EVIDENCE / COMMAND_RESULT_CORRELATION_MISMATCH` |

## Replay 契约

Replay 只读取首次 attempt 已持久化的输入、测量、Q19 typed decision 或失败摘要、Intent 身份和 digest。同 key 同 digest 返回原始决策/evidence，不重新实时查询 WMS，也不重复 Material、Context 或 DeviceCommand EFFECT。

同 key 不同 digest 必须产生 `IDEMPOTENCY_CONFLICT` 审计，并在首次检测冲突时执行一次 Session Hold EFFECT；后续对同一冲突结果的 replay 只复用已记录 evidence，不重复 Hold EFFECT。首次 Q19 timeout/unavailable 时，只保存失败摘要和“无成功 decision”事实；replay 不得把之后的实时查询结果伪装成首次成功 evidence。需要重新尝试外部查询时，应建立新的受控 attempt，而不是修改原 attempt。

普通 case 的 `recorded_evidence` 分为 `first_attempt` 与 `replay` 两阶段。`RS-SD-012` 必须额外使用 `subsequent_replay`，分别表达原始 attempt、首次 conflict detection 和后续 conflict replay。每个 BLOCK 首次处理都记录 BLOCK Intent 身份、独立 EFFECT 身份、scope、reason code 与 Session Hold 写入结果；后续 replay 复用相同 Intent/EFFECT 身份、原写入结果，并记录 `zero_new_hold_write`。

## 原因码决策记录

- `SCAN_NG_BY_RULE`：复用条码领域服务对 `PkgID` 命中 `SIZENG` / `THICKNESSNG` 的现有稳定码；规格不再使用 `BARCODE_RULE_NG`，禁止双码。
- `ROUGH_SORTER_MEASUREMENT_INVALID`：粗分机测量合同无效，当前没有发现全局稳定同义码，保留为目标码。
- `ROUGH_SORTER_PICK_RESULT_TIMEOUT`：表示已下发入料命令后等待终态结果超时；现有 `COMMAND_ACK_TIMEOUT` 仅表示 ACK 阶段，不是同义码，禁止互换。
- `ROUGH_SORTER_COMMAND_RESULT_TIMEOUT`：表示 `MOVE_FORWARD` / `MOVE_TO_NG` 等后续设备命令等待终态结果超时；平台 reconciliation 仍使用全局 `CALLBACK_DEADLINE_EXPIRED` 记录对账原因。
- Q19 timeout、provider 不可用或没有可复用 typed decision 时，当前通用 fail-closed 的 outcome reason 为空，稳定 provider failure code（例如 `WMS_PROVIDER_TIMEOUT`）只保存在 failure evidence 中；该分支发生在正常入料命令前。T6 尚未闭环 Session Hold，不得把它写成 `BLOCK:MATERIAL` 或已覆盖的 Hold。
- `IDEMPOTENCY_CONFLICT`：复用现有全局稳定码，不新增别名。
- `COMMAND_RESULT_CORRELATION_MISMATCH`：命令结果未命中当前等待锚点，作为 `ARCHIVED_EVIDENCE` 的稳定归档分类；不生成 `BLOCK` 或其他 RuntimeIntent，也不改变当前 Session。

## 当前实现对照

| 能力 | 状态 | 依据与缺口 |
| --- | --- | --- |
| 扫码 OK / NG、缺 PkgID | covered | production RuntimeInbox → generated Plugin → System Capability EFFECT 的 PostgreSQL 13-case E2E 已覆盖命令、物料与 Hold |
| Q19 前置准入 ADMIT / REJECT | covered | `ADMIT` 后才生成 `PICK_AND_PUT`；`REJECT` 直接生成输入臂 NG |
| Q19 provider failure 的 Session Hold | blocked（T6） | 当前仅保留稳定 provider failure evidence、零业务 Intent，并返回 `success=0/failed=1`；尚无 Session Hold EFFECT |
| 入料结果成功、失败、超时 | covered | typed callback、已持久化 Q19 evidence、`BLOCK:COMMAND` 与 TIMER reconciliation 均由真实 PostgreSQL 场景覆盖 |
| `MOVE_FORWARD` / `MOVE_TO_NG` 终态结果 | covered | 两类命令的成功、失败、超时共 6 个 PostgreSQL 回归；成功清除 wait，失败/超时进入各自 Hold owner |
| late/unknown callback | covered | correlation mismatch 只写 `ARCHIVED_EVIDENCE`，零新 Intent/EFFECT，不推进 Session |
| 测量合同校验、Q19 准入、成功/NG 分支 | covered | typed QUERY、provider call count、evidence、`PICK_AND_PUT` / `MOVE_FORWARD` / `MOVE_TO_NG` 决策均已覆盖 |
| scan-decision replay/冲突闭环 | covered | 同 digest recorded replay 与不同 digest 首次冲突均已覆盖；后续 replay 为零新 effect/outbox |

## 验收标准

1. fixture 的 `schema_version`、`slice_id` 和 `RS-SD-001` 至 `RS-SD-013` 固定且可由测试判定。
2. 13 个 case 都记录输入摘要、分阶段 replay evidence、目标状态、稳定 Intent/outcome、覆盖状态和精确源码引用；`RS-SD-012` 必须拆分首次 conflict detection 与后续 conflict replay。
3. fixture 中 12 个已闭环 case 的 `implementation_status` 为 `covered`；`RS-SD-010` 固定为 `blocked`，直到 T6 完成 Session Hold EFFECT 并获得对应验证证据。
4. replay 同 digest 不重新 QUERY、不重复 EFFECT；不同 digest 使用 `IDEMPOTENCY_CONFLICT` Hold。
5. Q19 timeout/unavailable 只记录稳定 provider failure evidence，当前返回 `success=0/failed=1`、零业务 Intent，不得创建正常入料命令或声称 Session Hold 已闭环。
6. 业务合同批准门禁已由业务批准责任人 kaizhou 通过；`status` 保持 `Approved`，`owner` 与 `approved_by` 均为 kaizhou，`approved_at` 保留批准时间。后续业务语义变更必须重新批准并更新批准证据。
