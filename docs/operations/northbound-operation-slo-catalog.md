# 北向 Operation SLO 与 Day-1 看板

> 状态：`implementation_baseline`。本目录只说明当前仍运行的北向观测实现；WMS 目标合同以顶层 SPEC 和薄接入计划为准。

本文是 `northbound-operation-slo.v1` 的运维视图；可执行真源位于
`src/app/runtime/orchestration/operation_observability.py`。任何新增 authored operation 必须先登记 SLO，
否则 provider binding authoring fail-closed。

## SLO 目录

| Operation identity | 30 天可用性 | p95 延迟 | UNKNOWN 上限 | 对账 age 上限 | Owner |
| --- | ---: | ---: | ---: | ---: | --- |
| `wms.inventory.query_inventory@v1` | 99.5% | 1.5s | 0.1% | 15min | runtime-platform |
| `wms.inventory.confirm_inbound@v1` | 99.5% | 2.0s | 0.1% | 15min | runtime-platform |
| `wms.fulfillment.notify_pkg_binding@v1` | 99.5% | 2.0s | 0.1% | 15min | runtime-platform |
| `wms.fulfillment.request_rack_supply@v1` | 99.5% | 3.0s | 0.1% | 15min | runtime-platform |

上表展示查询、同步 EFFECT 与异步 EFFECT 的代表性条目；可执行目录按当前 31 项静态 WMS registry 全量生成，
不得用本文示例行替代机器可读目录。

可用性成功样本为 `SUCCESS`；`BUSINESS_REJECT` 单独展示，不吞入技术错误。
技术/合同失败和 UNKNOWN 消耗错误预算，UNKNOWN 同时受独立比例与 reconciliation age 门禁。
多窗口 burn rate 阈值为 14.4 / 6.0 / 3.0；告警统一链接看板
`northbound-operation-day1` 与
[`northbound-operation-observability.md`](../runbooks/northbound-operation-observability.md)。

## 当前可执行观测面

当前 `operation_observability.py` 按 31 项静态 WMS registry 登记 `northbound.operation.*` authored signal、六类 outcome、延迟、
sample/UNKNOWN，以及下文五个静态告警。只读 API 当前只返回
`NorthboundOperationHealth` 的 provider/operation/mode、backlog、active lease、UNKNOWN、oldest queue age、
rate-limited、lease loss 和 open reconciliation 聚合。

| 面板 | 数据口径 | 分组/限制 |
| --- | --- | --- |
| Operation call / outcome | `sample_count`、六类闭集 outcome | operation + 唯一部署 profile |
| Query latency | `latency_ms` p50/p95/p99 | operation + 唯一部署 profile |
| Evidence failure | `wms_evidence.persistence_failure` | capability + policy version |
| Outbox health | backlog、oldest queue age、active lease | 仅平台计数，不展示 bucket |
| Rate limit / pause | 命中 bucket 数、暂停 bucket 数 | 不把 bucket 值作为 label |
| Lease steal/loss | contention 与 stale lease loss | capability + policy version |
| UNKNOWN / reconciliation | UNKNOWN ratio、open case 数、oldest age | operation + 唯一部署 profile |
| Credential resolve | resolved/revoked/failure/provider error | provider kind + 闭集 outcome |

## 联调采集映射与切换前目标

以下是 Task 9 的目标采集映射，不表示 Day-1 看板、registry signal、API 字段或告警已经实现。统一运营看板和新增
production instrumentation 明确不在本 Task 范围；联调必须从现有账本/worker/adapter 生成脱敏 evidence，或另行
实现并验证映射后，才能关闭切换门禁。

| 目标口径 | 联调证据来源 | 切换前验证 |
| --- | --- | --- |
| EFFECT submit accepted/ambiguous/not-sent 数量与延迟 | DispatchAttempt、submit bridge、authored operation signal | 三类分类与 latency 可复算 |
| EFFECT status 五态、latency/retry/age | RuntimeIntentLog 状态字段与 status worker 结果 | 五态、重试与 age 对账一致 |
| status backlog/batch | claim repository/worker | 数量、最大 age、领取量和批次耗时可复算 |
| 429/`Retry-After`/circuit-open/backoff | query evidence、adapter/breaker 结果 | 实际退避不早于下限 |
| `NOT_FOUND`/exhaustion/conflict/reconciliation | RuntimeIntentLog + ReconciliationCase | 数量与 case 可逐项关联 |
| callback hint | callback ingress、到期调度、enqueue/scanner 结果 | 接收/拒绝/重复/触发查询/降级可复算 |

所有当前和目标口径都不得接受 tenant、用户 ID、业务单号、payload、trace/correlation/evidence、credential ref
或 bucket 作为 metric label。行级诊断只能走受控 evidence 入口。

## 只读运维入口

- 路径：`GET /api/v1/workline/runtime-operations/northbound`
- 权限：`sys:runtime-operations:view`
- 普通用户：仅 `WorkLine.created_by == 当前用户`；可用 `workline_id` 进一步缩小范围。
- 超级管理员：显式 `PLATFORM` scope。
- 当前返回：provider/operation/mode、backlog、active lease、UNKNOWN、oldest queue age、rate-limited、
  lease loss、open reconciliation；不返回 payload、header、trace、凭据引用或业务键。
- 当前只读 API 不返回这些目标字段：submit 分类、status state/latency/retry/age/backoff、callback hint 分类。
- 所有允许/拒绝读取都写安全审计。

## 当前已配置告警

| Alert | Burn rate | Owner | Runbook |
| --- | ---: | --- | --- |
| `northbound-slo-fast-burn` | 14.4 | runtime-platform | `pause-resume` |
| `northbound-unknown-ratio` | 6.0 | runtime-platform | `unknown-reconciliation` |
| `northbound-credential-revoked` | 14.4 | security-platform | `credential-revoked` |
| `northbound-lease-loss` | 6.0 | runtime-platform | `lease-fencing` |
| `northbound-callback-contradiction` | 3.0 | runtime-platform | `callback-diagnostics` |

## 目标告警候选

`northbound-status-backlog-age`、`northbound-status-rate-limited` 和
`northbound-callback-enqueue-degraded` 只是联调验证后可评审的候选；当前
`NORTHBOUND_OPERATION_ALERT_CATALOG` 未配置它们。切换前若依赖这些告警作为门禁，必须提供实际配置、触发和路由
evidence，不能只引用本文名称。
