# WMS 北向联调验收与整体切换记录

本文是单工厂、单 WMS Provider 的发布证据模板。WES 只验收双方可观察的 submit、status query 和可选
callback hint 交互，不记录或推断 WMS 内部工作流。当前 P0 验收源是仓库内实际 Mock；未来外部 WMS 的联调、
数据清理和整体切换仍保留为后续模板，不阻塞当前开发 Mock 能力门禁。

## 当前结论

- 实际开发 Mock 验证：`PASS/GO`（当前 P0 门禁已关闭）
- 实际 Compose 验证：WMS image
  `sha256:ef142f2a47bd604f67c22802b22cd39b805f6f677c43e3a5e2f6e9e0e497348e`，
  ECS image `sha256:e65a6bc07c5e8150e87de43c8cf041e8dd1773acd172ca6f35383403047ab2bf`，
  容器入口/点时 digest/浮点覆盖 `5 passed`、验收镜像 live pytest `6 passed`、CLI 48 case 全部
  `passed=true`；Live 容器无源码挂载，且日志不含完整关联键与预期 deadline 断连 traceback
- 外部 WMS 联调验收模板：`PENDING`（后续，不阻塞当前 Mock 验收）
- 外部 WMS 观测映射与采集模板：`BLOCKED`
- 外部 WMS 联调测试数据清理模板：`BLOCKED`
- 外部 WMS 整体切换模板：`BLOCKED`

`PASS/GO` 表示 `docker-compose.wms-acceptance.yml` 启动的已构建 `mock_wms` 镜像已通过三类 typed EFFECT
的真实 TCP 黑盒合同验收，当前开发
Mock 能力可以进入后续 WES 开发。不得把该结论替代未来外部 WMS 的联调验收，也不得预填外部确认人、验收时间或构建版本。
只有下述真实证据完整、双方确认且清理目标经过单独审查后，才能改变外部模板的 `PENDING/BLOCKED` 状态。

实际 Mock 的 `PASS/GO` 已额外证明：`t0 / visibility_sla-1 / visibility_sla` 与
`retention-1 / retention` 按 UTC aware 时钟和精确边界工作；保留边界前同键重放不产生第二份 effect，
边界后旧记录过期且受控重提得到累计 effect=2。callback evidence 只公开 `operation_identity`、
`idempotency_key`、`dispatch_key` 与 `WMS_EFFECT_STATUS_HINT`，不携带 `COMPLETED`、`REJECTED`、`result`
或 `status` 等终态权威字段。最终状态仍必须由 `GET /northbound/operations/status` 获得。

Mock Submit/Status 直接使用 WES sandbox material-flow v1/v2 凭据引用，active 为
`secret://wms/material-flow-sandbox-hmac@v2`；没有 Mock 专用 credential。typed route
`POST /api/wms/fulfillment/full-box-exchange` 不发送历史完成 callback，后者仅保留在独立 legacy route。
legacy callback 固定为 `BUSINESS_COMPLETED`，即使不携带 `post_exchange_relations` 也保持既有生产消费语义；
`PHYSICAL_COMPLETED` 缺少该关系事实时仍进入资源对账。

## WMS 团队必须提供的能力

| 能力 | WMS 交付要求 | WES 验收方式 |
| --- | --- | --- |
| EFFECT submit | HTTP body 接收 operation-specific typed payload；从 `X-WES-Operation-Identity` 与 `Idempotency-Key` header 读取请求身份，以 `X-WES-Content-SHA256` 比较 fingerprint；在幂等写入前严格拒绝 missing/extra/blank/type/非法 decimal；不得要求 WES internal frozen binding 上 wire | 按合同矩阵验证 202、200、409、422、并发恰好一个首次受理、真实 deadline 超时、HMAC canonical input 及重复提交 |
| EFFECT status query | 按 `operation_identity + idempotency_key` 返回五态、单调 `source_version`、稳定拒绝码和 operation-specific typed result | 对三类 EFFECT 逐项回放可见状态、终态、`NOT_FOUND`、429、5xx、超时和响应体上限 |
| QUERY | 实现 `wms.inventory.query_inventory@v1` 的预算、分页、数值精度、业务拒绝、429 和错误形状 | 执行 QUERY 合同题库并保存规范化结果 |
| callback hint（可选） | 仅携带关联键，允许 WES 提前发起 status query；不提供终态权威 | 验证接收、拒绝、重复、触发查询以及 enqueue 失败后的 scanner 接管 |
| 保留与可见性 | 承诺幂等/状态记录保留期、状态可见性 SLA、最大响应体和 submit/status deadline | 验证 `retention >= WES max confirmation age + safety margin` 且 `visibility SLA <= NOT_FOUND grace` |
| 安全 | 提供 TLS 传输保护和 HMAC-SHA256 凭据交付流程；分别实现 Submit 的 `X-WES-*` 七项 canonical 与 Status query 的 `X-WMS-*` 五项 canonical | 分开采集两类签名证据；仅记录认证方式、凭据引用版本和脱敏/hash 后事实，不得出现密钥、完整 header 或未脱敏 body |

必须覆盖的 operation：

- `wms.inventory.query_inventory@v1`
- `wms.inventory.confirm_inbound@v1`
- `wms.fulfillment.full_box_exchange@v1`
- `wms.fulfillment.notify_pkg_binding@v1`

## 真实联调验收记录模板

以下字段只能从目标联调环境和发布构建采集，空白表示未验收。

| 发布事实 | 真实值 |
| --- | --- |
| Provider identity |  |
| Contract version |  |
| WES 构建版本 |  |
| WMS 构建版本 |  |
| 联调环境标识 |  |
| WES max confirmation age |  |
| WES `NOT_FOUND` grace |  |
| Safety margin |  |
| WMS 幂等记录保留期 |  |
| WMS 状态记录保留期 |  |
| WMS 状态可见性 SLA |  |
| Submit/status deadline |  |
| 最大响应体 |  |

### Submit 签名证据（七项 canonical）

每个真实 EFFECT operation 至少留存一个成功签名和一个篡改拒绝样例。只记录 header 名、脱敏/hash 后的值和校验
结论，不记录 secret、完整签名或完整业务 body。

| Operation | Method | Path | Timestamp evidence | Nonce evidence | Typed body SHA-256 | Operation identity | Idempotency key evidence | WMS 验签结论 | Evidence ref |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
|  |  |  |  |  |  |  |  |  |  |

canonical 顺序必须是：method → path → timestamp → nonce → payload hash → operation identity →
idempotency key。对应 header 必须使用 `X-WES-*`、`X-WES-Operation-Identity` 和 `Idempotency-Key`。

### Status query 签名证据（五项 canonical）

每个真实 EFFECT operation 至少留存一个成功签名和一个 timestamp/nonce/body hash 篡改拒绝样例。GET 空 body 的
hash 必须按原始空 bytes 计算；不得复制 Submit 的 operation identity/idempotency key 到五项 canonical。

| Operation | Method | Raw path | Timestamp evidence | Nonce evidence | Actual request body SHA-256 | WMS 验签结论 | Evidence ref |
| --- | --- | --- | --- | --- | --- | --- | --- |
|  |  |  |  |  |  |  |  |

canonical 顺序必须是：method → path → timestamp → nonce → body hash。对应 header 必须使用 `X-WMS-*`。
这里的 path 是包含原始 query string 的 HTTP raw path；`operation_identity` 和 `idempotency_key` 按 status query
parameter 传递，并通过该 raw-path 值共同被签名，但不是额外的第六、第七项 canonical 字段。

### 业务验收 case 证据

每个验收 case 都必须记录下列字段；签名事实引用上述两张独立表，失败证据只能保留有界、脱敏后的协议事实。

| Operation | Case ID | Typed body canonical hash | Submit/Status 签名 evidence ref | HTTP/status result | 规范化结果 | 耗时 | 脱敏 evidence ref | 结论 |
| --- | --- | --- | --- | --- | --- | ---: | --- | --- |
|  |  |  |  |  |  |  |  |  |

最低 case 集合为：首次受理、并发处理中同键重放、已完成同键重放、同键异 fingerprint、typed body
required/allowed/type/值域拒绝、提交真实 deadline 超时、可见性 SLA 精确边界、单调状态序列、拒绝、
`NOT_FOUND` 宽限期、受控同键重提、已见状态后丢失、保留期精确边界、流式响应体上限、429 +
`Retry-After`、固定 5xx 和状态查询超时。故障注入必须精确作用于 method/path/operation，且并发一次性故障
恰好由一个匹配请求消费。三类 EFFECT 的 `COMPLETED` 必须分别通过对应 typed result 校验。

双方确认字段：

| 角色 | 姓名 | 构建/合同确认 | 验收时间 | 结论 |
| --- | --- | --- | --- | --- |
| WES 确认人 |  |  |  |  |
| WMS 确认人 |  |  |  |  |

在上述表格为空时，真实 WMS 联调验收只能是 `PENDING`。frozen binding revision 只能作为 WES 内部 evidence
单独核对，不得出现在发给 WMS 的 request evidence。

## 观测映射与采集门禁

`wms_effect.*` 是本次联调要求的目标采集口径，不是当前生产代码已经发射的 signal。切换前必须把目标口径逐项
映射到当前可执行真源（operation signal、Outbox/DispatchAttempt、RuntimeIntentLog 状态查询字段、
callback ingress/调度结果、ReconciliationCase），并用联调采集证据证明计数、延迟、age、退避和告警路由可用。

| 目标口径 | 当前可执行真源 | 联调采集/告警 evidence ref | 结论 |
| --- | --- | --- | --- |
| submit accepted/ambiguous/not-sent | DispatchAttempt + bridge 分类 + authored operation signal |  |  |
| status state/latency/retry/age | RuntimeIntentLog 状态确认字段 + status worker 结果 |  |  |
| backlog/batch/429/Retry-After/breaker/backoff | status claim/worker、query evidence 与 breaker 状态 |  |  |
| NOT_FOUND/exhaustion/conflict/open reconciliation | RuntimeIntentLog + ReconciliationCase |  |  |
| callback hint receive/reject/duplicate/query/enqueue degraded | callback ingress、持久化到期调度与 enqueue/scanner 结果 |  |  |

当前没有这些真实联调采集与告警 evidence，因此观测映射与采集验证保持 `BLOCKED`。mock/replay 不能关闭该门禁，
也不能据此宣称 `wms_effect.*` 已在生产发射、API 已返回目标字段或新告警已配置。

## 清理前置条件与记录

联调测试数据清理不是仓库测试。执行前必须具备精确环境、数据库/租户范围、双方数据 owner、备份位置和经过
单独评审的清理清单/SQL；本仓库不提供可直接执行的破坏性脚本。

全部前置条件：

1. 真实联调验收全部 case 通过，三个 EFFECT 均已终结或对账关闭。
2. 停止联调任务及对应 Celery worker，并证明双方没有新的 EFFECT receipt。
3. 备份必要的脱敏诊断日志，解析 Intent、Outbox、inbox、reconciliation 及历史表的依赖顺序。
4. WES 与 WMS 数据 owner 共同确认精确删除目标、恢复方法和执行窗口。
5. 观测映射与采集门禁通过；清理后运行空环境 migration、健康检查、配置检查、backlog/worker 检查和一个
   QUERY smoke case。

| 清理事实 | 真实值 |
| --- | --- |
| 环境与数据范围 |  |
| 备份 evidence ref |  |
| 审批后的清理清单/SQL ref |  |
| 执行人与复核人 |  |
| 开始/完成时间 |  |
| 清理后残留检查 |  |

当前没有这些前置条件，因此联调测试数据清理保持 `BLOCKED`。

## 整体切换与回退边界

1. 使用唯一 active WMS Provider 配置启动，先保持真实 EFFECT admission 关闭。
2. 通过健康检查、配置/SLA 不变量、status query backlog/worker、观测采集/告警映射和 QUERY smoke
   preflight 后记录 GO。
3. GO 后才开放真实 EFFECT admission。首个真实 EFFECT 离开 WES 本地边界或结果不明确即越过不可逆点。
4. 首个真实 EFFECT 前，只有在双方零 receipt 有证据时才允许回退部署和 migration。
5. 首个真实 EFFECT 后发生故障时，立即关闭新 EFFECT admission；保持 status query、callback hint、租约恢复和
   reconciliation worker 运行，保留当前 schema/账本并 forward-fix。禁止 downgrade、清空在途数据或恢复
   callback 终态权威。

| Cutover 事实 | 真实值 |
| --- | --- |
| Preflight evidence ref |  |
| EFFECT admission 初始状态 |  |
| 双方零 receipt evidence ref |  |
| GO 确认人与时间 |  |
| 首个真实 EFFECT dispatch/receipt evidence ref |  |
| 不可逆点时间 |  |
| 失败时 admission 关闭及 forward-fix 记录 |  |

当前真实联调和清理均未完成，因此整体切换保持 `BLOCKED`，不得记录 GO。

## 仓内可重复证据

- QUERY：`tests/fixtures/wms_provider_conformance/query_inventory_replay.v1.json`
- 入库确认状态：`tests/fixtures/wms_provider_conformance/confirm_inbound_status_replay.v1.json`
- 满箱交换状态：`tests/fixtures/wms_provider_conformance/full_box_exchange_status_replay.v1.json`
- 料盘绑定状态：`tests/fixtures/wms_provider_conformance/notify_pkg_binding_status_replay.v1.json`

这些 fixture 只包含合成事实、规范化结果和 digest，可验证 WES parser/typed contract 的确定性；它们不包含生产
密钥、真实业务数据或真实 WMS 验收结论。
