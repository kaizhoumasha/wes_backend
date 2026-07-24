# WMS 北向最小交互合同

状态：Task 1 冻结；适用于单一部署 Provider 的开发 mock 联调与后续真实 WMS 验收。

## 1. 范围与身份

一个部署只绑定一个 WMS Provider。所有 EFFECT 提交和状态查询使用相同的唯一键：
`operation_identity + idempotency_key`。提交端点为 `POST /northbound/operations`，状态端点为
`GET /northbound/operations/status`；真实部署可改变路径，但必须保持本合同字段和语义。

提交 body 必须包含：

```json
{
  "operation_identity": "wms.<domain>.<operation>@v1",
  "idempotency_key": "opaque-stable-key",
  "canonical_payload": { "...": "冻结后的业务请求" }
}
```

`canonical_payload` 的规范化 fingerprint 是同一幂等键的比较对象。WMS 必须保存其规范化结果，不得因
JSON 字段顺序、空白或 transport retry 改变比较结论。

## 2. 提交幂等与冲突

| 条件 | HTTP | 稳定错误码/结果 | WES 行为 |
| --- | --- | --- | --- |
| 首次提交被受理 | 202 | 返回可关联的受理快照 | 查询状态直至终态 |
| 同 key、同 fingerprint，仍处理中 | 409 | `IDEMPOTENCY_REQUEST_IN_PROGRESS` | 不重发业务效果，改为查询 |
| 同 key、同 fingerprint，已完成 | 200 | 原业务结果与原 `source_version` | 视为幂等重放 |
| 同 key、不同 fingerprint | 422 | `IDEMPOTENCY_CONFLICT` | 立即人工对账；绝不是暂时并发重试 |

在保留期内，同一 operation identity、幂等键和 payload 的再次提交必须始终是原请求的幂等重放。这既适用于
普通 transport retry，也适用于 WES 在持续 `NOT_FOUND` 超过宽限期后唯一一次受控恢复重提。首次提交实际未到达
WMS 时，同键同 payload 的下一次提交必须能创建请求；首次已受理但状态暂不可见时，重提不得产生第二份业务效果。

## 3. 状态查询快照

查询参数必须为 `operation_identity` 和 `idempotency_key`。`state` 只能是：
`ACCEPTED | PROCESSING | COMPLETED | REJECTED | NOT_FOUND`。

每个响应包含 `provider_reference`、`reason_code`、`updated_at`、`source_version`、`result_payload`：

| 字段 | 格式与可空性 |
| --- | --- |
| `provider_reference` | 可见状态必须为非空 opaque string；`NOT_FOUND` 为 `null`。 |
| `reason_code` | 非拒绝状态可为 `null`；`REJECTED` 必须为稳定、可枚举的非空 string。 |
| `updated_at` | 可见状态必须是带时区的 RFC 3339/ISO-8601 string；`NOT_FOUND` 为 `null`。它不承担排序语义。 |
| `source_version` | 可见状态必须是同一查询键下从 0 或正整数开始的 integer；每个新状态严格递增，幂等重放保留原版本。`NOT_FOUND` 必须为 `null`。 |
| `result_payload` | 仅 `COMPLETED` 必须存在；`REJECTED` 和所有非终态必须为 `null`，不得携带可被误认为终态的 payload。 |

`COMPLETED.result_payload` 的 schema 由 `operation_identity` 对应的 WES result model 冻结。它必须含
`accepted: true`，并使 dispatch/correlation 字段与原请求一致；若内外层同时含 `source_version`，规范化后必须一致。
`REJECTED` 不得伪造成功结果。

## 4. 保留期、可见性与回调

- `WMS retention >= WES max confirmation age + safety margin`；保留期内不得让同一幂等键重新生效。
- `WMS visibility SLA <= WES NOT_FOUND grace period`，即从受理到可按查询键看见的最大延迟不得超过宽限期。
- WES 最大确认窗口、`NOT_FOUND` 宽限期、安全余量及 WMS 承诺值均是部署参数，必须进入联调验收；本通用合同不写死工厂无关的天数。
- callback 是可选提示。若提供，仅发送关联键以触发即时状态查询；不要求在 callback 中复制终态 payload，也不授予 callback 写业务终态的权威。

## 5. 传输、安全与运维

- 认证使用部署定义的 TLS 保护方式（mTLS 或短期 Bearer/OAuth）；密钥、完整认证 header、未脱敏 body 均不得写入探针、日志、证据或报告。
- 客户端和服务端必须为提交、状态查询分别声明 deadline；超时只按本合同状态查询/对账流程处理，不能猜测业务成功。
- 429 必须带合法 `Retry-After`（delta-seconds 或 HTTP-date）；WES 以其为下一次状态查询的时间下限，不在限流窗口忙重试。
- 5xx 使用稳定、可分类错误码；最大响应体大小作为部署承诺。超过承诺、畸形 JSON 或未知状态均为合同失败。
- 所有时钟字段使用 RFC 3339/ISO-8601 的 offset-aware UTC 时间；禁止用 `updated_at` 推断状态先后，唯一排序依据是 `source_version`。
- 日志只允许 operation identity、截断/哈希后的关联键、HTTP status、稳定错误码和版本；禁止记录 payload、token、签名或未脱敏响应体。

## 6. 联调验收矩阵

| 必测项 | 通过标准 |
| --- | --- |
| 已完成请求重复提交 | 200，返回原结果与原版本。 |
| 处理中请求重复提交 | 409 + `IDEMPOTENCY_REQUEST_IN_PROGRESS`。 |
| 同 key 不同 fingerprint | 422 + `IDEMPOTENCY_CONFLICT`，不得自动重试。 |
| 保留期 | 同键在承诺保留期内不得重新生效。 |
| 提交超时 | 明确区分实际未到达与结果不明；前者同键重提可创建。 |
| 可见性 SLA 边界 | 受理后在承诺延迟内可查询；宽限期内 `NOT_FOUND` 仅重查。 |
| 状态序列 | 覆盖 `ACCEPTED`、`PROCESSING`、`COMPLETED` 的单调版本和 typed result。 |
| 拒绝/未找到 | `REJECTED` 有稳定 reason code；`NOT_FOUND` 的 version/updated_at 为空。 |
| 已受理暂不可见 | 同键重提不产生第二份业务效果。 |
| 429 / 5xx / 状态查询超时 | 分别验证 `Retry-After`、稳定错误形状及 deadline 行为。 |

联调运行 `uv run python scripts/verify_wms_northbound_feasibility.py --base-url <stub-url>`。探针仅输出脱敏的
status、稳定错误码和协议字段；全部强制项通过且双方确认后，真实 WMS 才可给出生产验收结论。
