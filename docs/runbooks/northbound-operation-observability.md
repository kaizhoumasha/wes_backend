# 北向 operation 运维 Runbook

适用目录版本：`northbound-operation-slo.v1`。所有动作先确认看板
`northbound-operation-day1` 的 operation、唯一部署 provider profile、outcome 和 policy version，
不要用 tenant、业务单号、payload、trace ID 或凭据引用作为指标标签。以下判断只依据 WES 可见的 HTTP
响应、deadline、账本、租约和调度事实，不推断 WMS 内部队列、阶段、失败原因或补偿步骤。

六类 `wms_effect.*` 已注册并在 submit、status worker、reconciliation 与 callback hint 边界真实发射。
查询沿用现有 OpenTelemetry backend 与 `northbound-operation-day1`，按 signal name、静态 operation identity、
闭集 outcome/state 和 policy version 聚合；不得把 dispatch key hash、trace 或业务键提升为 metric label，也不建设
第二套看板框架。

## EFFECT 提交与状态总览

1. 查询 `wms_effect.submit` 的 accepted/ambiguous/not-sent 数量、p95 延迟和 retry count；`not-sent` 才能继续
   普通 transport retry，accepted/ambiguous 必须进入 status query。
2. 查询 `wms_effect.status_query`，按 operation/state 聚合五态数量、p95 延迟、retry count 和 age。只有规范化状态快照能推进业务终态；
   SystemOutbox/DispatchAttempt 仍只描述 transport。
3. 查询 `wms_effect.status_backlog` 与 `wms_effect.status_backpressure` 的 backlog 数量、最大 age、单批领取量/耗时、
   429、`Retry-After`、circuit-open 和实际退避时长。禁止在限流窗口手工高频重放。
4. 查询 `wms_effect.recovery` 的 `NOT_FOUND_GRACE_EXHAUSTED`、`QUERY_BUDGET_EXHAUSTED`、
   `IDEMPOTENCY_CONFLICT`、`RECONCILIATION_OPENED` 和 open reconciliation age；不能把
   `NOT_FOUND` 直接解释为 WMS 未处理。

## 阈值、动作与恢复判据

| Signal / 条件 | 阈值 | 处置 | 恢复判据 |
| --- | --- | --- | --- |
| `wms_effect.submit` 非 `ACCEPTED` | 5 分钟窗口触发现有 operation SLO `14.4` fast burn，或 p95 超过 catalog 对应阈值 | 暂停对应 operation 新提交，保留 status/reconciliation worker | 连续一个 SLO 快窗口低于阈值，且既有 accepted/ambiguous 均可由 status/reconciliation 解释 |
| `wms_effect.status_backlog.max_overdue_age_ms` | 连续两个扫描周期大于 `2 × WES_EFFECT_STATUS_SCAN_PERIOD_SECONDS × 1000` 为 warning；持续达到 `WES_EFFECT_STATUS_SCAN_BATCH_BUDGET_SECONDS × 1000` 为 stop-admission | 先检查 scanner/lease，再暂停新 EFFECT；禁止通过扩大并发绕过 WMS 限流 | 连续三个扫描周期低于 `WES_EFFECT_STATUS_SCAN_PERIOD_SECONDS × 1000`，且无超预算 intent |
| `wms_effect.status_backlog.max_confirmation_age_ms` | 达到 `WES_EFFECT_MAX_CONFIRMATION_AGE_SECONDS × 800` 为 warning；达到 `WES_EFFECT_MAX_CONFIRMATION_AGE_SECONDS × 1000` 为 stop-admission | 暂停新 EFFECT，保留 status/reconciliation worker，检查 WMS 确认链路 | 连续三个扫描周期低于 warning 阈值，且无超预算 intent 或未关闭 reconciliation |
| `wms_effect.status_backpressure` | 任一 `RATE_LIMITED`/`CIRCUIT_OPEN` 进入诊断；连续三个扫描周期仍出现或 backlog 达 stop-admission 阈值则暂停准入 | 遵守 `Retry-After` 和 actual backoff，保留 status worker；不手工抢跑 | 连续三个扫描周期无 `RATE_LIMITED`/`CIRCUIT_OPEN`，且 backlog age 持续下降 |
| `wms_effect.recovery` | 任一 recovery outcome 必须留痕；open reconciliation age 达 900 秒必须升级人工处置 | 按 UNKNOWN/对账步骤核对 typed evidence，禁止换幂等键重提 | case 已由 typed resolution 收口，且 RuntimeIntentLog/SystemOutbox/attempt ledger 可解释 |
| `wms_effect.callback_hint{outcome=ENQUEUE_DEGRADED}` | 任一事件触发 broker/scanner 检查 | 不改变 callback ACK；确认持久化到期行仍在并由 scanner 接管 | 连续两个扫描周期无新增降级，且对应到期行已被 claim 或已终态 |

<a id="pause-resume"></a>
## 暂停与恢复

1. 确认告警对应的唯一 provider profile 与 operation，记录当前 backlog、oldest age、429、实际退避和 UNKNOWN。
2. 使用既有 admission/dispatch policy 暂停明确 operation；不得构造第二 Provider 做运行时 fallback，也不得按
   payload 动态路由。
3. 等待 active lease 归零；有过期 lease 时先执行 fencing/对账流程，禁止直接重放 UNKNOWN。
4. 外部交互恢复后先开放一个 operation，观察一个 SLO 快窗口，再恢复其余 operation。
5. 在审计中记录责任人、catalog/policy version、暂停和恢复时间。

<a id="unknown-reconciliation"></a>
## UNKNOWN 与对账

1. 对照 UNKNOWN ratio、open reconciliation age 和 dispatch attempt evidence，定位受影响的 operation。
2. QUERY 先核对不可变 evidence ref。EFFECT accepted/ambiguous 后只能查询；仅当从未见可见状态、
   `NOT_FOUND` 超过已验收宽限期且单次预算尚未消耗时，允许同键、同 payload、同冻结 binding 受控重提一次。
3. 已见状态后再次 `NOT_FOUND`、幂等冲突、查询耗尽、同版本异内容和矛盾终态均创建/保持
   `ReconciliationCase`，禁止新幂等键重提。
4. 有确定的状态查询证据后通过唯一 reducer 收口 COMPLETED/REJECTED；证据仍冲突则保持 RECONCILING。
5. 关闭 case 前确认 RuntimeIntentLog、SystemOutbox、attempt ledger 三者状态可解释。

<a id="status-query-backpressure"></a>
## Status query 背压

1. 从 claim repository/worker evidence 记录 backlog 数量/最大 age、单批领取量/耗时和 lease reclaim；确认
   worker 使用小批量、批内顺序执行。
2. 429 必须读取合法 `Retry-After`，实际下一次查询不得早于该下限；无效值按合同失败处理。
3. timeout/5xx 使用 jittered backoff；circuit-open 时记录 breaker 状态与实际退避，不调用 WMS。
4. backlog age 持续增长时先关闭新的 EFFECT admission，并保留 status/callback/reconciliation worker；不要增加
   并发绕过 WMS 已给出的限流事实。
5. 查询预算耗尽或宽限/保留期不变量将被突破时进入人工对账，不猜测远端结果。

<a id="credential-revoked"></a>
## 凭据撤销

1. `REVOKED` 告警只用于确认解析结果；日志和审计中不得粘贴 secret ref、secret、Authorization 或签名 header。
2. 已冻结旧版本凭据的未发送 intent 必须 fail-closed，不得自动升级到新版本。
3. 发布新 provider binding/version，仅新建 intent 使用；既有 UNKNOWN 进入独立对账。
4. 通过脱敏凭据审计确认 `RESOLVED`，再按暂停/恢复流程小流量恢复。

<a id="lease-fencing"></a>
## Lease 与 fencing

1. 查看 active lease、expired lease/lease loss 与 contended bucket。
2. 过期 EXTERNAL_HTTP lease 必须 fence 为 UNKNOWN 并创建 attempt/reconciliation evidence。
3. 不得复用旧 owner token；确认后续 attempt 使用新 token 且旧 worker 写回被拒绝。
4. lease loss 持续增长时暂停对应 profile，排查 worker 时钟、数据库锁和网络。

<a id="callback-diagnostics"></a>
## Callback 诊断

1. 从 callback ingress、到期调度、enqueue/scanner 结果复算 callback hint 接收、拒绝、重复、触发查询和
   enqueue 降级数量；不要从 payload 授权。
2. callback 只允许持久化提前到期并提示查询，重复 hint 保持幂等；它不能提供或覆盖 COMPLETED/REJECTED。
3. 核对 callback type、operation identity、业务关联键与 RuntimeIntentLog correlation；只描述校验结果，不推断
   WMS 为何发送或未发送 hint。
4. enqueue 失败但合同响应成功时，确认持久化的到期调度仍存在且周期 scanner 已接管。
5. 恢复前验证拒绝/重复/enqueue degraded 指标回落，并保留审计记录。
