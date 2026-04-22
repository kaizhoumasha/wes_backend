# Workline 插件体系硬件错误码统一规划

## 1. 目标

本文档定义 Workline 插件体系中硬件错误码的统一边界、标准设备错误码使用原则，以及历史供应商数字码向标准语义码的迁移表。

目标只有三个：

1. **平台运行时错误码** 与 **设备执行错误码** 严格分层。
2. 插件业务逻辑只消费 [`DeviceErrorCode`](/Users/kaizhou/SynologyDrive/works/wes_backend-plugin-prereq-standardization/src/workline_runtime/contracts/device_error_codes.py)，不消费供应商数字码。
3. 历史供应商数字码仅作为 **历史文档 / 原始日志 / 现场证据** 保留，不再驱动当前插件业务控制逻辑。

## 2. 非目标

本文档**不是**在系统内部引入一层长期存在的 vendor → internal 转义层。

当前目标是：

- 现行硬件协议、Mock、E2E 协议示例直接输出标准语义错误码。
- 插件内部直接消费标准语义错误码。
- 如果需要保留 vendor 原始数字码，也只能作为日志证据附带保留，不能作为插件判断条件。

## 3. 三层边界

### 3.1 平台运行时错误码

定义位置：
- [`src/workline_runtime/diagnostics/codes.py`](/Users/kaizhou/SynologyDrive/works/wes_backend-plugin-prereq-standardization/src/workline_runtime/diagnostics/codes.py)

用途：
- callback 入站校验失败
- session / orchestrator / outbox / inbox / timeout 归因
- 插件执行异常、状态迁移异常

典型示例：
- `CALLBACK_SCHEMA_INVALID`
- `SESSION_CONTEXT_MISSING`
- `SESSION_RESOLVE_FAILED`
- `PLUGIN_EXECUTION_FAILED`
- `PLUGIN_TRANSITION_INVALID`
- `DEVICE_TIMEOUT`
- `OUTBOX_DISPATCH_FAILED`

这层回答的是：**平台为什么处理失败**。

### 3.2 标准设备错误码

定义位置：
- [`src/workline_runtime/contracts/device_error_codes.py`](/Users/kaizhou/SynologyDrive/works/wes_backend-plugin-prereq-standardization/src/workline_runtime/contracts/device_error_codes.py)

用途：
- 设备执行结果语义
- 插件业务分支判断
- Mock / E2E / 当前联调协议文档

当前标准集合：
- 成功哨兵：`NONE`
- 检测类：`INSPECTION_SIZE_NG`、`INSPECTION_THICKNESS_NG`
- 扫码类：`SCAN_CODE_INVALID`、`SCAN_CODE_INCOMPLETE`、`SCAN_FAILED`
- 搬运类：`PICK_FAILED`、`PLACE_FAILED`、`PICK_AND_PUT_FAILED`、`MOVE_FAILED`
- 现场 / 资源类：`TARGET_BLOCKED`、`BIN_FULL`
- 设备状态类：`DEVICE_BUSY`、`DEVICE_NOT_READY`、`DEVICE_FAULT`、`DEVICE_UNKNOWN_ERROR`

这层回答的是：**设备执行语义是什么**。

### 3.3 Vendor 原始码

用途：
- 历史协议文档
- 供应商接口原文
- 原始回调日志 / 证据保存

约束：
- 不得直接进入插件业务分支判断。
- 不得作为新的现行协议继续扩散。
- 若某个数字码语义不稳定，不允许“猜测映射”，必须先补标准语义码。

这层回答的是：**供应商当时原始上报了什么**。

## 4. 核心原则

### 原则 1：先分层，再映射

先回答问题属于哪一层：

- 是平台处理失败？→ 运行时错误码
- 是设备执行结果？→ 标准设备错误码
- 是供应商历史痕迹？→ vendor 原始码，仅作证据

### 原则 2：只有稳定语义才允许标准化

如果某个 vendor 数字码已经具备**明确、稳定、无歧义**的语义，可以直接前推到标准设备错误码。

如果语义不清晰，就先补新的 `DeviceErrorCode`，而不是硬套到现有错误码。

### 原则 3：插件只看标准语义，不看 vendor 数字

禁止：
- `if error_code == "2002": ...`
- `if error_code in {"1001", "1002"}: ...`

允许：
- `if error_code == DeviceErrorCode.PICK_AND_PUT_FAILED: ...`
- `if error_code == DeviceErrorCode.INSPECTION_SIZE_NG: ...`

### 原则 4：成功态也统一

设备结果中的成功态统一使用：
- `NONE`

历史成功态如 `0` 只作为历史协议样例保留，不再作为当前标准输出。

## 5. 正式迁移表

> 说明：
> - “立即替换”表示在**当前现行协议 / Mock / E2E / 当前文档**中应直接使用标准语义码。
> - “历史保留”表示原数字码只保留在历史文档或历史日志中，不再作为当前协议输出。
> - 下表是**迁移与治理依据**，不是要求插件运行时保留别名兼容。

| 来源设备/协议 | 历史码 | 历史含义 | 标准语义码 | 标准层级 | 处置策略 | 备注 |
| --- | --- | --- | --- | --- | --- | --- |
| SMT 粗分机机械臂（现行协议治理前） | `0` | 无错误 | `NONE` | 标准设备错误码 | 立即替换 | 成功态统一哨兵值 |
| SMT 粗分机机械臂 | `INSPECTION_SIZE_NG` | 尺寸检测 NG | `INSPECTION_SIZE_NG` | 标准设备错误码 | 保持不变 | 已经是标准语义码 |
| SMT 粗分机机械臂 | `INSPECTION_THICKNESS_NG` | 厚度检测 NG | `INSPECTION_THICKNESS_NG` | 标准设备错误码 | 保持不变 | 已经是标准语义码 |
| SMT 粗分机机械臂（历史数字码） | `2001` | 扫码异常 | `SCAN_FAILED` | 标准设备错误码 | 立即替换 | 表达“扫码执行失败”，而不是码内容非法 |
| SMT 粗分机机械臂（历史数字码） | `2002` | 搬运失败 | `PICK_AND_PUT_FAILED` | 标准设备错误码 | 立即替换 | 语义是整次搬运失败，不能假装细分为 pick / place |
| SMT 粗分机机械臂（历史数字码） | `2003` | 料箱已满 | `BIN_FULL` | 标准设备错误码 | 立即替换 | 语义稳定，可直接前推 |
| SMT 粗分机机械臂（历史数字码） | `9999` | 未知错误 | `DEVICE_UNKNOWN_ERROR` | 标准设备错误码 | 立即替换 | 仅作设备侧未知失败兜底 |
| SMT 流水线（现行协议治理前） | `1001` | 路径被阻挡 | `TARGET_BLOCKED` | 标准设备错误码 | 立即替换 | 属于现场/目标位阻挡 |
| SMT 流水线（现行协议治理前） | `1002` | 移动失败 | `MOVE_FAILED` | 标准设备错误码 | 立即替换 | 属于流水线传输/移动执行失败 |
| SMT 流水线（现行协议治理前） | `1003` | 设备故障 | `DEVICE_FAULT` | 标准设备错误码 | 立即替换 | 属于设备自身故障 |
| SMT 分拣机 ECS 历史文档（2026-03-18） | `1001` | 目标位置被阻挡 | `TARGET_BLOCKED` | 标准设备错误码 | 历史保留 | 历史文档保留原貌；若重新启用集成，设备应直接输出标准语义码 |
| SMT 分拣机 ECS 历史文档（2026-03-18） | `1002` | 取料失败 | `PICK_FAILED` | 标准设备错误码 | 历史保留 | 当前现行链路未使用该数字码 |
| SMT 分拣机 ECS 历史文档（2026-03-18） | `1003` | 放料失败 | `PLACE_FAILED` | 标准设备错误码 | 历史保留 | 当前现行链路未使用该数字码 |

## 6. 当前推荐口径

### 6.1 设备和协议发送什么

现行设备协议、Mock、E2E 示例应直接发送：

- `NONE`
- `INSPECTION_SIZE_NG`
- `INSPECTION_THICKNESS_NG`
- `SCAN_FAILED`
- `PICK_AND_PUT_FAILED`
- `MOVE_FAILED`
- `TARGET_BLOCKED`
- `BIN_FULL`
- `DEVICE_FAULT`
- `DEVICE_UNKNOWN_ERROR`

而不是发送：
- `0`
- `2001`
- `2002`
- `2003`
- `9999`
- `1001`
- `1002`
- `1003`

### 6.2 插件消费什么

插件逻辑只能消费 `DeviceErrorCode`。

例如：
- `INSPECTION_SIZE_NG` / `INSPECTION_THICKNESS_NG` → NG 业务分流
- `SCAN_FAILED` / `PICK_AND_PUT_FAILED` / `BIN_FULL` / `DEVICE_FAULT` → `MANUAL_HOLD` 或其他明确人工介入路径
- `MOVE_FAILED` → 硬件失败路径

### 6.3 日志保留什么

如果现场联调仍需要保留供应商原始值，建议只在日志或原始 payload 证据中保留，例如：

```json
{
  "error_detail": {
    "error_code": "PICK_AND_PUT_FAILED",
    "error_message": "搬运失败",
    "vendor_raw_error_code": "2002"
  }
}
```

注意：
- `vendor_raw_error_code` 只能是证据字段
- 插件业务逻辑不能基于它做分支

## 7. 与平台运行时错误码的边界示例

| 场景 | 正确归类 | 示例错误码 |
| --- | --- | --- |
| `/callback/event` 缺少 `event_type` | 平台运行时错误码 | `CALLBACK_SCHEMA_INVALID` |
| 能入站，但当前无法定位 Session | 平台运行时错误码 | `SESSION_CONTEXT_MISSING` / `SESSION_RESOLVE_FAILED` |
| Outbox 派发设备请求失败 | 平台运行时错误码 | `OUTBOX_DISPATCH_FAILED` |
| 设备长时间无结果 | 平台运行时错误码 | `DEVICE_TIMEOUT` |
| 设备回调表示“搬运失败” | 标准设备错误码 | `PICK_AND_PUT_FAILED` |
| 设备回调表示“料箱已满” | 标准设备错误码 | `BIN_FULL` |
| 设备回调表示“路径被阻挡” | 标准设备错误码 | `TARGET_BLOCKED` |

## 8. 落地约束

### 必须做

- 新插件只定义和消费 `DeviceErrorCode`
- 当前联调文档和 Mock 直接输出标准语义码
- 历史文档明确标注“历史参考、非当前标准”
- 新增 vendor 码时，先判断是否需要补新的 `DeviceErrorCode`

### 禁止做

- 在插件逻辑中保留 vendor 数字码别名兼容
- 在 registry / runtime / normalizer 中扩散数字码分支
- 把平台运行时错误码当作设备错误码使用
- 把 vendor 原始数字码当作业务逻辑输入

## 9. 当前结论

基于当前已知协议，以下映射已经满足“语义稳定、可以标准化”的条件：

- `0` → `NONE`
- `2001` → `SCAN_FAILED`
- `2002` → `PICK_AND_PUT_FAILED`
- `2003` → `BIN_FULL`
- `9999` → `DEVICE_UNKNOWN_ERROR`
- `1001`（流水线）→ `TARGET_BLOCKED`
- `1002`（流水线）→ `MOVE_FAILED`
- `1003`（流水线）→ `DEVICE_FAULT`
- `1001`（历史 ECS）→ `TARGET_BLOCKED`
- `1002`（历史 ECS）→ `PICK_FAILED`
- `1003`（历史 ECS）→ `PLACE_FAILED`

因此，当前插件体系可以稳定坚持：

- **平台归因看运行时错误码**
- **设备语义看 `DeviceErrorCode`**
- **vendor 数字码只留在历史和证据层**
