# 粗分机插件物理流程 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 按真实物理业务逻辑重建 `rough_sorter` 工作线插件，使扫码、测量、WMS 验证、入料、流水线前进、出料入箱、货架补给和 NG 物理闭环都由插件通过 `RuntimeIntent` 驱动。

**Architecture:** 插件只负责业务判定和下一步意图，Runtime 负责 Session、Outbox、资源投影、货架 operation、预约和 Hold。业务字段统一封装在 `payload.data` 中；顶层 payload 只承载系统字段。设备命令使用业务 action 与设备 `task_type` 分离的 contract 层。

**Tech Stack:** Python 3.13、Pydantic、Workline Runtime Plugin、`RuntimeIntent`、WMS typed ports、SMT rack/bin scheduling service、pytest、ruff、GitNexus。

---

## 0. 工程审查对齐结论

本计划已按 `/plan-eng-review` 讨论结果修订：

- `contract.py` 使用纯函数，不创建 `RoughSorterCommandContract` 类。
- 业务字段只允许出现在 `payload.data`；不兼容顶层业务字段。
- 插件测试调用真实 runtime 入口：`on_device_event(ctx, inbox)`、`on_command_result(ctx, inbox)`、`on_external_http(ctx, inbox)`。
- 测试和实现只使用 `RuntimeIntent` 真实字段：`payload_json`、`action`、`idempotency_key`、`context_patch`。
- 设备命令 builder 必须在 `params.action` 写入 WES 业务 action；设备顶层 `task_type` 只表达现场协议动作。
- WMS/RCS 货架到位恢复采用两阶段：先落资源事实并创建内部重试 inbox，下一轮再分配。
- 发起 rack operation 前写入 `resume_source_device_code` 和重试上下文，保证回调恢复能找到正确设备/拓扑。
- `ROUGH_SORTER_STORAGE_RETRY` 必须使用稳定 `event_id`，避免重复 WMS/RCS 回调创建多个 retry inbox。
- WMS 查询使用稳定 `request_id`，格式为 `rough-sorter:inventory:{business_key 或 PkgID}`，并携带 `trace_id`。
- WMS `query_inventory` 业务拒绝按业务判断失败进入物理 NG；WMS 不可用、超时、熔断、协议/证据异常进入 Hold。
- 测量边界统一为：硬件错误/失败走 Hold，业务判断失败走 NG；`SUCCESS` 但测量字段缺失或不可解析走 Hold。
- `PUT_TO_BIN FAILED` 默认保留 bin cell reservation 并进入 Hold；失败后 WES 不能确定料盘和硬件状态，不自动释放预约。
- WMS mock 合同要贴近生产：未知 `sku + lot_no` 返回 `items=[]`，已知物料返回匹配 item。

## 1. 文件结构与职责

- Create: `src/workline_plugins/rough_sorter/__init__.py`
  - 导出 `RoughSorterPlugin` 和必要合同函数。
- Create: `src/workline_plugins/rough_sorter/contract.py`
  - 定义插件 key、事件/命令常量、phase 常量、设备角色常量、NG reason code、设备 `task_type` 映射、`payload.data` 解析、命令 payload 构造函数、结果分类函数。
- Create: `src/workline_plugins/rough_sorter/context.py`
  - 定义 `RoughSorterContext`，保存 Session context 快照字段：六合一码、业务主键、测量结果、WMS 验证摘要、目标 bin location、rack operation、NG 原因、phase。
- Create: `src/workline_plugins/rough_sorter/plugin.py`
  - 实现 `RoughSorterPlugin`，通过 `@on_event`、`@on_command`、`on_external_http` 生成 runtime intents。
- Modify: `src/workline_plugin_registry.py`
  - 注册 `rough_sorter` 的 `WorklinePluginDefinition`。
- Modify: `tests/mock/wms_mock_server.py`
  - 调整库存查询 mock，使未知物料返回空 items，已知物料返回 `sku` 和 `lot_no`。
- Create/Modify tests:
  - `tests/workline_plugins/test_rough_sorter_contract.py`
  - `tests/workline_plugins/test_rough_sorter_plugin.py`
  - `tests/workline_runtime/test_session_resolver.py`
  - `tests/workline_runtime/test_runtime_intent_effects.py`
  - `tests/mock/test_wms_mock_server.py` 或现有 mock 合同测试文件
  - `tests/test_workline_service_plugin_validation.py`

## 2. 业务合同

### 2.1 事件与命令

```text
SCAN_COMPLETED
  -> MEASUREMENT_REEL
  -> PICK_AND_PUT
  -> MOVE_FORWARD
  -> storage allocation / rack operation
  -> PUT_TO_BIN
  -> COMPLETE
```

- `SCAN_COMPLETED` 由扫码点或入料机械臂上报，业务数据必须位于 `payload.data`。
- `MEASUREMENT_REEL` 是 WES 业务 action，默认映射设备 `task_type="TEST"`。
- `PICK_AND_PUT` 是入料机械臂从读码/检测点抓取到流水线进料位的业务动作。
- `MOVE_FORWARD` 是流水线将料盘移至出料位的业务动作。
- `PUT_TO_BIN` 是 WES 业务 action，默认映射设备 `task_type="PICK_AND_PUT"`，由出料机械臂执行。
- `MOVE_TO_NG` 是 WES 内部 NG 搬运业务 action，默认映射设备 `task_type="PICK_AND_PUT"` 且目标为 NG 位。
- 所有命令 payload builder 必须输出 `params.action=<WES 业务 action>`，确保设备回调即使只回传 `task_type` 也能由 callback 层还原业务命令类型。

### 2.1.1 设备角色合同

- `ROLE_INPUT_ARM`：执行 `MEASUREMENT_REEL` 和入料 `PICK_AND_PUT`。
- `ROLE_CONVEYOR`：执行 `MOVE_FORWARD`。
- `ROLE_OUTPUT_ARM`：执行 `PUT_TO_BIN` 和 `MOVE_TO_NG`。
- `SCAN_COMPLETED` 是入口事件，不声明独立设备角色，也不配置 `event_source_roles`。
- manifest 必须声明上述三个物理设备 role 常量和 `command_target_roles`，测试必须覆盖缺失关键角色时 WorkLine 绑定失败。

### 2.2 payload 边界

顶层 payload 只允许系统级字段，例如：

- `device_code`
- `event_type`
- `command_code`
- `command_type`
- `task_type`
- `result`
- `timestamp`
- `trace_id`
- `data`
- `error_detail`

业务字段必须放在 `payload.data`：

- `HHPN`
- `MfrPN`
- `Qty`
- `DateCode`
- `LotCode`
- `PkgID`
- 兼容现场别名时，只在 `payload.data` 内把 `ProductNo -> HHPN`、`PONumber -> PkgID` 归一。

不得让插件 resolver 信任顶层 `PkgID`、`LotCode`、`ProductNo` 等业务字段。

### 2.3 WMS 验证

- 强依赖 `ctx.services.wms_inventory_client.query_inventory(...)`。
- 查询字段：`sku = six_in_one.HHPN`，`lot_no = six_in_one.LotCode`。
- 存在匹配 `sku + lot_no` 的 item 即通过。
- 非空 items 中 `sku` 或 `lot_no` 不匹配时仍视为无匹配，不得按“items 非空”放行。
- 无匹配 item 或 `WmsBusinessRejectedError` 进入物理 NG，并保留 WMS reason/evidence。
- WMS client 未注入、超时、熔断、不可用、证据异常或响应无法解析进入 Hold，不得误判为业务 NG。
- 开发/测试环境的 `tests/mock/wms_mock_server.py` 必须按同一合同表达“存在/不存在”。

### 2.4 NG 与 Hold 边界

- 业务可判定不合格：条码非法、条码命中 NG 规则、测量业务 NG、WMS 无匹配或业务拒绝，走 `MARK_NG + MOVE_TO_NG`，NG 搬运成功后 `COMPLETE`。
- 物理状态不可信或外部依赖不可用：设备硬件错误/未知失败、WMS 不可用、测量成功但字段缺失/不可解析、投影/预约冲突、出料放置失败，走 `BLOCK`/RuntimeHold。

## 3. 状态流

```text
设备 SCAN_COMPLETED
  |
  v
解析 payload.data -> SixInOne -> barcode_decision_service
  |                      |
  | OK                   | INVALID / INCOMPLETE / NG_RULE
  v                      v
MEASUREMENT_REEL       MARK_NG -> MOVE_TO_NG -> COMPLETE
  |
  v
MEASUREMENT_REEL result
  | SUCCESS                         | SIZE/THICKNESS NG
  v                                 v
WMS query_inventory                 MARK_NG -> MOVE_TO_NG -> COMPLETE
  | matched     | no match           | unavailable
  v             v                    v
PICK_AND_PUT   MARK_NG -> MOVE_TO_NG BLOCK
  |
  v
MOVE_FORWARD
  |
  v
plan_allocation
  | ALLOCATED                  | RACK_OPERATION_REQUIRED
  v                            v
CLAIM_BIN_CELL -> PUT_TO_BIN   RACK_OPERATION_REQUEST -> WAITING_EXTERNAL
  |                            |
  v                            v
MATERIAL_MOUNTED + COMPLETE    WMS/RCS callback -> RESOURCE_FACT + RETRY_EVENT
                               |
                               v
                               retry event -> plan_allocation
```

## 4. 实施任务

### Task 1: 合同层与注册表

**Files:**
- Create: `src/workline_plugins/rough_sorter/contract.py`
- Create: `src/workline_plugins/rough_sorter/context.py`
- Create: `src/workline_plugins/rough_sorter/plugin.py`
- Create: `src/workline_plugins/rough_sorter/__init__.py`
- Modify: `src/workline_plugin_registry.py`
- Test: `tests/workline_plugins/test_rough_sorter_contract.py`
- Test: `tests/test_workline_service_plugin_validation.py`

- [x] 运行 GitNexus impact：`npx gitnexus impact WORKLINE_PLUGIN_REGISTRY --direction upstream`
- [x] 写合同层失败测试，覆盖 `payload.data` 解析、现场别名归一、PkgID 派生 business key、phase 常量、角色映射、命令 payload 映射。
- [x] 实现 `contract.py` 纯函数：
  - phase 常量：至少包含 `SCANNED`、`MEASURING`、`PICK_TO_PIPELINE`、`MOVING_FORWARD`、`WAITING_RACK`、`PUTTING_TO_BIN`、`NG_MOVING`、`COMPLETED`。
  - role 常量和 action→role 映射。
  - `normalize_six_in_one_payload(payload_json)`：只读取 `payload_json["data"]`。
  - `resolve_rough_sorter_business_key(payload_json)`：只基于 `payload.data.PkgID` 派生。
  - `build_measurement_reel_payload(...)`
  - `build_pick_and_put_payload(...)`
  - `build_move_forward_payload(...)`
  - `build_put_to_bin_payload(...)`
  - `build_move_to_ng_payload(...)`
  - `classify_rough_sorter_result(payload_json)`
  - 所有 command payload builder 必须在业务参数中写入 `action=<WES 业务 action>`，并按合同写入设备 `task_type`。
- [x] 实现 `RoughSorterContext`，字段保持可序列化 dict/list/scalar，不保存 ORM 对象。
- [x] 实现最小 `RoughSorterPlugin.manifest`，声明设备角色、事件源角色、命令目标角色、business key resolver、result classifier、NG reason catalog。
- [x] 在 `WORKLINE_PLUGIN_REGISTRY` 注册 `rough_sorter`。
- [x] 运行：
  - `uv run pytest tests/workline_plugins/test_rough_sorter_contract.py -v`
  - `uv run pytest tests/test_workline_service_plugin_validation.py -v`

### Task 2: Session 归属与扫码入口

**Files:**
- Modify: `src/workline_plugins/rough_sorter/plugin.py`
- Modify: `tests/workline_runtime/test_session_resolver.py`
- Test: `tests/workline_plugins/test_rough_sorter_plugin.py`

- [x] 运行 GitNexus impact：`npx gitnexus impact RoughSorterPlugin --direction upstream`
- [x] 增加 SessionResolver 测试：`DEVICE_EVENT` 的 `payload.data.PkgID` 能通过 rough_sorter manifest 派生 business key，并创建/复用同一 Session。
- [x] 增加扫码 OK 测试，调用 `RoughSorterPlugin().on_device_event(ctx, inbox)`，断言返回 `UPDATE_CONTEXT + COMMAND`，命令 action 为 `MEASUREMENT_REEL`。
- [x] 增加扫码 NG 测试，断言返回 `UPDATE_CONTEXT + MARK_NG + COMMAND`，命令 action 为 `MOVE_TO_NG`。
- [x] 增加 callback 路由测试：设备回调只带 `task_type="TEST"` 时，`DeviceCommand.params.action="MEASUREMENT_REEL"` 能让 command result 路由到 `MEASUREMENT_REEL` handler。
- [x] 实现 `@on_event("SCAN_COMPLETED")`：
  - 解析 `payload.data`。
  - 调用 `barcode_decision_service.evaluate(...)`。
  - OK 写入 context 并下发 `MEASUREMENT_REEL`。
  - 非 OK 写入 NG context，`MARK_NG` 后下发 `MOVE_TO_NG`。
- [x] 运行：
  - `uv run pytest tests/workline_runtime/test_session_resolver.py -k rough_sorter -v`
  - `uv run pytest tests/workline_plugins/test_rough_sorter_plugin.py -v`

### Task 3: 测量结果与 WMS 强校验

**Files:**
- Modify: `src/workline_plugins/rough_sorter/plugin.py`
- Modify: `tests/workline_plugins/test_rough_sorter_plugin.py`
- Modify: `tests/mock/wms_mock_server.py`
- Test: `tests/mock/test_wms_mock_server.py` 或现有 mock 合同测试文件

- [x] 写测量成功 + WMS 匹配测试，fake client 返回 `QueryInventoryResponse(items=[WmsInventoryItem(...)])`。
- [x] 写 WMS 无匹配测试，fake client 返回 `QueryInventoryResponse(items=[])`，断言进入物理 NG。
- [x] 写 WMS 非空错配测试：返回 wrong `sku` 或 wrong `lot_no` 的 item 时仍进入物理 NG。
- [x] 写 WMS 业务拒绝测试，fake client 抛 `WmsBusinessRejectedError`，断言进入 `MARK_NG + MOVE_TO_NG` 且保留 WMS reason/evidence。
- [x] 写 WMS 不可用测试，断言 `BLOCK` 且 reason code 保留 WMS 依赖问题。
- [x] 写 WMS request 测试，断言 `QueryInventoryRequest.request_id == "rough-sorter:inventory:{business_key 或 PkgID}"` 且携带 `trace_id`。
- [x] 写测量 `SUCCESS` 但 `reel_diameter` / `reel_thickness` 缺失或不可解析测试，断言进入 Hold。
- [x] 写测量尺寸/厚度 NG 测试，断言 `MARK_NG + MOVE_TO_NG`。
- [x] 写 WMS mock 合同测试：
  - 已知 `sku + lot_no` 返回匹配 item。
  - 未知 `sku + lot_no` 返回 `items=[]`。
- [x] 实现 `@on_command("MEASUREMENT_REEL", result="SUCCESS")`：
  - 校验 `reel_diameter`、`reel_thickness`。
  - 构造 `QueryInventoryRequest`。
  - WMS 匹配后写入 context 并下发 `PICK_AND_PUT`。
  - WMS 无匹配或业务拒绝走物理 NG。
  - WMS 不可用、协议异常或证据异常走 Hold。
- [x] 实现 `@on_command("MEASUREMENT_REEL", result="FAILED")`：
  - `INSPECTION_SIZE_NG` / `INSPECTION_THICKNESS_NG` 走物理 NG。
  - 其它失败走 Hold。
- [x] 运行：
  - `uv run pytest tests/workline_plugins/test_rough_sorter_plugin.py -v`
  - `uv run pytest tests/mock/test_wms_mock_server.py -v`

### Task 4: 入料、流水线前进与存储分配

**Files:**
- Modify: `src/workline_plugins/rough_sorter/plugin.py`
- Test: `tests/workline_plugins/test_rough_sorter_plugin.py`

- [x] 写 `PICK_AND_PUT SUCCESS` 测试，phase 为 `PICK_TO_PIPELINE` 时返回 `UPDATE_CONTEXT + COMMAND(MOVE_FORWARD)`。
- [x] 写 `MOVE_FORWARD SUCCESS` 且 `ALLOCATED` 测试，断言 `UPDATE_CONTEXT + RESOURCE_RESERVATION(CLAIM_BIN_CELL) + COMMAND(PUT_TO_BIN)`。
- [x] 写 `MOVE_FORWARD SUCCESS` 且 `RACK_OPERATION_REQUIRED` 测试，断言写入 `resume_source_device_code` 和 rack operation context，并返回 `RACK_OPERATION_REQUEST`。
- [x] 写 allocator `BLOCKED` 测试，断言进入 Hold。
- [x] 实现 `@on_command("PICK_AND_PUT", result="SUCCESS")`：
  - `phase == "NG_MOVING"` 时 `COMPLETE`。
  - `phase == "PICK_TO_PIPELINE"` 时下发 `MOVE_FORWARD`。
  - 其它 phase 走 Hold。
- [x] 实现 `@on_command("MOVE_FORWARD", result="SUCCESS")`：
  - 读取 active rack snapshot。
  - 调用 `bin_allocator.plan_allocation(...)`。
  - `ALLOCATED` 时先 claim bin cell，再下发 `PUT_TO_BIN`。
  - `RACK_OPERATION_REQUIRED` 时发起 rack operation，context 必须包含恢复锚点。
  - `BLOCKED` 或无 decision 时 Hold。
- [x] 实现 `PICK_AND_PUT` / `MOVE_FORWARD` 失败处理：默认 Hold，不误判业务 NG。
- [x] 运行：`uv run pytest tests/workline_plugins/test_rough_sorter_plugin.py -v`

### Task 5: WMS/RCS 到位两阶段恢复

**Files:**
- Modify: `src/workline_plugins/rough_sorter/plugin.py`
- Test: `tests/workline_plugins/test_rough_sorter_plugin.py`
- Test: `tests/workline_runtime/test_runtime_intent_effects.py`

- [x] 写 `on_external_http` 测试：`WMS_RACK_ARRIVED` / `RCS_RACK_ARRIVED` 返回 resource facts 和内部 retry event，不在同一 handler 内下发 `PUT_TO_BIN`。
- [x] 写 effect/orchestrator 级测试：resource fact 落地后创建内部重试 inbox；重试 inbox 再触发分配。
- [x] 写重复回调幂等测试：同一 rack operation 的 arrived callback 生成相同 `ROUGH_SORTER_STORAGE_RETRY` `event_id`，不得创建多个 retry inbox。
- [x] 实现 `on_external_http(ctx, inbox)`：
  - 仅处理 rack arrived 类型回调。
  - 生成 `RESOURCE_FACT RACK_ARRIVED`，必要时生成 `BIN_MOUNTED`。
  - 生成内部 `DEVICE_EVENT`，event type 为 `ROUGH_SORTER_STORAGE_RETRY`，`event_id="rough-sorter-storage-retry:{operation_key}:{session_id}"`，data 必须包含 `PkgID`、业务上下文和幂等键。
- [x] 在 manifest 中声明 `ROUGH_SORTER_STORAGE_RETRY`。
- [x] 实现 `@on_event("ROUGH_SORTER_STORAGE_RETRY")`，复用出料分配逻辑。
- [x] 运行：
  - `uv run pytest tests/workline_plugins/test_rough_sorter_plugin.py -v`
  - `uv run pytest tests/workline_runtime/test_runtime_intent_effects.py -k resource -v`

### Task 6: PUT_TO_BIN 终态与资源事实

**Files:**
- Modify: `src/workline_plugins/rough_sorter/plugin.py`
- Test: `tests/workline_plugins/test_rough_sorter_plugin.py`

- [x] 写 `PUT_TO_BIN SUCCESS` 测试，断言返回 `RESOURCE_RESERVATION(CONSUME_BIN_CELL) + RESOURCE_FACT(MATERIAL_MOUNTED) + COMPLETE`。
- [x] 写 `PUT_TO_BIN FAILED` 测试，断言只返回 `BLOCK` 并保留当前 bin cell reservation。
- [x] 实现 `@on_command("PUT_TO_BIN", result="SUCCESS")`：
  - 消费 bin cell reservation。
  - 记录 `MATERIAL_MOUNTED` resource fact。
  - 完成 Session。
- [x] 实现 `@on_command("PUT_TO_BIN", result="FAILED")`：
  - 不释放 reservation。
  - Hold，保留设备错误码和错误信息，等待人工确认料盘和硬件状态后再处理预约。
- [x] 运行：`uv run pytest tests/workline_plugins/test_rough_sorter_plugin.py -v`

### Task 7: 集成回归与质量门禁

**Files:**
- All changed files

- [x] 运行插件和注册表测试：

```bash
uv run pytest \
  tests/workline_plugins/test_rough_sorter_contract.py \
  tests/workline_plugins/test_rough_sorter_plugin.py \
  tests/test_workline_service_plugin_validation.py \
  -v
```

- [x] 运行 runtime 相关回归：

```bash
uv run pytest \
  tests/workline_runtime/test_session_resolver.py \
  tests/workline_runtime/test_smt_rack_bin_scheduling_service.py \
  tests/workline_runtime/test_runtime_intent_effects.py \
  tests/workline_runtime/test_plugin_next.py \
  -v
```

- [x] 运行 WMS mock / typed port 相关回归：

```bash
uv run pytest \
  tests/mock \
  tests/wms_integration \
  -v
```

- [x] 新增并运行 rough_sorter 业务集成 smoke：

```bash
uv run pytest tests/integration/workline_runtime/test_rough_sorter_physical_flow_integration.py -v
```

- [x] 运行 lint/format：

```bash
uv run ruff format src/workline_plugins/rough_sorter tests/workline_plugins tests/workline_runtime tests/mock src/workline_plugin_registry.py
uv run ruff check src/workline_plugins/rough_sorter tests/workline_plugins tests/workline_runtime tests/mock src/workline_plugin_registry.py
```

- [x] 提交前运行 GitNexus 变更检测：

```bash
npx gitnexus detect-changes
```

## 5. 验收标准

- 扫码 OK 后必须先下发 `MEASUREMENT_REEL`，不得跳过测量直接 `PICK_AND_PUT`。
- 所有业务字段都在 `payload.data` 中解析；顶层业务字段不会被接受为合法输入。
- 条码 NG、测量 NG、WMS 无匹配必须走物理 NG 搬运，NG 搬运成功后流程完成并保留 NG 证据。
- WMS 不可用、设备未知失败、流水线失败、出料放置失败、投影冲突必须进入 Hold。
- WMS 业务拒绝进入物理 NG；WMS 不可用、超时、熔断、协议异常和 evidence 异常进入 Hold。
- 设备命令回调必须通过 `DeviceCommand.params.action` 还原 WES 业务 action，不得依赖设备 `task_type` 与业务 action 一致。
- 无存储位置时必须通过 `RACK_OPERATION_REQUEST` 进入货架操作域，不直接调用 HTTP、Repository 或 DB。
- WMS/RCS 到位回调必须先落资源事实，再通过带稳定 `event_id` 的内部 retry event 重新分配；不得在同一 plugin handler 中读未落库投影。
- `PUT_TO_BIN` 成功后必须消费预约、记录 `MATERIAL_MOUNTED`、完成 Session。
- `PUT_TO_BIN` 失败后必须保留预约并进入 Hold；不得在物理状态未知时释放格位。
- 插件不直接写数据库、不直接访问 Repository、不直接派发 Outbox，只输出 `RuntimeIntent`。

## 6. NOT in scope

- 不实现监控仪表盘和告警规则；已有 `TODOS.md` 记录监控/告警后续项。
- 不实现 NG 返工工单、NG 容器生命周期、PDA 离线扫码；这些属于 Runtime Hold/NG 后续域。
- 不做 Workline worker 吞吐并发调优；该项已有独立 TODO。
- 不做 WMS evidence 留存清理/归档 job。
- 不在本次把 Runtime 扩展为一等 `business_action` 字段；本次用 `params.action` 作为局部合同，并已决定后续补充 TODO。
- 不实现 PUT_TO_BIN 失败自动释放预约；失败后 WES 不能确定料盘和硬件状态，必须人工确认后释放。
- 不引入新的 Repository、Service 或 API route；本次只接入现有 runtime/plugin 服务容器能力。

## 6.1 What already exists

- `WorklinePlugin`、`@on_event`、`@on_command`、`RuntimeIntent`、`PluginNext` 已提供插件入口和意图合同；本计划复用，不再新建并行运行时。
- `SessionResolver` 已通过插件 manifest 派生 business key；本计划只补 rough_sorter resolver 和 retry event 稳定身份。
- `WorklineRuntimeServices` 已注入 SMT rack allocator、active rack snapshot provider 和 WMS typed port；插件只调用服务容器，不直接访问 Repository/DB/HTTP。
- `RuntimeIntentEffectApplier` 已落地 command、rack operation、resource fact、resource reservation 和 internal device event；本计划只补插件输出和少量合同测试。
- `smt_rack_bin_scheduling_service` 已提供 allocation / rack operation decision；本计划复用其 `SmtRackOperationRequest`，不手写外部 rack 请求。
- `WmsTypedPortService` 已提供 query inventory、breaker、cache 和 evidence；本计划只补 rough_sorter 调用语义和 mock 合同。

## 6.2 Failure modes

| Codepath | Production failure | Test | Handling | User / operator sees |
|----------|--------------------|------|----------|----------------------|
| `SCAN_COMPLETED` | 缺少 `payload.data` 或业务字段拍平到顶层 | contract + plugin tests | `BLOCK` 或业务 NG，按原因分类 | 明确 payload/条码错误 |
| `MEASUREMENT_REEL SUCCESS` | 测量字段缺失或不可解析 | plugin tests | `BLOCK` | Hold 原因指向测量 payload |
| WMS query | no match、wrong sku/lot、business reject | plugin tests + WMS mock tests | physical NG | NG reason 和 WMS evidence |
| WMS query | timeout、circuit open、unavailable、evidence failure | plugin tests | `BLOCK` | Hold 原因指向 WMS 依赖 |
| `MOVE_FORWARD SUCCESS` | allocator blocked、snapshot partial、rack operation pending | plugin + allocator regression | `BLOCK` 或 `RACK_OPERATION_REQUEST` | Hold 或等待货架 operation |
| rack arrived callback | 重复 callback | effect/orchestrator test | stable retry `event_id` 幂等 | 不产生重复 retry |
| `PUT_TO_BIN FAILED` | 料盘/硬件状态不可信 | plugin test | 保留 reservation + `BLOCK` | 人工确认后处理 |

## 6.3 Diagram placement

- `src/workline_plugins/rough_sorter/plugin.py`：保留一段简短 ASCII 状态流，覆盖 scan -> measure -> WMS -> move -> allocate -> put/NG。
- `tests/integration/workline_runtime/test_rough_sorter_physical_flow_integration.py`：在测试顶部放一段端到端 smoke 流程图，说明每个 fake service 和 RuntimeIntent effect 的边界。
- 不在 `contract.py` 放长流程图；该文件只保留常量和 payload 合同，避免注释压过纯函数。

## 6.4 Implementation Tasks

Synthesized from this review's findings. Each task derives from a specific finding above. Run with Claude Code or Codex; checkbox as you ship.

- [x] **T1 (P1, human: ~2h / CC: ~20min)** — WMS validation — Lock business reject, stable request ID, and strict item matching.
  - Surfaced by: Architecture/Test review — WMS business reject, request_id, wrong sku/lot gaps.
  - Files: `src/workline_plugins/rough_sorter/plugin.py`, `src/workline_plugins/rough_sorter/contract.py`, `tests/workline_plugins/test_rough_sorter_plugin.py`, `tests/mock/test_wms_mock_server.py`
  - Verify: `uv run pytest tests/workline_plugins/test_rough_sorter_plugin.py tests/mock/test_wms_mock_server.py -v`
- [x] **T2 (P1, human: ~2h / CC: ~20min)** — Command contract — Preserve WES action through `params.action`.
  - Surfaced by: Architecture review — business action/device task_type split would otherwise route callbacks to `TEST`/`PICK_AND_PUT`.
  - Files: `src/workline_plugins/rough_sorter/contract.py`, `tests/workline_plugins/test_rough_sorter_contract.py`, callback route regression test
  - Verify: `uv run pytest tests/workline_plugins/test_rough_sorter_contract.py tests/workline_runtime/test_runtime_config_and_normalization.py -v`
- [x] **T3 (P1, human: ~1h / CC: ~10min)** — Physical safety — Keep reservation on `PUT_TO_BIN FAILED`.
  - Surfaced by: Architecture review — releasing a cell after uncertain placement can double-allocate a physically occupied cell.
  - Files: `src/workline_plugins/rough_sorter/plugin.py`, `tests/workline_plugins/test_rough_sorter_plugin.py`
  - Verify: `uv run pytest tests/workline_plugins/test_rough_sorter_plugin.py -k put_to_bin -v`
- [x] **T4 (P1, human: ~1.5h / CC: ~15min)** — Retry idempotency — Use stable storage retry `event_id`.
  - Surfaced by: Test review — retry event data-only idempotency would include timestamp and allow duplicate retry inboxes.
  - Files: `src/workline_plugins/rough_sorter/plugin.py`, `tests/workline_plugins/test_rough_sorter_plugin.py`, `tests/workline_runtime/test_runtime_intent_effects.py`
  - Verify: `uv run pytest tests/workline_plugins/test_rough_sorter_plugin.py tests/workline_runtime/test_runtime_intent_effects.py -k 'device_event or resource' -v`
- [x] **T5 (P2, human: ~1h / CC: ~10min)** — Contract hygiene — Centralize phase and role constants.
  - Surfaced by: Code Quality review — phase strings and role names were underspecified and would drift across tests/config.
  - Files: `src/workline_plugins/rough_sorter/contract.py`, `src/workline_plugins/rough_sorter/plugin.py`, `tests/test_workline_service_plugin_validation.py`
  - Verify: `uv run pytest tests/workline_plugins/test_rough_sorter_contract.py tests/test_workline_service_plugin_validation.py -v`
- [x] **T6 (P2, human: ~3h / CC: ~30min)** — Integration smoke — Add full rough_sorter physical-loop test.
  - Surfaced by: Test review — plugin/unit/effect tests do not prove the cross-layer physical loop.
  - Files: `tests/integration/workline_runtime/test_rough_sorter_physical_flow_integration.py`
  - Verify: `uv run pytest tests/integration/workline_runtime/test_rough_sorter_physical_flow_integration.py -v`

## 7. 并行执行策略

Sequential implementation, no parallelization opportunity.

原因：核心变更集中在 `src/workline_plugins/rough_sorter/`，测试共享同一插件行为和 runtime intent 合同。并行 worktree 会在同一模块和同一测试文件上产生冲突，收益低。

## 8. 关键风险

- `RACK_OPERATION_REQUEST` payload 必须包含 runtime effect 要求的 rack task specs、`trace_id`、`target_code` 和 timeout；实现时应复用 `smt_rack_bin_scheduling_service` 返回的 request，不手写外部请求。
- `RESOURCE_FACT` 和 `RESOURCE_RESERVATION` 返回后可能触发 reconciling 结果；插件不能假设后续 intent 一定继续执行。
- WMS mock 与 typed port 合同必须保持一致，否则开发/测试环境会放过生产会失败的物料验证。
- 左右线多设备场景依赖 `resume_source_device_code`；没有恢复锚点会导致外部回调后命令路由不稳定。

## 9. 文档自检

- Spec coverage: 覆盖扫码、条码验证、测量、WMS 强校验、入料、流水线前进、出料分配、货架补给、两阶段恢复、PUT_TO_BIN 终态、NG/Hold 边界。
- Placeholder scan: 无占位实现项。
- Project rules: 计划文档只描述接口、状态流、任务边界、验收标准和验证命令，不粘贴完整类/函数实现。

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | — | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 10 issues resolved, 0 critical gaps |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

- **UNRESOLVED:** 0
- **VERDICT:** ENG CLEARED — ready to implement.
