# SMT Rack Arrival Resume Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 SMT 粗分线在当前货架无可用料格时的恢复链路：移出当前货架并请求新货架后，SMT 主 session 等待新货架到位并继续完成当前料盘入格；满箱交换判断由第二插件独立处理，不阻塞 SMT 主流程。

**Architecture:** SMT 粗分插件负责主流程和 `WMS_RACK_ARRIVED` 恢复；满箱交换插件只处理被移出货架的释放快照和满箱交换结果。运行时需要在 SMT 发起换架请求时记录“当前料盘所在源设备”，并在外部回调没有 `device_code` 时从 session 上下文恢复 source device，避免要求 WMS/RCS 知道内部设备拓扑。

**Tech Stack:** Python 3.13, FastAPI runtime, SQLModel/SQLAlchemy async session, pytest, Celery workline runtime, GitNexus impact analysis.

---

## 业务约定

正确流程如下：

```text
SMT 粗分主流程：
SCAN_COMPLETED
-> MEASUREMENT_REEL
-> MOVE_FORWARD
-> 当前货架料箱料格调度
-> 找到可用格：ARM04 PICK_AND_PUT -> SMT session COMPLETED
-> 找不到可用格：
   发起 RACK_EXCHANGE_AND_SUPPLY
   SMT session WAITING_EXTERNAL，等待 WMS_RACK_ARRIVED
   新货架到位后重新调度料格
   ARM04 PICK_AND_PUT -> SMT session COMPLETED

满箱交换判断流程：
当前旧货架被移出
-> 释放事实和 4 箱快照
-> smt_full_box_exchange 插件判断是否需要满箱交换
-> 如需交换，发起 SMT_FULL_BOX_EXCHANGE
-> WMS_FULL_BOX_EXCHANGE_RESULT
-> 满箱交换插件自己的 session COMPLETED
```

关键边界：

- SMT 主 session 只等待 `WMS_RACK_ARRIVED`，不等待 `WMS_FULL_BOX_EXCHANGE_RESULT`。
- 满箱交换插件处理的是旧货架，不能决定当前 SMT session 是否完成。
- `WMS_RACK_ARRIVED` 真实回调不应强制携带内部设备码 `PIPELINE02`；runtime 应从 session 上下文推导当前料盘所在源设备。

## 文件结构

- Modify: `src/workline_plugins/smt_classifier/plugin.py`
  - 在 `MOVE_FORWARD SUCCESS` 触发 `RACK_EXCHANGE_AND_SUPPLY` 时，把恢复所需的源设备快照写入 `rack_exchange` 上下文。
- Modify: `src/workline_runtime/plugin_context.py`
  - 扩展 source device 解析：payload 没有 `device_code/location` 时，从 session `rack_exchange.resume_source_device_code` 回退解析。
- Test: `tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py`
  - 覆盖 SMT 触发换架请求时写入恢复源设备，不生成 `OUTPUT_ARM` 命令。
  - 覆盖 `WMS_RACK_ARRIVED` 仍只生成 `OUTPUT_ARM PICK_AND_PUT`，不依赖满箱交换结果。
- Test: `tests/workline_runtime/test_plugin_context_runtime_facts.py`
  - 覆盖 EXTERNAL_HTTP 回调缺少 `device_code` 时，从 session 上下文解析 `PIPELINE02` 为 source device。
- Optional docs: `docs/workline/smt-rack-exchange-flow.md`
  - 若仓库已有 workline 业务说明目录，则补充业务时序；没有目录则跳过新增文档，避免无关文档结构扩散。

## Task 1: 锁定影响范围和现有行为

**Files:**
- Read: `src/workline_plugins/smt_classifier/plugin.py`
- Read: `src/workline_runtime/plugin_context.py`
- Read: `src/workline_runtime/runtime_intent_effects.py`
- Read: `tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py`
- Read: `tests/workline_runtime/test_plugin_context_runtime_facts.py`

- [ ] **Step 1: 运行 GitNexus impact，记录风险**

Run:

```bash
gitnexus_impact target="_resolve_source_device" direction="upstream" file_path="src/workline_runtime/plugin_context.py" repo="wes_backend"
gitnexus_impact target="handle_conveyor_success" direction="upstream" file_path="src/workline_plugins/smt_classifier/plugin.py" repo="wes_backend"
```

Expected:

```text
记录直接调用者、影响流程、风险等级。
如任一结果为 HIGH 或 CRITICAL，先向用户汇报再继续执行代码修改。
```

- [ ] **Step 2: 运行当前相关测试作为基线**

Run:

```bash
uv run pytest \
  tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py::TestSmtClassifierPluginCommandResults::test_conveyor_success_requests_rack_exchange_and_supply_when_rack_cannot_store \
  tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py::TestSmtClassifierPluginCommandResults::test_external_rack_arrived_reallocates_and_commands_output_arm \
  tests/workline_runtime/test_plugin_context_runtime_facts.py \
  -q
```

Expected:

```text
现有测试通过；如果失败，先记录失败原因，不进入实现步骤。
```

- [ ] **Step 3: 检查工作区已有改动**

Run:

```bash
git status --short
```

Expected:

```text
识别用户已有改动；本计划只修改本计划列出的文件，不回退无关改动。
```

## Task 2: SMT 换架请求记录恢复源设备

**Files:**
- Modify: `src/workline_plugins/smt_classifier/plugin.py`
- Test: `tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py`

- [ ] **Step 1: 写失败测试，要求 rack_exchange 记录恢复源设备**

Modify:

```text
tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py
```

在 `test_conveyor_success_requests_rack_exchange_and_supply_when_rack_cannot_store` 中，把 `_command_payload(...)` 结果赋值给局部变量，并覆盖设备码：

```python
payload = _command_payload("MOVE_FORWARD", "SUCCESS", data={"pkg_id": pkg_id})
payload["device_code"] = "PIPELINE02"
result = await plugin.on_command_result(mock_context, _make_inbox(payload))
```

扩展 `rack_exchange` 断言，要求包含：

```python
"resume_source_device_code": "PIPELINE02",
"resume_source_device_role": "CONVEYOR",
"resume_callback_type": "WMS_RACK_ARRIVED",
```

Expected failure:

```text
断言失败，现有 rack_exchange 上下文没有 resume_source_device_code / resume_source_device_role / resume_callback_type。
```

- [ ] **Step 2: 运行失败测试**

Run:

```bash
uv run pytest tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py::TestSmtClassifierPluginCommandResults::test_conveyor_success_requests_rack_exchange_and_supply_when_rack_cannot_store -q
```

Expected:

```text
FAIL，缺少新增的恢复源设备字段。
```

- [ ] **Step 3: 实现最小修改**

Modify:

```text
src/workline_plugins/smt_classifier/plugin.py
```

实现方向：

```python
def _rack_exchange_context(..., resume_source_device_code: str | None = None, resume_source_device_role: str | None = None) -> dict[str, Any]:
    context = {..., "resume_callback_type": "WMS_RACK_ARRIVED"}
    if resume_source_device_code is not None:
        context["resume_source_device_code"] = resume_source_device_code
    if resume_source_device_role is not None:
        context["resume_source_device_role"] = resume_source_device_role
    return context
```

在 `handle_conveyor_success` 的 `RACK_EXCHANGE_AND_SUPPLY` 分支传入：

```python
resume_source_device_code=non_empty_str(result.device_code),
resume_source_device_role=_source_device_role(ctx) or self.CONVEYOR,
```

约束：

- 只写 session context，不要求 WMS/RCS 回调携带内部设备码。
- 不改变 external request 的业务含义。
- 不把 `WMS_FULL_BOX_EXCHANGE_RESULT` 写入 SMT 主流程等待条件。

- [ ] **Step 4: 运行测试确认通过**

Run:

```bash
uv run pytest tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py::TestSmtClassifierPluginCommandResults::test_conveyor_success_requests_rack_exchange_and_supply_when_rack_cannot_store -q
```

Expected:

```text
PASS。
```

- [ ] **Step 5: 提交本任务**

Run:

```bash
git add src/workline_plugins/smt_classifier/plugin.py tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py
git commit -m "fix(workline): 记录 SMT 换架恢复源设备"
```

Expected:

```text
提交成功；如果当前工作区存在用户未提交改动，只 stage 本任务修改。
```

## Task 3: EXTERNAL_HTTP 恢复时从 session 上下文解析 source device

**Files:**
- Modify: `src/workline_runtime/plugin_context.py`
- Test: `tests/workline_runtime/test_plugin_context_runtime_facts.py`

- [ ] **Step 1: 写失败测试，覆盖 WMS_RACK_ARRIVED 不带 device_code**

Modify:

```text
tests/workline_runtime/test_plugin_context_runtime_facts.py
```

新增测试函数：

```python
def test_build_resolves_external_http_source_device_from_session_rack_exchange_resume_code():
    session = SimpleNamespace(
        id=38,
        run_mode="SIMULATION",
        context_json={
            "rack_exchange": {
                "status": "REQUESTED",
                "resume_source_device_code": "PIPELINE02",
                "resume_source_device_role": "CONVEYOR",
            }
        },
        trace_id="trace-smt-full",
        workline_id=45,
        plugin_key="smt_classifier",
        contract_version="1.0",
        last_request_id=None,
    )
    workline = SimpleNamespace(
        id=45,
        line_code="WL-CONVEYOR-02",
        line_name="SMT 粗分右线",
        line_type="CONVEYOR",
        plugin_key="smt_classifier",
        contract_version="1.0",
        run_mode="SIMULATION",
        config={},
        runtime_config_json={},
        diagnostic_profile={},
    )
    conveyor = SimpleNamespace(
        id=40,
        device_code="PIPELINE02",
        device_name="右线输送线",
        device_role="CONVEYOR",
        role_index=0,
        upstream_device_id=39,
        work_line_id=45,
        protocol="HTTP",
        host="127.0.0.1",
        port=9002,
        timeout=30,
        callback_path="/callback",
        maintenance_mode=False,
        capabilities_json={},
        diagnostic_profile={},
    )
    output_arm = SimpleNamespace(
        id=41,
        device_code="ARM04",
        device_name="右线出料臂",
        device_role="OUTPUT_ARM",
        role_index=0,
        upstream_device_id=40,
        work_line_id=45,
        protocol="HTTP",
        host="127.0.0.1",
        port=9003,
        timeout=30,
        callback_path="/callback",
        maintenance_mode=False,
        capabilities_json={},
        diagnostic_profile={},
    )
    inbox = SimpleNamespace(
        id=145,
        kind="EXTERNAL_HTTP",
        payload_json={
            "callback_type": "WMS_RACK_ARRIVED",
            "dispatch_key": "external:smt_classifier:trace-smt-full:RACK_EXCHANGE_AND_SUPPLY",
            "active_bin_rack": {"rack_id": "RACK-NEXT-01", "cells": []},
        },
        trace_id="trace-smt-full",
        source_message_id=None,
        event_id=None,
        causation_id=None,
        workline_id=45,
        session_id=38,
        device_id=None,
        command_id=None,
    )
    ctx = PluginContextBuilder().build(
        session=session,
        workline=workline,
        devices_by_role={"CONVEYOR": [conveyor], "OUTPUT_ARM": [output_arm]},
        services=WorklineRuntimeServices(),
        trace_id="trace-smt-full",
        inbox=inbox,
    )
    assert ctx.source_device.device_code == "PIPELINE02"
    assert ctx.source_device_role == "CONVEYOR"
```

Expected failure:

```text
ctx.source_device 为 None，说明 builder 还不能从 session.rack_exchange 恢复源设备。
```

- [ ] **Step 2: 运行失败测试**

Run:

```bash
uv run pytest tests/workline_runtime/test_plugin_context_runtime_facts.py::test_build_resolves_external_http_source_device_from_session_rack_exchange_resume_code -q
```

Expected:

```text
FAIL，source_device 为空。
```

- [ ] **Step 3: 实现 source device fallback**

Modify:

```text
src/workline_runtime/plugin_context.py
```

实现方向：

```python
def _source_device_code_from_session(session: Any | None) -> str | None:
    context = _safe_dict(getattr(session, "context_json", None))
    rack_exchange = _safe_dict(context.get("rack_exchange"))
    for key in ("resume_source_device_code", "material_source_device_code", "source_device_code"):
        value = _safe_str(rack_exchange.get(key)) or _safe_str(context.get(key))
        if value:
            return value
    return None
```

调整 `_resolve_source_device` 签名：

```python
def _resolve_source_device(devices_by_role: dict[str, list[Any]], inbox: Any | None, session: Any | None = None) -> Any | None:
```

解析顺序：

```text
1. inbox.payload_json.device_code
2. inbox.payload_json.location
3. inbox.normalized_input.device_code
4. session.context_json.rack_exchange.resume_source_device_code
5. session.context_json.resume_source_device_code
```

在 `PluginContextBuilder.build()` 中传入 session：

```python
source_device = _resolve_source_device(devices_by_role, inbox, session)
```

约束：

- payload 显式设备码优先，避免覆盖设备自身回调。
- fallback 只做解析，不修改 inbox，不写数据库。
- 只从当前 workline 的 `devices_by_role` 中匹配，不能跨工作线找设备。

- [ ] **Step 4: 运行测试确认通过**

Run:

```bash
uv run pytest tests/workline_runtime/test_plugin_context_runtime_facts.py -q
```

Expected:

```text
全部通过。
```

- [ ] **Step 5: 提交本任务**

Run:

```bash
git add src/workline_runtime/plugin_context.py tests/workline_runtime/test_plugin_context_runtime_facts.py
git commit -m "fix(runtime): 外部回调从会话上下文恢复源设备"
```

Expected:

```text
提交成功。
```

## Task 4: 验证 SMT 主流程与满箱交换插件解耦

**Files:**
- Test: `tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py`
- Optional Test: `tests/workline_runtime/test_runtime_intent_effects.py`

- [ ] **Step 1: 扩展插件测试，明确 SMT 等待的是新货架到位**

Modify:

```text
tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py
```

在 `test_conveyor_success_requests_rack_exchange_and_supply_when_rack_cannot_store` 中增加断言：

```python
assert result[0].context_patch["rack_exchange"]["resume_callback_type"] == "WMS_RACK_ARRIVED"
assert result[0].context_patch["full_box_exchange"]["dispatch_key"] == result[1].dispatch_key
assert result[1].payload_json["resume_callback_type"] == "WMS_RACK_ARRIVED"
assert "WMS_FULL_BOX_EXCHANGE_RESULT" not in str(result[0].context_patch)
```

Expected:

```text
断言清楚表达 SMT 主流程等待 WMS_RACK_ARRIVED，而不是等待满箱交换完成。
```

- [ ] **Step 2: 扩展 WMS_RACK_ARRIVED 插件测试**

Modify:

```text
tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py
```

在 `test_external_rack_arrived_reallocates_and_commands_output_arm` 中确保 session context 不包含满箱交换完成信息也能恢复：

```python
assert "full_box_exchange" not in mock_context.session.context_json
assert [intent.kind for intent in result] == [RuntimeIntentKind.UPDATE_CONTEXT, RuntimeIntentKind.COMMAND]
assert result[0].context_patch["rack_exchange"]["status"] == "ARRIVED"
_assert_command(result[1], action="PICK_AND_PUT", device_role="OUTPUT_ARM")
```

Expected:

```text
插件层证明 WMS_RACK_ARRIVED 足以恢复 SMT 当前料盘，不依赖 WMS_FULL_BOX_EXCHANGE_RESULT。
```

- [ ] **Step 3: 运行插件测试**

Run:

```bash
uv run pytest \
  tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py::TestSmtClassifierPluginCommandResults::test_conveyor_success_requests_rack_exchange_and_supply_when_rack_cannot_store \
  tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py::TestSmtClassifierPluginCommandResults::test_external_rack_arrived_reallocates_and_commands_output_arm \
  -q
```

Expected:

```text
全部通过。
```

- [ ] **Step 4: 提交本任务**

Run:

```bash
git add tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py
git commit -m "test(workline): 明确 SMT 换架恢复不依赖满箱交换完成"
```

Expected:

```text
提交成功。
```

## Task 5: 端到端沙箱验证

**Files:**
- No source file changes.
- Runtime data only: Docker Postgres + localhost API.

- [ ] **Step 1: 启动作业线依赖**

Run:

```bash
docker-compose up -d
uv run uvicorn main:app --host 0.0.0.0 --port 8001
uv run celery -A src.celery_app.app worker --loglevel=info --queues=default,celery
```

Expected:

```text
Postgres/Redis/API/Celery 可用。
```

- [ ] **Step 2: 确认基础数据**

Run:

```bash
docker exec -i wes_postgres_dev psql -U wes_user -d wes_db -P pager=off <<'SQL'
select id, line_code, plugin_key, run_mode, runtime_status, is_active
from wes_biz.work_lines
where line_code in ('WL-CONVEYOR-02', 'WL-SMT-FULL-BOX-EXCHANGE-01')
order by id;

select id, device_code, device_status, work_line_id, is_active, current_command_id
from wes_biz.devices
where device_code in ('ARM03', 'PIPELINE02', 'ARM04', 'SMT_FULL_EXCHANGE_TRIGGER_01')
order by id;
SQL
```

Expected:

```text
两条工作线均为 SIMULATION / READY / is_active=true。
四个设备均为 IDLE / is_active=true。
```

- [ ] **Step 3: 验证正常入格不触发满箱交换**

Run:

```bash
curl -sS -X POST 'http://localhost:8001/api/v1/callback/event' \
  -H 'Content-Type: application/json' \
  -d '{
    "trace_id": "trace_smt_normal_verify_001",
    "device_code": "ARM03",
    "event_type": "SCAN_COMPLETED",
    "timestamp": 1779000000000,
    "data": {
      "location": "ARM03",
      "HHPN": "620100L00-011-G",
      "MfrPN": "CC0402JRNPO9BN220",
      "Qty": "7387",
      "DateCode": "122625",
      "LotCode": "8904936031",
      "PkgID": "VERIFY-NORMAL-001"
    }
  }'
```

Then use sandbox command-result helper or existing operation API to complete:

```text
MEASUREMENT_REEL SUCCESS
inject active_bin_rack with at least one EMPTY cell
MOVE_FORWARD SUCCESS
expect ARM04 PICK_AND_PUT command
PICK_AND_PUT SUCCESS
```

Expected SQL evidence:

```text
SMT session = COMPLETED
device_commands = ARM03 MEASUREMENT_REEL, PIPELINE02 MOVE_FORWARD, ARM04 PICK_AND_PUT
workline_outbox for this session has no EXTERNAL_HTTP rows
```

- [ ] **Step 4: 验证满格后触发换架但 SMT 等新货架到位**

Run:

```text
创建 trace_smt_exchange_verify_001 的 SMT session。
在 MOVE_FORWARD SUCCESS 前把 session.context_json.active_bin_rack 设置为：
- 所有 cells status=OCCUPIED
- DateCode/LotCode 均不兼容当前 PkgID
- 无 EMPTY cell
回填 MOVE_FORWARD SUCCESS。
```

Expected SQL evidence:

```text
SMT session = WAITING_EXTERNAL
rack_exchange.reason_code = NO_COMPATIBLE_OR_EMPTY_CELL
outbox = external:smt_classifier:<trace>:RACK_EXCHANGE_AND_SUPPLY
outbox.payload.resume_callback_type = WMS_RACK_ARRIVED
```

- [ ] **Step 5: 验证满箱交换插件独立完成**

Run:

```text
为旧货架创建 resource_rack_releases CANDIDATE + 4 条 resource_rack_release_bin_snapshots。
运行 scan_smt_full_box_exchange_candidates_batch 或 smt_full_box_exchange_candidate_service.scan_candidates。
回填 WMS_FULL_BOX_EXCHANGE_RESULT / BUSINESS_COMPLETED。
```

Expected SQL evidence:

```text
full-box session = COMPLETED
resource_full_box_exchange_tasks.exchange_status = BUSINESS_COMPLETED
SMT session 仍不因 full-box callback 自动完成；它仍等待 WMS_RACK_ARRIVED。
```

- [ ] **Step 6: 验证 WMS_RACK_ARRIVED 恢复 SMT 当前 session**

Run:

```bash
curl -sS -X POST 'http://localhost:8001/api/v1/callback/external' \
  -H 'Content-Type: application/json' \
  -d '{
    "callback_type": "WMS_RACK_ARRIVED",
    "trace_id": "trace_smt_exchange_verify_001",
    "dispatch_key": "external:smt_classifier:trace_smt_exchange_verify_001:RACK_EXCHANGE_AND_SUPPLY",
    "source_system": "WMS",
    "source_event_id": "wms-rack-arrived-trace_smt_exchange_verify_001",
    "source_version": "1",
    "occurred_at": "2026-05-17T10:00:00Z",
    "request_id": "sandbox-wms-rack-arrived-verify-001",
    "timestamp": 1779000001000,
    "signature": "sandbox-signature",
    "active_bin_rack": {
      "rack_id": "RACK-SMT-RIGHT-NEXT-VERIFY-01",
      "rack_code": "RACK-SMT-RIGHT-NEXT-VERIFY-01",
      "cells": [
        {
          "rack_id": "RACK-SMT-RIGHT-NEXT-VERIFY-01",
          "rack_code": "RACK-SMT-RIGHT-NEXT-VERIFY-01",
          "bin_id": "BIN-SMT-NEXT-VERIFY-01",
          "bin_code": "BIN-SMT-NEXT-VERIFY-01",
          "bin_type": "九格箱",
          "bin_cell_location": "1",
          "status": "EMPTY"
        }
      ]
    }
  }'
```

Expected:

```text
不需要 device_code=PIPELINE02。
SMT session 从 WAITING_EXTERNAL -> WAITING_DEVICE_RESULT。
生成 ARM04 PICK_AND_PUT。
回填 ARM04 SUCCESS 后，SMT session = COMPLETED。
```

- [ ] **Step 7: 提交验证记录**

Run:

```bash
git status --short
```

Expected:

```text
只有本计划相关代码/测试改动；沙箱数据库数据不进入 git。
```

## Task 6: 全量相关测试和质量门禁

**Files:**
- No new source file changes expected.

- [ ] **Step 1: 运行相关测试集**

Run:

```bash
uv run pytest \
  tests/workline_runtime/test_plugin_context_runtime_facts.py \
  tests/workline_runtime/test_runtime_intent_effects.py \
  tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py \
  tests/workline_runtime/test_smt_rack_bin_scheduling_service.py \
  tests/workline_runtime/test_smt_full_box_exchange_candidate_service.py \
  -q
```

Expected:

```text
全部通过。
```

- [ ] **Step 2: 运行格式和 lint**

Run:

```bash
uv run ruff format src/workline_runtime/plugin_context.py src/workline_plugins/smt_classifier/plugin.py tests/workline_runtime/test_plugin_context_runtime_facts.py tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py
uv run ruff check src/workline_runtime/plugin_context.py src/workline_plugins/smt_classifier/plugin.py tests/workline_runtime/test_plugin_context_runtime_facts.py tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py
```

Expected:

```text
ruff format 无异常；ruff check 0 errors。
```

- [ ] **Step 3: 运行 GitNexus detect changes**

Run:

```bash
gitnexus_detect_changes scope="all" repo="wes_backend"
```

Expected:

```text
变更只影响 SMT 粗分换架恢复、PluginContext source device 解析、对应测试。
如出现不相关流程，先分析原因再提交。
```

- [ ] **Step 4: 最终提交**

Run:

```bash
git add src/workline_runtime/plugin_context.py src/workline_plugins/smt_classifier/plugin.py tests/workline_runtime/test_plugin_context_runtime_facts.py tests/integration/workline_plugins/test_smt_classifier_plugin_command_results.py
git commit -m "fix(workline): 修复 SMT 换架到位后恢复当前料盘入格"
```

Expected:

```text
提交成功，commit message 使用中文说明。
```

## Self-Review

- Spec coverage:
  - 当前货架无可用格时发起移出当前货架和请求新货架：Task 2、Task 4 覆盖。
  - 满箱交换判断由第二插件独立处理，不影响 SMT 主流程：Task 4、Task 5 覆盖。
  - 新货架到位后恢复当前 SMT session 并完成当前料盘入格：Task 3、Task 5 覆盖。
  - 不要求 WMS/RCS 传内部设备 `PIPELINE02`：Task 3、Task 5 覆盖。
- Placeholder scan:
  - 本计划没有保留占位项。
  - 所有任务均给出具体文件、命令和验收标准。
- Type consistency:
  - 新增上下文字段统一使用 `rack_exchange.resume_source_device_code`、`rack_exchange.resume_source_device_role`、`rack_exchange.resume_callback_type`。
  - 现有字段 `dispatch_key`、`target_code`、`source_system`、`requested_actions` 保持不变。
