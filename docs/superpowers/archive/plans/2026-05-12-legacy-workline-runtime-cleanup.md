# Legacy Workline Runtime Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 清理 WORKLINE 插件旧链路，让生产 callback 链路以 `RuntimeIntent` 为插件唯一输出，以 Runtime/拓扑作为状态和流转的唯一所有者；最终删除 `PluginResult`、`TransitionValidator`、per-plugin `state_machine_class`、`requires_state`、`plugin_state` 等旧状态机机制。
**Architecture:** 插件只解释事件/结果并返回领域意图；Runtime 负责拓扑解析、命令创建、等待、幂等、超时、Timeline、Session/物料位置更新和监控投影。执行过程中允许短生命周期桥接以保持每个 checkpoint 可测试，但最终验收禁止旧链路残留。
**Tech Stack:** FastAPI backend, SQLModel/SQLAlchemy async, Pydantic v2, Alembic, pytest, ruff, uv.

---

## First Principles

用户关心的是：物料当前在哪台设备、哪台设备正在做什么、哪里被阻断、为什么阻断、下一步能不能继续。插件开发者关心的是：当前设备收到事件或命令结果后，业务上应该让哪个设备做什么，或完成、阻断、转 NG、等待人工。

因此最终设计必须满足：

- 插件不拥有状态机。
- 插件不校验流程合法性。
- 插件不更新 Session 状态。
- 插件不维护物料当前位置。
- 插件不处理命令幂等、ACK、Result、Timeout、Retry。
- Runtime 从拓扑、当前等待命令和运行时事件推导合法流程。
- 监控、统计、追踪从 Runtime 事实事件和 Timeline 派生，不从插件私有状态派生。

---

## Current Legacy Inventory

清理前需要确认这些入口仍然存在：

- `src/workline_runtime/types.py`
  - `PluginResult`
  - `CommandIntent`
  - `WaitIntent`
  - `BusinessDecisionIntent`
  - `FailureIntent`
- `src/workline_runtime/transition_validator.py`
  - `TransitionValidator`
  - `TransitionDecision`
- `src/workline_runtime/plugin_base.py`
  - `PluginResultBuilder`
  - `requires_state`
  - `step`
  - `_expected_states`
  - `build_state_mismatch_failure`
- `src/workline_runtime/orchestrator.py`
  - 以 `PluginResult` 为插件返回合同
  - 以 `state_machine_class` + `transition` 推导 `plugin_state`
- `src/celery_app/tasks/workline.py`
  - 将 `PluginResult.commands/wait/failure/complete/transition` 落成 DB effect
  - 写入 `session.plugin_state`
  - 写入 `session.current_wait_token`
  - 写入 `DeviceCommand.issued_plugin_state`
- `src/workline_plugins/smt_classifier/plugin.py`
  - `PluginResultBuilder`
  - `@requires_state`
  - `SmtClassifierStateMachine`
  - 按 `ctx.plugin_state` 路由命令结果
- `src/workline_plugins/inbound_tote_qc/plugin.py`
  - `PluginResultBuilder`
  - `InboundToteQcStateMachine`
- `src/workline_runtime/device_target_resolver.py`
  - 依赖 legacy `CommandIntent`

最终验收时，上述旧入口应删除或不再被生产路径引用。

---

## Target Invariants

- `RuntimeIntent` 是插件唯一返回合同。
- `RuntimeIntentKind.COMMAND` 自动创建 `DeviceCommand`、`WorklineOutbox`、等待状态和 Timeline。
- `RuntimeIntentKind.UPDATE_CONTEXT` 是插件更新业务上下文的唯一方式。
- `RuntimeIntentKind.COMPLETE` 是插件表达流程完成的唯一方式。
- `RuntimeIntentKind.BLOCK` 是插件表达阻断的唯一方式。
- `RuntimeIntentKind.MARK_NG` 是插件表达业务 NG 事实的唯一方式，不等同于系统失败。
- `Destination` 是插件表达“去哪里”的唯一方式。
- Runtime 使用拓扑解析 `Destination`，插件不得自己找下游设备。
- 命令结果路由不依赖 `plugin_state`，只依赖命令本身、设备角色、标准化结果和上下文。
- `awaiting_command_id` 可以保留为 Runtime 内部等待锚点；它不是插件状态。
- `plugin_state`、`current_wait_token`、`issued_plugin_state` 最终删除。

---

## Task 1: Complete RuntimeIntent Vocabulary

先补齐目标合同，避免迁移插件时继续发明临时字段。

- [x] Add failing tests in `tests/workline_runtime/test_runtime_intent_contract.py`.

```python
from src.workline_runtime.runtime_intent import (
    BlockScope,
    Destination,
    RuntimeIntent,
    RuntimeIntentKind,
)


def test_update_context_intent_carries_patch() -> None:
    intent = RuntimeIntent.update_context({"pkg_id": "L0001-1"})

    assert intent.kind == RuntimeIntentKind.UPDATE_CONTEXT
    assert intent.context_patch == {"pkg_id": "L0001-1"}


def test_complete_intent_carries_optional_context_patch() -> None:
    intent = RuntimeIntent.complete({"bin_code": "BIN_463"})

    assert intent.kind == RuntimeIntentKind.COMPLETE
    assert intent.context_patch == {"bin_code": "BIN_463"}


def test_mark_ng_intent_records_business_fact_without_failure() -> None:
    intent = RuntimeIntent.mark_ng(
        reason_code="SCAN_NG",
        message="扫码判定 NG",
        payload={"PkgID": "BAD"},
    )

    assert intent.kind == RuntimeIntentKind.MARK_NG
    assert intent.reason_code == "SCAN_NG"
    assert intent.message == "扫码判定 NG"
    assert intent.payload_json == {"PkgID": "BAD"}


def test_continue_next_uses_topology_destination() -> None:
    intent = RuntimeIntent.continue_next(action="MOVE_FORWARD", payload={"pkg_id": "L0001-1"})

    assert intent.kind == RuntimeIntentKind.CONTINUE_NEXT
    assert intent.destination == Destination.next()
    assert intent.action == "MOVE_FORWARD"


def test_block_still_requires_scope_reason_and_message() -> None:
    intent = RuntimeIntent.block(
        scope=BlockScope.MATERIAL,
        reason_code="PAYLOAD_INVALID",
        message="缺少 PkgID",
    )

    assert intent.kind == RuntimeIntentKind.BLOCK
```

- [x] Update `src/workline_runtime/runtime_intent.py`.

```python
    @classmethod
    def update_context(cls, patch: dict[str, Any]) -> RuntimeIntent:
        return cls(kind=RuntimeIntentKind.UPDATE_CONTEXT, context_patch=patch)

    @classmethod
    def complete(cls, patch: dict[str, Any] | None = None) -> RuntimeIntent:
        return cls(kind=RuntimeIntentKind.COMPLETE, context_patch=patch or {})

    @classmethod
    def mark_ng(
        cls,
        *,
        reason_code: str,
        message: str,
        payload: dict[str, Any] | None = None,
        destination: Destination | None = None,
    ) -> RuntimeIntent:
        return cls(
            kind=RuntimeIntentKind.MARK_NG,
            reason_code=reason_code,
            message=message,
            payload_json=payload or {},
            destination=destination,
        )

    @classmethod
    def continue_next(
        cls,
        *,
        action: str | None = None,
        payload: dict[str, Any] | None = None,
        destination: Destination | None = None,
    ) -> RuntimeIntent:
        return cls(
            kind=RuntimeIntentKind.CONTINUE_NEXT,
            action=action,
            payload_json=payload or {},
            destination=destination or Destination.next(),
        )
```

- [x] Extend `validate_intent()` so `MARK_NG` requires `reason_code` and `message`.

```python
        if self.kind == RuntimeIntentKind.MARK_NG:
            if not self.reason_code:
                raise ValueError("MARK_NG intent requires reason_code")
            if not self.message:
                raise ValueError("MARK_NG intent requires message")
```

- [x] Update `src/workline_runtime/plugin_next.py`.

```python
    def update_context(self, patch: dict[str, Any]) -> RuntimeIntent:
        return RuntimeIntent.update_context(patch)

    def complete(self, patch: dict[str, Any] | None = None) -> RuntimeIntent:
        return RuntimeIntent.complete(patch)

    def mark_ng(
        self,
        *,
        reason_code: str,
        message: str,
        payload: dict[str, Any] | None = None,
        destination: Destination | None = None,
    ) -> RuntimeIntent:
        return RuntimeIntent.mark_ng(
            reason_code=reason_code,
            message=message,
            payload=payload,
            destination=destination,
        )

    def continue_next(
        self,
        *,
        action: str | None = None,
        payload: dict[str, Any] | None = None,
        destination: Destination | None = None,
    ) -> RuntimeIntent:
        return RuntimeIntent.continue_next(
            action=action,
            payload=payload,
            destination=destination,
        )
```

- [x] Run:

```bash
uv run pytest tests/workline_runtime/test_runtime_intent_contract.py
```

Expected:

```text
passed
```

---

## Task 2: Add an Intent-Native Plugin Boundary

This task creates a short-lived bridge so existing tests can stay green while plugins migrate. The bridge must be removed in Task 7.

- [x] Add `tests/workline_runtime/test_plugin_base_runtime_intents.py`.

```python
from types import SimpleNamespace

import pytest

from src.workline_runtime.plugin_base import WorklinePlugin, on_event
from src.workline_runtime.runtime_intent import RuntimeIntentKind


class IntentPlugin(WorklinePlugin):
    plugin_key = "intent_plugin"

    @on_event("SCAN_COMPLETED")
    async def handle_scan(self, ctx, event):
        return [
            ctx.next.update_context({"pkg_id": event["data"]["PkgID"]}),
            ctx.next.command(
                device_role="INPUT_ARM",
                action="MEASUREMENT_REEL",
                payload={"pkg_id": event["data"]["PkgID"]},
                destination_role="INPUT_ARM",
                timeout_seconds=300,
            ),
        ]


@pytest.mark.asyncio
async def test_plugin_handler_can_return_runtime_intents() -> None:
    plugin = IntentPlugin()
    ctx = SimpleNamespace(next=plugin.next if hasattr(plugin, "next") else None)
    ctx.next = __import__("src.workline_runtime.plugin_next", fromlist=["PluginNext"]).PluginNext()
    ctx.logger = SimpleNamespace(warning=lambda *_: None, error=lambda *_: None, exception=lambda *_: None)
    inbox = SimpleNamespace(
        payload_json={
            "event_type": "SCAN_COMPLETED",
            "data": {"PkgID": "L0001-1"},
        }
    )

    intents = await plugin.on_device_event(ctx, inbox)

    assert [intent.kind for intent in intents] == [
        RuntimeIntentKind.UPDATE_CONTEXT,
        RuntimeIntentKind.COMMAND,
    ]
    assert intents[1].action == "MEASUREMENT_REEL"
```

- [x] Update `src/workline_runtime/plugin_base.py` with a target result type.

```python
from collections.abc import Sequence

from src.workline_runtime.runtime_intent import BlockScope, RuntimeIntent

PluginHandlerResult = RuntimeIntent | Sequence[RuntimeIntent] | None
```

- [x] Add normalization helper in `src/workline_runtime/plugin_base.py`.

```python
def _normalize_handler_result(result: Any) -> list[RuntimeIntent]:
    if result is None:
        return []
    if isinstance(result, RuntimeIntent):
        return [result]
    if isinstance(result, Sequence) and not isinstance(result, (str, bytes, bytearray)):
        intents = list(result)
        if all(isinstance(intent, RuntimeIntent) for intent in intents):
            return intents
    raise TypeError("Plugin handler must return RuntimeIntent, list[RuntimeIntent], or None")
```

- [x] Add a new payload failure helper and stop adding new usages of `build_state_mismatch_failure`.

```python
def build_payload_invalid_block(message: str) -> RuntimeIntent:
    return RuntimeIntent.block(
        scope=BlockScope.MATERIAL,
        reason_code="PAYLOAD_INVALID",
        message=message,
        suggested_action="检查设备回调 payload",
    )
```

- [x] Change `WorklinePlugin.on_device_event()`, `on_command_result()`, `on_external_http()`, `on_manual_operation()`, and `_invoke_handler()` return type to `list[RuntimeIntent]`.

Implementation rule:

- Missing handler returns `[]`.
- Payload validation returns one `BLOCK` intent.
- No state precheck.
- No `PluginResult()` fallback.

- [x] Run:

```bash
uv run pytest tests/workline_runtime/test_plugin_base_runtime_intents.py
```

Expected:

```text
passed
```

---

## Task 3: Make PluginContext Expose Runtime Facts Instead of Plugin State

SMT currently branches by `ctx.plugin_state`. Replace that with device/topology facts so command-result routing is derived from Runtime.

- [x] Add tests in `tests/workline_runtime/test_plugin_context_runtime_facts.py`.

```python
from types import SimpleNamespace

from src.workline_runtime.plugin_context import PluginContextBuilder
from src.workline_runtime.services import WorklineRuntimeServices


def test_context_resolves_source_device_and_role_from_inbox_device_code() -> None:
    device = SimpleNamespace(
        id=18,
        device_code="ARM01",
        device_role="INPUT_ARM",
        role_index=0,
        upstream_device_id=None,
    )
    session = SimpleNamespace(run_mode="AUTO")
    workline = SimpleNamespace(
        id=30,
        plugin_key="smt_classifier",
        contract_version="1.0",
        config={},
        runtime_config_json={},
        diagnostic_profile={},
    )
    inbox = SimpleNamespace(
        payload_json={"device_code": "ARM01", "event_type": "SCAN_COMPLETED"},
        trace_id="trace-test",
    )

    ctx = PluginContextBuilder().build(
        session=session,
        workline=workline,
        devices_by_role={"INPUT_ARM": [device]},
        services=WorklineRuntimeServices(),
        inbox=inbox,
        trace_id="trace-test",
    )

    assert ctx.source_device.device_code == "ARM01"
    assert ctx.source_device_role == "INPUT_ARM"
```

- [x] Update `src/workline_runtime/plugin_context.py`.

```python
def _resolve_source_device(devices_by_role: dict[str, list[Any]], inbox: Any | None) -> Any | None:
    payload = _safe_dict(getattr(inbox, "payload_json", None))
    device_code = _safe_str(payload.get("device_code")) or _safe_str(payload.get("location"))
    if not device_code:
        normalized_input = getattr(inbox, "normalized_input", None)
        device_code = _safe_str(getattr(normalized_input, "device_code", None))
    if not device_code:
        return None
    for devices in devices_by_role.values():
        for device in devices:
            if _safe_str(getattr(device, "device_code", None)) == device_code:
                return device
    return None
```

Add fields:

```python
    source_device: Any | None = None
    source_device_role: str | None = None
```

Populate in `PluginContextBuilder.build()`:

```python
        source_device = _resolve_source_device(devices_by_role, inbox)
        source_device_role = _safe_str(getattr(source_device, "device_role", None))
```

Return:

```python
            source_device=source_device,
            source_device_role=source_device_role,
```

- [x] Keep `plugin_state` temporarily for old tests, but add a deprecation comment and do not use it in migrated plugins.

- [x] Run:

```bash
uv run pytest tests/workline_runtime/test_plugin_context_runtime_facts.py
```

Expected:

```text
passed
```

---

## Task 4: Add RuntimeIntent Effects on the Real Orchestrator Path

At this checkpoint, Runtime can apply intent lists while legacy `PluginResult` still exists for unmigrated plugins.

- [x] Add tests in `tests/workline_runtime/test_orchestrator_runtime_intents.py`.

```python
from types import SimpleNamespace

from src.workline_runtime.orchestrator import OrchestratorService
from src.workline_runtime.runtime_intent import Destination, RuntimeIntent, RuntimeIntentKind


def test_orchestrator_result_preserves_runtime_intents() -> None:
    service = OrchestratorService(lock_provider=lambda _key: None)
    result = service._process_intents(
        [
            RuntimeIntent.update_context({"pkg_id": "L0001-1"}),
            RuntimeIntent.command(
                device_role="INPUT_ARM",
                action="MEASUREMENT_REEL",
                payload={"pkg_id": "L0001-1"},
                destination=Destination.role("INPUT_ARM"),
                timeout_seconds=300,
            ),
        ],
        session=SimpleNamespace(),
    )

    assert result.success is True
    assert [intent.kind for intent in result.intents] == [
        RuntimeIntentKind.UPDATE_CONTEXT,
        RuntimeIntentKind.COMMAND,
    ]
```

- [x] Update `src/workline_runtime/orchestrator.py`.

Add to `OrchestratorResult`:

```python
    intents: list[RuntimeIntent] | None = None
```

Add method:

```python
    def _process_intents(self, intents: list[RuntimeIntent], session: Any) -> OrchestratorResult:
        for intent in intents:
            if intent.context_patch:
                try:
                    assert_context_patch_has_no_reserved_key(intent.context_patch)
                except ValueError as exc:
                    logger.exception("Plugin attempted to write reserved runtime state")
                    return _error_result(ErrorCode.PLUGIN_TRANSITION_INVALID, str(exc))

        return OrchestratorResult(success=True, intents=intents)
```

Change `_call_plugin()` return type to `list[RuntimeIntent] | PluginResult` while the bridge exists.

Change `_process_read_phase()`:

```python
        if isinstance(result, list):
            return self._process_intents(result, session)
        return self._process_result(result, session, getattr(workline, "state_machine_class", None), inbox=inbox)
```

- [x] Add `src/workline_runtime/runtime_intent_effects.py`.

Responsibilities:

- Apply `UPDATE_CONTEXT`.
- Apply `MARK_NG` to Timeline as business decision.
- Apply `COMMAND` to `DeviceCommand`, `WorklineOutbox`, waiting fields, Timeline.
- Apply `BLOCK` to `session.status = "BLOCKED"`, failure fields, Timeline.
- Apply `COMPLETE` to `session.status = "COMPLETED"`, clear waiting fields, Timeline.

Core API:

```python
class RuntimeIntentEffectApplier:
    async def apply(
        self,
        ctx: EffectApplyContext,
        intents: list[RuntimeIntent],
    ) -> None:
        for intent in intents:
            if intent.kind == RuntimeIntentKind.UPDATE_CONTEXT:
                self._apply_context(ctx, intent)
            elif intent.kind == RuntimeIntentKind.MARK_NG:
                await self._apply_mark_ng(ctx, intent)
            elif intent.kind == RuntimeIntentKind.COMMAND:
                await self._apply_command(ctx, intent)
            elif intent.kind == RuntimeIntentKind.BLOCK:
                await self._apply_block(ctx, intent)
            elif intent.kind == RuntimeIntentKind.COMPLETE:
                await self._apply_complete(ctx, intent)
            elif intent.kind == RuntimeIntentKind.CONTINUE_NEXT:
                await self._apply_continue_next(ctx, intent)
            else:
                raise ValueError(f"Unsupported runtime intent: {intent.kind}")
```

- [x] Refactor `src/celery_app/tasks/workline.py` so `_apply_orchestrator_effects()` first checks `orch_result.intents`.

```python
    if orch_result.intents is not None:
        from src.workline_runtime.runtime_intent_effects import RuntimeIntentEffectApplier

        await RuntimeIntentEffectApplier().apply(ctx, orch_result.intents)
        return
```

- [x] Do not delete legacy `_apply_command_effects()` yet. It is removed after all production plugins migrate.

- [x] Run:

```bash
uv run pytest tests/workline_runtime/test_orchestrator_runtime_intents.py tests/workline_runtime/test_runtime_intent_contract.py
```

Expected:

```text
passed
```

---

## Task 5: Migrate `inbound_tote_qc` to RuntimeIntent

This is a smaller production-like plugin and should move first.

- [x] Replace `src/workline_plugins/inbound_tote_qc/plugin.py` to return `RuntimeIntent` lists.

Rules:

- `TOTE_ARRIVED` returns `UPDATE_CONTEXT + COMMAND(WEIGH_TOTE -> WEIGH_SCALE)`.
- `WEIGH_TOTE SUCCESS` returns `UPDATE_CONTEXT + COMMAND(DIVERT_TOTE -> DIVERT_CONVEYOR)`.
- `DIVERT_TOTE SUCCESS` returns `COMPLETE`.
- Payload invalid returns `BLOCK`.
- No `PluginResultBuilder`.
- No `transition`.
- No `wait`.
- No `requires_state`.
- No `ctx.plugin_state`.
- Manifest does not set `state_machine_class`.

Pattern:

```python
@on_event("TOTE_ARRIVED")
async def handle_tote_arrived(self, ctx: PluginContext, event: ToteArrivedPayload):
    return [
        ctx.next.update_context({"tote_id": event.tote_id, "station_code": event.station_code}),
        ctx.next.command(
            device_role="WEIGH_SCALE",
            action="WEIGH_TOTE",
            payload={"tote_id": event.tote_id, "station_code": event.station_code},
            destination_role="WEIGH_SCALE",
            timeout_seconds=120,
        ),
    ]
```

- [x] Delete `src/workline_plugins/inbound_tote_qc/state_machine.py`.

- [x] Update `tests/workline_plugins/test_inbound_tote_qc_plugin.py`.

Assertions should check intent kinds and payloads, not transitions:

```python
assert [intent.kind for intent in intents] == [
    RuntimeIntentKind.UPDATE_CONTEXT,
    RuntimeIntentKind.COMMAND,
]
assert intents[1].action == "WEIGH_TOTE"
assert intents[1].device_role == "WEIGH_SCALE"
```

- [x] Run:

```bash
uv run pytest tests/workline_plugins/test_inbound_tote_qc_plugin.py
```

Expected:

```text
passed
```

---

## Task 6: Migrate `smt_classifier` to RuntimeIntent

This is the real callback chain that was tested with `PkgID=L0001-1`.

- [x] Remove these imports from `src/workline_plugins/smt_classifier/plugin.py`:

```python
PluginResultBuilder
build_state_mismatch_failure
requires_state
CommandTargetScope
SmtClassifierState
SmtClassifierStateMachine
```

- [x] Manifest must no longer set `state_machine_class`.

Before:

```python
state_machine_class=SmtClassifierStateMachine,
```

After:

```python
context_model=SmtClassifierContext,
```

- [x] Delete `src/workline_plugins/smt_classifier/state_machine.py` after tests are migrated.

- [x] Replace scan OK with intent list.

```python
return [
    ctx.next.update_context(
        SmtClassifierContext(
            device_code=event.device_code,
            barcodes=barcode_decision.barcodes,
            location=location,
            barcode=pkg_id,
        ).to_patch()
    ),
    ctx.next.command(
        device_role=self.INPUT_ARM,
        action="MEASUREMENT_REEL",
        payload=build_measurement_reel_params(pkg_id),
        destination_role=self.INPUT_ARM,
        timeout_seconds=300,
    ),
]
```

- [x] Replace scan NG helper with `MARK_NG + UPDATE_CONTEXT + COMMAND`.

```python
return [
    ctx.next.mark_ng(
        reason_code=reason_code,
        message=reason_message,
        payload={
            "barcode": barcode,
            "barcodes": barcodes,
            "location": location,
            "device_code": device_code,
        },
    ),
    ctx.next.update_context(context_patch),
    ctx.next.command(
        device_role=self.INPUT_ARM,
        action="PICK_AND_PUT",
        payload=build_pick_scan_ng_params(barcode=barcode, location=location),
        destination_role=self.INPUT_ARM,
        timeout_seconds=300,
    ),
]
```

- [x] Replace measurement success with `UPDATE_CONTEXT + COMMAND(MOVE_FORWARD -> CONVEYOR)`.

```python
return [
    ctx.next.update_context(
        SmtClassifierContext(
            pkg_id=measurement_data.PkgID,
            reel_diameter=measurement_data.reel_diameter,
            reel_thickness=measurement_data.reel_thickness,
        ).to_patch()
    ),
    ctx.next.command(
        device_role=self.CONVEYOR,
        action="MOVE_FORWARD",
        payload=build_move_forward_params(measurement_data.PkgID),
        destination_role=self.CONVEYOR,
        timeout_seconds=300,
    ),
]
```

- [x] Replace `PICK_AND_PUT SUCCESS` routing by source device role and context, not `ctx.plugin_state`.

```python
source_role = ctx.source_device_role

if source_role == self.INPUT_ARM:
    if smt_ctx.pick_place_reason == "SCAN_NG" or smt_ctx.ng_reason == "SCAN_NG":
        return [
            ctx.next.update_context(SmtClassifierContext(ng_handled=True).to_patch()),
            ctx.next.complete(),
        ]

    barcode = smt_ctx.barcode or ""
    pick_place_data = parse_pick_place_result_data(result)
    return [
        ctx.next.update_context(
            SmtClassifierContext(
                reel_diameter=pick_place_data.reel_diameter if pick_place_data else None,
                reel_thickness=pick_place_data.reel_thickness if pick_place_data else None,
            ).to_patch()
        ),
        ctx.next.command(
            device_role=self.CONVEYOR,
            action="MOVE_FORWARD",
            payload=build_move_forward_params(barcode),
            destination_role=self.CONVEYOR,
            timeout_seconds=300,
        ),
    ]

if source_role == self.OUTPUT_ARM:
    return [ctx.next.complete()]

return [
    ctx.next.block(
        scope=BlockScope.MATERIAL,
        reason_code="UNEXPECTED_DEVICE_ROLE",
        message=f"PICK_AND_PUT SUCCESS 不期望来自设备角色 {source_role}",
        suggested_action="检查设备绑定和命令结果回调来源",
    )
]
```

- [x] Replace `PICK_AND_PUT FAILED` routing by source device role and device error code.

Rules:

- `INPUT_ARM + INSPECTION_SIZE_NG/INSPECTION_THICKNESS_NG` returns `MARK_NG + UPDATE_CONTEXT + COMMAND(PICK_AND_PUT -> INPUT_ARM)`.
- `INPUT_ARM + manual-hold error` returns `BLOCK(scope=MATERIAL)`.
- `OUTPUT_ARM + manual-hold error` returns `BLOCK(scope=MATERIAL)`.
- Unknown failure returns `BLOCK(scope=COMMAND)`.

- [x] Replace `MOVE_FORWARD SUCCESS` with `UPDATE_CONTEXT + COMMAND(PICK_AND_PUT -> OUTPUT_ARM)`.

- [x] Replace terminal output success with `COMPLETE`.

- [x] Update integration plugin tests:

Files:

- `tests/integration/workline_plugins/test_smt_classifier_plugin_events.py`
- `tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py`
- `tests/workline_plugins/test_smt_classifier_state_context_diagnostics.py`

Required changes:

- Stop setting `mock_context.plugin_state`.
- Set `mock_context.source_device_role`.
- Assert `RuntimeIntentKind` sequence.
- Assert `action`, `device_role`, `destination`, `payload_json`.
- Remove `TransitionValidator` assertions.

- [x] Run:

```bash
uv run pytest \
  tests/integration/workline_plugins/test_smt_classifier_plugin_events.py \
  tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py \
  tests/workline_plugins/test_smt_classifier_state_context_diagnostics.py
```

Expected:

```text
passed
```

---

## Task 7: Remove Legacy PluginResult and State Machine Bridge

After `inbound_tote_qc` and `smt_classifier` return RuntimeIntent, delete the bridge and old contracts.

- [x] Delete `src/workline_runtime/types.py`.
- [x] Delete `src/workline_runtime/transition_validator.py`.
- [x] Delete `src/workline_runtime/device_target_resolver.py` if no longer referenced.
- [x] Delete `src/workline_runtime/plugin_state.py` after schema cleanup removes `plugin_state`.
- [x] Remove from `src/workline_runtime/plugin_base.py`:

```python
PluginResultBuilder
requires_state
step
build_state_mismatch_failure
build_payload_invalid_failure
_expected_states
PluginResult
WaitIntent
CommandIntent
FailureIntent
BusinessDecisionIntent
```

- [x] Remove from `src/workline_runtime/orchestrator.py`:

```python
TransitionValidator
TransitionDecision
_process_result()
_resolve_transition_state()
state_machine_class
transition
business_decisions
commands
wait
failure
complete
```

Final `OrchestratorResult` should be close to:

```python
@dataclass
class OrchestratorResult:
    success: bool
    error: str | None = None
    error_code: str | None = None
    error_domain: str | None = None
    intents: list[RuntimeIntent] | None = None
```

- [x] Remove from `src/workline_runtime/plugin_manifest.py`:

```python
state_machine_class: type[Any] | None = None
```

- [x] Remove `WorkLine.state_machine_class` property from `src/app/workline/models/workline.py`.

- [x] Update `src/workline_runtime/__init__.py` exports.

- [x] Delete or rewrite tests that exclusively test deleted contracts:

Delete:

- `tests/workline_runtime/test_types.py`
- `tests/workline_runtime/test_transition_validator.py`

Rewrite:

- `tests/workline_runtime/test_plugin_base.py`
- `tests/workline_runtime/test_orchestrator.py`
- `tests/workline_runtime/test_null_plugin.py`

- [x] Run old-symbol search for Task 7 symbols:

```bash
rg -n "PluginResult|PluginResultBuilder|TransitionValidator|TransitionDecision|requires_state|state_machine_class|WaitIntent|CommandIntent|BusinessDecisionIntent|FailureIntent|current_wait_token|plugin_state|issued_plugin_state" src tests -g '*.py'
```

Expected after Task 8 schema cleanup:

```text
no matches
```

At the end of this task, `current_wait_token`, `plugin_state`, and `issued_plugin_state` may still appear in SQLModel/API files until Task 8.

Task 7 checkpoint result:

```bash
rg -n "PluginResult|PluginResultBuilder|TransitionValidator|TransitionDecision|requires_state|state_machine_class|WaitIntent|CommandIntent|BusinessDecisionIntent|FailureIntent|workline_runtime\.types|transition_validator|device_target_resolver" src tests -g '*.py'
```

Result: no matches.

---

## Task 8: Schema and API Cleanup

The system is unreleased, so remove old schema fields rather than preserving compatibility.

- [x] Generate migration with Alembic.

```bash
uv run alembic revision -m "drop legacy plugin state fields"
```

Expected:

```text
Generating ...drop_legacy_plugin_state_fields.py ... done
```

- [x] Edit the generated migration to drop legacy columns and indexes.

Schema fields to remove:

- `workline_sessions.plugin_state`
- `workline_sessions.current_wait_token`
- `device_commands.issued_plugin_state`

Do not drop `workline_sessions.awaiting_command_id`; it is Runtime-owned command correlation, not plugin state.

- [x] Update `src/app/workline/models/session.py`.

Remove:

```python
plugin_state: str | None
current_wait_token: str | None
```

- [x] Update `src/app/device/models/command.py`.

Remove:

```python
issued_plugin_state: str | None
```

- [x] Update query/trace models to expose runtime facts instead of plugin facts.

Files:

- `src/app/workline/models/runtime.py`
- `src/app/workline/services/runtime_query_service.py`
- `src/app/workline/services/trace_response_builder.py`
- `src/app/workline/services/trace_query_service.py`
- `src/app/workline/repositories/outbox_repository.py`

Replace old fields:

```text
plugin_state
current_wait_token
issued_plugin_state
```

With target facts:

```text
current_device_id
current_device_role
current_action
awaiting_command_id
wait_reason
blocked_reason
```

- [x] Update reconciliation/hold-release services to use `awaiting_command_id` and `deadline_at`, not wait token.

Files:

- `src/app/workline/services/runtime_reconciliation_service.py`
- `src/app/workline/services/runtime_hold_release_service.py`
- `src/app/workline/repositories/session_repository.py`

Rules:

- Command callback correlation uses `command_code -> DeviceCommand.id -> session.awaiting_command_id`.
- Timeout recovery uses `deadline_at`.
- There is no string wait token.

- [x] Run:

```bash
uv run pytest \
  tests/workline_runtime/test_runtime_reconciliation_service.py \
  tests/workline_runtime/test_runtime_hold_release_service.py \
  tests/workline_runtime/test_ng_return_item_service.py
```

Expected:

```text
passed
```

---

## Task 9: Collapse Duplicate Target Resolution

After legacy `CommandIntent` is gone, only `Destination` + topology may resolve command targets.

- [x] Ensure all command target resolution flows through:

```python
src/workline_runtime/material_target_resolver.py
```

- [x] Delete or fully retire:

```python
src/workline_runtime/device_target_resolver.py
```

- [x] Add tests in `tests/workline_runtime/test_runtime_intent_effects.py`.

Required cases:

- `Destination.current()` targets source device.
- `Destination.next()` targets topology downstream.
- `Destination.role("CONVEYOR")` targets first matching reachable role.
- `Destination.device(id)` rejects devices outside the workline topology.
- `Destination.ng_route()` resolves NG route from topology/config.
- Unreachable destination creates a `BLOCK`/failure effect, not a plugin exception.

- [x] Run:

```bash
uv run pytest tests/workline_runtime/test_runtime_intent_effects.py tests/workline_runtime/test_material_target_resolver.py
```

Expected:

```text
passed
```

---

## Task 10: Update Documentation to the New Contract

- [x] Update `docs/business/workline_plugin_architecture_design.md`.

Required wording:

```text
插件 handler 返回 RuntimeIntent 或 list[RuntimeIntent]。
插件不得返回 PluginResult。
插件不得声明 state_machine_class。
插件不得使用 requires_state。
Runtime 根据拓扑、当前命令、Session lifecycle 和 RuntimeEvent 校验流程。
```

- [x] Update or archive old docs that still teach `PluginResult`.

Files to inspect:

- `docs/business/wms_rcs_interface_requirements.md`
- `docs/business/workline_smt_classifier_runtime_flow.md`
- `docs/business/workline_plugin_refactor_next_phase_plan.md`

Rule:

- If a document describes historical implementation, move wording under `Legacy notes`.
- If a document describes current target architecture, replace `PluginResult` with `RuntimeIntent`.

- [x] Update `docs/superpowers/plans/2026-05-11-workline-material-flow-runtime.md` with a completion note that the physical cleanup is covered by this plan.

- [x] Run:

```bash
rg -n "PluginResult|TransitionValidator|state_machine_class|requires_state|plugin_state|current_wait_token|issued_plugin_state" docs src tests -g '*.md' -g '*.py'
```

Expected:

```text
Only Legacy notes and this cleanup plan may match.
```

---

## Task 11: End-to-End Verification with Callback Data

- [x] Start backend if not already running:

```bash
uv run uvicorn main:app --reload --host 0.0.0.0 --port 8001
```

- [x] Send the known callback payload with a fresh `PkgID`.

```bash
curl -X 'POST' 'http://localhost:8001/api/v1/callback/event' \
  -H "Content-Type: application/json" \
  -d '{
  "device_code": "ARM01",
  "event_type": "SCAN_COMPLETED",
  "timestamp": '$(date +%s000)',
  "data": {
  "location": "ARM01",
  "HHPN": "620100L00-011-G",
  "MfrPN": "CC0402JRNPO9BN220",
  "Qty": "7387",
  "DateCode": "122625",
  "LotCode": "8904936031",
  "PkgID": "L0001-CLEANUP-1"
  }
  }'
```

Expected response:

```json
{"success": true}
```

Observed response uses the current response envelope:

```json
{"code":"1000","message":"Event received","data":{"status":"submitted","device_code":"ARM01"}}
```

- [x] Verify DB chain:

Expected Runtime/TIMELINE facts:

- Session lifecycle reaches `COMPLETED`.
- No `plugin_state` column or field is read.
- No `current_wait_token` column or field is read.
- Commands are created in order:
  - `MEASUREMENT_REEL` on `INPUT_ARM`
  - `MOVE_FORWARD` on `CONVEYOR`
  - `PICK_AND_PUT` on `OUTPUT_ARM`
- Each command has `trace_id`.
- Each command result correlates through `awaiting_command_id`.
- Timeline includes:
  - `PLUGIN_DECISION_MADE`
  - `COMMAND_CREATED` or equivalent command dispatch timeline
  - `MATERIAL_ENTERED_DEVICE`
  - `PROCESS_COMPLETED`

Observed on 2026-05-12:

- `PkgID=L0001-CLEANUP-20260512-1328`
- `trace_id=trace_68a006ed702b4b75ae2ffd3310faab6b`
- `session_id=603`, `status=COMPLETED`
- Commands completed in order:
  - `MEASUREMENT_REEL` on `ARM01` / `INPUT_ARM`
  - `MOVE_FORWARD` on `PIPELINE01` / `CONVEYOR`
  - `PICK_AND_PUT` on `ARM02` / `OUTPUT_ARM`
- Live dev DB still had not applied the drop-column migration at verification time, but legacy columns were null and source/tests have no reads or writes for them.
- Timeline included `COMMAND_SENT`, `WAIT_STARTED`, `COMMAND_ACKED`, and `SESSION_COMPLETED`.

- [x] Run focused regression:

```bash
uv run pytest \
  tests/workline_runtime \
  tests/workline_plugins \
  tests/integration/workline_plugins \
  tests/integration/workline_runtime
```

Expected:

```text
passed
```

- [x] Run full backend checks:

```bash
uv run ruff format --check .
uv run ruff check .
uv run pytest tests/
```

Expected:

```text
All checks passed
```

---

## Final Acceptance Checklist

- [x] `rg -n "PluginResult|PluginResultBuilder|TransitionValidator|TransitionDecision|requires_state|state_machine_class|WaitIntent|CommandIntent|BusinessDecisionIntent|FailureIntent" src tests -g '*.py'` returns no matches.
- [x] `rg -n "plugin_state|current_wait_token|issued_plugin_state" src tests -g '*.py'` returns no matches.
- [x] `src/workline_plugins/smt_classifier/plugin.py` returns only `RuntimeIntent` or `list[RuntimeIntent]`.
- [x] `src/workline_plugins/inbound_tote_qc/plugin.py` returns only `RuntimeIntent` or `list[RuntimeIntent]`.
- [x] No plugin imports `transitions`.
- [x] No plugin owns a state machine class.
- [x] Runtime command creation uses `Destination` and topology resolver.
- [x] Runtime command result correlation uses `awaiting_command_id`.
- [x] Material position is visible from Runtime/TIMELINE/projection facts, not plugin state.
- [x] Known callback payload with fresh `PkgID` completes the SMT chain.
- [x] Full `uv run pytest tests/` passes.
- [x] Ruff format and lint pass.

---

## Execution Notes

- This plan intentionally removes compatibility because WES is unreleased.
- If an intermediate task adds a bridge, the same plan must later delete it. Do not stop after Task 4.
- Do not preserve old fields in API responses “just in case”.
- Do not keep `getattr(..., default)` fallbacks for deleted runtime shape except at external input boundaries.
- Do not add a second command target resolver.
- Do not let plugin tests assert internal Session state; assert returned intents.
- Keep `awaiting_command_id` because it is Runtime command correlation, not plugin state ownership.
