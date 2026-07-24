# ADR：北向交互收敛为单 Provider 的幂等提交与状态查询

- 状态：已接受
- 日期：2026-07-24
- 决策范围：未上线系统的 WMS 北向 EFFECT 交互

## Context

系统尚未上线；每座工厂在任一部署中只有一个 WMS Provider，可在联调环境清理数据并整体切换。现有方向包含
shadow/readiness、动态 profile 和 staging 签名 attestation，增加了未产生业务价值的运行时分支。

## Decision

部署只装配一个 Provider。所有 WMS EFFECT 用冻结 canonical payload 和幂等键提交，并按
`operation_identity + idempotency_key` 查询权威状态；callback 仅是可选提示，触发查询而非直接终结业务状态。

移除的目标是生产 shadow/readiness、动态 profile 选择和签名 staging attestation。保留 typed operation、既有
Outbox、canonical payload、冻结 binding 与人工对账。`IDEMPOTENCY_CONFLICT` 立即打开人工对账，不进入并发重试。

## Consequences

- 不支持运行时热切换 Provider；新增 Provider 必须先通过同一份合同验收，并作为新的部署版本发布。
- WMS 必须承诺幂等保留期、可见性 SLA、版本单调性和 typed terminal result；部署参数在验收时比对。
- 开发阶段可用仓库 mock/stub 关闭合同语义；真实 WMS 的双方联调、书面确认与生产验收保留给 Task 9。
- 后续实现将把状态查询、重查和对账纳入既有 Outbox/RuntimeIntent 主链，不建立平行派发器或账本。
