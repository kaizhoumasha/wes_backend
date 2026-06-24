# ADR 0002: 外部履约 11 态机 + 4 timeout 转移

**状态**: Accepted
**日期**: 2026-06-23
**适用范围**: WmsFulfillmentPort / 跨域履约请求生命周期

## 背景

原 11 态机缺少 4 条 timeout 转移规则（REQUESTED 永远发不出 / SENT 超时无响应 / ACCEPTED 无 RUNNING 进展 / RUNNING 长时间无 SUCCEEDED），导致 Adapter 宕机或下游处理慢时请求卡死。Circuit breaker 已实现于 `src/app/wms_integration/services/circuit_breaker_service.py`，但未嵌入 11 态机，CB open 期间新请求会消耗 SENT 配额导致失败。

## 决策

1. **新增 TIMEOUT 状态**：与 FAILED 区分，便于统计与告警（运维可分别告警"超时"和"失败"）。
2. **新增 BLOCKED_BY_CB 状态**：CB open 期间新请求直接进此态，不消耗 SENT 配额；CB close 后自动恢复为 REQUESTED 重试。
3. **4 条 timeout 转移**：
   - `REQUESTED` 持续 30s 仍卡住 → `FAILED`（Adapter 宕机 / CB open 持续 / 进程崩溃）
   - `SENT` 60s 无 ACCEPTED/REJECTED → `TIMEOUT`
   - `ACCEPTED` 5 分钟无 RUNNING → `RECONCILING`（下游接受但未执行）
   - `RUNNING` 30 分钟无 SUCCEEDED → `RECONCILING`（WES 端降级；实际超时由 WMS 端决定）
4. **RECONCILING 合法出口**：`RUNNING / SUCCEEDED / FAILED / CANCELLED / BLOCKED_BY_CB`；**不允许**直接 `RECONCILING -> ACCEPTED/REJECTED/TIMEOUT`。
5. **超时时长按 WorkLine 配置可覆盖**（不同 WorkLine 业务节奏不同）。

## 后果

- 11 态机现在覆盖所有可观察的转移路径（包括 timeout、CB 阻塞）。
- WmsFulfillmentAdapter 集成 circuit breaker，CB 状态机可观测、可恢复。
- 监控告警可按 TIMEOUT/FAILED 分类触发不同通知。
- RECONCILING 不再是黑洞——有 5 个合法出口和 5/30 分钟两阶段升级。

## 验收

- `docs/architecture/specs/workline-restructuring/30-fulfillment-state-machine.md` 发布。
- 状态机代码实现 + 单元测试覆盖所有 11 态 + 5 条 timeout 转移。
- Circuit breaker 集成测试通过。
- 监控告警按 TIMEOUT/FAILED 分类。

## 引用

- 顶层设计：[`../../workline-and-plugin-restructuring.md`](../../workline-and-plugin-restructuring.md)
- Sub-spec 30：[`../../specs/workline-restructuring/30-fulfillment-state-machine.md`](../../specs/workline-restructuring/30-fulfillment-state-machine.md)
- ADR 0001：[`0001-b方案选择与capability-freeze.md`](0001-b方案选择与capability-freeze.md)
