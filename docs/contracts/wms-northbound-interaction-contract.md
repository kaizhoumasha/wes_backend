# WMS 北向最小交互合同

状态：Task 1 冻结；适用于单一部署 Provider 的开发 mock 联调与后续真实 WMS 验收。

## 1. 范围与身份

一个部署只绑定一个 WMS Provider。所有 EFFECT 提交和状态查询使用相同的唯一键：
`operation_identity + idempotency_key`。提交端点为 `POST /northbound/operations`，状态端点为
`GET /northbound/operations/status`；真实部署可改变路径，但必须保持本合同字段和语义。

HTTP body 是 operation-specific typed payload 的 canonical JSON，即对应
`ConfirmInboundOperationRequest`、`FullBoxExchangeOperationRequest` 或 `NotifyPackageBindingOperationRequest`
经 gateway 投影后的 wire 字段；没有 `operation_identity/idempotency_key/canonical_payload/frozen_binding`
外层包络。所有 string 在进入 wire 前已去除首尾空白且不得为空；表中 `1..N` 表示 UTF-8 JSON string 的字符长度
约束。body 只允许表中字段，当前三个请求均无枚举字段。

### `wms.inventory.confirm_inbound@v1`

| Wire 字段 | JSON 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `dispatch_key` | string | 是 | 1..240 |
| `inbound_key` | string | 是 | 1..120 |
| `material_code` | string | 是 | 1..120 |
| `quantity` | decimal string | 是 | `> 0`、有限十进制数；gateway 以 `str(Decimal)` 编码，不是 JSON number |
| `warehouse_code` | string | 否 | 1..120；无值时字段整体省略，不发送 `null` |
| `owner_code` | string | 否 | 1..120；无值时字段整体省略，不发送 `null` |
| `lot_no` | string | 否 | 1..120；无值时字段整体省略，不发送 `null` |

当前无枚举字段。不发送：`workline_id`、`session_id`、`trace_id`，以及 provider profile、frozen binding、
operation identity、idempotency key、Outbox/Intent 元数据；后两项只按下文进入 header。

### `wms.fulfillment.full_box_exchange@v1`

| Wire 字段 | JSON 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `dispatch_key` | string | 是 | 1..240 |
| `rack_id` | string | 是 | 1..120 |
| `empty_box_id` | string | 是 | 1..120 |
| `full_box_id` | string | 是 | 1..120 |

当前无枚举字段。不发送：`provider_code`、`workline_id`、`session_id`、`trace_id`，以及 provider profile、
frozen binding、operation identity、idempotency key、Outbox/Intent 元数据。`provider_code` 只参与 WES 内部
operation key，不是 WMS wire body 字段。

### `wms.fulfillment.notify_pkg_binding@v1`

| Wire 字段 | JSON 类型 | 必填 | 约束 |
| --- | --- | --- | --- |
| `dispatch_key` | string | 是 | 1..240 |
| `package_id` | string | 是 | 1..120 |
| `pallet_id` | string | 是 | 1..120 |
| `station_code` | string | 是 | 1..120 |

当前无枚举字段。不发送：`provider_code`、`workline_id`、`session_id`、`trace_id`，以及 provider profile、
frozen binding、operation identity、idempotency key、Outbox/Intent 元数据。`provider_code` 只参与 WES 内部
operation key，不是 WMS wire body 字段。

以上表格是 WMS 开发团队实现 request validator 的自包含输入合同，并与三个 Pydantic Request model 及 gateway
投影同步；Request model 中存在但表中未列出的字段均为 WES 内部字段，不得推断为可选 wire 字段。

frozen binding 仅为 WES 内部持久化事实，绝不进入 HTTP body、header 或 query。它冻结 endpoint、credential
reference、operation identity 和 binding revision，使重试/重启继续构造同一远端请求；WMS 无需理解或保存 WES
binding revision。

`X-WES-Content-SHA256` 是实际 HTTP body bytes 的传输完整性哈希，进入七项 HMAC 并在解析前校验。
幂等 fingerprint 则是在 typed schema 校验后重新生成 canonical JSON bytes 再计算 SHA-256；不得直接以 `X-WES-Content-SHA256` 作为幂等 fingerprint。
Canonical JSON 使用 UTF-8、object key 排序、无额外空白且
拒绝 NaN/Infinity。WMS 必须按
`X-WES-Operation-Identity + Idempotency-Key` 定位原请求，并用 fingerprint 比较 body，不得因原始 JSON
字段顺序、空白、timestamp/nonce 更新或 transport retry 改变结论。

### Submit 请求认证

Submit 使用 `X-WES-*` HMAC header。`Idempotency-Key` 和 `X-WES-Operation-Identity` 是 HTTP header；当前 WES
sender 的封闭 header 集为：

- `Content-Type: application/json`
- `X-WES-Content-SHA256`
- `X-WES-Credential-Reference`
- `X-WES-Nonce`
- `X-WES-Signature`
- `X-WES-Signature-Algorithm`：固定值 `HMAC_SHA256`
- `X-WES-Timestamp`
- `X-WES-Operation-Identity`
- `Idempotency-Key`

WMS 不得要求把这些传输元数据复制进 typed body。`canonical_dispatch.py` 的 Submit 七项 canonical input 顺序严格为
method → path → timestamp → nonce → payload hash → operation identity → idempotency key，各字段使用换行符连接：

```text
POST
/resolved/path
<X-WES-Timestamp>
<X-WES-Nonce>
<X-WES-Content-SHA256>
<X-WES-Operation-Identity>
<Idempotency-Key>
```

这里的 path 是冻结 endpoint URL 的 path（空 path 归一为 `/`），不含 scheme、host 或 query。任何字段、顺序或
分隔符变化都会产生不同签名。

### Status query 请求认证

Status query 使用另一套既有 `X-WMS-*` HMAC header，不能沿用或重命名 Submit 的 `X-WES-*` header：

- `X-WMS-Content-SHA256`
- `X-WMS-Credential-Reference`
- `X-WMS-Nonce`
- `X-WMS-Signature`
- `X-WMS-Signature-Algorithm`：固定值 `HMAC_SHA256`
- `X-WMS-Timestamp`

`http_transport.py` 的 Status query 五项 canonical input 顺序严格为
method → path → timestamp → nonce → body hash，各字段使用换行符连接：

```text
GET
/resolved/status/path?operation_identity=<encoded>&idempotency_key=<encoded>
<X-WMS-Timestamp>
<X-WMS-Nonce>
<X-WMS-Content-SHA256>
```

这里的 path 实际取自 `httpx.Request.url.raw_path`：不含 scheme 和 host，但包含 `?` 后的原始 query string。
因此 `operation_identity`、`idempotency_key` 的字段名、编码值和发送顺序都作为第二项的一部分参与签名；WMS 必须
对收到的 request target 原样取值，不能先解析、排序或重新编码 query。当前 Status query 使用 GET。GET 的 request body 为空 bytes，因此
`X-WMS-Content-SHA256` 必须是 `SHA-256(b"")`。若未来某个已冻结 status target 使用非空实际 body，则该 header
和五项 canonical 的最后一项都必须改为该次实际 raw body bytes 的 SHA-256；不得使用 response body 或解析后的
dict 计算。

## 2. 提交幂等与冲突

| 条件 | HTTP | 稳定错误码/结果 | WES 行为 |
| --- | --- | --- | --- |
| 首次提交被受理 | 202 | 返回可关联的受理快照 | 查询状态直至终态 |
| 同 key、同 fingerprint，仍处理中 | 409 | `IDEMPOTENCY_REQUEST_IN_PROGRESS` | 不重发业务效果，改为查询 |
| 同 key、同 fingerprint，已完成 | 200 | 原业务结果与原 `source_version` | 视为幂等重放 |
| 同 key、不同 fingerprint | 422 | `IDEMPOTENCY_CONFLICT` | 立即人工对账；绝不是暂时并发重试 |

在保留期内，同一 operation identity、幂等键和 fingerprint 的再次提交必须始终是原请求的幂等重放；同一
operation identity、同一 key 但 fingerprint 不同必须是 `IDEMPOTENCY_CONFLICT`。普通 transport retry 可重用
相同 typed body bytes、operation identity 和 key；timestamp、nonce 与签名可按本次 HTTP attempt 重建。

WES 的唯一一次受控恢复重提还必须同时满足：此前从未观察到任何可见状态、`NOT_FOUND` 已持续超过宽限期，
并且 wire 三项身份完全未变；WES 内部还必须继续使用原 frozen binding，不得切换 endpoint/credential revision。
若曾观察到任何可见状态，后续出现
`NOT_FOUND` 必须直接进入人工对账，禁止恢复重提。首次提交实际未到达 WMS 时，同键同 payload 的下一次提交必须能
创建请求；首次已受理但状态暂不可见时，重提不得产生第二份业务效果。

## 3. 状态查询快照

查询参数必须为 `operation_identity` 和 `idempotency_key`。`state` 只能是：
`ACCEPTED | PROCESSING | COMPLETED | REJECTED | NOT_FOUND`。

每个响应包含 `provider_reference`、`reason_code`、`updated_at`、`source_version`、`result_payload`：

| 字段 | 格式与可空性 |
| --- | --- |
| `provider_reference` | 可见状态必须为非空 opaque string；`NOT_FOUND` 为 `null`。 |
| `reason_code` | 仅 `REJECTED` 为 operation 冻结的允许集合中的稳定非空 string；所有其它状态必须为 `null`。 |
| `updated_at` | 可见状态必须是 offset 为 `+00:00` 的 RFC 3339/ISO-8601 UTC string；`NOT_FOUND` 为 `null`。它不承担排序语义。 |
| `source_version` | 可见状态必须是同一查询键下从任意非负整数开始的 integer；每个新状态严格递增，幂等重放保留原版本。`NOT_FOUND` 必须为 `null`。 |
| `result_payload` | 仅 `COMPLETED` 必须存在；`REJECTED` 和所有非终态必须为 `null`，不得携带可被误认为终态的 payload。`NOT_FOUND` 的 `provider_reference`、`updated_at`、`reason_code`、`source_version` 和 `result_payload` 均必须为 `null`。 |

`COMPLETED.result_payload` 的 schema 由 `operation_identity` 对应的 WES result model 冻结。它必须含
`accepted: true`，并使 dispatch/correlation 字段与原请求一致；若内外层同时含 `source_version`，规范化后必须一致。
`REJECTED` 不得伪造成功结果。当前开发 mock operation
`wms.fulfillment.notify_pkg_binding@v1` 的允许拒绝码为 `WMS_BUSINESS_REJECTED`；新增 operation 必须随合同冻结其集合。
完成态幂等重放必须逐字段保留原 `source_version`、`provider_reference` 和 typed result；重复状态查询也必须保留拒绝码、版本和全部快照内容。

## 4. 保留期、可见性与回调

- `WMS retention >= WES max confirmation age + safety margin`；这是最小保留期。保留期边界前不得让同一幂等键重新生效；合同不要求边界后立即过期或产生第二份业务效果。
- `WMS visibility SLA <= WES NOT_FOUND grace period`，即从受理到可按查询键看见的最大延迟不得超过宽限期。
- WES 最大确认窗口、`NOT_FOUND` 宽限期、安全余量及 WMS 承诺值均是部署参数，必须进入联调验收；本通用合同不写死工厂无关的天数。
- callback 是可选提示。若提供，仅发送关联键以触发即时状态查询；不要求在 callback 中复制终态 payload，也不授予 callback 写业务终态的权威。

## 5. 传输、安全与运维

- TLS 仅提供传输保护；HMAC-SHA256 是当前应用层认证。Submit 使用上述 `X-WES-*` 七项 canonical，Status query
  使用上述 `X-WMS-*` 五项 canonical。Bearer/OAuth 不是当前合同；如未来引入，必须作为新版本合同单独设计、评审
  和验收，不能替换或混入本版本 HMAC 语义。
- 服务端验签成功后必须校验 timestamp 新鲜度并原子消费 credential reference + nonce。当前开发 Mock 使用前后
  30 秒时钟窗口和 300 秒 nonce TTL；Submit 接受 WES sender 的 UTC aware ISO-8601，Status 接受 Unix 秒。
  合法 transport retry 必须保留 operation identity、幂等键和 typed body，但为每次 HTTP attempt 生成新的
  timestamp、nonce 与签名。
- 密钥、完整认证 header、未脱敏 body 均不得写入探针、日志、证据或报告。
- 客户端和服务端必须为提交、状态查询分别声明 deadline；联调以真实客户端 deadline（服务端 sleep/断连）验证提交超时，而非用 HTTP 504 代替。超时后同键同 payload 重提，仍按本合同幂等语义处理，不能猜测业务成功。
- 429 必须带合法 `Retry-After`（delta-seconds 或 HTTP-date）；WES 以其为下一次状态查询的时间下限，不在限流窗口忙重试。
- 5xx 使用稳定、可分类错误码；最大响应体大小作为部署承诺。超过承诺、畸形 JSON 或未知状态均为合同失败。
- 所有时钟字段使用 RFC 3339/ISO-8601 的 offset-aware UTC 时间；禁止用 `updated_at` 推断状态先后，唯一排序依据是 `source_version`。
- 日志只允许 operation identity、截断/哈希后的关联键、HTTP status、稳定错误码和版本；禁止记录 payload、token、签名或未脱敏响应体。探针输出更严格：只允许本地 case 枚举、布尔结论和本地计数，不得拼接任意远端字段。

## 6. 联调验收矩阵

| 必测项 | 通过标准 |
| --- | --- |
| 已完成请求重复提交 | 200，逐字段返回原 `source_version`、`provider_reference` 和 typed result。 |
| 处理中请求重复提交 | 409 + `IDEMPOTENCY_REQUEST_IN_PROGRESS`。 |
| 同 key 不同 fingerprint | 422 + `IDEMPOTENCY_CONFLICT`，不得自动重试。 |
| 保留期 | 同键在承诺保留期内不得重新生效。 |
| 提交超时 | 明确区分实际未到达与结果不明；前者同键重提可创建。 |
| 可见性 SLA 边界 | 用可控时钟跨越真实边界：受理后在承诺延迟内可查询；宽限期内 `NOT_FOUND` 仅重查。 |
| 状态序列 | 覆盖 `ACCEPTED`、`PROCESSING`、`COMPLETED` 的单调版本和 typed result。 |
| 拒绝/未找到 | `REJECTED` 有稳定 reason code；`NOT_FOUND` 的 version/updated_at 为空。 |
| 已受理暂不可见 | 同键重提后，经公开效果计数/等价观察面证明仅有一份业务效果。 |
| 受控恢复/已见状态后丢失 | 仅在从未见可见状态且超宽限期时保持 wire 三项身份及 WES 内部 frozen binding 重提一次；已见状态后 `NOT_FOUND` 直接人工对账。 |
| 保留期边界/响应体上限 | 仅在 `retention_seconds - 1`（非负）验证仍幂等；边界及之后不强制任何过期行为。body 以有界流式分块读取，首个越过上限的分块即关闭并拒绝。 |
| 429 / 5xx / 状态查询超时 | 分别验证 `Retry-After`、稳定错误形状及 deadline 行为。 |

联调运行 `uv run python scripts/verify_wms_northbound_feasibility.py --base-url <stub-url>`。探针仅输出本地 case
枚举和布尔结论；全部强制项通过且双方确认后，真实 WMS 才可给出生产验收结论。
