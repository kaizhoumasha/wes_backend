# WorkLine Legacy Cleanup Execution Plan

## 结论

Technical cleanup scope 已完成并随 PR #78 合并。Business legacy cleanup scope 携带 regenerated production/runtime artifacts 后已通过 readiness gate，并已随 PR #79（`v0.14.0.0`，merge SHA `8c833610c08005005406b3a774c92519f69b7886`）执行并合并 business legacy absence cleanup：104 条 phase4 carrier 全部在 `docs/architecture/business-legacy-absence-ledger.csv` 关闭或保留为目标态测试证据。2026-07-08 restructuring cleanup 进一步删除旧 handling 队列表面，迁移并删除 WorkLine 运行态物理列。raw `reports/` artifacts 仍由 Git 忽略，重新验证前必须从 restored field/CI evidence 重新生成。

## 执行顺序

目标链路固定为：

```text
RuntimeInbox
  -> InboundNormalizerRegistry
  -> RuntimeCapabilityDispatcher
  -> material-flow runtime service
  -> RuntimeIntent / EffectPort
```

执行纪律：

1. 先新增 `RuntimeCapabilityDispatcher` 与 runtime capability catalog。
2. 再把 `contract version`、SixInOne、business key、material identity、NG reason、assignment validation 拆到目标态 catalog/domain。
3. 替换生产 import 后，删除 `src.app.workline.plugins.*`、`src.workline_plugin_registry`、`src.workline_plugins/*` 的运行路径。
4. 旧 `src/workline_plugins/*` 只保留在 `docs/archive/legacy-workline-plugins/`，不再位于可 import 的 `src/`。
5. 最后更新 absence guardrail、characterization 来源、cleanup matrix 与本执行文档。

## 技术清理范围

已退出运行路径：

- `src/workline_plugin_registry.py`
- `src/app/workline/plugins/*`
- `src/workline_plugins/*`
- `docs/templates/workline_plugin/*`

目标态承接：

- `src/app/runtime/capability_dispatcher.py`
- `src/app/runtime/capability_catalog.py`
- `src/app/runtime/normalization/*`
- `src/app/runtime/orchestration/services/session/session_resolver.py`
- `src/app/runtime/capabilities/material_flow/contracts/*`
- `src/app/runtime/capabilities/material_flow/smt_inbound_handoff_route_service.py`

## 验收与门禁

必须通过：

- `uv run pytest tests/architecture/test_legacy_absence_guardrail.py tests/workline_runtime/test_runtime_capability_dispatcher.py -q`
- `uv run pytest tests/architecture/test_runtime_capability_context_routing.py tests/workline_runtime/test_sorter_inbound_runtime_service.py -q`
- `uv run python scripts/check_runtime_production_closure_gate.py --closure-profile production --p0-e2e-artifact reports/phase3/phase3-p0-e2e.json --benchmark-artifact reports/phase3/phase3-production-benchmark.json`
- `uv run python scripts/check_runtime_evidence_readiness_gate.py --readiness-profile production --runtime-evidence-artifact reports/phase4/runtime-evidence-production.json --p0-e2e-artifact reports/phase3/phase3-p0-e2e.json --benchmark-artifact reports/phase3/phase3-production-benchmark.json`
- `uv run python scripts/check_business_legacy_absence_gate.py --mode final`
  - `Business legacy absence gate passed: mode=final`
- `./scripts/git-quality-gate.sh --profile quality`
  - runtime production closure、runtime evidence、business legacy absence、process naming、architecture guardrails 与 import-linter 长期门禁通过。

本轮已确认：

- technical gate passed。
- Runtime production closure gate passed。
- Runtime production evidence gate passed。
- business legacy absence gate passed。
- restructuring cleanup migration smoke passed；旧 handling 队列表面和 WorkLine 运行态物理列 absence guardrail passed。
- PR #79 已 merged to `develop`；未检测到 GitHub deploy workflow 且未提供生产 URL，因此 land report 记录为 `DEPLOYED (UNVERIFIED)`。
- tracked provenance ledger: `docs/architecture/phase3-phase4-production-evidence-bundle.md`。

## 回滚

单 PR 回滚优先使用 `git revert`。restructuring cleanup 包含 Alembic migration；若已升级数据库，先按发布流程评估 `alembic downgrade -1` 的数据边界，再执行代码回滚。旧 handling 队列表数据无法仅靠 downgrade 恢复，必须依赖数据库备份或生产恢复流程。

失败模式：

- Unknown capability：`RuntimeCapabilityRouteError`，不 fallback 到 null plugin。
- Undeclared provider capability：`RuntimeCapabilityUndeclaredError`，按 `ExternalContractProfile` fail closed。
- Legacy import 回流：`tests/architecture/test_legacy_absence_guardrail.py` 阻断。
- WorkLine 运行态投影迁移由 restructuring cleanup migration 与 migration smoke 证明；后续不得重新把运行态字段加回 WorkLine 配置表。
