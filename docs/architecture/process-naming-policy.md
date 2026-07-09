# 过程命名策略

## Active Code Rule

生产代码、active gate 脚本、默认回归测试必须使用稳定的业务或架构命名，不使用实现里程碑命名，例如 `phase4`、`Phase5`、`wave2`、`business lane` 或 `final cleanup`。

Active production code, active gates, default regression tests, and current architecture docs must not use old restructuring shorthand such as `C3`, `C4`, `R-I3c`, `R-WLR`, or `wlr` for new/current concepts. Use stable names like `AUTHORITY_METADATA_BOUNDARY`, `DEVICE_COMMAND_BOUNDARY`, `INBOUND_NORMALIZER_OWNERSHIP`, and `LEGACY_RUNTIME_IMPORT`.

`closure`、`readiness`、`cleanup` 这类通用发布词可以使用，但不能与阶段编号、wave、lane、burn-down 或 final-cleanup 过程语境绑定。稳定示例包括 `production_closure`、`runtime_evidence_readiness`、`workline_restructuring_readiness` 和 `business_legacy_absence`。

## Allowed Historical Records

历史计划、归档 spec、release/evidence log、legacy matrix 审计字段和 Alembic revision 文件名可以保留过程命名，因为它们描述已经发生的事实。

这些历史记录不得作为新代码、新脚本或新测试命名的依据；新增 active surface 必须使用稳定词汇。

## Stable Replacement Vocabulary

优先使用以下稳定词汇：

- `material_flow`
- `production_closure`
- `runtime_evidence`
- `workline_restructuring`
- `business_legacy_absence`
- `runtime_benchmark`
- `runtime_production_e2e`

## Verification

关闭该技术债前必须运行：

```bash
uv run pytest tests/architecture/test_process_naming_guardrail.py -q
```
