# SMT 货架补充与满箱交换事件拆分 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 SMT 粗分机“请求新货架补充”和“通知满箱交换插件处理旧货架”拆成两个明确工作流，并保证初次无货架时只请求新货架、不触发满箱交换。

**Architecture:** SMT 插件只负责当前料盘 session 的料格分配与新货架补充等待；当存在旧货架且无可用料格时，SMT 额外发出一个内部 `DEVICE_EVENT` 给 `smt_full_box_exchange` 插件，由该插件独立判断并执行旧货架满箱交换。运行时新增一个非命令型 RuntimeIntent 来落地内部设备事件，避免插件直接访问数据库或跨层调用 Service。

**Tech Stack:** Python 3.13, FastAPI runtime plugin framework, SQLModel/SQLAlchemy async session, pytest, Ruff, GitNexus.

---

## 业务规则

1. `active_bin_rack` 不存在：SMT 当前 session 只请求新货架补充，等待 `WMS_RACK_ARRIVED` 后恢复分配；不向满箱交换插件发事件。
2. `active_bin_rack` 存在但无兼容料格：SMT 当前 session 同时做两件事：
   - 向满箱交换插件发内部 `DEVICE_EVENT`，事件类型为 `SINGLE_LAYER_RACK_RELEASED`，表示当前货架已从 SMT 当前工作上下文释放，由满箱交换插件判断和执行旧货架处理。
   - 向 WMS/RCS 发起新货架补充外部请求，当前 SMT session 等待 `WMS_RACK_ARRIVED`。
3. 新货架到位后，SMT 恢复当前 session 并重新分配料格；只有出料机械臂成功放入料格后，当前 SMT session 才完成。
4. 如果新货架仍无可用料格，SMT 再次按规则发起补架；若这次回调带来了新的 `active_bin_rack`，也应对该旧货架发内部释放事件。
5. 满箱交换插件不负责恢复 SMT 当前料盘 session；它只处理旧货架是否需要交换、如何交换、交换结果记录。

## File Structure

- Modify: `src/workline_runtime/runtime_intent.py`
  - 新增非命令型 `RuntimeIntentKind.DEVICE_EVENT` 与 `RuntimeIntent.device_event(...)` 构造器。
- Modify: `src/workline_runtime/plugin_next.py`
  - 新增 `PluginNext.device_event(...)`，供插件声明内部事件。
- Modify: `src/workline_runtime/runtime_intent_effects.py`
  - 新增 `_apply_device_event(...)`，通过 `WorklineInboxService.create_device_event_inbox(..., auto_commit=False)` 创建内部事件 inbox。
- Modify: `src/app/resource/services/smt_rack_bin_scheduling_service.py`
  - 将当前 `RACK_EXCHANGE_REQUIRED` 决策拆成“新货架补充请求”和“可选旧货架释放事件”。
- Modify: `src/workline_plugins/smt_classifier/plugin.py`
  - SMT 插件根据调度决策返回 `UPDATE_CONTEXT` + 可选 `DEVICE_EVENT` + `EXTERNAL_REQUEST`。
  - `WMS_RACK_ARRIVED` 回调使用 `rack_supply` 上下文恢复 session。
- Modify: `src/workline_plugins/smt_full_box_exchange/contract.py`
  - 明确 SMT 来源的 `SINGLE_LAYER_RACK_RELEASED` 事件字段。
- Modify: `src/workline_plugins/smt_full_box_exchange/plugin.py`
  - 识别 SMT 释放原因并进入满箱交换判断流程。
- Modify: `tests/workline_runtime/test_runtime_intent_effects.py`
  - 覆盖内部 `DEVICE_EVENT` intent 落库行为。
- Modify: `tests/workline_runtime/test_plugin_next.py`
  - 覆盖 `PluginNext.device_event(...)`。
- Modify: `tests/workline_runtime/test_smt_rack_bin_scheduling_service.py`
  - 覆盖“初次无货架只补架”和“有旧货架无格位则补架 + 发释放事件”。
- Modify: `tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py`
  - 覆盖 SMT 插件意图序列、回调恢复、二次无格位继续补架。
- Modify: `tests/workline_plugins/test_smt_full_box_exchange_plugin.py`
  - 覆盖满箱交换插件消费 SMT 释放事件。
- Modify: `tests/mock/smt_classifier/rack_exchange_mock.py`
  - 将 mock 的请求类型语义从“交换并补充”调整为“新货架补充”。
- Modify: `tests/mock/smt_classifier/test_smt_classifier_mock.py`
  - 同步 mock 断言。
- Modify: `docs/architecture/SRS.md`
  - 写清 SMT 与满箱交换两个插件的职责边界。

## Task 1: Runtime 支持内部 DEVICE_EVENT Intent

**Files:**
- Modify: `src/workline_runtime/runtime_intent.py`
- Modify: `src/workline_runtime/plugin_next.py`
- Modify: `src/workline_runtime/runtime_intent_effects.py`
- Test: `tests/workline_runtime/test_runtime_intent_effects.py`
- Test: `tests/workline_runtime/test_plugin_next.py`

- [ ] **Step 1: 影响分析**

Use GitNexus MCP:

```text
impact(repo="wes_backend", target="RuntimeIntent", file_path="src/workline_runtime/runtime_intent.py", direction="upstream", includeTests=true)
impact(repo="wes_backend", target="PluginNext", file_path="src/workline_runtime/plugin_next.py", direction="upstream", includeTests=true)
impact(repo="wes_backend", target="RuntimeIntentEffectApplier", file_path="src/workline_runtime/runtime_intent_effects.py", direction="upstream", includeTests=true)
```

Expected: 如返回 HIGH 或 CRITICAL，先暂停并向用户说明风险；LOW/MEDIUM 可继续。

- [ ] **Step 2: 写失败测试**

在 `tests/workline_runtime/test_plugin_next.py` 增加断言：

```python
intent = PluginNext().device_event(
    device_code="SMT-RACK-RELEASE",
    event_type="SINGLE_LAYER_RACK_RELEASED",
    data={"rack_release_id": "release-001"},
    event_id="smt-release:release-001",
)
assert intent.kind == RuntimeIntentKind.DEVICE_EVENT
assert intent.payload_json["device_code"] == "SMT-RACK-RELEASE"
assert intent.payload_json["event_type"] == "SINGLE_LAYER_RACK_RELEASED"
```

在 `tests/workline_runtime/test_runtime_intent_effects.py` 增加断言：

```python
intent = RuntimeIntent.device_event(
    device_code="SMT-RACK-RELEASE",
    event_type="SINGLE_LAYER_RACK_RELEASED",
    data={"rack_release_id": "release-001"},
    event_id="smt-release:release-001",
)
await RuntimeIntentEffectApplier(inbox_service=recording_inbox_service).apply(ctx, [intent])
assert recording_inbox_service.created["event_type"] == "SINGLE_LAYER_RACK_RELEASED"
assert ctx["session"].current_wait_type is None
```

- [ ] **Step 3: 跑失败测试**

Run:

```bash
uv run pytest tests/workline_runtime/test_plugin_next.py::test_device_event_intent_builder tests/workline_runtime/test_runtime_intent_effects.py::test_device_event_intent_creates_device_event_inbox_without_waiting_current_session -q
```

Expected: FAIL，原因是 `device_event` 或 `RuntimeIntentKind.DEVICE_EVENT` 尚不存在。

- [ ] **Step 4: 实现 RuntimeIntent 与 PluginNext**

实现约束：
- `RuntimeIntentKind.DEVICE_EVENT = "DEVICE_EVENT"`。
- `RuntimeIntent.device_event(...)` 将 `device_code/event_type/timestamp/data/event_id/causation_id/canonical_event_type` 写入 `payload_json`。
- `PluginNext.device_event(...)` 只代理构造 RuntimeIntent，不做任何数据库调用。
- `RuntimeIntent` validator 要求 `DEVICE_EVENT` 至少包含 `device_code`、`event_type`、`data`。

- [ ] **Step 5: 实现 effect 落库**

实现约束：
- `_SUPPORTED_INTENT_KINDS` 包含 `RuntimeIntentKind.DEVICE_EVENT`。
- `_is_command_producing_intent(...)` 不把 `DEVICE_EVENT` 视为命令型 intent。
- `RuntimeIntentEffectApplier.__init__` 接受可注入的 `inbox_service`，测试用 stub，生产默认导入 `workline_inbox_service`。
- `_apply_device_event(...)` 调用 `create_device_event_inbox(..., auto_commit=False)`，并传递 `trace_id`、`event_id`、`causation_id`。
- 当前 session 不进入 `EXTERNAL_HTTP` 或 `COMMAND_RESULT` 等等待态。

- [ ] **Step 6: 跑 Runtime 测试**

Run:

```bash
uv run pytest tests/workline_runtime/test_plugin_next.py tests/workline_runtime/test_runtime_intent_effects.py -q
```

Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add src/workline_runtime/runtime_intent.py src/workline_runtime/plugin_next.py src/workline_runtime/runtime_intent_effects.py tests/workline_runtime/test_plugin_next.py tests/workline_runtime/test_runtime_intent_effects.py
git commit -m "feat(workline): 支持插件发出内部设备事件"
```

## Task 2: SMT 调度决策拆分补架请求与旧货架释放事件

**Files:**
- Modify: `src/app/resource/services/smt_rack_bin_scheduling_service.py`
- Test: `tests/workline_runtime/test_smt_rack_bin_scheduling_service.py`

- [ ] **Step 1: 影响分析**

Use GitNexus MCP:

```text
impact(repo="wes_backend", target="SmtRackBinSchedulingService", file_path="src/app/resource/services/smt_rack_bin_scheduling_service.py", direction="upstream", includeTests=true)
impact(repo="wes_backend", target="SmtRackBinSchedulingDecision", file_path="src/app/resource/services/smt_rack_bin_scheduling_service.py", direction="upstream", includeTests=true)
```

Expected: 如返回 HIGH 或 CRITICAL，先暂停并向用户说明风险；LOW/MEDIUM 可继续。

- [ ] **Step 2: 写“初次无货架”失败测试**

在 `tests/workline_runtime/test_smt_rack_bin_scheduling_service.py` 增加断言：

```python
decision = service.plan_allocation("PKG-001", context=_context(active_bin_rack=None))
assert decision.kind == "RACK_SUPPLY_REQUIRED"
assert decision.rack_supply_request is not None
assert decision.rack_release_event is None
assert decision.rack_supply_request.payload["request_type"] == "SMT_RACK_SUPPLY"
assert decision.rack_supply_request.payload["actions"] == ["SUPPLY_EMPTY_RACK"]
```

- [ ] **Step 3: 写“有旧货架无可用格”失败测试**

同文件增加断言：

```python
decision = service.plan_allocation("PKG-001", context=_context(active_bin_rack=full_rack))
assert decision.kind == "RACK_SUPPLY_REQUIRED"
assert decision.rack_supply_request is not None
assert decision.rack_release_event is not None
assert decision.rack_release_event.event_type == "SINGLE_LAYER_RACK_RELEASED"
assert decision.rack_release_event.data["single_layer_rack_id"] == full_rack["rack_id"]
assert decision.rack_release_event.data["release_reason_code"] == "NO_COMPATIBLE_OR_EMPTY_CELL"
```

- [ ] **Step 4: 跑失败测试**

Run:

```bash
uv run pytest tests/workline_runtime/test_smt_rack_bin_scheduling_service.py::test_plan_allocation_without_active_rack_requests_supply_only tests/workline_runtime/test_smt_rack_bin_scheduling_service.py::test_plan_allocation_with_unusable_active_rack_requests_supply_and_release_event -q
```

Expected: FAIL，原因是 `RACK_SUPPLY_REQUIRED`、`rack_supply_request` 或 `rack_release_event` 尚不存在。

- [ ] **Step 5: 调整领域模型**

实现约束：
- 新增 `SmtRackSupplyRequest`，字段沿用当前外部请求需要的 `dispatch_key/target_code/payload/timeout_seconds/source_system`。
- 新增 `SmtRackReleaseEvent`，字段为 `device_code/event_type/data/event_id/causation_id/canonical_event_type`。
- `SmtRackBinSchedulingDecisionKind` 增加 `"RACK_SUPPLY_REQUIRED"`。
- `SmtRackBinSchedulingDecision` 增加 `rack_supply_request` 与 `rack_release_event`。
- `external_request` 保持兼容读取，返回 `rack_supply_request`。
- `full_box_exchange_request` 保持兼容读取，但不再表示满箱交换插件请求；实现后续任务会从 SMT 插件移除该字段使用。

- [ ] **Step 6: 调整调度策略**

实现约束：
- `active_bin_rack` 不存在时，返回 `RACK_SUPPLY_REQUIRED`，只填 `rack_supply_request`。
- `active_bin_rack` 存在但无可用料格时，返回 `RACK_SUPPLY_REQUIRED`，同时填 `rack_supply_request` 与 `rack_release_event`。
- 新货架补充 payload 使用 `request_type = "SMT_RACK_SUPPLY"`，`actions = ["SUPPLY_EMPTY_RACK"]`。
- 旧货架释放事件 payload 使用 `event_type = "SINGLE_LAYER_RACK_RELEASED"`，并包含 `rack_release_id`、`single_layer_rack_id`、`single_layer_rack_code`、`source_classifier_line_code`、`source_task_batch_id`、`release_reason_code`、`bin_snapshots`。
- `rack_release_event` 的 `device_code` 从配置读取 `smt_full_box_release_device_code` 或 `full_box_release_device_code`；缺失时返回 `BLOCKED`，reason 为 `FULL_BOX_RELEASE_EVENT_DEVICE_MISSING`。

- [ ] **Step 7: 跑调度服务测试**

Run:

```bash
uv run pytest tests/workline_runtime/test_smt_rack_bin_scheduling_service.py -q
```

Expected: PASS。

- [ ] **Step 8: 提交**

```bash
git add src/app/resource/services/smt_rack_bin_scheduling_service.py tests/workline_runtime/test_smt_rack_bin_scheduling_service.py
git commit -m "feat(smt): 拆分货架补充请求与旧货架释放事件"
```

## Task 3: SMT 插件按拆分后的决策返回意图序列

**Files:**
- Modify: `src/workline_plugins/smt_classifier/plugin.py`
- Test: `tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py`

- [ ] **Step 1: 影响分析**

Use GitNexus MCP:

```text
impact(repo="wes_backend", target="handle_conveyor_success", file_path="src/workline_plugins/smt_classifier/plugin.py", direction="upstream", includeTests=true)
impact(repo="wes_backend", target="_handle_rack_exchange_callback", file_path="src/workline_plugins/smt_classifier/plugin.py", direction="upstream", includeTests=true)
```

Expected: 如返回 HIGH 或 CRITICAL，先暂停并向用户说明风险；LOW/MEDIUM 可继续。

- [ ] **Step 2: 写“无货架只补架”失败测试**

在 `tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py` 增加断言：

```python
result = await plugin.handle_conveyor_success(mock_context_without_active_rack, conveyor_result)
assert [intent.kind for intent in result] == [RuntimeIntentKind.UPDATE_CONTEXT, RuntimeIntentKind.EXTERNAL_REQUEST]
assert result[0].context_patch["rack_supply"]["reason_code"] == "NO_ACTIVE_RACK"
assert "rack_release_event" not in result[0].context_patch
assert "full_box_exchange" not in result[0].context_patch
assert result[1].payload_json["request_type"] == "SMT_RACK_SUPPLY"
```

- [ ] **Step 3: 写“有旧货架无格位”失败测试**

同文件增加断言：

```python
result = await plugin.handle_conveyor_success(mock_context_with_full_rack, conveyor_result)
assert [intent.kind for intent in result] == [
    RuntimeIntentKind.UPDATE_CONTEXT,
    RuntimeIntentKind.DEVICE_EVENT,
    RuntimeIntentKind.EXTERNAL_REQUEST,
]
assert result[1].payload_json["event_type"] == "SINGLE_LAYER_RACK_RELEASED"
assert result[1].payload_json["data"]["single_layer_rack_id"] == "NHW-1CLJ-0096"
assert result[2].payload_json["request_type"] == "SMT_RACK_SUPPLY"
assert "full_box_exchange" not in result[0].context_patch
```

- [ ] **Step 4: 写“到位后仍无格位继续补架”失败测试**

同文件增加断言：

```python
result = await plugin.on_external_http(mock_context_waiting_supply, wms_rack_arrived_with_unusable_rack)
assert [intent.kind for intent in result] == [
    RuntimeIntentKind.UPDATE_CONTEXT,
    RuntimeIntentKind.DEVICE_EVENT,
    RuntimeIntentKind.EXTERNAL_REQUEST,
]
assert result[0].context_patch["rack_supply"]["status"] == "REQUESTED"
assert result[2].payload_json["request_type"] == "SMT_RACK_SUPPLY"
```

- [ ] **Step 5: 跑失败测试**

Run:

```bash
uv run pytest tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py::TestSmtClassifierPluginCommandResults::test_conveyor_success_without_active_rack_requests_supply_only tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py::TestSmtClassifierPluginCommandResults::test_conveyor_success_with_unusable_active_rack_emits_release_event_and_supply_request tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py::TestSmtClassifierPluginCommandResults::test_external_rack_arrived_without_usable_cell_requests_next_supply -q
```

Expected: FAIL，原因是 SMT 插件仍把旧逻辑当成 `full_box_exchange` 外部请求。

- [ ] **Step 6: 实现 SMT 意图构造**

实现约束：
- 新增私有 helper，例如 `_rack_supply_required_intents(...)`，负责把 `RACK_SUPPLY_REQUIRED` 决策转换成 RuntimeIntent 序列。
- context 使用 `rack_supply` 记录当前 SMT 等待的新货架补充请求。
- 如果决策带 `rack_release_event`，返回 `ctx.next.device_event(...)`。
- 返回的新货架补充请求仍是 `ctx.next.external_request(...)`，因此当前 SMT session 会等待 `EXTERNAL_HTTP`。
- 不再向 SMT session 写入 `full_box_exchange`。

- [ ] **Step 7: 调整 WMS_RACK_ARRIVED 恢复逻辑**

实现约束：
- `_rack_exchange_from_context(...)` 改为 `_rack_supply_from_context(...)`，优先读取 `rack_supply`。
- 为兼容已有 session，可短期 fallback 读取旧 `rack_exchange`。
- 新货架到位后重新调用 `_allocate_bin(...)`。
- 如果重新分配仍返回 `RACK_SUPPLY_REQUIRED`，返回新的补架意图序列，不直接 `BLOCK`。
- 分配成功时下发出料机械臂 `PICK_AND_PUT`，并在 context 中写入 `active_bin_rack`、`rack_supply.status = "ARRIVED"`、`bin_location`。

- [ ] **Step 8: 跑 SMT 集成测试**

Run:

```bash
uv run pytest tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py -q
```

Expected: PASS。

- [ ] **Step 9: 提交**

```bash
git add src/workline_plugins/smt_classifier/plugin.py tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py
git commit -m "fix(smt): 补架等待与满箱交换事件解耦"
```

## Task 4: 满箱交换插件消费 SMT 释放事件

**Files:**
- Modify: `src/workline_plugins/smt_full_box_exchange/contract.py`
- Modify: `src/workline_plugins/smt_full_box_exchange/plugin.py`
- Test: `tests/workline_plugins/test_smt_full_box_exchange_plugin.py`

- [ ] **Step 1: 影响分析**

Use GitNexus MCP:

```text
impact(repo="wes_backend", target="SmtFullBoxExchangePlugin", file_path="src/workline_plugins/smt_full_box_exchange/plugin.py", direction="upstream", includeTests=true)
impact(repo="wes_backend", target="resolve_smt_full_box_exchange_business_key", file_path="src/workline_plugins/smt_full_box_exchange/contract.py", direction="upstream", includeTests=true)
```

Expected: 如返回 HIGH 或 CRITICAL，先暂停并向用户说明风险；LOW/MEDIUM 可继续。

- [ ] **Step 2: 写 SMT 释放事件测试**

在 `tests/workline_plugins/test_smt_full_box_exchange_plugin.py` 增加断言：

```python
result = await SmtFullBoxExchangePlugin().on_device_event(
    _ctx(config=full_box_exchange_config),
    _inbox(_smt_release_payload(reason_code="NO_COMPATIBLE_OR_EMPTY_CELL")),
)
assert [intent.kind for intent in result] == [
    RuntimeIntentKind.UPDATE_CONTEXT,
    RuntimeIntentKind.EXTERNAL_REQUEST,
]
assert result[0].context_patch["exchange_required"] is True
assert result[1].payload_json["request_type"] == "SMT_FULL_BOX_EXCHANGE"
assert result[1].payload_json["source_classifier_line_code"] == "WL-SMT-CLASSIFIER-01"
```

- [ ] **Step 3: 跑失败测试**

Run:

```bash
uv run pytest tests/workline_plugins/test_smt_full_box_exchange_plugin.py::test_smt_release_event_requests_exchange_even_when_usage_snapshot_is_low -q
```

Expected: FAIL，原因是当前策略只按 usage/status 判断，SMT 释放原因未强制进入交换流程。

- [ ] **Step 4: 更新满箱交换判断**

实现约束：
- `contract.py` 增加 SMT 释放原因常量集合，至少包含 `NO_COMPATIBLE_OR_EMPTY_CELL`。
- `plugin.py` 在 `_exchange_bins(...)` 或调用处识别 `release_reason_code`。
- 当 `release_reason_code` 属于 SMT 释放原因集合时，使用事件中的 `bins` 或 `bin_snapshots` 作为 `exchange_bins` 候选，进入现有外部请求流程。
- 保留原有 usage/status 判断，非 SMT 释放事件行为不变。

- [ ] **Step 5: 跑满箱交换插件测试**

Run:

```bash
uv run pytest tests/workline_plugins/test_smt_full_box_exchange_plugin.py -q
```

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add src/workline_plugins/smt_full_box_exchange/contract.py src/workline_plugins/smt_full_box_exchange/plugin.py tests/workline_plugins/test_smt_full_box_exchange_plugin.py
git commit -m "fix(smt-full-box): 处理SMT释放货架事件"
```

## Task 5: Mock、SRS 与沙箱数据语义同步

**Files:**
- Modify: `tests/mock/smt_classifier/rack_exchange_mock.py`
- Modify: `tests/mock/smt_classifier/test_smt_classifier_mock.py`
- Modify: `docs/architecture/SRS.md`

- [ ] **Step 1: 写 mock 失败测试**

在 `tests/mock/smt_classifier/test_smt_classifier_mock.py` 增加或调整断言：

```python
request = rack_exchange_mock.RackExchangeRequest(
    request_type="SMT_RACK_SUPPLY",
    dispatch_key="external:smt_classifier:trace-001:RACK_SUPPLY",
    actions=["SUPPLY_EMPTY_RACK"],
)
record = await rack_exchange_mock.RackExchangeSimulator(mode="success").execute_request(request)
assert record.callback_type == "WMS_RACK_ARRIVED"
```

- [ ] **Step 2: 跑失败测试**

Run:

```bash
uv run pytest tests/mock/smt_classifier/test_smt_classifier_mock.py::test_rack_supply_mock_accepts_smt_rack_supply_request -q
```

Expected: FAIL，原因是 mock 请求类型仍以旧“交换并补充”语义为主。

- [ ] **Step 3: 调整 mock**

实现约束：
- `RackExchangeRequest.request_type` 默认值改为 `SMT_RACK_SUPPLY`。
- mock 文案从“换架请求”调整为“新货架补充请求”。
- 成功回调仍为 `WMS_RACK_ARRIVED`，携带 Excel 协议格式的 `active_bin_rack`。
- 保留旧 `SMT_RACK_EXCHANGE_AND_SUPPLY` 作为兼容输入，不作为新测试默认值。

- [ ] **Step 4: 更新 SRS**

在 `docs/architecture/SRS.md` 写清：
- 初次无货架：SMT 请求新货架补充，不触发满箱交换。
- 有当前货架但无可用料格：SMT 发 `SINGLE_LAYER_RACK_RELEASED` 内部事件给满箱交换插件，同时请求新货架补充。
- 满箱交换插件只处理旧货架判断与交换，不恢复 SMT 当前料盘 session。

- [ ] **Step 5: 跑 mock 测试**

Run:

```bash
uv run pytest tests/mock/smt_classifier/test_smt_classifier_mock.py -q
```

Expected: PASS。

- [ ] **Step 6: 提交**

```bash
git add tests/mock/smt_classifier/rack_exchange_mock.py tests/mock/smt_classifier/test_smt_classifier_mock.py docs/architecture/SRS.md
git commit -m "docs(smt): 明确补架与满箱交换职责边界"
```

## Task 6: 回归验证与提交前检查

**Files:**
- No source edits expected in this task.

- [ ] **Step 1: 跑核心目标测试**

Run:

```bash
uv run pytest \
  tests/workline_runtime/test_smt_rack_bin_scheduling_service.py \
  tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py \
  tests/workline_plugins/test_smt_full_box_exchange_plugin.py \
  tests/mock/smt_classifier/test_smt_classifier_mock.py \
  -q
```

Expected: PASS。

- [ ] **Step 2: 跑插件目录回归**

Run:

```bash
uv run pytest tests/workline_plugins -q
```

Expected: PASS，允许既有 skip。

- [ ] **Step 3: 跑 Runtime 相关回归**

Run:

```bash
uv run pytest tests/workline_runtime/test_runtime_intent_effects.py tests/workline_runtime/test_plugin_next.py -q
```

Expected: PASS。

- [ ] **Step 4: Ruff 与 diff 检查**

Run:

```bash
uv run ruff format \
  src/workline_runtime/runtime_intent.py \
  src/workline_runtime/plugin_next.py \
  src/workline_runtime/runtime_intent_effects.py \
  src/app/resource/services/smt_rack_bin_scheduling_service.py \
  src/workline_plugins/smt_classifier/plugin.py \
  src/workline_plugins/smt_full_box_exchange/contract.py \
  src/workline_plugins/smt_full_box_exchange/plugin.py \
  tests/workline_runtime/test_runtime_intent_effects.py \
  tests/workline_runtime/test_plugin_next.py \
  tests/workline_runtime/test_smt_rack_bin_scheduling_service.py \
  tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py \
  tests/workline_plugins/test_smt_full_box_exchange_plugin.py \
  tests/mock/smt_classifier/rack_exchange_mock.py \
  tests/mock/smt_classifier/test_smt_classifier_mock.py
uv run ruff check \
  src/workline_runtime/runtime_intent.py \
  src/workline_runtime/plugin_next.py \
  src/workline_runtime/runtime_intent_effects.py \
  src/app/resource/services/smt_rack_bin_scheduling_service.py \
  src/workline_plugins/smt_classifier/plugin.py \
  src/workline_plugins/smt_full_box_exchange/contract.py \
  src/workline_plugins/smt_full_box_exchange/plugin.py \
  tests/workline_runtime/test_runtime_intent_effects.py \
  tests/workline_runtime/test_plugin_next.py \
  tests/workline_runtime/test_smt_rack_bin_scheduling_service.py \
  tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py \
  tests/workline_plugins/test_smt_full_box_exchange_plugin.py \
  tests/mock/smt_classifier/rack_exchange_mock.py \
  tests/mock/smt_classifier/test_smt_classifier_mock.py
git diff --check
```

Expected: PASS。

- [ ] **Step 5: GitNexus detect_changes**

Use GitNexus MCP:

```text
detect_changes(repo="wes_backend", scope="all")
```

Expected: risk low 或 medium，affected_processes 与本次 SMT/满箱交换/runtime intent 范围一致；如出现高风险或无关流程，先复查 diff。

- [ ] **Step 6: 最终提交**

如果前面按任务分拆提交，本步骤只检查工作区：

```bash
git status --short
```

Expected: 只剩用户明确保留的无关本地文件，或工作区干净。

如使用单次提交：

```bash
git add src/workline_runtime/runtime_intent.py src/workline_runtime/plugin_next.py src/workline_runtime/runtime_intent_effects.py src/app/resource/services/smt_rack_bin_scheduling_service.py src/workline_plugins/smt_classifier/plugin.py src/workline_plugins/smt_full_box_exchange/contract.py src/workline_plugins/smt_full_box_exchange/plugin.py tests/workline_runtime/test_runtime_intent_effects.py tests/workline_runtime/test_plugin_next.py tests/workline_runtime/test_smt_rack_bin_scheduling_service.py tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py tests/workline_plugins/test_smt_full_box_exchange_plugin.py tests/mock/smt_classifier/rack_exchange_mock.py tests/mock/smt_classifier/test_smt_classifier_mock.py docs/architecture/SRS.md
git commit -m "fix(smt): 拆分补架请求与满箱交换事件"
```

## Self-Review

**Spec coverage:**
已覆盖初次无货架不触发满箱交换、有旧货架无格位时向满箱交换插件发事件、SMT 自己请求新货架补充、新货架到位后恢复当前 session、满箱交换插件只处理旧货架判断与交换。

**Placeholder scan:**
计划没有留下占位式任务；每个任务都给出文件、关键断言、命令与通过标准。

**Type consistency:**
计划中统一使用 `rack_supply_request`、`rack_release_event`、`rack_supply`、`SINGLE_LAYER_RACK_RELEASED`、`SMT_RACK_SUPPLY`、`DEVICE_EVENT`；旧字段 `external_request/full_box_exchange_request/rack_exchange` 只作为兼容说明出现。
