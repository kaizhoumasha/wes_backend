# Callback 入站基础能力边界

> 权威设计：[`2026-07-31-wes-minimal-execution-architecture-convergence-design.md`](../superpowers/specs/2026-07-31-wes-minimal-execution-architecture-convergence-design.md)

## 1. 文档定位

本文只定义 WES 核心 callback ingress 的共享基础能力。厂商 Payload 由对应 Adapter 验证，WMS 拥有业务结果，工作线插件
只验证结果关联并映射执行，四者不得相互代测或越权解释。

## 2. 分层职责

### 2.1 核心 ingress

核心只负责：

- HTTP 方法、认证、请求大小和可解析性；
- 选择已显式绑定的厂商 Adapter；
- 在 ACK 前持久化原始 Payload、来源身份、接收时间和 payload digest；
- 处理相同幂等身份的重复或冲突请求；
- 返回传输层 ACK，并将已持久化的 `InboundEvidence` 交给后续处理。

核心不定义统一 `event_type` 枚举，不解析厂商业务字段，也不决定业务是否成立。

### 2.2 厂商 Adapter

Adapter 根据实际厂商合同负责：

- 厂商请求 DTO、签名和字段校验；
- 厂商事件身份、事件类型和 Payload 映射；
- 厂商错误到稳定接入错误的映射；
- 厂商 wire fixture 与合同测试。

Adapter 不写数据库、不调用 Repository、不决定工作线下一步，也不维护运行时注册表。

### 2.3 WorkLine 插件

插件只处理已由 Adapter 映射并由核心持久化的 evidence：

- 校验当前工作线输入和 WMS 结果的关联、版本、时效与物理可执行性；
- 读取注入的只读投影和 WMS 类型化业务结果；
- 将 WMS 结果映射为等待、发送、暂停、隔离或对账等封闭执行 Decision。

扫码原始字段属于 Adapter；料盘业务身份、目标料格和 NG/等待/替代语义属于 WMS；下一条逻辑设备动作属于插件执行映射。
这些职责都不属于 callback ingress。

## 3. ACK-before-processing

合法输入遵循固定顺序：

1. 完成共享传输检查并选择显式 Adapter；
2. Adapter 校验厂商 wire 合同并给出稳定事件身份；
3. 核心持久化 `InboundEvidence`；
4. 同步返回 ACK；
5. 异步调用已绑定的 WorkLine 插件。

无法建立 evidence、幂等身份冲突或 Adapter 校验失败时不得返回成功 ACK。ACK 成功不表示业务 Decision 成功，
更不表示设备动作完成。

## 4. 未知与重复输入

- 相同幂等身份、相同 digest：返回首次 ACK，不重复执行插件。
- 相同幂等身份、不同 digest：拒绝并保存冲突证据。
- 无 Adapter、未知设备或缺失稳定事件身份：保存允许保留的接入诊断，不推进业务对象。
- 已 ACK 后的业务失败：保留插件返回的真实稳定原因，不覆盖为通用 schema 错误。

## 5. 测试所有权

| 测试内容 | 唯一所有者 |
| --- | --- |
| HTTP、认证、大小限制、ACK-before-persist、幂等与 evidence 可靠性 | WES 核心测试 |
| 厂商请求 DTO、事件名称、字段、签名和错误码 | 厂商 Adapter 测试 |
| WMS 业务 OK/NG/目标结果 | Phase 3 WMS Adapter 合同测试 |
| 结果关联、物理执行校验和后续命令映射 | WorkLine 插件测试 |

核心测试不得构造具体工作线成功路径来证明 ingress；插件测试不得替代核心持久化、幂等和传输可靠性测试。
