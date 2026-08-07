# 设备错误语义与 Adapter 映射边界

> 状态：Implementation Baseline。设备错误语义与供应商原始证据边界继续有效；第 3.1 节只记录 Phase 3/7
> 收敛前的现有平台诊断实现，不是最终 Runtime 设计或新架构输入。
> 具体厂商原始码映射不属于核心合同，只能位于对应 Adapter 的实现输入、fixture 与合同测试中。

## 1. 目标

本文档定义核心设备错误语义、厂商 Adapter 映射职责，以及供应商原始错误码的证据保留边界。

目标只有三个：

1. **平台运行时错误码** 与 **设备执行错误码** 严格分层。
2. 插件业务逻辑只消费规范化类型结果：共享错误使用
   [`DeviceErrorCode`](../../src/app/workline/domain/contracts/device_error_codes.py)，具体检测结果使用显式的
   Adapter → 插件类型化合同；两者都不得暴露供应商数字码。
3. 厂商原始错误码由对应 Adapter 显式映射并作为原始证据保留，不进入核心或插件业务判断。

## 2. 非目标

本文档**不是**建设一个跨厂商的全局错误码 registry、动态转义层或兼容链。

当前目标是：

- 真实设备继续遵循厂商原始协议，不要求厂商改写为 WES 错误码。
- 每个厂商 Adapter 在自己的 DTO、fixture 和合同测试中显式完成原始码到 `DeviceErrorCode` 的映射。
- 核心无业务语义 fake 直接产生标准设备语义；具体厂商 Mock/E2E 必须由对应 Adapter 包交付，不能替代核心可靠性测试。
  插件测试只验证标准设备语义和 WMS 业务结果驱动的执行行为。

## 3. 三层边界

### 3.1 收敛前平台诊断实现

定义位置：

- [`src/app/runtime/orchestration/diagnostics/codes.py`](../../src/app/runtime/orchestration/diagnostics/codes.py)

当前实现用途：

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

最终目标不保留 session/orchestrator/outbox/inbox 作为通用执行所有者。Phase 3/7 必须把仍有价值的诊断分别落到
`InboundEvidence`、`DeviceCommand`、`TransportTask`、`WmsConfirmation`、具体执行对象和投影；旧 owner 名称
随对应实现删除，不建立别名、兼容码或转发层。

### 3.2 标准设备错误码

定义位置：

- [`src/app/workline/domain/contracts/device_error_codes.py`](../../src/app/workline/domain/contracts/device_error_codes.py)

用途：

- 设备执行结果语义
- 插件业务分支判断
- Adapter 规范输出、核心 fake 和插件业务输入

当前标准集合：

- 成功哨兵：`NONE`
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

- 只能由对应厂商 Adapter 解析和映射，不得直接进入核心或插件业务分支判断。
- 必须随原始回调证据保存，禁止映射后丢失现场值。
- 若某个原始码语义不稳定，不允许“猜测映射”；Adapter 必须 fail closed 或输出明确的未知设备错误语义。

这层回答的是：**供应商当时原始上报了什么**。

## 4. 核心原则

### 原则 1：先分层，再映射

先回答问题属于哪一层：

- 是平台处理失败？→ 运行时错误码
- 是设备执行结果？→ 标准设备错误码
- 是供应商原始协议值？→ 由对应 Adapter 映射，同时保留原始证据

### 原则 2：只有稳定语义才允许标准化

如果某个 vendor 数字码已经具备**明确、稳定、无歧义**的语义，对应 Adapter 可以显式映射到标准设备错误码。

只有在至少两个已确认消费者中都成立、且跨厂商稳定的共享设备语义，才允许通过独立 TDD 变更补充
`DeviceErrorCode`。具体厂商或具体业务线语义不得为了复用而提升到核心；它应保留在 Adapter 输出的类型化扩展结果
和对应插件合同中。语义不清晰时必须 fail closed，不得硬套现有码或预建兼容枚举。

### 原则 3：插件只看规范化类型语义，不看 vendor 数字

禁止：

- `if error_code == "2002": ...`
- `if error_code in {"1001", "1002"}: ...`

允许：

- `if error_code == DeviceErrorCode.PICK_AND_PUT_FAILED: ...`
- `if error_code == DeviceErrorCode.SCAN_FAILED: ...`
- Adapter → 插件合同中的类型化“尺寸 NG”结果进入插件 NG 分支（该类型不属于核心枚举）。

### 原则 4：成功态也统一

设备结果中的成功态统一使用：

- `NONE`

只有当前厂商合同明确声明的成功值，才由对应 Adapter 映射为 `NONE`，并作为原始证据保留。

## 5. Adapter 映射文档所有权

具体厂商映射必须满足：

- 放在对应 Adapter 包内，并引用 `docs/hardware/` 的原始供应商资料。
- 与该 Adapter 的实现、DTO、fixture 和合同测试同所有者；没有 Adapter 实现时不得维护或发布映射表。
- 核心与其他 Adapter 不得依赖具体厂商映射；具体厂商映射也不得提升为核心运行时规则。

## 6. 当前推荐口径

### 6.1 设备、Adapter 和测试分别输出什么

- **真实设备协议**：发送厂商文档定义的原始错误码和 Payload；`docs/hardware/` 保持原样。
- **厂商 Adapter**：校验厂商 DTO，输出 `DeviceErrorCode`，并把原始错误码保存在 evidence 中。
- **核心无业务语义 fake**：直接产生 `DeviceErrorCode`，只验证核心持久化、幂等、传输和可靠性不变量。
- **具体厂商 Mock/E2E**：复现厂商原始协议并验证 Adapter 映射，只随对应 Adapter 包交付，不进入核心默认测试。

### 6.2 插件消费什么

插件逻辑对共享设备失败只能消费 `DeviceErrorCode`；具体检测能力可以消费 Adapter 输出的显式类型化结果，但不得
读取厂商原始数字码或把具体检测结果伪装成核心枚举。

例如：

- `SCAN_FAILED` / `PICK_AND_PUT_FAILED` / `BIN_FULL` / `DEVICE_FAULT` → `MANUAL_HOLD` 或其他明确人工介入路径
- `MOVE_FAILED` → 硬件失败路径

尺寸 NG、厚度 NG 等检测结果属于具体厂商与具体工作线合同：Adapter 负责输出对应类型化结果，插件负责据此进行
NG 业务分流。除非后续证明它们是跨厂商共享语义并按 TDD 纳入核心，否则不得虚构核心
`DeviceErrorCode` 成员。

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
| 能入站，但当前无法关联目标执行对象 | 平台运行时错误码 | 对象关联失败诊断码 |
| `DeviceCommand` 发送失败 | 平台运行时错误码 | 命令传输失败诊断码 |
| 设备长时间无结果 | 平台运行时错误码 | `DEVICE_TIMEOUT` |
| 设备回调表示“搬运失败” | 标准设备错误码 | `PICK_AND_PUT_FAILED` |
| 设备回调表示“料箱已满” | 标准设备错误码 | `BIN_FULL` |
| 设备回调表示“路径被阻挡” | 标准设备错误码 | `TARGET_BLOCKED` |

## 8. 落地约束

### 必须做

- 新插件只消费共享 `DeviceErrorCode` 或显式 Adapter → 插件类型化结果，不定义核心设备错误码
- 每个厂商 Adapter 显式拥有 DTO、原始码映射、fixture 和合同测试
- 核心 fake 与具体厂商 Mock/E2E 严格分开，不能互相替代测试所有权
- 新增 vendor 码时，先核对厂商原文，再映射到既有共享语义或 Adapter 专属类型化结果；只有满足跨厂商共享条件
  并完成 TDD 时才补核心 `DeviceErrorCode`

### 禁止做

- 在插件逻辑中保留 vendor 数字码别名兼容
- 在核心、通用 normalizer 或业务插件中扩散厂商数字码分支
- 把平台运行时错误码当作设备错误码使用
- 把 vendor 原始数字码当作业务逻辑输入

## 9. 当前结论

当前核心与插件体系只稳定坚持以下分层，不拥有任何具体厂商映射表：

- 平台归因：运行时错误码。
- 共享设备失败语义：`DeviceErrorCode`；具体检测语义：显式 Adapter → 插件类型化合同。
- vendor 数字码：只由对应 Adapter 解析，并保留在原始证据层。
