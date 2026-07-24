# 北向 Operation SLO 与 Day-1 看板

本文是 `northbound-operation-slo.v1` 的运维视图；可执行真源位于
`src/app/runtime/orchestration/operation_observability.py`。任何新增 authored operation 必须先登记 SLO，
否则 provider binding authoring fail-closed。

## SLO 目录

| Operation identity | 30 天可用性 | p95 延迟 | UNKNOWN 上限 | 对账 age 上限 | Owner |
| --- | ---: | ---: | ---: | ---: | --- |
| `wms.inventory.query_inventory@v1` | 99.5% | 1.5s | 0.1% | 15min | runtime-platform |
| `wms.inventory.confirm_inbound@v1` | 99.5% | 2.0s | 0.1% | 15min | runtime-platform |
| `wms.fulfillment.notify_pkg_binding@v1` | 99.5% | 2.0s | 0.1% | 15min | runtime-platform |
| `wms.fulfillment.full_box_exchange@v1` | 99.5% | 3.0s | 0.1% | 15min | runtime-platform |

可用性成功样本为 `SUCCESS`；`BUSINESS_REJECT` 单独展示，不吞入技术错误。
技术/合同失败和 UNKNOWN 消耗错误预算，UNKNOWN 同时受独立比例与 reconciliation age 门禁。
多窗口 burn rate 阈值为 14.4 / 6.0 / 3.0；告警统一链接看板
`northbound-operation-day1` 与
[`northbound-operation-observability.md`](../runbooks/northbound-operation-observability.md)。

## Day-1 看板面板

下列面板只展示 WES 可观察的 submit、status query、callback hint 和本地账本事实，不推断 WMS 内部队列、
阶段或补偿行为。

| 面板 | 数据口径 | 分组/限制 |
| --- | --- | --- |
| Operation call / outcome | `sample_count`、六类闭集 outcome | operation + 唯一部署 profile |
| Query latency | `latency_ms` p50/p95/p99 | operation + 唯一部署 profile |
| Evidence failure | `wms_evidence.persistence_failure` | capability + policy version |
| EFFECT submit | submit accepted/ambiguous/not-sent 数量与延迟 | operation + 唯一部署 profile |
| EFFECT status states | `ACCEPTED/PROCESSING/COMPLETED/REJECTED/NOT_FOUND` 数量、延迟、重试次数与 age | operation + 唯一部署 profile |
| Status query backlog | status query backlog 数量、最大 age、单批领取量与批次耗时 | 仅平台级计数和测量 |
| Status backpressure | 429、`Retry-After`、circuit-open、实际退避时长 | operation + 闭集结果 |
| Status exhaustion | 超宽限期 `NOT_FOUND`、查询耗尽、幂等冲突 | operation + 稳定 reason code |
| Outbox health | backlog、oldest queue age、active lease | 仅平台计数，不展示 bucket |
| Rate limit / pause | 命中 bucket 数、暂停 bucket 数 | 不把 bucket 值作为 label |
| Lease steal/loss | contention 与 stale lease loss | capability + policy version |
| UNKNOWN / reconciliation | UNKNOWN ratio、open case 数、oldest age | operation + 唯一部署 profile |
| Callback hint | callback hint 接收、拒绝、重复、触发查询、enqueue 降级数量 | operation + 闭集 outcome |
| Credential resolve | resolved/revoked/failure/provider error | provider kind + 闭集 outcome |

看板不得接受 tenant、用户 ID、业务单号、payload、trace/correlation/evidence、credential ref 或 bucket
作为 metric label。需要行级诊断时，先通过专用 RBAC 接口取得租户作用域的聚合快照，再使用受控 evidence
入口调查；不能从 payload 授权。

## 只读运维入口

- 路径：`GET /api/v1/workline/runtime-operations/northbound`
- 权限：`sys:runtime-operations:view`
- 普通用户：仅 `WorkLine.created_by == 当前用户`；可用 `workline_id` 进一步缩小范围。
- 超级管理员：显式 `PLATFORM` scope。
- 返回：provider/operation、backlog、active lease、UNKNOWN、oldest age、rate-limit、lease loss、
  submit 分类、status state/age/retry/backoff、callback hint、open reconciliation；不返回 payload、header、
  trace、凭据引用或业务键。
- 所有允许/拒绝读取都写安全审计。

## 告警路由

| Alert | Burn rate | Owner | Runbook |
| --- | ---: | --- | --- |
| `northbound-slo-fast-burn` | 14.4 | runtime-platform | `pause-resume` |
| `northbound-unknown-ratio` | 6.0 | runtime-platform | `unknown-reconciliation` |
| `northbound-status-backlog-age` | 6.0 | runtime-platform | `status-query-backpressure` |
| `northbound-status-rate-limited` | 6.0 | runtime-platform | `status-query-backpressure` |
| `northbound-credential-revoked` | 14.4 | security-platform | `credential-revoked` |
| `northbound-lease-loss` | 6.0 | runtime-platform | `lease-fencing` |
| `northbound-callback-contradiction` | 3.0 | runtime-platform | `callback-diagnostics` |
| `northbound-callback-enqueue-degraded` | 3.0 | runtime-platform | `callback-diagnostics` |
