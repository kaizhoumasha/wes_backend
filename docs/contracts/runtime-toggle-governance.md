# Phase 3 Runtime Toggle Governance

Phase 3 只允许 typed runtime toggle。toggle 用于 release / ops 调试、provider version、adapter path 或调度策略切换，不得改变状态机安全语义。

## Required Fields

每个 toggle 必须声明以下字段：

| Field | Requirement |
| --- | --- |
| `owner` | 负责人或团队，不能为空 |
| `expiry` | 到期日期；过期 toggle 阻塞发布 |
| `scope` | 影响范围，例如 provider、workline、device role 或 environment |
| `default` | 默认值；生产默认必须是安全值 |
| `rollback` | 回滚动作和预期恢复时间 |
| `test_matrix` | 覆盖默认值、开启值、回滚值的测试集合 |

## Security Boundary

任何 toggle 都不能绕过以下能力：

- HMAC body 签名验证
- nonce TTL 去重
- idempotency 同 key 不同 hash 冲突检测
- ECS IDLE 准入
- RuntimeHold 阻断语义
- evidence 写入
- RECONCILING 隔离

## Lifecycle

- toggle 必须短生命周期，默认在同一 Phase 内清理。
- release toggle 必须在 PR 描述中列出 owner、expiry、scope、default、rollback、test_matrix。
- ops toggle 只允许收窄 provider/adapter 行为，不能扩大权限或绕过鉴权。
