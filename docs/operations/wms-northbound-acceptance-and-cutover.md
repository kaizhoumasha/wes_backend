# WMS 北向联调验收与整体切换记录

本文是单工厂、单 WMS Provider 的发布证据模板。WES 只验收双方可观察的 QUERY、EFFECT submit、E08–E14
status query 和可选 callback hint 交互，不记录或推断 WMS 内部工作流。2026-07-25 的开发 Mock 证据只属于当时
E08–E14 七项异步 EFFECT / 48 case 的历史 P0；当前 35 operation 发布必须重新取得目标 WMS 的 REAL_TCP 证据，
历史 Mock、仓内静态 registry 或 fixture 均不能关闭外部联调与 GO 门禁。

## 当前结论

- 2026-07-25 历史开发 Mock P0：E08–E14 七项异步 EFFECT / 48 case `PASS`，仅证明当时 Mock 基线，不代表
  当前 16 项 EFFECT、35 operation 或目标 WMS `PASS/GO`
- 2026-07-25 历史 Compose 证据：WMS image
  `sha256:ef142f2a47bd604f67c22802b22cd39b805f6f677c43e3a5e2f6e9e0e497348e`，
  ECS image `sha256:e65a6bc07c5e8150e87de43c8cf041e8dd1773acd172ca6f35383403047ab2bf`，
  容器入口/点时 digest/浮点覆盖 `5 passed`、验收镜像 live pytest `6 passed`、CLI 48 case 全部
  `passed=true`；Live 容器无源码挂载，且日志不含完整关联键与预期 deadline 断连 traceback
- 当前仓内静态能力：registry 精确覆盖 19 QUERY + 16 EFFECT，仓内 typed fixture、静态矩阵与 REAL_TCP runner
  已具备；这不是目标 endpoint 的 live 证据
- 目标 WMS 35 operation REAL_TCP 联调验收：`BLOCKED`
- 外部 WMS 观测采集证据：`BLOCKED`（六类 `wms_effect.*` 已真实发射，但尚无目标现场采集/告警证据）
- 外部 WMS 联调测试数据清理模板：`BLOCKED`
- 外部 WMS 整体切换模板：`BLOCKED`

上述历史 `PASS` 表示 `docker-compose.wms-acceptance.yml` 启动的已构建 `mock_wms_acceptance` 镜像在
2026-07-25 通过了当时 E08–E14 七项 / 48 case 的 Mock TCP 黑盒验收。它不得被扩写为当前全部 16 项 EFFECT、
35 operation 或目标 WMS 的 REAL_TCP 结论，也不得预填外部确认人、验收时间或构建版本。只有下述 35 项真实证据
完整并由四方确认后，才能改变外部模板的 `BLOCKED` 状态。

该历史 Mock P0 还证明了当时 E08–E14 合同的 `t0 / visibility_sla-1 / visibility_sla` 与
`retention-1 / retention` 按 UTC aware 时钟和精确边界工作；保留边界前同键重放不产生第二份 effect，
边界后旧记录过期且受控重提得到累计 effect=2。callback evidence 只公开 `operation_identity`、
`idempotency_key`、`dispatch_key` 与 `WMS_EFFECT_STATUS_HINT`，不携带 `COMPLETED`、`REJECTED`、`result`
或 `status` 等终态权威字段。最终状态仍必须由 `GET /northbound/operations/status` 获得。

该历史 Mock Submit/Status 直接使用 WES sandbox material-flow v1/v2 凭据引用，active 为
`secret://wms/material-flow-sandbox-hmac@v2`；没有 Mock 专用 credential。typed route
`POST /api/wms/fulfillment/full-box-exchange` 的 typed terminal result 是唯一完成事实；
`BUSINESS_COMPLETED` 必须携带 `post_exchange_relations`，`PHYSICAL_COMPLETED` 缺少该关系事实时进入资源对账。

## WMS 团队必须提供的能力

| 能力 | WMS 交付要求 | WES 验收方式 |
| --- | --- | --- |
| EFFECT submit | 对 E01–E16 接收 operation-specific typed payload；从 `X-WES-Operation-Identity` 与 `Idempotency-Key` header 读取请求身份；以 `X-WES-Content-SHA256` 校验实际 wire bytes，并在 typed schema 校验后重新生成 canonical JSON fingerprint；在幂等写入前严格拒绝 missing/extra/blank/type/非法 decimal；不得要求 WES internal frozen binding 上 wire | 对 registry 中全部 16 项 EFFECT 执行对应 completion mode 合同矩阵，验证同步 typed terminal result、异步 ACK、409、422、真实 deadline、签名及同键重放 |
| EFFECT status query | 仅对 E08–E14 按 `operation_identity + idempotency_key` 返回五态、单调 `source_version`、稳定拒绝码和 operation-specific typed result | 对 registry 中 7 项异步 EFFECT 逐项回放可见状态、终态、`NOT_FOUND`、429、5xx、超时和响应体上限；9 项同步 EFFECT 禁止伪造 status 能力 |
| QUERY | 实现 registry 中 Q01–Q19 的 operation-specific typed 请求/结果、预算、分页、数值精度、业务拒绝、429 和错误形状 | 对全部 19 项 QUERY 执行静态注册表驱动的 REAL_TCP 合同题库并保存规范化结果 |
| callback hint（可选） | 仅携带关联键，允许 WES 提前发起 status query；不提供终态权威 | 验证接收、拒绝、重复、触发查询以及 enqueue 失败后的 scanner 接管 |
| 保留与可见性 | 承诺幂等/状态记录保留期、状态可见性 SLA、最大响应体和 submit/status deadline | 验证 `retention >= WES max confirmation age + safety margin` 且 `visibility SLA <= NOT_FOUND grace` |
| 安全 | 按 active profile 实现经评审的 `HMAC_SHA256`，或在 `NONE + isolated_lan` 时接受网络隔离边界；HMAC 模式分别实现 Submit 的 `X-WES-*` 七项 canonical 与 Status query 的 `X-WMS-*` 五项 canonical | HMAC 模式分开采集两类签名证据；NONE 模式采集 VLAN/防火墙/反向代理隔离和网络 owner 确认；两者均不得记录密钥、完整 header 或未脱敏 body |

必须覆盖当前静态 registry manifest 中全部且仅有的 35 operation（19 QUERY + 16 EFFECT）。不得以历史四项样例、
E08–E14 七项 Mock、单个业务流或已通过的 operation 子集替代；manifest 缺失、重复、未知或任一项未通过均为
`BLOCKED/NO-GO`。

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

HMAC 模式下，E01–E16 每个真实 EFFECT operation 至少留存一个成功签名和一个篡改拒绝样例。只记录 header 名、
脱敏/hash 后的值和校验结论，不记录 secret、完整签名或完整业务 body。`NONE + isolated_lan` 模式不伪造签名证据，
改为引用经网络 owner 确认的隔离证据。

| Operation | Method | Path | Timestamp evidence | Nonce evidence | Typed body SHA-256 | Operation identity | Idempotency key evidence | WMS 验签结论 | Evidence ref |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
|  |  |  |  |  |  |  |  |  |  |

canonical 顺序必须是：method → path → timestamp → nonce → payload hash → operation identity →
idempotency key。对应 header 必须使用 `X-WES-*`、`X-WES-Operation-Identity` 和 `Idempotency-Key`。

### Status query 签名证据（五项 canonical）

HMAC 模式下，E08–E14 每个真实异步 EFFECT operation 至少留存一个成功签名和一个
timestamp/nonce/body hash 篡改拒绝样例。GET 空 body 的 hash 必须按原始空 bytes 计算；不得复制 Submit 的
operation identity/idempotency key 到五项 canonical。9 项同步 EFFECT 不生成 status query 签名证据。

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

registry 中全部 35 operation 必须按各自 QUERY / 同步 EFFECT / 异步 EFFECT 模式进入题库。适用的最低 case
集合为：首次受理、并发处理中同键重放、已完成同键重放、同键异 fingerprint、typed body
required/allowed/type/值域拒绝、提交真实 deadline 超时、可见性 SLA 精确边界、单调状态序列、拒绝、
`NOT_FOUND` 宽限期、受控同键重提、已见状态后丢失、保留期精确边界、流式响应体上限、429 +
`Retry-After`、固定 5xx 和状态查询超时。故障注入必须精确作用于 method/path/operation，且并发一次性故障
恰好由一个匹配请求消费。全部 16 项 EFFECT 的 terminal result 必须分别通过对应 typed result 校验；只有
E08–E14 使用五态 status 收敛。

四方确认字段：

| 角色 | 姓名 | 构建/合同确认 | 验收时间 | 结论 |
| --- | --- | --- | --- | --- |
| WES 确认人 |  |  |  |  |
| WMS 确认人 |  |  |  |  |
| 网络确认人 |  |  |  |  |
| 业务确认人 |  |  |  |  |

在上述表格为空或任一方未签字时，真实 WMS 联调验收只能是 `BLOCKED`。frozen binding revision 只能作为 WES 内部 evidence
单独核对，不得出现在发给 WMS 的 request evidence。

## 观测映射与采集门禁

六类 `wms_effect.*` 已进入 `RuntimeObservabilityRegistry` allow-list，并在 submit、status worker、
reconciliation 与 callback hint 边界真实发射。切换前仍必须在目标现场通过既有 OpenTelemetry backend 和
`northbound-operation-day1` 采集这些 signal，并以联调证据证明计数、延迟、age、退避和告警路由可用；仓内发射
能力不能替代现场采集证据。

| 目标口径 | 当前真实发射边界 | 联调采集/告警 evidence ref | 结论 |
| --- | --- | --- | --- |
| submit accepted/ambiguous/not-sent | `wms_effect.submit`：Outbox、Attempt、Reducer evidence 成功提交后 |  | `BLOCKED` |
| status state/latency/retry/age | `wms_effect.status_query`：status worker 校验 typed snapshot 后 |  | `BLOCKED` |
| backlog/batch/429/Retry-After/breaker/backoff | `wms_effect.status_backlog` / `wms_effect.status_backpressure`：scanner 与 failure 归约后 |  | `BLOCKED` |
| NOT_FOUND/exhaustion/conflict/open reconciliation | `wms_effect.recovery`：recovery/reconciliation 事务提交后 |  | `BLOCKED` |
| callback hint receive/reject/duplicate/query/enqueue degraded | `wms_effect.callback_hint`：typed callback router 与持久化 hint 调度边界 |  | `BLOCKED` |

当前没有这些真实联调采集与告警 evidence，因此观测映射与采集验证保持 `BLOCKED`。mock/replay 不能关闭该门禁，
也不能据此宣称目标现场 exporter/collector 已采集、API 已返回目标字段或新告警已配置。

## 清理前置条件与记录

联调测试数据清理不是仓库测试。执行前必须具备精确环境、数据库/租户范围、双方数据 owner、备份位置和经过
单独评审的清理清单/SQL；本仓库不提供可直接执行的破坏性脚本。

全部前置条件：

1. 真实联调验收全部 case 通过，E01–E16 EFFECT 均已终结或对账关闭。
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

### GO 前置条件

以下条件必须全部满足：

1. 目标 WMS 对 registry manifest 中全部 35 operation 完成 REAL_TCP conformance，报告绑定
   endpoint/profile digest、WES/WMS build、合同版本及 WMS 环境安全确认。
2. active profile 的 35 项 endpoint 均非 Mock、localhost、占位域名或未批准地址，零 Mock endpoint。
3. 目标工厂 workload envelope 已填写真实值，四组 workload/capacity 场景均达到四方签字的 p95/p99、
   backlog-age、锁等待、Provider QPS 和资源使用门槛。
4. 项目统一 retention owner、保留周期、执行责任和容量证据已冻结；未落统一清理能力时，数据库容量已证明覆盖
   完整规划周期。
5. `NONE + isolated_lan` 的 VLAN/防火墙/反向代理隔离范围已由网络 owner 验收；HMAC 模式的凭据和签名证据已完成。
6. WES、WMS、网络、业务四方已签署同一 GO 时间窗及剩余风险。

### 切换步骤

1. 使用唯一 active WMS Provider 配置启动，先保持真实 EFFECT admission 关闭。
2. 上述 GO 前置条件以及健康检查、配置/SLA 不变量、status query backlog/worker、观测采集/告警映射和 QUERY
   smoke preflight 全部通过后记录 GO。
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

当前 35 项 REAL_TCP、零 Mock endpoint、四组容量证据、统一 retention 责任、现场网络隔离与四方签字均未完成，
因此整体切换保持 `BLOCKED`，不得记录 GO。

## 仓内可重复证据

- 当前静态 registry / typed fixture：仓内矩阵精确覆盖 19 QUERY + 16 EFFECT，并驱动通用 conformance runner；
  只证明合同与 runner 能力，不是目标 WMS live 证据
- QUERY inventory replay：`tests/fixtures/wms_provider_conformance/query_inventory_replay.v1.json`
- E08–E14 status：由静态 registry 派生的共享 typed status contract 与测试内固定 payload 覆盖

QUERY fixture 只包含合成事实、规范化结果和 digest，可验证 WES parser/typed contract 的确定性；它不包含生产
密钥、真实业务数据或真实 WMS 验收结论。仓内全量 typed fixture 同样不能替代 35 项 REAL_TCP。E03/E07 是同步
EFFECT，不保留 status replay；E11 与 E08–E10/E12–E14 共用 registry 驱动的 status contract。
