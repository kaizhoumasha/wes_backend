# Phase5 Legacy Cleanup Execution Plan

## 结论

Phase5 `phase5-tech` 已完成并随 PR #78 合并。`phase5-business` 携带 regenerated Phase3/Phase4 artifacts 后已通过 readiness gate，并已随 PR #79（`v0.14.0.0`，merge SHA `8c833610c08005005406b3a774c92519f69b7886`）执行并合并 business destructive cleanup：104 条 phase4 carrier 全部在 `docs/architecture/phase5-business-destructive-cleanup-ledger.csv` 关闭或保留为目标态测试证据。raw `reports/` artifacts 仍由 Git 忽略，重新验证前必须从 restored field/CI evidence 重新生成。

## 执行顺序

目标链路固定为：

```text
RuntimeInbox
  -> InboundNormalizerRegistry
  -> RuntimeCapabilityDispatcher
  -> Phase4 runtime service
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
- `src/app/runtime/capabilities/phase4/contracts/*`
- `src/app/runtime/capabilities/phase4/smt_inbound_handoff_route_service.py`

## 验收与门禁

必须通过：

- `uv run pytest tests/architecture/test_phase5_legacy_absence_guardrail.py tests/workline_runtime/test_runtime_capability_dispatcher.py -q`
- `uv run pytest tests/architecture/test_runtime_capability_context_routing.py tests/workline_runtime/test_sorter_inbound_runtime_service.py -q`
- `uv run python scripts/check_phase5_readiness_gate.py --lane technical`
- `uv run python scripts/check_phase3_closure_gate.py --closure-profile production --p0-e2e-artifact reports/phase3/phase3-p0-e2e.json --benchmark-artifact reports/phase3/phase3-production-benchmark.json`
- `uv run python scripts/check_phase4_runtime_readiness_gate.py --readiness-profile production --phase4-runtime-evidence-artifact reports/phase4/runtime-evidence-production.json --p0-e2e-artifact reports/phase3/phase3-p0-e2e.json --benchmark-artifact reports/phase3/phase3-production-benchmark.json`
- `uv run python scripts/check_phase5_readiness_gate.py --lane business --phase3-p0-e2e-artifact reports/phase3/phase3-p0-e2e.json --phase3-benchmark-artifact reports/phase3/phase3-production-benchmark.json --phase4-evidence-artifact reports/phase4/runtime-evidence-production.json`
  - `Phase 5 readiness passed: lane=business`
- `uv run python scripts/check_phase5_business_destructive_cleanup_gate.py --mode final`
  - `Phase5 business destructive cleanup gate passed: mode=final`

本轮已确认：

- technical gate passed。
- Phase3 production closure gate passed。
- Phase4 production runtime evidence gate passed。
- business gate passed。
- business destructive cleanup gate passed。
- PR #79 已 merged to `develop`；未检测到 GitHub deploy workflow 且未提供生产 URL，因此 land report 记录为 `DEPLOYED (UNVERIFIED)`。
- tracked provenance ledger: `docs/architecture/phase3-phase4-production-evidence-bundle.md`。

## 回滚

单 PR 回滚优先使用 `git revert`。本轮不包含 Alembic migration，不允许以 business lane 数据 drop 作为回滚手段。

失败模式：

- Unknown capability：`RuntimeCapabilityRouteError`，不 fallback 到 null plugin。
- Undeclared provider capability：`RuntimeCapabilityUndeclaredError`，按 `ExternalContractProfile` fail closed。
- Legacy import 回流：`tests/architecture/test_phase5_legacy_absence_guardrail.py` 阻断。
- `WorkLine.runtime_status` schema/data 删除仍需独立 migration plan；本轮不包含 Alembic migration，不 drop 业务数据。
