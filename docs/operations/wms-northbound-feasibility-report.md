# WMS 北向可行性报告（实际开发 Mock 合同门禁）

- 结论：**GO（实际开发 Mock，P0 可行性门禁已关闭）**
- 确认时间：2026-07-25
- WES owner：WES Runtime Team
- 开发 WMS stub owner：WES Mock WMS Team
- Mock/build version：Docker image
  `sha256:29be6894b99d3f66cd0b84ed5f2013a67f415446d47f31e984c0cd6170d21a05`
- WES 确认状态：实际 Compose Mock TCP 黑盒探针 `PASS`
- 开发 Mock WMS 确认状态：三个 typed EFFECT 公开 HTTP 路由 `PASS`
- 依据：[WMS 北向最小交互合同](../contracts/wms-northbound-interaction-contract.md)

## 范围与限制

用户已明确开发阶段 WMS 能力由 mock 提供。本报告的 GO 仅证明开发 mock 能以 HTTP 黑盒方式满足最小合同，
不等同真实 WMS 书面确认或生产准入。真实 WMS 的 endpoint、认证、operation 清单、保留期、SLA、响应大小、
限流承诺及双方签字，必须在 Task 9 重新验收；未完成前不得把本报告解释为真实 WMS 的 GO。

## 承诺参数（开发 mock）

| 参数 | 值 | 门槛 |
| --- | ---: | --- |
| WMS 幂等记录保留期 | 9 秒 | `/northbound/contract.idempotency_retention_seconds` |
| WMS 状态可见性 SLA | 2 秒 | `/northbound/contract.status_visibility_sla_seconds` |
| Submit deadline | 2 秒 | `/northbound/contract.submit_deadline_seconds` |
| Status deadline | 2 秒 | `/northbound/contract.status_deadline_seconds` |
| 最大响应体 | 4096 bytes | `/northbound/contract.max_response_bytes` 与有界读取负测 |

实际 Mock 直接复用 WES sandbox material-flow 的真实版本化 credential reference：
`secret://wms/material-flow-sandbox-hmac@v1` 与
`secret://wms/material-flow-sandbox-hmac@v2`，active version 为 v2。secret 仅从
`WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V1/V2` 读取，不存在 Mock 专用 credential。Mock 分别校验
Submit 的 `X-WES-*` 七项与 Status query 的 `X-WMS-*` 五项 HMAC canonical input，并拒绝 content hash
或签名篡改；探针不记录 credential、secret、完整签名或业务 body。

## 开发 mock 公开面与 operation 清单

| 项目 | 值 |
| --- | --- |
| confirm inbound submit | `POST /api/wms/inventory/confirm-inbound` |
| full box exchange submit | `POST /api/wms/fulfillment/full-box-exchange` |
| legacy full box exchange | `POST /api/wms/legacy/full-box-exchange`，仅该路由发送 `BUSINESS_COMPLETED` 历史完成 callback |
| package binding submit | `POST /api/wms/fulfillment/package-binding` |
| status endpoint | `GET /northbound/operations/status` |
| 公开效果观察面 | `GET /debug/northbound/effects`，仅返回 effect count |
| Mock-only 控制面 | `POST /debug/northbound/faults`、`/debug/northbound/visibility`、`/debug/northbound/reject`、`/debug/northbound/clock`、`/debug/reset` |
| Mock-only callback evidence | `GET /debug/northbound/callback-hints`，仅含关联键与 callback type 的脱敏投影 |
| operation | `wms.inventory.confirm_inbound@v1`、`wms.fulfillment.full_box_exchange@v1`、`wms.fulfillment.notify_pkg_binding@v1` |

`/debug/northbound/*` 与 `/debug/reset` 只供开发 Mock 探针使用，绝不属于未来外部 WMS 接口。

Submit wire 与当前 sender 一致：HTTP body 直接是 typed operation payload；
`X-WES-Operation-Identity` 和 `Idempotency-Key` 是 header。Mock 不接收 `canonical_payload` 外层包络或
`frozen_binding`；frozen binding 只在 WES 内部保证重试使用原 endpoint/credential revision。本开发 mock 对
canonical raw body bytes 计算并校验 `X-WES-Content-SHA256`，幂等 fingerprint 也以该 hash 比较，不按解析后的
dict 相等性判断。实际 Mock 已验证 Submit 七项与 Status query 五项换行 canonical input；未来外部 WMS 仍须
分别保留同等签名证据，不能用本报告替代其认证兼容验收。

## 黑盒探针证据

2026-07-25 先运行快速合同测试，再显式构建并启动实际 Docker Compose 服务：

- `uv run pytest tests/contracts/wms_integration/test_wms_northbound_feasibility_probe.py -q`；
- `docker compose --profile dev up -d --build mock_wms`；
- `WMS_NORTHBOUND_LIVE_BASE_URL=http://127.0.0.1:8011 WMS_NORTHBOUND_LIVE_TIMEOUT_SECONDS=0.25
  uv run pytest tests/integration/test_wms_mock_northbound_live.py -q`，结果 `3 passed`；
- `uv run python scripts/verify_wms_northbound_feasibility.py
  --base-url http://127.0.0.1:8011 --timeout-seconds 0.25`，结果 45 个 case 全部 `passed=true`。

快速测试用 `ASGITransport` 提供诊断速度；最终 `GO` 依据后两项真实 TCP 黑盒证据。探针不读取 Mock 内部状态，
只经 submit/status/debug 的公开 HTTP 路由断言。并发证据由具名 live case
`test_compose_mock_wms_concurrent_identical_replay_over_tcp` 与
`test_compose_mock_wms_concurrent_fault_claim_over_tcp` 直接通过 Docker published socket 采集，不再借用
ASGI 公共路由测试代替。所有强制 case 通过：

| case | 结果 |
| --- | --- |
| 三个 operation 的首次提交、并发同键重放、已完成重放、同 key 冲突与单一 effect | PASS |
| 三个 operation 的 required/extra/blank/type/finite-positive typed body 负测，失败无记录 | PASS |
| 三个 operation 的 ACCEPTED → PROCESSING → COMPLETED、单调版本、非空 typed result 关联字段、REJECTED、NOT_FOUND | PASS |
| `t0 / visibility_sla-1 / visibility_sla` 可见性边界、`retention-1 / retention` 过期边界与边界后 effect=2 | PASS |
| Submit content hash、真实 WES sender/signature、Status signature 篡改拒绝 | PASS |
| 精确 path/method/operation fault scope；并发匹配请求恰好一个 claim，health/inventory/legacy 不消费 | PASS |
| 429 + Retry-After、固定 5xx、真实 submit/status deadline、流式超限 body、可见后丢失、公开 reset | PASS |
| callback hint 首次受理仅记录一次脱敏投影；投影无终态字段，COMPLETED 只由 status 查询获得 | PASS |

探针输出只含本地 case 枚举和布尔结果；恶意远端 body 的负测已验证 stdout、stderr 和报告均不含 secret/PII/body。
