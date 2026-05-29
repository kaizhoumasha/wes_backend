# Workline 命令 task_type 合同优化设计

## 背景

粗分机当前命令合同把同一条设备指令拆成两层语义：

- 顶层 `task_type`：下发给设备的协议动作，例如 `TEST`、`PICK_AND_PUT`。
- `params.action`：WES 内部业务动作，例如 `MEASUREMENT_REEL`、`PUT_TO_BIN`、`MOVE_TO_NG`。

这个设计来自早期粗分机物理流规划，用于解决设备通用动作与 WES 业务动作不一致时的回调路由问题。但它与
`docs/integration/third_party_integration_whitepaper.md` 的要求不一致。白皮书规定：

- 顶层只放协议控制字段，包括 `task_type`。
- `task_type` 是指令类型。
- `params` 只放业务参数。
- 业务参数不得拍平到顶层。

因此 `params.action` 实际上成了 WES 内部路由字段，不是设备执行所需业务参数。它让设备侧、前端沙箱和回调路由都必须额外理解一层隐藏语义。

对于 `PICK_AND_PUT` 语义下复用的 `PUT_TO_BIN` 和 `MOVE_TO_NG` 等指令，同样存在此问题。

## 目标

采用方案 A：统一 `task_type` 为对外设备指令类型和 WES 回调路由 key，删除新合同中的 `params.action`。
同时，全局清理其他复用此模式的指令（如 `PUT_TO_BIN`、`MOVE_TO_NG`），彻底消灭 `params.action` 二级路由设计（Boil the lake）。

新的粗分机测量命令目标形态：

```json
{
  "command_code": "CMD-20260529-MEASUREMENT_REEL-XXXXXXXX",
  "task_type": "MEASUREMENT_REEL",
  "priority": 5,
  "timeout": 300000,
  "params": {
    "business_key": "...",
    "six_in_one": {}
  }
}
```

核心约定：

- `task_type` 直接表达设备要执行的 WES 指令类型。
- `params` 不再包含 `action`。
- `command_type` 如果仍出现在内部 payload 中，必须与 `task_type` 保持一致。
- 回调路由直接使用回调 payload 或已持久化 `DeviceCommand.task_type`。
- 不保留 `TEST + params.action=MEASUREMENT_REEL` 的向后兼容路径。

## 非目标

- 不引入新的 `business_action` 字段。
- 不保留旧命令回调兼容逻辑。
- 不改造整个设备命令模型为枚举闭集；插件扩展字符串仍然允许。
- 不重写第三方回调 API，只调整 Workline 命令合同与沙箱生成逻辑。

## 架构决策

### 1. 单一命令类型来源

`task_type` 是唯一命令类型来源。

下发、持久化、治理校验、沙箱展示、Result 回调路由都以 `task_type` 为准。`params.action` 从粗分机命令 builder 中移除，相关测试也应从“业务 action 与设备 task_type 分离”改为“task_type 即业务指令类型”。

### 2. 粗分机扩展 task_type

白皮书的数据字典当前列出通用类型：`PICK`、`PUT`、`SCAN`、`ROTATE`、`PROCESS`。项目已有运行时能力支持插件扩展字符串，因此粗分机可定义扩展 `task_type`：

- `MEASUREMENT_REEL`
- `PICK_AND_PUT`
- `MOVE_FORWARD`
- `PUT_TO_BIN`
- `MOVE_TO_NG`

这些扩展类型需要写入白皮书或补充接入说明，避免供应商只按通用示例理解协议。

### 3. 不保留旧合同兼容

经 Eng Review 确认：**不需要保留向后兼容，保持代码清爽（Hard cut-off）**。代码应删除或改写以下逻辑：

- `DEVICE_TASK_TYPE_BY_ACTION` 中 `MEASUREMENT_REEL -> TEST`、`PUT_TO_BIN -> PICK_AND_PUT` 等映射。
- `_base_command_payload()` 写入 `params.action` 的行为。
- `CallbackOrchestrationService._resolve_command_type()` 从 `command_params.action` 推导命令类型的主路径。
- `WorklineOperationService.submit_sandbox_result()` 从 `command.params.action` 推导 `command_type` 的逻辑。
- 前端沙箱以 `params.action` 作为主路径识别命令类型的逻辑。

如果测试或文档仍断言 `TEST + params.action`，应更新为新合同，而不是添加兼容兜底。

## 数据流

```text
+-------------+         +------------------+         +---------------+
| WES Plugin  | Intent  | Rough Sorter     | Outbox  |  Device /     |
| (MEASUREMENT| ------> | Command Builder  | ------> |  Sandbox UI   |
| _REEL)      |         | (Set task_type)  |         | (Reads type)  |
+-------------+         +------------------+         +---------------+
                                |                            |
                                v                            |
                        +------------------+                 |
                        | DeviceCommand DB |                 |
                        | (task_type)      |                 |
                        +------------------+                 |
                                ^                            |
                                | Result / ACK               |
                        +------------------+                 |
                        | Callback Router  | <---------------+
                        | (Find by task)   |
                        +------------------+
```

### 下发

1. 插件产生 `RuntimeIntent.command(action="MEASUREMENT_REEL", payload_json={...})`。
2. 粗分机 builder 输出顶层 `task_type="MEASUREMENT_REEL"`。
3. Runtime 创建 `DeviceCommand.task_type="MEASUREMENT_REEL"`。
4. Outbox payload 下发 `task_type="MEASUREMENT_REEL"`，`params` 只包含执行参数。

### ACK

ACK 继续按 `command_code` 关联，不依赖 `params.action`。

### Result

设备 Result 回调中：

- `command_code` 定位已持久化 `DeviceCommand`。
- `command_type` 和 `task_type` 均从已持久化 `DeviceCommand.task_type` 派生。
- 设备回调 payload 中携带的 `command_type` 或 `task_type` 不参与业务路由，也不覆盖已持久化命令类型。
- 生成的 `COMMAND_RESULT` inbox 中 `command_type` 和 `task_type` 均为 `MEASUREMENT_REEL`。
- 插件 `@on_command("MEASUREMENT_REEL", result="SUCCESS")` 正常命中。

### 沙箱

前端默认 Result JSON 根据 `task_type` 生成，不再依赖 `params.action`：

- `MEASUREMENT_REEL`：生成 `PkgID`、`reel_diameter`、`reel_thickness`、`measurement_result`。
- 其他命令：按对应 `task_type` 生成默认 payload。

## 影响范围

### 后端

- `src/workline_plugins/rough_sorter/contract.py`
- `src/workline_plugins/rough_sorter/plugin.py`
- `src/app/callback/services/callback_orchestration_service.py`
- `src/app/workline/services/operation_service.py`
- `src/app/workline/services/write_back_service.py`
- 粗分机插件测试、Runtime intent 测试、沙箱 Result 测试、回调 API 相关测试。

### 前端

- `../wes_frontend/src/components/runtime/sandbox/SandboxResultComposer.vue`
- `../wes_frontend/tests/unit/components/runtime/sandboxResultComposer.test.ts`

前端已有一版兜底修复，但实施本 SPEC 后应把主路径改成直接识别 `task_type=MEASUREMENT_REEL`，并移除对 `params.action` 的主路径依赖。

### 文档

- `docs/integration/third_party_integration_whitepaper.md`
- `docs/superpowers/plans/2026-05-28-rough-sorter-physical-flow.md` 中旧的 `params.action` 说明应标记为已废弃或按新合同更新。

## 风险

- 旧的已下发未完成命令如果仍是 `TEST + params.action`，在新逻辑下不再被兼容处理。实施前应清理沙箱/过程数据，生产环境则需要确认没有未完成旧命令。这是一个 Stop-the-world 依赖，但换取了代码的长期清爽。
- 设备能力配置若使用 `supports_command_types=["TEST", "PICK_AND_PUT"]`，需要同步改为包含所有具体的扩展类型（如 `MEASUREMENT_REEL`, `PUT_TO_BIN`, `MOVE_TO_NG`）。
- 供应商如果已经按 `TEST` 或二级路由接入命令，需要同步变更接口合同。

## 验收标准

1. 新生成的测量命令 Outbox payload 顶层 `task_type` 为 `MEASUREMENT_REEL`。
2. 新生成的命令 payload `params` 不包含 `action`。
3. 沙箱 Result 生成的 `COMMAND_RESULT` inbox 中 `command_type` 与 `task_type` 均为 `MEASUREMENT_REEL`。
4. 粗分机 `SCAN_COMPLETED -> MEASUREMENT_REEL -> ACK -> Result` 完整链路通过。
5. 旧合同测试不再保留：不再断言 `task_type=TEST` 或 `params.action=MEASUREMENT_REEL`。
6. 前端沙箱在 `task_type=MEASUREMENT_REEL` 时能生成包含 `reel_diameter` 和 `reel_thickness` 的默认 Result JSON。
7. 白皮书或补充接入说明明确粗分机扩展 `task_type` 列表。

## 验证计划

- 后端单测：
  - `tests/workline_plugins/test_rough_sorter_contract.py`
  - `tests/workline_plugins/test_rough_sorter_plugin.py`
  - `tests/workline_runtime/test_runtime_intent_effects.py`
  - `tests/workline_runtime/test_workline_operation_service.py`
  - 回调 API 中依赖 command type 的相关测试。
- 前端单测：
  - `tests/unit/components/runtime/sandboxResultComposer.test.ts`
  - `tests/unit/components/runtime/sandboxResultFlow.test.ts`
- 沙箱手工验证：
  - 清理 Workline 45 过程数据。
  - 触发 `SCAN_COMPLETED`。
  - 确认下发命令为 `CMD-*-MEASUREMENT_REEL-*`，payload `task_type=MEASUREMENT_REEL`，且 `params.action` 不存在。
  - 触发其他指令（如 `PUT_TO_BIN`），验证同样不再包含 `params.action`。
  - ACK 后提交默认 SUCCESS Result。
  - 确认 Session 不因缺失测量字段进入 `MANUAL_HOLD`。

## Eng Review Report

### 结论

方案 A 通过工程评审，但必须按 hard cut-over 执行：不保留 `TEST + params.action` 兼容路径，命令类型单一来源为已持久化的 `DeviceCommand.task_type`。

### 必须落实

- 粗分机 builder 输出具体扩展 `task_type`：`MEASUREMENT_REEL`、`PUT_TO_BIN`、`MOVE_TO_NG`。
- `params.action` 从新命令合同中移除。
- Callback 生成 `COMMAND_RESULT` 时，`command_type` 与 `task_type` 都来自 `DeviceCommand.task_type`。
- 设备能力配置必须声明具体扩展类型，旧 `TEST` 能力不视为支持 `MEASUREMENT_REEL`。
- 前端沙箱默认 Result 以 `payload_json.task_type` 为主路径生成。

### 验收

- 后端合同、runtime、callback、能力治理测试覆盖 hard cut-over。
- 前端沙箱单测覆盖 `task_type=MEASUREMENT_REEL` 且旧 `params.action` 不影响默认测量 Result。
- 沙箱手工验证前必须清理旧未完成命令数据。
