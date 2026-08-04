# WorkLine 插件迁移清单与跨环境批准

> 状态：`implementation_baseline`。只要当前 inventory/matrix/preflight 脚本仍在库中执行，本操作说明继续保留；脚本退役时必须同步归档。

## 职责边界

本流程只生成只读 inventory、跨环境 migration matrix 和批准证据门禁，不执行配置冻结、排空、历史
trace replay 或原子切换。上述动作仍属于插件平台 T8。

单环境报告由 `scripts/workline_migration_inventory.py` 在调用方持有的 PostgreSQL
`REPEATABLE READ + READ ONLY` 快照中生成。报告包含：

- WorkLine plugin/binding/provider 快照；
- WorkItem/Intent 固定的 binding 与索引引用；
- Plugin 允许的 System Capability，以及从 generated index 派生的 Provider admission 和 Port 类型；
- Plugin/System Capability generated index digest；
- 未完成 Session/Command/Outbox/Inbox/RuntimeHold 引用。

## 执行顺序

1. 在每个必需环境生成单环境报告，并保存 JSON artifact。
2. 由对应环境责任人审查报告；批准记录必须绑定该报告的 `environment`、`inventory_digest` 与
   `generated_at`。
3. 使用 `scripts/workline_migration_matrix.py` 聚合全部报告和批准记录。
4. 使用 `--check` 进行机器判定：`0` 表示 inventory gate 通过，`3` 表示矩阵仍有阻断项。
5. matrix 生成器以当前执行代码的 Plugin/System Capability generated index digest 作为部署期望；任一环境
   报告与其不一致时 fail closed。
6. T8 preflight 必须固定并重新验证 `matrix_digest`，并按现场冻结窗口显式传入正的
   `max_inventory_age`；报告过期，或报告、批准、generated index 发生变化后，必须重新生成和批准。

批准记录字段为 `environment`、`inventory_digest`、`inventory_generated_at`、`approved_by`、`approved_at`
和 `reason`。`inventory_generated_at` 必须等于报告的 `generated_at`，且 `approved_at` 不得早于报告生成时间。
批准只对精确报告实例有效，不允许将旧批准复用于重新生成的报告。

示例命令：

```bash
uv run python scripts/workline_migration_matrix.py \
  --inventory-report reports/workline-inventory-test.json \
  --inventory-report reports/workline-inventory-prod.json \
  --approval reports/workline-inventory-test-approval.json \
  --approval reports/workline-inventory-prod-approval.json \
  --required-environment test \
  --required-environment prod \
  --check
```

矩阵通过只表示 T1 inventory gate 已闭合，不表示 T8 cutover 已执行或可以跳过现场 GO/NO-GO。
