---
title: Phase 9 人工 Bin 处理设备合同附录
status: ApprovedForImplementation
implementation_authorization: true
annex_key: manual-bin-processing-device-contract
contract_version: "1.0"
scope: 四条并排滚筒线的 SCAN1～SCAN4、出入料口、方向动作和不可读码处理
owners: [ECS, WES, 业务负责人, 项目交付负责人]
related:
  - docs/contracts/wms-manual-bin-processing-integration-requirements.md
  - docs/integration/third_party_integration_whitepaper.md
  - docs/hardware/SMT流水线接口调用说明书20260320-v1.md
---

# Phase 9 人工 Bin 处理设备合同附录

## 1. 边界与供应商事实

本文只冻结 Phase 9 人工线所需的逻辑设备事实。固定 HTTP 路径、公共包络、DeviceCommand 身份、ACK/CALLBACK、状态端点、
delivery unknown 和错误映射复用 [`third_party_integration_whitepaper.md`](../../integration/third_party_integration_whitepaper.md)，
不得在插件中复制 Adapter、HTTP Client、重试器或供应商 DTO。

[`SMT流水线接口调用说明书20260320-v1.md`](../../hardware/SMT流水线接口调用说明书20260320-v1.md) 是保留不变的供应商原始输入。
它当前给出的 `MOVE_FORWARD | MOVE_BACKWARD | MOVE_LEFT | MOVE_RIGHT`、`SCAN_COMPLETED` 和无 Bin 身份的出入料事件，尚不足以证明
现场四线方向、Bin 码映射和最终点位一致性。ECS/网关负责把供应商私有字段映射为本文逻辑事实；供应商一致性通过前，不能宣称现场可用。

本文批准实现，不批准虚构 PLC 能力：WES 只下发命令并等待 ECS CALLBACK；ECS 自行处理设备内部等待和重试。WES 不要求“先释放到自动线”
之类供应商并不存在的控制项。

## 2. 四线拓扑

现场四条 WorkLine 从左向右编号 1、2、3、4：

```text
LINE-1 (automatic) → LINE-2 (automatic) → LINE-3 (manual) → LINE-4 (manual) → NGZone
```

Phase 9 第一阶段只激活 3、4 号人工线，但拓扑必须允许 NG Bin 从任意上游线继续向右。每条 WorkLine 独立拥有：

- 一个 CTU 投料口与持久 `INGRESS occupied_count`；
- `SCAN1`、`SCAN2`、`SCAN3`、`SCAN4` 四个扫码/分流工位；
- 一个滚筒线出料口与本线 `RETURN_BUFFER`；
- 独立 `workline_code`、设备 binding 和位置 binding。

NGZone 是第四线 SCAN3 右侧的共享物理区域，不属于任一 WorkLine 的 INGRESS/RETURN 容量。跨线只允许 SCAN3 向右流转；
不建立跨线工作流、父子执行、`next_workline_id` 或共享缓存队列。

## 3. Epoch 绑定与可配置方向

每个活动 Epoch 必须冻结本线以下角色的 `device_code + endpoint + contract_key + contract_version + command_timeout_ms`：

| `device_role` | 数量 | 职责 |
| --- | --- | --- |
| `SCAN1` | 1 | 读 Bin，并执行进 SCAN2 或向右旁路 |
| `SCAN2` | 1 | 读 Bin/确认工作位到达，并释放到 SCAN3 |
| `SCAN3` | 1 | 读 Bin，并执行向右或进入 SCAN4 |
| `SCAN4` | 1 | 读 Bin，确认进入本线 RETURN_BUFFER |
| `INGRESS_PORT` | 1 | 记录 CTU 投料口物理事件；不提供逐 Bin 队列 |
| `RETURN_OUTPUT` | 1 | 记录 Bin 被 CTU 从本线出料口搬离 |

每条线的物理方向可能随最终供应商版本调整。`WorkLine.config["manual_bin_processing"]` 只保存一张静态、显式映射表：

| 逻辑动作 | 允许工位 | 业务语义 |
| --- | --- | --- |
| `MOVE_TOP` | SCAN1 | 进入本线 SCAN2 |
| `MOVE_RIGHT` | SCAN1、SCAN3 | 跳过本线工作位，或继续向右进入下一线/NGZone |
| `RELEASE` | SCAN2 | 释放当前工作位 Bin 到 SCAN3 |
| `MOVE_DOWN` | SCAN3 | 进入本线 SCAN4 |

映射值是当前 Phase 7 DeviceCommand 支持的一个实际 `task_type` 字符串。实施只读取当前 Epoch 冻结映射，不新增动态 registry、
运行时脚本或兼容别名；供应商最终版本变化时直接修改配置并新开 Epoch。

每线位置 binding 固定包含 `SCAN1 | SCAN2 | SCAN3 | SCAN4 | INGRESS_PORT | RETURN_OUTPUT`；部署拓扑另冻结唯一共享
`NG_ZONE` 及第四线 SCAN3 到该区域的右移关系。每个 `location_id` 在 Epoch 内唯一。RETURN FIFO 的当前 Bin 位置使用
`PositionProjection`，INGRESS 不创建逐 Bin 位置。

## 4. SCAN_COMPLETED 规范化事实

ECS/网关把供应商扫码事件规范化为公共设备 Event，`event_type=SCAN_COMPLETED`。`data` 严格为以下联合之一：

可读：

```json
{
  "read_status": "READABLE",
  "bin_id": "BIN-001",
  "location_id": "LINE-3-SCAN1"
}
```

不可读：

```json
{
  "read_status": "UNREADABLE",
  "location_id": "LINE-3-SCAN1"
}
```

规则：

- `READABLE` 必须有非空 `bin_id`；`UNREADABLE` 禁止携带 `bin_id`；
- `location_id` 必须命中事件 `device_code` 在当前 Epoch 的冻结位置；
- 公共 `source_event_id` 全部署唯一，同身份重放内容不变；同身份不同内容进入证据冲突；
- ECS/网关保留供应商原始六字段、原始码和映射诊断，但不把它们放入 WES 核心业务合同；
- WES 不用 Transport 计划成员、INGRESS 顺序或其它扫码结果补写本次 `bin_id`。

正常链路中，SCAN1 的可靠可读事件可以创建 BinExecution，SCAN2～SCAN4 只推进既有执行。不可读链在任一下游扫码点首次读出
`bin_id` 时，必须立即创建带 `UPSTREAM_SCAN_UNREADABLE` 的 NG 执行；与不可读链无关的 SCAN4 可读事件若缺失执行，则以现场身份创建
普通执行并告警。不可读事件始终不发明身份。

## 5. 路由 DeviceCommand

插件只返回 SDK 的封闭 `CreateDeviceCommand` Decision；人工业务应用经 deployment adapter 将其转换为现有
`DeviceCommandRequest`，再调用 DeviceCommand 应用端口。固定字段责任：

- `device_code`、`line_run_epoch_id`、`contract_key`、`contract_version` 来自 Epoch binding；
- `task_type` 来自第 3 节当前逻辑动作的冻结映射；
- 可读 Bin 使用 `execution_ref_type=BIN_EXECUTION` 和实际 `bin_execution_id`；
- 不可读 Bin 使用 `execution_ref_type=SCAN_EVIDENCE` 和当前 `scan_evidence_id`；`material_execution_id` 为空；
- `params` 严格包含 `logical_action + scan_evidence_id`，只在可读时增加 `bin_id`。

不在 `params` 中复制 `task_id`、WMS operation、FIFO 序号、物料、库存、优先级、超时、PLC 点位或供应商私有字段。

同一设备同时最多一个未终态 DeviceCommand。ACK 只表示接纳；WES 等待 CALLBACK 的 `SUCCESS | FAILED`。取得 ACK 或投递不确定后，
不得换 `command_code` 重发等价动作。重试、动作等待和底层互锁由 ECS 负责；WES 只在确定未接纳时按 Phase 7 规则重提原命令。

## 6. SCAN1 行为

| 扫码/业务结果 | 命令 | INGRESS 计数 |
| --- | --- | --- |
| 可读、有活动 Task、WMS 返回 `MOVE_TOP` | `MOVE_TOP` | CALLBACK `SUCCESS` 后减 1 |
| 可读、有活动 Task、WMS 返回 `MOVE_RIGHT` | `MOVE_RIGHT` | CALLBACK `SUCCESS` 后减 1 |
| 可读、无活动 Task | `MOVE_RIGHT` | CALLBACK `SUCCESS` 后减 1 |
| 可读、已有 NG 标识 | `MOVE_RIGHT` | 下游线不修改；只有来源线首次 SCAN1 修改 |
| 不可读 | `MOVE_RIGHT` | 来源线 CALLBACK `SUCCESS` 后减 1 |
| WMS 超时/未知/非法 | 不发命令，Bin 留在 SCAN1 | 不变 |

若 SCAN1 事件先于对应 Transport 整批结果，保存事件，但等待 `occupied_count` 已入账再创建路由命令。命令失败或结果未知不减少计数。
计数不能为负；不一致只暂停当前线受影响 Bin 并告警，不停其它线。

## 7. SCAN2、SCAN3、SCAN4

### 7.1 SCAN2

只有 SCAN1 `MOVE_TOP` 的 Bin 进入。可读扫码后 WES 向 WMS 报告人工工作位到达；获得
`manual.bin.release_decided@v1` 后发送同一个 `RELEASE` 逻辑动作。WMS/PDA 不直接调用 ECS。

`RELEASE` 成功只表示 Bin 已从工作位释放到下游，不关闭 BinExecution，也不证明已到 SCAN3。

### 7.2 SCAN3

- 有 NG 标识：`MOVE_RIGHT`；
- 本次不可读：`MOVE_RIGHT`；
- 普通可读：`MOVE_DOWN`。

第一次 NG Bin 的 SCAN3 `MOVE_RIGHT` 成功后，WES 只确认 NG 路由，不把尚未到达的 NGZone 写入 PositionProjection。若不是第四线，
实物继续到下一线 SCAN1，并由后续扫码更新实际位置；如果是第四线，则进入右侧物理 NGZone。到达 NGZone 只进入人工接管区；操作员通过
WMS PDA 扫码并实际取走后形成的 `workline.bin.ng_removed@v1`，才关闭可识别活动执行。

### 7.3 SCAN4

SCAN4 可读后，WES 将 Bin 写入本线 RETURN_BUFFER FIFO 和 PositionProjection。SCAN4 不访问 WMS，不分配回库储位，也不查询 CTU 容量。
不可读仍保存设备证据并告警，不允许用 FIFO 队首或上一工位计划身份补写；是否形成匿名现场异常由人工盘库处理。

## 8. 不可读码跨线链

固定链路如下：

```text
SCAN1 unreadable → SCAN3 unreadable → next WorkLine SCAN1 → ...
→ WorkLine 4 SCAN3 MOVE_RIGHT → physical NGZone
```

- 未读出身份前不创建 BinExecution；命令引用扫码 evidence；
- 任一下游扫码点首次读出 `bin_id`，立即创建 BinExecution 并设置
  `ng_reason_code=UPSTREAM_SCAN_UNREADABLE`，此后直接向右，不访问 WMS；
- 全程不可读时只保留 evidence、DeviceCommand 和 NGZone 日志，不创建匿名/占位 BinExecution；
- 下游 WorkLine 的 SCAN1 不修改自己的 INGRESS 计数，因为该 Bin 不是从本线 CTU 投入口进入。

业务问题不得导致整台分拣机停机。只有设备不可用、WMS 无确定结果、命令未知或物理身份冲突才暂停受影响 Bin/线；其它线继续。

## 9. 出入料口事件

`INGRESS_PORT` 事件只用于审计；Phase 9 的权威 INGRESS 数量来自 Transport 整批最终成功数和来源线 SCAN1 命令成功数，不从端口事件重复计数。

`RETURN_OUTPUT` 的 `BIN_DEPARTED` 是本线出料口物理离开事实。它必须能由当前 Epoch、出料口唯一占用和 PositionProjection 唯一解析到
一个 RETURN FIFO 成员；否则只记录冲突，不猜队首。可靠应用后立即释放该 RETURN_BUFFER 位置，后续 Transport 成败不重新占用缓存。
供应商事件没有稳定身份或无法唯一解析时，供应商一致性门禁不通过，WES 不用计划 Bin 补齐。

## 10. CALLBACK 与错误

CALLBACK 必须关联原 `command_code + device_code`，并返回 `SUCCESS | FAILED`。`SUCCESS` 是本逻辑动作完成的权威结果；下一个扫码工位的
独立 `SCAN_COMPLETED` 才证明 Bin 到达下一站。无需额外创建 `BIN_ARRIVED` 才允许本次命令完成。

失败错误继续使用 Phase 7 稳定闭集：`ACTION_FAILED | SAFETY_INTERLOCK | POSITION_CONFLICT | POSITION_UNKNOWN | DEVICE_FAULT |
INTERNAL_ERROR`。供应商原始错误只进入诊断证据。缓存满、WMS 明确旁路、无活动 Task、业务 NG 和人工处理慢不是设备错误。

## 11. 配置与验收

部署前必须冻结：

- 3/4 号人工线真实 `workline_code`、左右顺序、每线 `configured_ingress_capacity`；
- 六个设备角色和七个位置角色的唯一 binding；
- 每线四个逻辑动作到供应商 `task_type` 的映射；
- 第四线 SCAN3 右侧物理 NGZone 和 `ng_zone_code`；
- SCAN1～SCAN4 可读/不可读事件、命令 ACK/CALLBACK、RETURN_OUTPUT 离开事件的供应商一致性证据。

基础 DeviceCommand 测试只验证公共可靠性；插件测试只验证逻辑动作选择；供应商一致性验证真实字段和方向；现场 E2E 验证实物流。
四者不得互相代证。配置可以随供应商最终版本直接修改，但活动 Epoch 内不得静默变化。
