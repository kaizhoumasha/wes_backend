# SMT Classifier Bin Allocation And Rack Operation Design

> Legacy notes: 本文是 SMT classifier Bin/Rack Operation 目标态设计草案；当前 Runtime 状态源和事实源口径以 `workline_material_flow_runtime.md` 为准。

**最后更新**: 2026-05-25 (Architecture Refactored: AGV -> Rack Operation Domain)

本文档定义 `smt_classifier` 在 OK 主链路尾段引入“库位分配 + 货架操作 (Rack Operation)”后的目标运行时设计。

> **文档状态说明（2026-05-25）**：根据 2026-05-20 的《货架操作服务设计》(rack-operation-service-design.md) 核心决策，所有的 AGV 物理运输操作（移出货架、换架等）已完全收拢至“货架操作域（Rack Operation Domain）”。本设计已全面清除过时的独立 AGV 服务概念，对齐最新的业务约束。涉及现状时请结合实际代码与运行时 SSOT 文档交叉确认。

设计目标：

- `ARM02` 出料前，由 `WES` 主动调用正式“库位分配接口”
- 若当前无有效 `target_bin`，插件只需发出 `RACK_OPERATION_REQUEST`（要求换架/补充），由通用的 `WorklineRackOperationService` 解析为底层的 `WorklineRackTask`（包含移出旧架和补入新架的派发动作）。
- 货架操作完成后（RCS/WMS 触发回调），`WES` 再次重新执行库位分配逻辑。
- 仅当拿到完整 `target_bin` 后，才允许创建 `ARM02` 的正式出料命令。

约束前提：

- `plugin` 与硬件接入统一口径，避免运行时再做语义转义，且不再直接操作外部 AGV 接口。
- `ARM02` 只执行明确命令，不参与业务决策。
- 当前 mock 阶段，库位分配和货架操作任务可以由对应的 mock service 提供。
- WES 内部采用 `RACK_OPERATION_REQUEST` 意图流转。

## 1. 问题陈述

当前仓库已完成以下对齐：

- `SMT` 主链路可跑通：`SCAN_COMPLETED -> ARM01 -> PIPELINE01 -> ARM02`
- `ARM02` 出料命令的 `target_type` 已从旧值 `OUTPUT_PLATFORM` 修正为 `BIN`
- callback 契约、`step_code` 快照、`callback_logs/workline_inbox` 留痕已落地

关键业务需求：
- `ARM02` 出料命令需要稳定、真实的 `target_bin` 来源。
- 当当前粗分机无可用单层货架料格时，需触发底层的货架搬运。根据最新架构原则，**AGV 搬运不再作为独立的业务实体，而是归属于货架操作域**。因此 `MOVE_FORWARD SUCCESS` 后，必须走“库位分配”步骤，并且该步骤可能会触发换架的货架操作。

## 2. 总体设计

### 2.1 目标主链路

OK 主链路调整为：

1. `INPUT_ARM` 上报 `SCAN_COMPLETED`
2. `ARM01` 执行输入抓取/检测
3. `PIPELINE01` 执行 `MOVE_FORWARD`
4. 插件内调用库位分配逻辑
5. 分两种分支：
   - 若分配成功，记录目标料箱信息，创建 `ARM02` 出料命令
   - 若当前无可用料箱/货架（返回 `RACK_OPERATION_REQUIRED`），发出类型为 `REPLACE_CLASSIFIER_WORK_RACK` 的 `RACK_OPERATION_REQUEST` 意图，将状态设为等待外部货架操作完成。
6. WES Runtime 统一截获 `RACK_OPERATION_REQUEST` 意图，调用 `WorklineRackOperationService`，落库底层货架搬运任务（如 `MOVE_RACK` 与 `ALLOCATE_AND_MOVE_RACK`）。
7. RCS 完成物理搬运后回调 WES，货架任务生命周期完结。
8. Session 恢复并重新执行分配逻辑。
9. 拿到完整 `target_bin` 后创建 `ARM02` 命令。
10. `ARM02` 回调成功，`session` 结束。

### 2.2 职责边界

`plugin`

- 负责流程编排
- 决策是否需要换架（抛出 `RACK_OPERATION_REQUEST`）
- 不感知外部 AGV 接口，不硬编码最终 `target_bin`。

`WES Runtime (Rack Operation Domain)`

- 通用 `WorklineRackOperationService` 解析意图，拆解并保存 `WorklineRackTask`。
- 负责与下层硬件控制（RCS/WMS）对接 AGV 协议和生命周期。

`ARM02`

- 只消费已决策完成的出料命令。
- 不负责二次查询 bin。

## 3. 运行时状态机设计

### 3.1 步骤与等待态

不再新增特定的 `WAITING_AGV_DELIVERY`，而是基于通用的外部等待态：

- 插件分配中若需换架，将 session 等待类型挂起：进入 `WAITING_EXTERNAL`，并标记 `pending_external_task_type` 为 `RACK_OPERATION`。

### 3.2 Session 推进规则

`PIPELINE_MOVE_FORWARD SUCCESS`
- 不再直接创建 `ARM02` 命令
- 进行 Bin Allocation 计算

`BIN ALLOCATION = ALLOCATED`
- 将完整 `target_bin` 写入 `session.context_json`
- 创建 `ARM02 DeviceCommand`
- 进入 `OUTPUT_PICK_PLACE`

`BIN ALLOCATION = RACK_OPERATION_REQUIRED`
- 插件抛出 `RuntimeIntent` (类型: `RACK_OPERATION_REQUEST`, 操作: `REPLACE_CLASSIFIER_WORK_RACK`)
- `session` 挂起，进入 `WAITING_EXTERNAL` (等待 Rack Operation 完成)

`RACK OPERATION RESULT = SUCCEEDED`
- `session` 恢复执行，重新进行 Bin Allocation。

`RACK OPERATION RESULT = FAILED`
- `session` 抛出严重业务异常或挂起等待人工介入。

`ARM02 RESULT = SUCCESS`
- `session` 进入 `COMPLETED`

## 4. 接口契约

### 4.1 库位分配逻辑

插件内部逻辑或者业务领域服务（而非调用硬编码的外部分配服务）。

若无可用 Bin，返回的业务语义：

```json
{
  "allocation_status": "RACK_OPERATION_REQUIRED",
  "reason": "NO_AVAILABLE_BIN"
}
```

### 4.2 货架操作意图与下发

由 `plugin` 发出，由 Runtime 截获的 `RuntimeIntent`:

```json
{
  "kind": "RACK_OPERATION_REQUEST",
  "operation_type": "REPLACE_CLASSIFIER_WORK_RACK",
  "source_line_code": "WL-CONVEYOR-01",
  "payload": {
    "source_rack_location": "STATION_OUTPUT1",
    "reason": "NO_AVAILABLE_BIN"
  }
}
```

WES Runtime 底层的 `WorklineRackTaskSpec` 结构详见 `docs/superpowers/archive/specs/2026-05-20-rack-operation-service-design.md`。包含：
- `MOVE_RACK` 移出当前满架
- `ALLOCATE_AND_MOVE_RACK` 请求空架补入

异步回调完全遵循通用的 Callback 生命周期与 `workline_rack_tasks` 的状态机。

## 5. 失败策略

### 5.1 货架任务异常

遵循已定义的通用货架操作失败规范。若 RACK TASK 被拒绝或长期超时，WES Runtime 会将其状态置为 FAILED。
此状态会联动导致处于 `WAITING_EXTERNAL` 的 `session` 也进入异常或报警池。

### 5.2 成功后二次分配仍失败

- 允许一次 `RACK_OPERATION_REQUIRED -> RACK OPERATION SUCCEEDED -> BIN ALLOCATION` 闭环。
- 二次分配仍拿不到有效 `target_bin` 时，必须失败或抛出告警。
- 不得进入无限循环请求新货架。

### 5.3 ARM02 前置校验失败

若最终 `target_bin` 缺少关键字段（如 `rack_id`, `bin_id`, `bin_cell_location`），`WES` 拒绝创建 `ARM02 DeviceCommand`。

## 6. 数据持久化建议

所有请求的持久化不再散落在独立的 agv 表：

- 业务流程本身信息在 `workline_sessions.context_json`
- 货架调度的底层生命周期均落于 `workline_rack_tasks` 数据库表。
- 任务执行的快照，外部系统的回调均通过 `workline_inbox` 及 `callback_logs` 进行统一留痕。

建议 `session.context_json` 在分配成功后包含：

```json
{
  "allocation_status": "ALLOCATED",
  "target_bin": {
    "station_location_id": "STATION_OUTPUT1",
    "rack_id": "RACK_001",
    "bin_id": "BIN_104",
    "bin_type": "三格箱",
    "bin_cell_location": "1"
  }
}
```

## 7. Mock 设计

当前 mock 阶段：

- 测试中无需独立的 `agv_mock`，统一由 `WorklineRackTask` 的 mock 生命周期驱动回调。
- 主链路不得依赖 `/debug/*`。

## 8. 测试改造

### 8.1 单元测试

补以下场景：

- `MOVE_FORWARD SUCCESS` 后进行 Bin Allocation
- 分配返回 `RACK_OPERATION_REQUIRED` 时抛出 `RACK_OPERATION_REQUEST` 意图，进入挂起。

重点文件：

- `tests/workline_plugins/smt_classifier/test_plugin.py`

### 8.2 Runtime/E2E 测试

补以下主链路：

1. `allocation = ALLOCATED`
   - `MOVE_FORWARD SUCCESS`
   - 分配成功 -> 创建 `ARM02` -> `ARM02 SUCCESS` -> `COMPLETED`

2. `allocation = RACK_OPERATION_REQUIRED`
   - 发出货架操作意图，`session` 挂起
   - WES Rack Operation 任务成功完结，状态回传
   - 恢复 `session`，再次分配成功
   - 创建 `ARM02` -> `ARM02 SUCCESS` -> `COMPLETED`

## 9. 验收标准

满足以下条件才算完成：

1. `plugin` 代码中彻底剔除独立的 AGV/RCS 直接调用逻辑和概念。
2. 无 `target_bin` 情况下绝不创建 `ARM02` 命令。
3. 换架逻辑完全复用通用的 `WorklineRackOperationService` 及相关的意图派发。
4. 测试覆盖完整的 `RACK_OPERATION_REQUIRED` 分支及重分配逻辑。
