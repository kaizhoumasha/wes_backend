# ADR 0005: idempotency_key 复合主键

**状态**: Accepted
**日期**: 2026-06-23
**适用范围**: WmsFulfillmentPort + 跨域履约请求幂等

## 背景

现有 `idempotency_key` 实现分散在 4 处：`inbox.py:98`（可选）、`object_transition_event.py:61`（可选）、`runtime_hold.py:124`（必填 + UniqueConstraint）、`runtime_hold_api.py:88`（必填）。无统一命名空间规范，无 `request_hash` 校验。同一 `idempotency_key="abc"` 被 2 个不同 WES session submit 时语义未定义。

## 决策

1. **复合主键 `(provider_code, operation_kind, idempotency_key)`**：
   - `provider_code: "WMS" / "RCS" / "AGV" / "CTU"`
   - `operation_kind: "fulfillment" / "callback" / "reconciliation"`
   - 跨域跨 provider 隔离
2. **immutable 字段**：`request_hash`（请求体 hash）、`execution_correlation_id`（关联 correlation key）、`business_owner_key`、`created_at`。
3. **同 key 不同 hash 行为**：返回 `409 Conflict` + 安全审计事件（**不静默**返回旧 record）。
4. **同 key 同 hash 行为**：直接返回旧 record（不重新走状态机）。
5. **TTL 30 天**：超时后允许同 key 不同 hash 覆盖。
6. **现有 runtime_hold UniqueConstraint 迁移**：从 `source_idempotency_key` 单一字段迁移到 `(provider_code, operation_kind, idempotency_key)` 复合主键。

## 后果

- 跨域幂等语义统一。
- 跨 session 复用幂等键是攻击信号，必须 409 + 审计。
- 30 天 TTL 控制历史键增长。
- 现有 runtime_hold 需迁移，可能影响现有调用方。

## 验收

- `docs/architecture/specs/workline-restructuring/40-resource-projection.md` §5 发布。
- `src/app/wms_integration/models/ports.py` 引入复合主键。
- Alembic 迁移 + downgrade。
- 安全测试覆盖同 key 不同 hash 409 路径。

## 引用

- 顶层设计：[`../../workline-and-plugin-restructuring.md`](../../workline-and-plugin-restructuring.md)
- Sub-spec 40 §5：[`../../specs/workline-restructuring/40-resource-projection.md`](../../specs/workline-restructuring/40-resource-projection.md)
- ADR 0006：[`0006-wms-callback-hmac.md`](0006-wms-callback-hmac.md)
