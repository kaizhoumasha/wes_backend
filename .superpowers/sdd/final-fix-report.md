# WMS Mock 北向能力最终修复报告

## 结论

2026-07-25 最终复核提出的 8 组问题已全部修复。当前开发阶段的 WMS 北向 P0 门禁为
`PASS/GO`，依据是最终源树构建出的真实 Docker Compose `mock_wms`，不是内嵌 stub 或仅
`ASGITransport` 证据。外部真实 WMS 的联调、观测采集、数据清理和生产切换门禁仍保持
`PENDING/BLOCKED`，本次结论不替代外部验收。

最终镜像：
`sha256:29be6894b99d3f66cd0b84ed5f2013a67f415446d47f31e984c0cd6170d21a05`。

## GitNexus 影响分析

- 修改前已对 `NorthboundOperationStore.submit/query/reject/reset/register_callback_hint/effect_count`、
  `resolve_mock_northbound_credential`、typed submit routes、fault middleware/control、`run_probe`
  及相关测试符号运行 upstream impact analysis。
- `NorthboundOperationStore.submit` 与 `run_probe` 为 `MEDIUM`；其它相关符号为 `LOW`。
- 所有直接 caller 均位于 Mock、探针或测试边界，未发现生产执行流；没有 `HIGH/CRITICAL` 风险。
- 实现完成后的首次 `detect-changes --scope all`：18 个已跟踪文件、194 个符号、0 条受影响执行流、
  风险 `LOW`。
- 全部报告与 live test 暂存后的最终 `detect-changes --scope staged`：20 个文件、196 个符号、
  0 条受影响执行流、风险 `LOW`。

## 最终复核问题与修复

### 1. 凭据必须复用真实 WES v1/v2

- 删除 `secret://wms/mock-northbound-hmac@v1` 与 `MOCK_WMS_NORTHBOUND_HMAC_SECRET_V1`。
- Mock allowlist 直接使用
  `secret://wms/material-flow-sandbox-hmac@v1/v2`，active version 为 v2。
- Docker 注入 `WMS_MATERIAL_FLOW_SANDBOX_HMAC_SECRET_V1/V2`。
- 新增真实 `ExternalHttpDispatchRequest` + `sign_wms_hmac_request` 到 Mock 的集成测试。

### 2. 可见性与保留期使用真实时间语义

- 每条记录在首次受理时冻结 UTC aware `accepted_at`、`visible_at`、`expires_at`。
- `now < visible_at` 返回 `NOT_FOUND`；`now >= visible_at` 可见。
- `now >= expires_at` 原子过期；边界前同键仍为重放，边界后同键可创建新 effect。
- 删除按查询次数模拟可见性的验收方式，覆盖
  `t0 / visibility_sla-1 / visibility_sla / retention-1 / retention`。

### 3. typed body 在写入前严格校验

- 三个 operation 各自冻结 required/allowed fields、字符串非空/去空格/长度约束。
- `confirm_inbound.quantity` 必须是有限正十进制字符串。
- missing、extra、blank、数字类型、`NaN`、非正数统一返回
  `422 INVALID_TYPED_REQUEST`，且不创建记录。
- typed completed result 的 `document_no`、`exchange_request_code` 和原始关联字段均非空。

### 4. 真实 Docker 与真实 deadline

- 修复 Dockerfile 复制已删除 `src/workline_runtime/*` 的问题，改为仅复制自包含
  `sandbox_catalog_bridge.py`。
- 删除 `tests.mock` 包入口对 ECS/WMS 的 eager import，使 WMS 镜像不依赖完整 WES/ECS 运行时。
- submit ambiguous timeout 使用服务端受理后的真实延迟；status timeout 使用独立真实延迟。
- 新增显式 heavy test `tests/integration/test_wms_mock_northbound_live.py`，无 skip 兜底。

### 5. 故障作用域与并发 claim

- 故障按 HTTP method、精确 target path 和可选 operation identity 匹配。
- 一次性故障在第一个 `await` 前由 `RLock` 原子 claim。
- health/contract、inventory QUERY、legacy full-box 路由不会消费北向 typed fault。
- 并发匹配请求证明恰好一个请求消费 fault，其余请求正常执行。
- 可见后丢失作为独立一次性 `NOT_FOUND` 故障，不再依赖状态推进计数。

### 6. legacy callback 与 typed callback hint 分离

- typed `POST /api/wms/fulfillment/full-box-exchange` 只返回 202/callback hint。
- 历史完成 callback 仅由
  `POST /api/wms/legacy/full-box-exchange` 触发。
- `.env.dev/.env.test` 的 legacy URL 同步到独立路由。

### 7. 固定 5xx 与流式超限响应

- 5xx 固定返回 `TEMPORARILY_UNAVAILABLE`；429 固定返回 `RATE_LIMITED` 并携带
  `Retry-After`。
- 超限 body 使用 `StreamingResponse` 分块发送，探针在真实读取过程中执行有界拒绝，不依赖
  `Content-Length`。

### 8. 并发同键重放

- 8 个并发 HTTP submit 使用同 key、同 body。
- 断言恰好 `1 × 202`、`7 × 409`、累计 effect count 为 1。
- store 的查询、提交、过期、回调 hint、拒绝与 reset 共享同一个 `RLock` 临界区。

## 终审 Important 追加波次

### Legacy 满箱完成语义

- RED：legacy route 回归期望 `BUSINESS_COMPLETED`，实际得到 `PHYSICAL_COMPLETED`。
- GREEN：`POST /api/wms/legacy/full-box-exchange` 恢复 `BUSINESS_COMPLETED`，typed route 仍保持
  HTTP 202 + hint-only。
- legacy callback 明确不携带 `post_exchange_relations`；生产消费者回归证明
  `BUSINESS_COMPLETED` 无 relations 正常完成，而 `PHYSICAL_COMPLETED` 无 relations 进入资源对账。

### 并发证据升级为真实 TCP

- RED：release evidence 守卫要求两个具名 live 并发 case，原 heavy test 与可行性报告均不存在这些证据。
- GREEN：新增
  `test_compose_mock_wms_concurrent_identical_replay_over_tcp` 和
  `test_compose_mock_wms_concurrent_fault_claim_over_tcp`。
- 最终镜像经 Docker published socket 实测：8 路同键为 `1 × 202 / 7 × 409 / effect=1`；
  双请求一次性 fault claim 为 `1 × 503 / 1 × 200`。
- 可行性报告明确给出具名 live case；ASGI 公共路由测试只保留为快速诊断证据。
- 本波次 GitNexus `detect-changes --scope all`：9 个文件、18 个符号、0 条受影响执行流、风险 `LOW`。

## TDD 证据

- 凭据 RED：真实 material-flow v1 被旧 allowlist 拒绝，真实 WES v2 sender 收到 401；GREEN：2 passed。
- typed/legacy/result RED：非法 body 被 202 接受、关联字段为空、legacy route 404，共
  `11 failed, 1 passed`；GREEN：12 passed。
- 时间 RED：store 不接受 retention/visibility 参数；GREEN：store 与公开 HTTP 精确边界测试通过。
- 故障 RED：health 被错误消费、可见后丢失未生效、超限依赖 `Content-Length`；GREEN：3 passed。
- Compose RED：Mock 未注入真实 v1/v2 secret 且仍保留旧 secret；GREEN：2 passed。
- Dockerfile RED：镜像仍复制已删除模块；GREEN：部署合同测试通过并成功构建。
- 包入口 RED：导入 WMS 时 eager import ECS 导致容器 `ModuleNotFoundError`；GREEN：独立镜像启动。
- CLI RED：直接执行脚本无法导入 `src`；GREEN：`--help` 子进程测试与实际 CLI 均通过。

## 验证结果

- 相关 Mock/contract/deployment/runtime/topology：`164 passed`。
- 默认测试收集：`4088 tests collected`。
- 默认全仓测试：`4083 passed, 5 skipped, 6 warnings`，耗时 445.67 秒。
- 最终源树 Docker heavy/live：`3 passed`，耗时 1.33 秒。
- 最终源树 CLI 黑盒探针：45 个 case 全部 `passed=true`。
- `ruff format --check`、`ruff check`：通过。
- Bandit：106754 行生产代码，0 issue。
- `./scripts/git-quality-gate.sh --profile quality`：完整通过。
- `git diff --check`：通过。

## 文档与边界

- 需求、当前实施计划、主简化计划、可行性报告和验收/切换记录均已同步最终语义与真实镜像证据。
- 开发 Mock `PASS/GO` 只关闭当前 P0 开发门禁；真实 WMS 的双方确认、采集、清理和整体切换仍未授权，
  不在本次变更范围内。
- 本次没有修改 `src/` 生产代码、数据库 schema 或 migration。

## 剩余关注

- 全仓测试的 5 个 skip 和 6 个 deprecation warning 均为既有环境/依赖行为，与本次修改无关。
- Docker Compose 健康检查仍沿用镜像级进程检查；本次 live test 通过真实合同端点确认应用可用。
