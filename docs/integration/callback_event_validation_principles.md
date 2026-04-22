# Callback Event 前置校验原则

> 适用入口：`POST /api/v1/callback/event`
>
> 相关文档：
> - [`third_party_integration_whitepaper.md`](./third_party_integration_whitepaper.md)
> - [`../hardware/SMT粗分机接口调用说明书20260321-v1.md`](../hardware/SMT粗分机接口调用说明书20260321-v1.md)
> - [`../business/workline_plugin_architecture_design.md`](../business/workline_plugin_architecture_design.md)

## 1. 问题定义

`/callback/event` 是 **统一硬件事件入口**。

它的职责不是判断“这是不是一个业务上有效的扫码结果”，而是判断：

1. 这是不是一个可解析的 HTTP 请求
2. 这是不是一个最小包络合法的设备事件
3. 这条事件是否能被系统识别、路由、入站（Inbox）

业务字段完整性、插件协议映射、`SixInOne` 解析、`business_key` 生成，都不属于这个入口层的职责。

## 2. 第一性原理结论

### 2.1 入口层只校验“是否可入站”

`/callback/event` 前置校验应只覆盖四层：

1. **请求可解析**：Body 是合法 JSON object
2. **最小包络合法**：`device_code / event_type / timestamp / data`
3. **上下文可路由**：设备存在、设备绑定 workline、能力声明支持该事件
4. **可安全入站**：幂等与 Inbox 写入成功

### 2.2 入口层不校验“业务是否成立”

以下判断不应放在 `/callback/event`：

- `SCAN_COMPLETED` 是否带齐 6 个一维码字段
- `SCAN_COMPLETED` 是否能解析为 `SixInOne`
- `MATERIAL_ARRIVED` 是否必须携带 `location`
- 某插件的字段别名是否已映射成功
- 是否能生成 `business_key`

这些都应该由 **插件 / runtime / orchestrator** 在异步处理链路中决定。

## 3. 推荐验证分层

### 3.1 请求层（HTTP ingress）

**应拒绝**：
- 非 JSON object
- 缺失 `device_code`
- 缺失 `event_type`
- `timestamp` 类型或范围非法
- `data` 既不是 object，也不是 `null`

**应接受并继续路由**：
- `SCAN_COMPLETED` 只带部分扫码字段
- 设备业务负载不完整但仍能作为事件事实入站
- 未来插件定义的新事件类型（前提是设备能力声明支持）

### 3.2 路由层（device/workline capability）

**应拒绝**：
- `device_code` 对应设备不存在
- 设备未绑定有效 workline
- 设备能力配置无效
- 设备未声明支持该 canonical event

这层仍属于入口层职责，因为它决定事件能否被系统识别和路由。

### 3.3 业务层（Inbox → Orchestrator → Plugin）

这里再做：
- 插件协议字段映射
- `SixInOne` 解析
- 扫码是否完整
- 事件实例标识校验（如 `MATERIAL_ARRIVED.event_id` 用于 session 归属）
- 业务 OK/NG 判断
- Session 恢复/创建
- 设备命令派发与超时处理

## 4. 与当前硬件协议的关系

白皮书和硬件接口文档都把 `event` 定义为统一事件上报入口，包络字段稳定为：

- `device_code`
- `event_type`
- `timestamp`
- `data`

其中 `data` 是**业务负载容器**，并非入口层统一语义模型。

因此：

- callback 层维护 **Minimal Envelope**
- plugin contract 维护 **厂商协议真相**
- runtime/orchestrator 维护 **控制流与业务投影**

三者不能混层。

## 5. 设计约束（防止过度设计）

### 5.1 不在 callback 层维护厂商事件枚举

callback 只接受 `event_type: str`。
合法值由：
- workline runtime event mapping
- device capabilities
- plugin contract

共同决定。

### 5.2 不在 callback 层做插件私有 payload 校验

callback 不解析：
- `ProductNo -> HHPN`
- `PONumber -> PkgID`
- 任何插件私有字段别名

这些映射只能由插件或插件侧 normalizer 负责。

### 5.3 不让请求入口覆盖真实失败归因

如果事件已经通过最小包络校验并成功入站，则后续失败应保留真实原因，例如：
- `SESSION_CONTEXT_MISSING`
- `DEVICE_TIMEOUT`
- `dispatch_robot` 失败
- plugin business failure

入口层不能再把这些失败重新覆盖成 `CALLBACK_SCHEMA_INVALID`。

## 6. 当前落地策略

### `src/app/callback/v1/callback.py`
- 先做 `CallbackEventRequest` 最小包络校验
- 再做 `device_context_service.resolve`
- 再做 capability / canonical event 校验
- 成功后写入 Inbox 并立即 ACK

### `src/celery_app/tasks/workline.py`
- 仅保留 **registry 无关** 的 `SCAN_COMPLETED` malformed payload gate
- 该 gate 仅用于拦截“完全不像扫码事件”的空 payload
- 不依赖 `plugin_key`、registry 或插件 parser

---

一句话总结：

> `/callback/event` 只验证“这是不是一个可识别、可路由、可入站的硬件事件”，
> 不提前验证“这是不是一个业务上成立的插件事件”。
