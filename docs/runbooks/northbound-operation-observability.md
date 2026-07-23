# 北向 operation 运维 Runbook

适用目录版本：`northbound-operation-slo.v1`。所有动作先确认看板
`northbound-operation-day1` 的 operation、provider profile、outcome 和 policy version，
不要用 tenant、业务单号、payload、trace ID 或凭据引用作为指标标签。

<a id="pause-resume"></a>
## 暂停与恢复

1. 确认告警对应的 provider profile 与 operation，记录当前 backlog、oldest queue age、rate-limit 和 UNKNOWN。
2. 使用既有 `DispatchPolicyRegistry` 暂停明确的 profile 或 bucket；不得按 payload 动态暂停。
3. 等待 active lease 归零；有过期 lease 时先执行 fencing/对账流程，禁止直接重放 UNKNOWN。
4. 根因解除后先恢复单个 bucket，观察一个 SLO 快窗口，再恢复整个 profile。
5. 在审计中记录责任人、catalog/policy version、暂停和恢复时间。

<a id="unknown-reconciliation"></a>
## UNKNOWN 与对账

1. 对照 UNKNOWN ratio、open reconciliation age 和 dispatch attempt evidence，定位受影响的 operation。
2. QUERY 先核对不可变 evidence ref；EFFECT 只通过 `ReconciliationCase` 裁决，禁止自动重试。
3. 有确定供应商证据后通过唯一 reducer 收口 COMPLETED/REJECTED；证据仍冲突则保持 RECONCILING。
4. 关闭 case 前确认 RuntimeIntentLog、SystemOutbox、attempt ledger 三者状态可解释。

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

1. 以受控 trace/evidence 查询入口串联 dispatch attempt、callback 和 reconciliation；不要从 payload 授权。
2. 重复 callback 应保持幂等；矛盾 callback 必须进入 UNKNOWN/RECONCILING，禁止覆盖终态证据。
3. 核对 callback type、operation callback contract 和 RuntimeIntentLog correlation。
4. 恢复前验证 duplicate/contradiction 指标回落，并保留审计记录。
