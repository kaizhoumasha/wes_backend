# Phase5 Technical Lane Legacy Cleanup Execution Plan

## 结论

Phase5 本轮只执行 `phase5-tech`。`phase5-business` 继续阻塞，原因是 Phase3 production closure 仍缺 `phase3-p0-e2e-artifact` 与 `phase3-benchmark-artifact` provenance。

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
- `src/app/workline/domain/contracts/*`
- `src/app/workline/domain/contexts/*`

## 验收与门禁

必须通过：

- `uv run pytest tests/architecture/test_phase5_legacy_absence_guardrail.py tests/workline_runtime/test_runtime_capability_dispatcher.py -q`
- `uv run pytest tests/architecture/test_runtime_capability_context_routing.py tests/workline_runtime/test_sorter_inbound_runtime_service.py -q`
- `uv run python scripts/check_phase5_readiness_gate.py --lane technical`
- `uv run python scripts/check_phase5_readiness_gate.py --lane business` 必须继续失败，直到 production closure artifacts 补齐。

本轮已确认：

- technical gate passed。
- business gate failed: `MISSING_PHASE3_PRODUCTION_CLOSURE`，缺 `phase3-p0-e2e-artifact` 与 `phase3-benchmark-artifact`。

## 回滚

单 PR 回滚优先使用 `git revert`。本轮不包含 Alembic migration，不允许以 business lane 数据 drop 作为回滚手段。

失败模式：

- Unknown capability：`RuntimeCapabilityRouteError`，不 fallback 到 null plugin。
- Undeclared provider capability：`RuntimeCapabilityUndeclaredError`，按 `ExternalContractProfile` fail closed。
- Legacy import 回流：`tests/architecture/test_phase5_legacy_absence_guardrail.py` 阻断。
- Phase3 production provenance 未补齐：business gate 继续失败，禁止删除业务承载 legacy。
