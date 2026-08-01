# Business Legacy Absence Ledger

> 机器真源：`docs/architecture/business-legacy-absence-ledger.csv`。
> 本文只做审计摘要，不作为 gate 输入。

## 当前快照

来源：`docs/architecture/legacy-cleanup-matrix.csv` 中 `phase4_carrier=True` 的条目。

| 指标 | 数量 |
| --- | ---: |
| total_entries | 71 |
| active-source | 0 |
| test-only | 5 |
| already-removed | 66 |
| schema-deferred | 0 |

## 执行口径

- `active-source`：当前仍有 tracked production source，Packet B 才允许按 ledger 逐行迁移或删除。
- `test-only`：仍证明通用 WES 配置或可靠性边界的测试；具体插件行为不得继续由核心测试承接。
- `already-removed`：历史 matrix 行，不能算作本 PR 删除成果；只验证业务语义、目标 capability 与引用面。
- `cleanup_disposition=pending` 不触发 strict absence guardrail；只有 `moved`、`deleted`、`test-only-migrated` 进入 strict。
- `external-contract-blocker` 表示 `plugin_key` / `contract_version` 等字段仍属于目标外部合同，本 PR 不删除字段，只迁移 ownership 或删除旧入口。

当前 disposition：

| disposition | 数量 | 说明 |
| --- | ---: | --- |
| moved | 54 | 旧 WorkLine business contracts / services 已迁入 material-flow contracts 或 runtime capability service |
| kept-config-only | 5 | `tests/workline_runtime` 中仍可作为通用配置证据的测试暂留，后续继续执行 `CORE_REWRITE` / `LEGACY_DELETE` |
| already-removed | 12 | 前序 material-flow 迁移已移除的历史 WorkLine service rows，仅保留审计闭环 |

插件专属测试已从核心移出。原 evidence path 不再指向核心业务合同；仍有通用证据的条目保留现有测试，缺少核心行为证据的条目改以所有权门禁记录，并标记为对核心 `semantics-obsolete`，其行为由未来对应插件包重建。

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
