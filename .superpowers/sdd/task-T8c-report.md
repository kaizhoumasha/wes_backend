# T8c 实施报告：统一 EXTERNAL_HTTP typed transport result

## 结论

T8c 已完成。所有 `EXTERNAL_HTTP` sender 已收敛到唯一封闭 typed result，不再存在 bool sender 合同或兼容
fallback。transport 明确区分 `NOT_SENT`、`ACCEPTED` 与 `AMBIGUOUS`，并同时记录 phase、HTTP/协议结果、
安全重试结论和错误摘要。通用 `SystemOutboxEngine` 与 workline dispatcher 使用相同终态规则，所有外部 HTTP
attempt 都会保存 typed evidence。

本任务没有实现 capability semantic reducer、callback/reconciliation 生命周期、lease/fencing 新协议、credential
snapshot、WMS 专用 dispatcher 或旧数据迁移；也没有写入 `RuntimeIntentLog` 语义状态。

## 合同与状态映射

- 新增 `ExternalHttpTransportResult`、`ExternalHttpTransportOutcome`、`ExternalHttpTransportPhase` 与
  `ExternalHttpProtocolResult`；结果对象冻结且校验组合不变量。
- HTTP 2xx：`ACCEPTED + protocol ACCEPTED`，outbox/attempt 均进入 `SENT`。
- HTTP 3xx/4xx：请求已被远端明确拒绝，因此是 `ACCEPTED + protocol REJECTED`；transport outbox/attempt 仍进入
  `SENT`，不把 `SENT` 误当 capability 语义成功。
- HTTP 5xx：语义不明，归类 `AMBIGUOUS + protocol UNKNOWN`；outbox/attempt 进入 `UNKNOWN`，禁止自动重放。
- connect timeout、write/read timeout、connection reset/read/write error 与未知 sender 异常：归类 `AMBIGUOUS`，
  进入 `UNKNOWN` 且不重试。
- 只有能证明请求未离开本地边界的 `ConnectError` 才是可安全重试的 `NOT_SENT`，进入现有有界
  `RETRY_WAIT`；非法 URL、endpoint/canonical 准备失败是不可重试 `NOT_SENT`，直接进入 `FAILED`。
- sender 返回非 typed 对象时 fail closed 为 `AMBIGUOUS`，没有 bool fallback。
- EXTERNAL_HTTP 沙箱出口返回 `ACCEPTED + SANDBOX + protocol NOT_AVAILABLE`，不伪造 HTTP 状态码。

## Attempt evidence 与迁移

- `WorklineDispatchAttempt` 新增 nullable `transport_outcome`、`transport_phase`、`protocol_result`、
  `safe_to_retry`、`http_status_code`；非 HTTP 历史 attempt 保持为空。
- typed finalize 同时写入结构化列和完整 `response_json.transport`，并保存实际 outbox finalization（包括
  `sent`、`retry_wait`、`failed`、`unknown` 或 `fenced`）。
- 通过 Alembic 生成器创建 revision `8de7cb4de434`，增加五列、三项封闭枚举约束和两个索引；没有 backfill。
- 本地 PostgreSQL 已验证 upgrade、downgrade、再次 upgrade，最终版本为 `8de7cb4de434 (head)`。
- PostgreSQL attempt evidence 往返集成测试通过；`alembic check` 仍报告仓库既有大范围 schema 漂移，但过滤
  T8c 五列、约束与索引后没有新增 drift。

## 验证结果

- T8c typed result、双 dispatcher、attempt evidence 与 EFFECT 相关回归：`74 passed`。
- T8c 最终核心复验（含 SANDBOX evidence）：`24 passed`。
- PostgreSQL typed attempt evidence 集成测试：`1 passed`。
- `tests/sys` 加 dispatcher observability：`30 passed`。
- 测试拓扑守卫：`6 passed`；显式 collect-only：`3643 tests collected`。
- `./scripts/git-quality-gate.sh --profile quality`：通过（Ruff format/check、Bandit、runtime guardrails、
  import-linter、architecture guardrails、测试拓扑均通过）。
- 默认快速全集：`3635 passed, 5 skipped, 3 failed`。3 个失败与 T8b 已记录基线完全一致：cleanup matrix CSV、
  legacy matrix CSV 和 northbound inventory 的 `notify_pkg_binding` 清单漂移；与 T8c transport/attempt 路径无关。
- `rg` 审计确认代码和测试中的 EXTERNAL_HTTP bool sender 合同归零。

## 影响分析与提交边界

GitNexus 对 `WorklineDispatchAttemptBase/WorklineDispatchAttempt` 报告 MEDIUM，直接依赖分别为 7/6；
`SystemOutboxRepository` 与 `dispatch_external_http` 为 MEDIUM；dispatcher、attempt service 与其方法为 LOW。
新建的 transport 类型尚未进入索引，impact 查询返回 UNKNOWN/0；逐符号写前分析没有 HIGH/CRITICAL。

提交前 staged `gitnexus_detect_changes` 覆盖 19 个文件、72 个 changed symbols、9 个 affected processes，汇总风险为
HIGH；风险来自共享 workline/SystemOutbox `dispatch` 流程的跨社区影响面。设备分支语义未改，受影响 dispatcher、
安全检查与设备解析路径已由全量回归、相关 74 项回归和 quality profile 覆盖。

提交范围只包含 T8c 计划、实现、迁移、测试与本报告，不包含用户维护的 `AGENTS.md`、`CLAUDE.md`。
