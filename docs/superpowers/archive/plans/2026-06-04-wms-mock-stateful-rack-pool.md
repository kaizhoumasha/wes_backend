# WMS MOCK 有状态货架池优化方案

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **Implementation status (2026-06-05):** 已实现并通过验收。下方 checkbox 保留为原始执行计划，不表示当前未完成状态。

**Goal:** 将当前 WMS MOCK 从“固定货架模板回调”优化为“有限、有状态、符合物理约束的货架/料箱/料格模拟器”，支撑后续粗分机、分拣机和换架流程联调。

**Architecture:** 优化只落在 MOCK 层，不改变 WES 生产业务代码契约。WMS MOCK 维护独立内存状态，`rack-operation` 根据物料尺寸、货架池状态和任务类型分配或释放货架，并通过现有外部回调入口通知 WES。调试前通过 debug reset 恢复确定性初始状态。

**Tech Stack:** FastAPI mock server, pytest, existing `src/workline_runtime/sandbox_catalog.py`, existing WES external callback contract.

---

## 工程评审锁定决策

本计划已根据工程评审补齐下列执行约束。实现阶段不得再自行改换语义：

- 失败合同：所有 rack-operation 业务失败都返回 `accepted=true`，并发送 `WMS_RACK_EXCHANGE_FAILED`。失败回调必须携带原 `dispatch_key`、`operation_key`、`status=FAILED`、`reason_code` 和 `reason_message`。不要用 `WMS_RACK_ARRIVED` 表达失败，因为 WES 当前会把到架回调映射为成功。
- 状态边界：实现拆成三层：纯 payload builder、`MockWmsState` 状态 transition、FastAPI route 编排。现有 `_rack_operation_callback_payload()` 的物理布局构造能力应保留为无状态纯函数，不允许在 builder 内突变货架池。
- 测试隔离：`tests/mock/test_wms_mock_server.py` 必须新增 pytest `autouse` reset fixture，每个测试前直接调用 reset helper，避免全局内存状态导致顺序依赖。
- 并发安全：`MockWmsState` 内置 `asyncio.Lock`。`POST /api/wms/rack-operation` 在 route 阶段完成“选 rack + 标记 `ALLOCATED` + 记录 operation”，BackgroundTasks 只负责发送已经确定的 callback payload。

## 现状判断

当前 `tests/mock/wms_mock_server.py` 的货架主数据只有两个单层货架：

- `RACK-001`: `SINGLE_LAYER`, 6 格箱布局，适配 7 寸物料。
- `RACK-3CELL-001`: `SINGLE_LAYER`, 3 格箱布局，适配 13 寸物料。

`GET /api/wms/racks` 只返回这两个货架，不会动态新增。`POST /api/wms/rack-operation` 当前不维护货架占用、在途、移出、释放等状态，因此同一个货架模板可以被反复回调给 WES。这个行为适合验证基础串行流程，但不适合验证后续分拣机所依赖的真实换架、补给、耗尽和容量边界。

## 实际需求

本轮优化面向开发联调，不追求完整 WMS 实现。需要覆盖下列真实业务约束：

- 7 寸料盘只能进入 6 格箱货架。
- 13 寸料盘只能进入 3 格箱货架的大格，当前大格 `capacity_depth_mm` 统一为 `80`。
- 货架池是有限库存，不能无限生成。
- 货架被分配后不能再次作为可用空架返回。
- active work rack 被 move-out 后，同一 operation 内应释放目标工位容量，再允许新货架补给。
- 没有可用匹配货架时，WMS MOCK 应返回明确失败状态，用于验证 WES HOLD/RETRY/失败展示。
- 调试脚本仍只负责按间隔发送 `SCAN_COMPLETED`，不接管设备状态确认。

## 非目标

- 不实现真实 WMS 库存账务。
- 不实现跨进程持久化状态，容器重启或 debug reset 可恢复初始状态。
- 不改动设备插件的串行控制职责。
- 不把 WMS MOCK 变成分拣机业务决策引擎，放置决策仍由 WES/插件完成。

## 目标状态模型

WMS MOCK 维护独立内存状态，建议命名为 `MockWmsState` 或同等清晰结构。

职责边界：

- `build_active_bin_rack_payload(rack_id, request_payload)` 或同等函数：只根据指定 rack 和请求 payload 构造 `active_bin_rack`、`bin_mounts`、cells，不读取或修改状态。
- `MockWmsState.apply_task(payload)` 或同等方法：只负责校验任务、选择 rack、更新 rack/work position/recent operations，并返回 callback 决策。
- FastAPI route：只负责按 task sequence 调用状态 transition，并把 callback 决策交给 BackgroundTasks 发送。

状态处理流程：

```text
POST /api/wms/rack-operation
        |
        v
parse task sequence
        |
        v
async with MockWmsState.lock
        |
        +-- MOVE_OUT_ACTIVE_RACK    -> release position, old rack MOVED_OUT
        +-- ALLOCATE_AND_MOVE_RACK  -> choose AVAILABLE, mark ALLOCATED, prepare arrived callback, mark ACTIVE
        +-- MOVE_RACK               -> update rack location/status
        |
        v
return accepted=true
        |
        v
BackgroundTasks sends decided callback payload
```

货架状态：

- `AVAILABLE`: 可作为空架补给。
- `ALLOCATED`: 已被某个 rack-operation 分配，等待移动或回调。
- `ACTIVE`: 已到达目标工位，作为当前工作货架。
- `MOVED_OUT`: 已从工位移出，可视业务场景进入空架区。
- `UNAVAILABLE`: 故障或人工禁用，仅用于故障注入。

货架布局：

- `SIX_CELL`: 每个 rack slot 挂 6 格箱，格位 `1,2,3,4,5,6`。
- `THREE_CELL`: 每个 rack slot 挂 3 格箱，格位 `1,2,7`，其中 `7` 为大格。

工位状态：

- `SINGLE_LAYER_A` 只能有一个 active rack。
- 同一个 rack-operation 内包含 move-out 和 allocate/move-in 时，应先在 MOCK 状态中释放工位，再分配新货架。
- 没有 move-out 的情况下，如果目标工位已有 active rack，新的补给请求应失败。

## 货架池初始数据建议

为了支撑连续 7 寸和 13 寸联调，初始池不应只有两个货架。建议默认提供有限但足够的开发库存：

- 6 格箱货架：`RACK-6CELL-001` 到 `RACK-6CELL-006`。
- 3 格箱货架：`RACK-3CELL-001` 到 `RACK-3CELL-004`。

兼容要求：

- 保留 `RACK-001` 作为 6 格箱别名或历史货架，避免破坏已有测试和已有调试数据理解。
- `RACK-3CELL-001` 继续作为首个 3 格箱货架。
- `GET /api/wms/racks` 返回当前状态，而不是只返回静态模板。

## API 行为优化

### `GET /api/wms/racks`

返回当前 WMS MOCK 货架池状态，支持按 `type` 过滤。每个货架应包含：

- `rack_id`
- `rack_type`
- `status`
- `current_location`
- `layout_code`
- `bin_type`
- `active_position_code`
- `allocated_operation_key`

### `GET /api/wms/racks/{rack_id}`

返回单个货架当前状态。未知货架继续返回 404。

### `POST /api/wms/rack-operation`

按 task 类型处理状态：

- `MOVE_OUT_ACTIVE_RACK`: 将指定或当前 active rack 从目标工位移出，释放目标工位。
- `ALLOCATE_AND_MOVE_RACK`: 按物料尺寸选择匹配 layout 的 `AVAILABLE` 货架，标记为 `ALLOCATED` 后回调 `WMS_RACK_ARRIVED`，再变更为 `ACTIVE`。
- `MOVE_RACK`: 如果指定 rack，按目标位置移动并更新状态。

失败返回和失败回调：

- 没有匹配 layout 的可用货架：返回 `accepted=true`，发送 `WMS_RACK_EXCHANGE_FAILED`，失败原因使用 `NO_AVAILABLE_RACK`。
- 目标工位已有 active rack 且 operation 未释放：返回 `accepted=true`，发送 `WMS_RACK_EXCHANGE_FAILED`，失败原因使用 `TARGET_POSITION_OCCUPIED`。
- 请求指定 rack 与物料 layout 不匹配：返回 `accepted=true`，发送 `WMS_RACK_EXCHANGE_FAILED`，失败原因使用 `RACK_LAYOUT_MISMATCH`。
- 失败回调沿用 WES 现有 external callback contract，不修改 WES 生产代码。`WMS_RACK_ARRIVED` 只表达成功到架，不承载失败状态。

### Debug API

新增或完善调试接口：

- `POST /debug/reset`: 恢复 MOCK 初始状态、清空故障注入状态。
- `GET /debug/racks`: 返回完整货架池、工位、最近 rack-operation 记录。
- `POST /debug/racks/{rack_id}/status`: 手动设置某货架状态，用于耗尽和失败场景测试。

## 任务拆分

### Task 1: 提取货架池状态模型

**Files:**

- Modify: `tests/mock/wms_mock_server.py`
- Test: `tests/mock/test_wms_mock_server.py`

- [ ] 新增有状态货架池初始化函数，保留现有静态物理布局常量。
- [ ] 新增 `MockWmsState` 或同等结构，内置 `asyncio.Lock`、rack pool、work position、recent operation 记录。
- [ ] 将现有 `active_bin_rack` / `bin_mounts` / cells 构造拆为纯 builder，确保直接调用 builder 不会修改货架状态。
- [ ] 为 `tests/mock/test_wms_mock_server.py` 增加 pytest `autouse` reset fixture，每个测试前恢复 rack pool、工位、recent operations 和 fault injection。
- [ ] 增加测试：`GET /api/wms/racks` 返回多台 6 格箱和 3 格箱货架，并包含状态字段。
- [ ] 增加测试：`POST /debug/reset` 后货架状态恢复为初始值。
- [ ] 运行 `uv run pytest tests/mock/test_wms_mock_server.py`，确保现有物理约束测试继续通过。

### Task 2: rack-operation 分配可用货架

**Files:**

- Modify: `tests/mock/wms_mock_server.py`
- Test: `tests/mock/test_wms_mock_server.py`

- [ ] 增加测试：7 寸物料分配 6 格箱货架，分配后该货架不再是 `AVAILABLE`。
- [ ] 增加测试：13 寸物料分配 3 格箱货架，回调 `active_bin_rack.cells` 只包含 `1,2,7`，大格容量为 `80`。
- [ ] 增加测试：连续两次 13 寸补给应返回不同 3 格箱货架，直到池耗尽。
- [ ] 实现 route 内原子分配逻辑：在 `MockWmsState.lock` 内完成选择 rack、标记 `ALLOCATED`、记录 operation，避免同一个可用货架被重复返回。
- [ ] 增加测试：并发两个 `ALLOCATE_AND_MOVE_RACK` 请求不会拿到同一个 rack。
- [ ] 运行 `uv run pytest tests/mock/test_wms_mock_server.py`。

### Task 3: 支持工位 active rack 和 move-out release

**Files:**

- Modify: `tests/mock/wms_mock_server.py`
- Test: `tests/mock/test_wms_mock_server.py`

- [ ] 增加测试：`SINGLE_LAYER_A` 已有 active rack 时，未包含 move-out 的新补给失败。
- [ ] 增加测试：同一 operation 先 `MOVE_OUT_ACTIVE_RACK` 再 `ALLOCATE_AND_MOVE_RACK` 时，新货架可以到达 `SINGLE_LAYER_A`。
- [ ] 增加测试：move-out 后旧货架状态为 `MOVED_OUT`，目标工位 active rack 更新为新货架。
- [ ] 实现 operation 内按 sequence 处理任务，确保释放先于补给。
- [ ] 运行 `uv run pytest tests/mock/test_wms_mock_server.py`。

### Task 4: 失败回调和可观测性

**Files:**

- Modify: `tests/mock/wms_mock_server.py`
- Test: `tests/mock/test_wms_mock_server.py`

- [ ] 增加测试：3 格箱货架池耗尽时，WMS MOCK 返回 `accepted=true` 并生成 `WMS_RACK_EXCHANGE_FAILED`，原因是 `NO_AVAILABLE_RACK`。
- [ ] 增加测试：指定 6 格箱货架处理 13 寸物料时，WMS MOCK 返回 `accepted=true` 并生成 `WMS_RACK_EXCHANGE_FAILED`，原因是 `RACK_LAYOUT_MISMATCH`。
- [ ] 增加测试：`SINGLE_LAYER_A` 占用且 operation 未释放时，失败回调为 `WMS_RACK_EXCHANGE_FAILED`，原因是 `TARGET_POSITION_OCCUPIED`。
- [ ] 增加 `GET /debug/racks`，返回货架池、工位状态、最近 rack-operation 结果。
- [ ] 运行 `uv run pytest tests/mock/test_wms_mock_server.py`。

### Task 5: 集成调试验证

**Files:**

- Modify only if needed: existing debug scripts under `scripts/` or `tests/`
- No production source changes expected.

- [ ] 重启 `mock_wms` 容器。
- [ ] 使用现有调试清理能力清理 WES 调试数据。
- [ ] 通过登录接口使用 `admin/admin123` 获取 token。
- [ ] 一次性压入 10 条 `SCAN_COMPLETED`，脚本只发送事件。
- [ ] 验证 WES DB 中 session、inbox、device_commands、rack_operations 状态符合串行处理预期。
- [ ] 验证 `/runtime/integration-debug` 能展示当前 session、等待 session、失败 session 和 rack-operation 等待状态。
- [ ] 验证 WMS MOCK `/debug/racks` 与 WES DB 中 active rack、bin mount、cell capacity 的调试数据一致。

## 验收标准

- 7 寸物料只使用 6 格箱货架。
- 13 寸物料只使用 3 格箱货架，且进入大格 `7`，大格容量 `80`。
- WMS MOCK 不再无限重复返回同一可用空架。
- 货架池耗尽能产生 `WMS_RACK_EXCHANGE_FAILED`，且 WES 可按现有 rack lifecycle 进入外部失败/人工处理路径。
- 同一 rack-operation 内 move-out release 能释放目标工位容量。
- 并发补给请求不会分配同一个 `AVAILABLE` 货架。
- 每个 mock 单测之间不会共享突变后的 rack/work position 状态。
- 调试脚本仍只发送 `SCAN_COMPLETED`，不模拟设备完成状态。
- `uv run pytest tests/mock/test_wms_mock_server.py` 通过。
- 完整粗分机联调中，WMS MOCK 状态、WES resource projection、integration-debug 页面三者能对齐。

## 测试覆盖目标

```text
CODE PATHS
[+] state reset                 -> reset helper + /debug/reset
[+] rack query                  -> list/detail/type filter/current status
[+] allocate success            -> 7 inch / 13 inch / distinct rack / cells
[+] operation sequence          -> move-out release before allocate
[+] failure callback            -> occupied / mismatch / exhausted
[+] concurrency                 -> lock prevents duplicate allocation

DEBUG FLOWS
[+] mock reset before run        -> deterministic local debug
[+] 10 SCAN_COMPLETED burst      -> WMS state, WES projection, integration-debug align
[+] exhausted rack pool          -> WES shows external failure path, not silent success
```

## 风险与控制

- 风险：状态化 MOCK 可能影响现有依赖固定 `RACK-001` 的测试。控制：保留 `RACK-001` 兼容，并先用单测锁定旧行为兼容点。
- 风险：异步回调失败语义与 WES 当前处理不完全匹配。控制：业务失败统一使用 `WMS_RACK_EXCHANGE_FAILED`，不使用 `WMS_RACK_ARRIVED` 表达失败，不改 WES 入口契约。
- 风险：内存状态在并发测试中互相污染。控制：新增 autouse reset fixture，每个测试前直接调用 reset helper 恢复状态。
- 风险：并发 rack-operation 重复分配同一货架。控制：在 route 内使用 `MockWmsState.lock` 原子完成分配和状态记录，BackgroundTasks 只发送已确定的回调。
- 风险：payload 构造和状态突变耦合后难以测试。控制：保持物理布局 builder 为纯函数，状态 transition 只返回 callback 决策。
- 风险：MOCK 变复杂后偏离开发调试目标。控制：只实现货架池、工位、布局和失败原因，不实现真实库存账务。

## 推荐执行顺序

1. 先完成 Task 1 和 Task 2，让货架池从静态模板变成有限可分配资源。
2. 再完成 Task 3，验证 13 寸换架流程中的 move-out release。
3. 然后完成 Task 4，让失败场景可测、可观察。
4. 最后执行 Task 5，用 10 条 `SCAN_COMPLETED` 做端到端验证。
