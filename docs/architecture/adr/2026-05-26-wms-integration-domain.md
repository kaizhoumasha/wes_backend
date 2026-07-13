# ADR: WMS 对接辅助域

## 状态

Accepted - 2026-05-26

## 背景

SRS 和 WMS/RCS 接口规划明确：主数据信息、库存、预留、扣减、账务和 RCS 调度授权均由 WMS 持有。WES 只保存执行事实、过程快照、资源投影、回写证据和对账证据。

当前代码中 WMS/RCS 合同分散在 callback、rack、handling、resource 和 workline runtime 中。继续分散会让外部协议、字段别名、熔断和证据留痕渗透到业务域，增加影子 WMS 风险。

## 决策

1. 新增 `src/app/wms_integration` 作为 WMS 对接辅助域。
2. 该域是 Anti-Corruption Layer，不是 WES 主数据域，不提供本地物料、GRN、库存或货架主账 CRUD。
3. 查询、库存预留、释放、入库确认和出库确认通过同步 WMS client 执行。
4. 搬运、交换和 RCS 代理调度请求继续通过 `SystemOutbox(EXTERNAL_HTTP)` 派发，`wms_integration` 只生成 endpoint code 和 payload 合同。
5. 所有 WMS 调用写入独立调用证据；请求和响应快照必须脱敏。
6. 查询结果允许 Redis 短时缓存，TTL 不得超过 30 秒；缓存失效或 Redis 不可用时必须重新查询 WMS。
7. WMS 连续超时或 5xx 触发熔断。`wms_integration` 抛出明确不可用异常，由调用方创建 RuntimeHold 或诊断并暂停当前业务。
8. WMS/RCS 运行时回调统一通过 `/api/v1/callback/external`，最小包络校验和字段标准化由
   `wms_integration` 提供；接收成功后写入 `RuntimeInbox(kind=EXTERNAL_HTTP)`，再由正式
   `RuntimeInboxService` 与三阶段 processor 异步推进，callback API 不直接修改业务状态。

## 后果

- `resource` 域保持 WES 运行时资源投影职责，不承担库存可用性判断。
- `rack` 和 `handling` 域不再自行散落 endpoint code、callback_type 和厂商 payload 规则。
- 后续新增 WMS 接口必须先进入 `wms_integration` 端口，再由业务域调用。
- 任何计划若要求 WES 本地维护库存主账、空箱授权或库存扣减，必须先提出新的 ADR。
