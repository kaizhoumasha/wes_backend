# Business Legacy Absence Ledger

> 机器真源：`docs/architecture/business-legacy-absence-ledger.csv`。
> 本文只做审计摘要，不作为 gate 输入。

## 当前快照

来源：`docs/architecture/legacy-cleanup-matrix.csv` 中 `phase4_carrier=True` 的条目。

| 指标 | 数量 |
| --- | ---: |
| total_entries | 105 |
| active-source | 0 |
| test-only | 22 |
| already-removed | 83 |
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
| moved | 53 | 旧 WorkLine business contracts / services 已迁入 material-flow contracts 或 runtime capability service |
| test-only-migrated | 10 | 旧 `tests/workline_plugins` rough sorter 合同测试已迁入 `tests/contracts/workline` |
| kept-config-only | 22 | `tests/workline_runtime` 目标态 regression / contract tests 保留 |
| already-removed | 20 | 前序 material-flow 迁移已移除的历史 WorkLine service rows，仅保留审计闭环 |

## Gate 链路

```text
legacy-cleanup-matrix.csv
  -> business-legacy-absence-ledger.csv
  -> check_business_legacy_absence_gate.py --mode draft
  -> business legacy cleanup migration and deletion
  -> check_business_legacy_absence_gate.py --mode final
```

## 当前剩余风险

- WorkLine 运行态物理字段已由 restructuring cleanup migration 迁入 runtime/orchestration 原生投影；本 ledger 当前无 schema-deferred 项。
- `delete_commit=8c833610c08005005406b3a774c92519f69b7886` 是 63 个 strict disposition 行对应旧路径的真实历史删除/迁移证据；final gate 会校验该提交可解析且确实删除或迁移相应路径。
