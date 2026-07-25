# WMS 北向可行性报告（实际开发 Mock 合同门禁）

- 结论：**GO（实际开发 Mock，P0 可行性门禁已关闭）**
- 确认时间：2026-07-25
- WES owner：WES Runtime Team
- 开发 WMS stub owner：WES Mock WMS Team
- Mock/build version：`d4f80502`（`tests/mock/wms_mock_server.py`）
- WES 确认状态：实际 Mock 黑盒探针 `PASS`
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

实际 Mock 使用版本化 credential reference `secret://wms/mock-northbound-hmac@v1`，secret 仅从
`MOCK_WMS_NORTHBOUND_HMAC_SECRET_V1` 读取。它分别校验 Submit 的 `X-WES-*` 七项与 Status query 的
`X-WMS-*` 五项 HMAC canonical input，并拒绝 content hash 或签名篡改；探针不记录 credential、secret、完整签名或业务 body。

## 开发 mock 公开面与 operation 清单

| 项目 | 值 |
| --- | --- |
| confirm inbound submit | `POST /api/wms/inventory/confirm-inbound` |
| full box exchange submit | `POST /api/wms/fulfillment/full-box-exchange` |
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

运行：`uv run pytest tests/contracts/wms_integration/test_wms_northbound_feasibility_probe.py -q`。
探针以 `httpx.ASGITransport(app=tests.mock.wms_mock_server.app)` 连接实际 Mock，不导入 WES 生产 adapter、
不读取 Mock 内部状态；只经 submit/status/debug 的公开 HTTP 路由断言。所有强制 case 通过：

| case | 结果 |
| --- | --- |
| 三个 operation 的首次提交、处理中重放、已完成重放、同 key 冲突与单一 effect | PASS |
| 三个 operation 的 ACCEPTED → PROCESSING → COMPLETED、单调版本、严格 typed result 关联字段/时间/空值、REJECTED、NOT_FOUND | PASS |
| 已受理后暂时 `NOT_FOUND(null version)`、公开可见性读数预算、恢复可见和同键重放单一 effect | PASS |
| Submit content hash、Submit signature、Status signature 篡改拒绝 | PASS |
| 429 + Retry-After、5xx、总 deadline、实际超过 contract body budget 的有界读取、公开 reset | PASS |
| callback hint 首次受理仅记录一次脱敏投影；投影无终态字段，COMPLETED 只由 status 查询获得 | PASS |

探针输出只含本地 case 枚举和布尔结果；恶意远端 body 的负测已验证 stdout、stderr 和报告均不含 secret/PII/body。
