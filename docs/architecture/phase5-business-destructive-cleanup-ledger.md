# Phase5 Business Destructive Cleanup Ledger

> 机器真源：`docs/architecture/phase5-business-destructive-cleanup-ledger.csv`。
> 本文只做审计摘要，不作为 gate 输入。

## 当前快照

来源：`docs/architecture/legacy-cleanup-matrix.csv` 中 `phase4_carrier=True` 的条目。

| 指标 | 数量 |
| --- | ---: |
| total_entries | 104 |
| active-source | 0 |
| test-only | 18 |
| already-removed | 86 |
| schema-deferred | 0 |

## 执行口径

- `active-source`：当前仍有 tracked production source，Packet B 才允许按 ledger 逐行迁移或删除。
- `test-only`：旧 characterization / contract test，只能迁入目标态测试或反转为 absence guardrail。
- `already-removed`：历史 matrix 行，不能算作本 PR 删除成果；只验证业务语义、目标 capability 与引用面。
- `cleanup_disposition=pending` 不触发 strict absence guardrail；只有 `moved`、`deleted`、`test-only-migrated` 进入 strict。
- `external-contract-blocker` 表示 `plugin_key` / `contract_version` 等字段仍属于目标外部合同，本 PR 不删除字段，只迁移 ownership 或删除旧入口。

当前 disposition：

| disposition | 数量 | 说明 |
| --- | ---: | --- |
| moved | 55 | 旧 WorkLine business contracts / services 已迁入 Phase4 contracts 或 runtime capability service |
| test-only-migrated | 10 | 旧 `tests/workline_plugins` rough sorter 合同测试已迁入 `tests/contracts/workline` |
| kept-config-only | 18 | `tests/workline_runtime` 目标态 runtime regression / contract tests 保留 |
| already-removed | 21 | 前序 Phase4 已移除的历史 WorkLine service rows，仅保留审计闭环 |

## Gate 链路

```text
legacy-cleanup-matrix.csv
  -> phase5-business-destructive-cleanup-ledger.csv
  -> check_phase5_business_destructive_cleanup_gate.py --mode draft
  -> Packet B/C migration and deletion
  -> check_phase5_business_destructive_cleanup_gate.py --mode final
```

## 当前剩余风险

- `WorkLine.runtime_status` 物理字段删除不属于本 ledger；仍按独立 schema/data cleanup 决策处理。
- `delete_commit=pending-current-pr` 表示当前工作区尚未落单独 packet commit；commit 后可替换为对应 hash。
