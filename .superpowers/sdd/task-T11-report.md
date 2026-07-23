# T11 实施报告：北向 Operation 可运营与安全边界

## 结论

T11 已交付北向 capability 的最小可运营闭环：`northbound-operation-slo.v1` 覆盖全部四个 authored WMS
operation，统一 observability registry 对所有新旧 signal 实施 exact allow-list 与低基数 metric projection，
QUERY evidence 和 EFFECT typed delivery result 接入 operation metric/trace，公平调度接入 backlog、queue age、
rate-limit、pause、lease contention/loss 与 UNKNOWN 平台指标；告警、Day-1 看板合同和五类 Runbook 已落库。

新增只读运维入口
`GET /api/v1/workline/runtime-operations/northbound`，严格遵循 API → Query Service → Repository：
专用权限 `sys:runtime-operations:view`，普通用户按 `WorkLine.created_by` 限定 owner tenant scope，
超级管理员使用显式 `PLATFORM` scope；跨租户拒绝和允许读取均写安全审计。Repository 只读取 typed columns，
不读取 payload、header、trace、credential reference 或业务键。

出站凭据 Provider 统一由脱敏审计 wrapper 包装，只发射闭集 provider kind 和 resolution outcome，
不记录 secret ref、secret material、header 或原始异常文本。未引入兼容接口、业务 operation 迁移、
业务数据库 migration、Jenkins 或 GitLab 变更。

## SLO、指标与 Trace

- SLO catalog 覆盖：
  - `wms.inventory.query_inventory@v1`
  - `wms.inventory.confirm_inbound@v1`
  - `wms.fulfillment.notify_pkg_binding@v1`
  - `wms.fulfillment.full_box_exchange@v1`
- 统一 30 天窗口、99.5% 可用性、UNKNOWN 比例上限 0.1%、open reconciliation age 15 分钟；
  operation p95 延迟目标为 1.5s / 2s / 2s / 3s，burn rate 阈值为 14.4 / 6.0 / 3.0。
- Provider binding authoring 必须命中版本化 SLO catalog；缺失条目 fail-closed。
- Operation metric label 仅允许
  `capability_identity / operation_identity / provider_profile_identity / outcome / policy_version`；
  profile 与 outcome 都是闭集。
- Span/log 保留已验证的 trace/correlation/evidence；metric 只导出闭集 label 与非负有限数值。
  payload、tenant、用户、业务键、trace/correlation/evidence、credential ref、bucket 不得成为 metric label。
- Trace stage 闭集覆盖
  `PLUGIN_EXECUTION → QUERY_EVIDENCE → POLICY_DECISION → RUNTIME_INTENT_LOG → DISPATCH_ATTEMPT → CALLBACK → RECONCILIATION`。
- Dispatcher health 输出 backlog、active lease、UNKNOWN、oldest queue age、rate-limited/paused/contended bucket
  数和 lease loss 数；具体 bucket 值不进入指标。

## Exact allow-list 与 secret redaction

`RuntimeObservabilitySignal` 现在显式声明 required/allowed/fixed attributes、metric labels、metric measurements 和
allowed values。Registry 对未知 signal、额外属性、固定属性覆盖、非标量、非法枚举、NaN/无穷/负测量、
敏感字段名和敏感字符串值全部 fail-closed。

`AuditedVersionedCredentialProvider` 包装 environment/custom Provider，审计结果仅包含：

- provider kind：`environment / custom`
- outcome：`RESOLVED / REVOKED / RESOLUTION_FAILED / PROVIDER_ERROR`

原始异常继续按原类型抛出，观测失败不改变凭据解析结果；secret/header/ref 不进入日志、metric、evidence 或 API。

## 只读运维入口与审计

- API 仅组装认证 principal 并调用 Query Service，不直接访问 Repository/Database。
- Query Service 校验 owner scope、选择 `WORKLINE_OWNER/PLATFORM` scope、调用 Repository，并在允许/拒绝路径提交审计。
- Repository 按 owner + 可选 workline 范围聚合 SystemOutbox 状态、lease、queue age、UNKNOWN，
  再关联 open reconciliation 和 latest readiness；无 payload/trace 授权或读取。
- 公开响应只包含稳定 provider/operation identity 与聚合 SLI，不包含行级 evidence 或秘密。
- PostgreSQL 集成测试使用同一 operation/profile 下两个不同 owner 的 outbox，确认普通租户只得到自身一条 backlog，
  并读取最新 readiness verdict。

## 告警、看板与 Runbook

Day-1 看板 `northbound-operation-day1` 定义 operation outcome、query latency、evidence failure、
shadow/readiness、outbox backlog/age、rate limit/pause、lease steal/loss、UNKNOWN/reconciliation、
callback duplicate/contradiction 和 credential resolve 面板。

五个静态告警均绑定 owner、看板和 checked-in Runbook anchor：

- `northbound-slo-fast-burn` → pause/resume
- `northbound-unknown-ratio` → UNKNOWN/reconciliation
- `northbound-credential-revoked` → credential revoked
- `northbound-lease-loss` → lease/fencing
- `northbound-callback-contradiction` → callback diagnostics

Runbook 明确禁止复制 secret ref、Authorization、签名 header 或 payload，并要求 UNKNOWN 只能经 typed evidence 与
`ReconciliationCase` 收口。

## Inventory 与 T9/T10 AST 门禁收口

- `NBWMS-041` 与 `NBWMS-123` 的 `metric_path` 已指向中央
  `src/app/runtime/orchestration/operation_observability.py`，精确 operation identity 可被 inventory AST scanner
  识别。两行保留到 T12 完成对应真实业务迁移，不以 T11 指标冒充业务迁移完成。
- 通用 QUERY transport 从 binding 的 `contract.identity` 调用 generic emitter，不引入 operation switch。
- 按主任务要求，同时收口 T9/T10 两个 effect adapter：
  `ConfirmInboundEffectAdapter` 与 `NotifyPackageBindingEffectAdapter` 去除
  `**frozen_binding.as_persisted_fields()` 动态展开，逐字段传递 scheduling identity、profile/binding hash、
  target snapshot/hash、auth scheme 和 credential reference；不放宽 AST guard。

## TDD、回归与 PostgreSQL 17

TDD 首轮新增 19 项合同测试均按预期失败；最小实现后转绿，随后新增 missing-SLO activation guard 并完成
RED → GREEN。最终验证：

- T11 新增核心测试：20 passed。
- QUERY transport 边界与合同：44 passed。
- effect adapter AST 门禁与相关 typed effect/consumer/applier：33 passed。
- 测试目录拓扑：6 passed。
- 默认显式收集：3822 tests collected。
- 第二轮完整默认回归：`3817 passed, 5 skipped`，0 failed，耗时 403.55s。
- `./scripts/git-quality-gate.sh --profile quality`：通过。
  - 1053 files Ruff format check；
  - Ruff lint 全绿；
  - Bandit 105196 行、0 issues；
  - runtime toggle/readiness/production closure；
  - 345 项 runtime contract guardrails；
  - 11 项 process naming；
  - import-linter；
  - enforced architecture 0 violations；
  - topology 6 passed。

PostgreSQL 使用精确命名的独立 `timescale/timescaledb:latest-pg17` 容器，版本 17.6、独立端口与测试库：

- 空库 `alembic upgrade head` 成功；
- `tests/integration/test_northbound_operations_postgresql.py`：1 passed；
- 测试后只停止并删除本次容器。

## GitNexus 风险与变更检测

写前 impact 结果：

- `RuntimeObservabilityRegistry.validate/emit`：CRITICAL；覆盖 callback/device/outbox/runtime inbox 等 6 个模块。
  因此所有旧 signal 同步迁移到统一 exact allow-list/metric projection，并以完整默认测试和 quality gate 验证。
- `dispatch_external_http`：CRITICAL；12 个直接上游、6 个模块。实现只在 typed result 固化后 best-effort 发射，
  未改变发送、retry、lease、UNKNOWN 或事务语义。
- `WmsQueryTransportExecutor`：MEDIUM；30 个上游、7 个直接依赖。保持通用 transport 无 operation switch。
- 两个 effect adapter：LOW；各 9 个上游、4 个直接依赖。
- 其余已索引生产 symbol 为 LOW/MEDIUM；新增 symbol 在刷新前部分返回 UNKNOWN，未跳过已有 symbol 的写前分析。

最终 unstaged GitNexus detect 为 MEDIUM，识别 104 个 changed symbols、5 个 dispatch process。
风险集中于已预告的 registry/dispatch 观测接线；完整回归、质量门禁与 typed result/lease 合同均通过。
提交明确排除用户维护中的 `AGENTS.md`、`CLAUDE.md`。最终 staged GitNexus detect 为 MEDIUM，
识别 31 个 staged files、98 个 changed symbols、5 个 dispatch processes；没有超出 T11 observability/security
及主任务明确要求的两个 effect adapter AST 门禁收口范围。

## Review P1：cleanup matrix 同步

Architecture review 发现 T11 新增的
`src/app/workline/v1/runtime_operations.py:route_get_L37` 已进入 `parse_entries()`，而已提交 CSV 仍为 605 条，
导致 generated matrix contract 的 key 集合不一致。

通过仓库唯一入口 `uv run python scripts/generate_legacy_matrix.py` 重建 CSV，没有手工添加 CSV 行、修改生成器、
allowlist 或 guard。新 route entry 为：

- `entry_type=api_route`
- `current_owner=workline`
- `strategy=keep-contract`
- `drop_phase=phase5-tech`
- `risk=LOW`
- `phase4_carrier=False`

矩阵派生统计同步为 606 entries、22 api routes、253 keep-contract、264 phase5-tech、418 workline entries。
Phase-4 carrier 仍为 110，新增 route 不属于 business legacy scope，因此
`business-legacy-absence-ledger.csv/.md` 的 entry 集合与统计无需修改。

P1 验证：

- matrix / absence / ledger / closure 定向合同：33 passed；
- business legacy absence final gate：通过；
- 完整 `tests/architecture`：390 passed，1 skipped；
- 完整 `./scripts/git-quality-gate.sh --profile quality`：通过。
- staged GitNexus detect：3 files、9 document symbols、0 affected processes、LOW。
