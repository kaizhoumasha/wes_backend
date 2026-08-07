# WMS HTTP Client 使用合同

> 状态：`IMPLEMENTED`（Phase 3 共享 HTTP/JSON Client，2026-08-07）。
> 本文只定义 Phase 3 WMS HTTP 访问标准，不定义任何具体 WMS 业务 API。
> 外部输入：`docs/hardware/wms_rcs_interface_requirements.md` 只读保留；具体业务开发时再从中选择并确认真实接口。

## 1. 目的

Phase 3 提供一个类似前端 Axios/API Client 的 `WmsClient`，统一 WES 调用 WMS 时的 HTTP 和 JSON 处理。

本文回答：

- 如何构造和复用 WMS Client。
- 如何发送 GET/POST JSON 请求。
- 如何获得标准响应和传输失败事实。
- 后续业务开发如何增加一个具体 WMS API。

本文不回答具体业务 API 的 path、字段、错误码、幂等和业务含义。这些内容由后续业务开发依据真实 WMS 合同逐项定义。

## 2. 权限边界

- WMS 给出所有业务结果。
- WES 业务模块校验具体 API 合同并解释 WMS 结果。
- WES 执行模块只根据有效业务结果和现场物理条件作出等待、发送、暂停、隔离或对账等执行决策。
- `WmsClient` 不知道库存、来源、目标、优先级、路线、NG、替代、取消、恢复或业务终态。

因此 Phase 3 不建立业务 Port、业务 Outcome、operation 清单或消费者矩阵。

## 3. WmsClient 负责什么

- 持有一个 Phase 2 `OutboundHttpTransport`。
- 接收 relative path、query、headers 和 JSON body。
- 将 GET/POST 映射为 `OutboundHttpRequest`。
- 对标准 JSON 值做一次严格 UTF-8 编码，对完整响应做一次严格 JSON 解码，并区分空响应与 JSON `null`。
- 通过字段冻结的 `WmsAccessResult` 保留 HTTP status、response headers、`delivery_state`、`failure_kind` 和 JSON 解码事实。
- 提供显式且幂等的 `aclose()`。

每个运行时/事件循环 owner 构造并长期复用自己的 WMS Client，不得跨事件循环共享；单次业务调用不得创建新的 HTTP
Client。Phase 3 不接入生产 Composition Root，真实 FastAPI/Celery owner 的装配与关闭随对应业务接线实施。

## 4. WmsClient 不负责什么

- 具体业务 request/response DTO 和字段闭集。
- 业务成功、拒绝、等待、NG 或终态解释。
- 数据库、事务、evidence、重试、breaker、缓存、分页或对账。
- WMS inbound endpoint、路由或 normalizer。
- RCS、AGV、CTU 和 `TransportTask` 业务语义。
- 生产动态 registry、Provider、Profile、Service Locator、代码生成或配置驱动 API。
- 当前隔离局域网不需要的认证、签名、nonce、clock 或 IP allowlist。

## 5. 统一调用规则

### 5.1 请求

- `path` 必须是以 `/` 开始的 relative path，不允许调用方覆盖 WMS origin。
- 当前只支持 Phase 2 已提供的 GET 和 POST；真实 API 需要其他 method 时再独立扩展 Phase 2 和本文。
- GET 使用 query，不发送 JSON body。
- POST 的 `json` 参数必传，`None` 表示 JSON `null`。值域只允许 `None`、布尔、整数、有限浮点、字符串、上述值组成
  的 list，以及字符串 key 的 dict；tuple、非字符串 key、`NaN`、正负 `Infinity` 和其他对象在发送前拒绝。
- POST 使用无 BOM UTF-8 紧凑编码并设置 `Content-Type: application/json`；调用方以任意大小写传入 `Content-Type`
  都会在发送前被拒绝。GET 不自动添加该 header。
- 调用方可以传入真实 API 所需的其他普通 headers，但不得以任意大小写覆盖 Client 拥有的 `Content-Type`、
  `Content-Encoding`，或 Transport 拥有的 `Host`、`Content-Length`、`Transfer-Encoding`、`Accept-Encoding`。
  Client 发送未压缩 JSON，因此不得声明请求体压缩；Transport 独占 origin、报文分帧和响应压缩协商。覆盖请求会在
  发送前被对应层拒绝。
- 每次调用最多执行一次 `send`；Client 不自动 retry、轮询或翻页。
- `query` 与 `headers` 均使用可选 `Mapping[str, str]`，默认 `None`；Client 只转换为 Phase2 string-pair tuple，非法值继续
  由 Phase2 fail closed。真实 API 需要重复 query key 时再独立扩展。

### 5.2 结果

- 收到完整响应时，返回 status、headers、`body_present`、`json_failure` 和解码后的 JSON；非 2xx 不自动变成业务异常。
- 空响应返回 `body_present=False`、`json_body=None`；JSON `null` 返回 `body_present=True`、`json_body=None`。
- `json_body` 使用标准 JSON 值（object 为 `dict`，array 为 `list`），业务 DTO 可直接使用严格
  `model_validate(...)` 校验；业务代码校验后只使用 DTO，不在访问结果上原地修改 WMS 响应。
- 非空响应只做一次严格 UTF-8/RFC 8259 解码；纯空白 body、非法 UTF-8、非法 JSON 和非标准
  `NaN`/`Infinity` 分别设置 `json_failure=INVALID_UTF8` 或 `INVALID_JSON`，同时保留 status、headers 和交付事实。
- 响应 Content-Type 不参与共享 Client 解码；具体 API 若要求严格 media type，由自己的业务合同验证。
- request 编码失败在发送前抛出；response JSON 解码失败返回访问事实，两者都不得伪装成 WMS 业务拒绝。
- `NOT_SENT`、`DELIVERY_UNKNOWN` 和响应阶段失败必须原样保留 Phase 2 事实，并返回
  `body_present=False`、`json_body=None`。
- `CancelledError`、关闭异常和 Phase 2 的关闭后调用异常原样传播。Client 不复制 Transport 关闭状态；关闭后的合法请求
  调用一次 `send` 并由 Transport 在产生底层 HTTP 请求前拒绝。请求值域或编码错误发生在 `send` 前，因此调用零次
  `send`，不会被关闭状态改写。
- Client 不决定是否重提、暂停或推进状态。

具体业务模块必须基于自己的响应 DTO 和 WMS 合同解释 status 与 JSON，不得把 HTTP 200 等同于业务成功。

## 6. 构造与生命周期

factory 只接收：

- `base_url`
- `timeout_seconds`

factory 调用 Phase 2 builder，并固定 `system_id="wms"`。当前 outbound 认证为 `NONE`，不增加认证配置或扩展接口。

调用方必须在进程关闭时调用 `WmsClient.aclose()`。关闭失败不得静默吞掉。

## 7. 新增具体 WMS API 的开发标准

具体业务开发按以下步骤增加 API：

1. 在真实业务 owner 中定义具名 API 方法。
2. 定义该 API 自己的 request/response DTO，不使用宽泛 `dict[str, Any]` 作为业务合同。
3. request DTO 通过 `model_dump(mode="json")` 转成标准 JSON 值，再用固定 relative path 调用 `WmsClient.get()` 或
   `WmsClient.post()`；Client 不依赖 Pydantic。
4. 先检查 `failure_kind`，再校验 `status_code` 是否属于该 API 合同允许的 HTTP 状态集合；随后检查 `json_failure` 与
   `body_present`，最后才由业务 DTO `model_validate(...)` 校验合法 response JSON，并解释 WMS 给出的业务结果。
5. 使用 WMS 双方确认的 fixture/schema 编写该 API 的合同测试。
6. 只在真实重复出现后提取业务 helper；不得把业务字段或结果解释放进 `WmsClient`。

结构示例：

```text
ExampleWmsApi.query_example(request DTO)
  → request DTO.model_dump(mode="json")
  → WmsClient.post("/example/query", json=标准 JSON 值)
  → 检查 result.failure_kind / status_code / json_failure / body_present
  → response DTO.model_validate(result.json_body)
  → 返回 WMS 给出的业务结果
```

该示例只指导目录职责和调用方向；`/example/query` 不是生产 API，不得据此生成占位实现、公共基类或 registry。

## 8. Phase 3 验收边界

Phase 3 测试只验证：

- GET query、POST JSON、GET/POST body 约束和 header 映射。
- relative path、query/header 默认与重复项、`Content-Type`/`Content-Encoding`/`Host`/HTTP 报文分帧与压缩协商 header
  所有权、严格 JSON 值域/编解码和一次 send。
- status/headers/传输事实保留。
- 空响应、JSON `null`、响应 Content-Type、非 2xx、无效 UTF-8/JSON 的事实保留、取消和关闭行为。
- 新包不依赖数据库、旧 WMS 包、RCS/AGV/CTU 或生产注册机制。

Phase 3 测试不得验证任何库存、PickingTask、NG、业务拒绝或 WorkLine 执行能力。具体业务合同测试由未来业务 API 自己拥有。

Phase 3 不再等待任何具体 WMS API wire 批准；具体 wire 只阻断对应业务 API，不阻断共享 Client 实施和验收。
