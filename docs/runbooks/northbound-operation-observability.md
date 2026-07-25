# 北向 operation 运维 Runbook

适用目录版本：`northbound-operation-slo.v1`。所有动作先确认看板
`northbound-operation-day1` 的 operation、唯一部署 provider profile、outcome 和 policy version，
不要用 tenant、业务单号、payload、trace ID 或凭据引用作为指标标签。以下判断只依据 WES 可见的 HTTP
响应、deadline、账本、租约和调度事实，不推断 WMS 内部队列、阶段、失败原因或补偿步骤。

本 Runbook 同时列出现有操作面与 Task 9 切换前目标。目标口径不是已存在的生产指标：
`wms_effect.*` 尚未注册，submit/status/callback 细分尚未由当前只读 API 返回，status backlog/enqueue degraded
新告警也尚未配置。操作者必须先从当前 authored `northbound.operation.*`、DispatchAttempt、
RuntimeIntentLog、worker/adapter 结果、callback ingress 和 ReconciliationCase 形成脱敏联调证据；没有映射
evidence 时不得把下列目标步骤用于 cutover GO。

## EFFECT 提交与状态总览

1. 从 DispatchAttempt 与 submit bridge 结果复算 accepted/ambiguous/not-sent 数量、延迟；`not-sent` 才能继续
   普通 transport retry，accepted/ambiguous 必须进入 status query。
2. 从 RuntimeIntentLog 和 status worker 结果按 operation 复算五态数量、延迟、重试次数和 age。只有规范化状态快照能推进业务终态；
   SystemOutbox/DispatchAttempt 仍只描述 transport。
3. 从 claim/worker、query evidence 和 breaker 结果复算 status query backlog 数量、最大 age、单批领取量/耗时、
   429、`Retry-After`、circuit-open 和实际退避时长。禁止在限流窗口手工高频重放。
4. 从 RuntimeIntentLog/ReconciliationCase 复算 `NOT_FOUND` 超过宽限期、查询耗尽、幂等冲突和 open
   reconciliation 数量；不能把
   `NOT_FOUND` 直接解释为 WMS 未处理。

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
