# WMS MOCK 北向能力需求

## 1. 目的与验收真源

当前阶段不再等待外部 WMS 团队书面确认。仓库 Docker/E2E 环境实际运行的
`tests/mock/wms_mock_server.py` 是北向能力确认的唯一 WMS 真源。

以下证据不能单独形成验收 `GO`：

- `tests/contracts/wms_integration/test_wms_northbound_feasibility_probe.py` 内嵌的 FastAPI stub；
- `tests/fixtures/wms_provider_conformance/` 下的 replay fixture；
- 只验证 WES parser、adapter 或 service 的单元测试。

验收必须通过公开 HTTP 访问实际 `mock_wms`，不得读取其内部状态。

## 2. 当前实现基线

实际 `mock_wms` 已提供三个 typed EFFECT 提交接口、统一 status query、HMAC 校验、幂等记录、
operation-specific typed result、故障控制和 reset：

| Operation | Submit endpoint |
| --- | --- |
| `wms.inventory.confirm_inbound@v1` | `POST /api/wms/inventory/confirm-inbound` |
| `wms.fulfillment.full_box_exchange@v1` | `POST /api/wms/fulfillment/full-box-exchange` |
| `wms.fulfillment.notify_pkg_binding@v1` | `POST /api/wms/fulfillment/package-binding` |

Mock 直接复用 WES sandbox material-flow 的真实版本化凭据引用：
`secret://wms/material-flow-sandbox-hmac@v1` 与
`secret://wms/material-flow-sandbox-hmac@v2`，active version 为 v2。secret 分别只从
`WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V1/V2` 注入；禁止再创建 Mock 专用 credential reference
或 secret 环境变量。

## 3. P0 必须能力

### 3.1 Submit 幂等语义

三个 typed EFFECT submit endpoint 必须：

1. 从 `X-WES-Operation-Identity` 和 `Idempotency-Key` 读取请求身份；
2. 对原始 HTTP body bytes 计算 SHA-256，并校验 `X-WES-Content-SHA256`；
3. 校验 Submit 七项 canonical input 的 HMAC-SHA256；
4. 在验签成功后校验签名时间处于服务端真实时钟前后 30 秒窗口内，并以 credential reference + nonce
   做 300 秒原子去重；Submit 兼容 WES sender 的 UTC aware ISO-8601 时间戳；
5. 以 `operation_identity + idempotency_key` 为作用域保存 canonical body fingerprint；
6. 首次受理返回 HTTP 202；
7. 同 key、同 fingerprint 且仍处理中时返回 HTTP 409 和
   `IDEMPOTENCY_REQUEST_IN_PROGRESS`；
8. 同 key、同 fingerprint 且已终结时返回原业务结果，不产生第二份业务效果；
9. 同 key、不同 fingerprint 时返回 HTTP 422 和 `IDEMPOTENCY_CONFLICT`；
10. callback hint 只发送一次可关联提示，不携带或决定终态；
11. typed body 必须在创建幂等记录前按冻结 wire schema 校验 required/allowed fields、非空字符串、长度和
    `quantity` 的有限正十进制字符串；失败固定返回 HTTP 422 + `INVALID_TYPED_REQUEST`，不得留下记录或 effect；
12. 并发同 key、同 fingerprint 首次提交必须恰好一个 HTTP 202，其余为 HTTP 409，effect count 恒为 1；
    每个 HTTP attempt 必须使用独立 timestamp、nonce 和签名。

### 3.2 Status query

提供：

```text
GET /northbound/operations/status
  ?operation_identity=<identity>
  &idempotency_key=<key>
```

Status query 必须校验 `X-WMS-*` 五项 canonical input，并对 Unix 秒时间戳执行相同的 30 秒新鲜度和
300 秒 nonce 去重。响应状态只允许
`ACCEPTED | PROCESSING | COMPLETED | REJECTED | NOT_FOUND`。

可见状态包含 `provider_reference`、`updated_at` 和单调非负整数 `source_version`；
`NOT_FOUND.source_version` 为 `null`。`COMPLETED` 返回 operation-specific typed
`result_payload`；`REJECTED` 返回稳定 `reason_code` 且不携带成功结果。

### 3.3 Typed result

状态查询必须分别覆盖入库确认、满箱交换和料盘绑定。结果 schema 以
`docs/contracts/wms-northbound-interaction-contract.md` 及
`tests/fixtures/wms_provider_conformance/*_status_replay.v1.json` 为准。
入库确认的 `document_no`、满箱交换的 `exchange_request_code` 以及三个 operation 的原始关联字段
必须非空且可追溯到提交 body；禁止为了通过 schema 而返回空占位值。

### 3.4 保留、可见性、故障与重置

实际 Mock 必须公开可配置且可验证的幂等/状态保留期、可见性 SLA、最大响应体、deadline、429 +
`Retry-After`、5xx、慢响应、暂时 `NOT_FOUND`、可见后丢失和受控同键恢复重提。

时间语义以 UTC aware Mock 时钟为准：首次受理时同时冻结 `accepted_at`、`visible_at` 和 `expires_at`。
当 `now < visible_at` 时 status 返回 `NOT_FOUND`；当 `now >= visible_at` 时记录可见；当
`now >= expires_at` 时原记录原子过期，同键再次提交可创建新 effect。保留边界前的同键重放仍必须命中原记录，
不得依赖查询次数模拟时间。

故障必须以 HTTP method、精确 target path 及可选 operation identity 为作用域，并在并发请求到达第一个
`await` 前原子 claim；不匹配的 health/contract、inventory QUERY 与 legacy full-box 路由不得消费故障。
5xx 固定使用 `TEMPORARILY_UNAVAILABLE`，超大响应必须流式发送，submit/status deadline 必须由真实网络等待触发。

typed `POST /api/wms/fulfillment/full-box-exchange` 只返回 HTTP 202 和 callback hint；历史完成 callback
仅由独立的 `POST /api/wms/legacy/full-box-exchange` 触发，二者不得混用。

故障注入只存在于 Mock/测试环境。reset 必须清理 typed EFFECT 幂等记录、状态快照、HMAC nonce、
callback hint 去重记录、故障注入和可控时钟；清理后相同 idempotency key 可以作为新请求重新受理。

## 4. 完成标准

1. 黑盒探针连接验收 Compose 的实际 `mock_wms_acceptance` 并覆盖全部三个 EFFECT，不创建内嵌 stub；
2. 全部 P0 case 通过，无 `xfail`、skip 或放宽断言；
3. Docker/E2E submit/status URL 与探针公开 URL 一致；
4. `tests/mock/`、`tests/contracts/wms_integration/` 和相关 runtime contract tests 全绿；
5. reset 测试证明全部北向状态已清空；
6. 验收报告记录 Mock image digest、承诺参数、命令和结果；
7. 必须先以 `docker compose --profile dev build mock_ecs mock_wms` 构建共享镜像，再以
   `docker compose -f docker-compose.wms-acceptance.yml up -d --force-recreate mock_wms_acceptance`
   启动无源码挂载的独立验收服务；heavy/live pytest 和 CLI 探针默认通过
   `http://127.0.0.1:18011` 建立真实 TCP 连接，ASGITransport 测试不能替代该证据。

P0 完成前不新增 WMS 内部工作流模型；WES 只验收公开 submit、status query 和可选 callback hint。
