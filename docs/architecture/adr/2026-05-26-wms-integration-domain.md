# ADR: WMS 对接辅助域

## 状态

Accepted - 2026-05-26

## 背景

当前顶层 SPEC 和 WMS/RCS 合同明确：主数据信息、库存、预留、扣减、账务和 RCS 调度授权均由 WMS 持有。WES 只保存执行事实、过程快照、资源投影、回写证据和对账证据。

当前代码中 WMS/RCS 合同分散在 callback、rack、handling、resource 和 workline runtime 中。继续分散会让外部协议、字段别名、熔断和证据留痕渗透到业务域，增加影子 WMS 风险。

## 决策

1. 新增 `src/app/wms_integration` 作为 WMS 对接辅助域。
2. 该域是 Anti-Corruption Layer，不是 WES 主数据域，不提供本地物料、GRN、库存或货架主账 CRUD。
3. 同步主数据、单据和库存查询通过注入的窄 `WmsCapabilities` 执行；会改变 WMS 业务状态的确认持久化为 `WmsConfirmation`。
4. 搬运、交换和 RCS 代理调度请求由 `TransportTask` 拥有生命周期；`wms_integration` 只提供无状态 WMS 转发 Client，不拥有可靠任务状态。
5. 所有 WMS 调用写入独立调用证据；请求和响应快照必须脱敏。
6. QUERY 不做跨请求缓存；单次具体对象执行只查询一次并复用同一 authority snapshot。
7. WMS 连续超时或 5xx 触发熔断。超时、断路器打开和 5xx 映射为封闭 outcome 中的
   `WmsDependencyFailure`，调用方依据显式 `retryable` 和 `retry_after_seconds` 决定当前具体执行对象的依赖停顿/重试并保留诊断证据。
   只有无效本地配置、缺失依赖注入、程序错误或 evidence 基础设施失败抛出明确异常。
8. WMS 四类普通业务事件通过 `/api/v1/callback/event` 接收，`WMS_EFFECT_STATUS_HINT` 通过
   `/api/v1/callback/external` 接收；最小包络校验和字段标准化由 `wms_integration` 提供。接收成功后先持久化为
   有限类型 `InboundEvidence` 并返回 ACK，再交给对应工作线对象或 `TransportTask` owner 异步处理；callback API
   不直接修改业务状态。状态提示只唤醒匹配的 `TransportTask` 查询，返回同步终态的 `WmsConfirmation` 不消费
   callback。

## 后果

- `resource` 域保持 WES 运行时资源投影职责，不承担库存可用性判断。
- `rack` 和 `handling` 域不再自行散落 endpoint code、callback_type 和厂商 payload 规则。
- 后续新增 WMS 接口必须先进入 `wms_integration` 端口，再由业务域调用。
- 任何计划若要求 WES 本地维护库存主账、空箱授权或库存扣减，必须先提出新的 ADR。
