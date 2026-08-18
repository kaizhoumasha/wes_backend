# Phase 8 粗分机本机 Mock 模拟验收补齐实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在生产部署组合不变、仅外部 WMS/ECS HTTP 边界由本机 Mock 替代的条件下，补齐 Phase 8 可在开发环境完成的工程验收和模拟联调证据；供应商一致性、真实现场联调和业务验收继续明确标记为 `NOT RUN / BLOCKED`。

**Architecture:** 复用现有粗分机插件 E2E 的真实 PostgreSQL、Redis、WES API、Celery Worker/Beat 和当前源码镜像，只增强测试私有的 WMS/ECS Stub。基础能力证据仍由核心 FAST/HEAVY 测试所有者提供，粗分机插件只验证业务闭环；不把 Mock 引入生产代码，不新增通用 Mock 框架，不复制核心幂等、事务和 prefork 测试。

**Tech Stack:** Python 3.13、pytest、stdlib `ThreadingHTTPServer`、Docker、PostgreSQL、Redis、FastAPI/Uvicorn、Celery、现有 WMS Adapter/ECS 统一合同。

**Spec:** `docs/superpowers/plans/2026-08-03-rough-sorter-plugin-convergence.md` 的 Task 9，以及 `docs/integration/rough-sorter-joint-acceptance.md`、`docs/integration/rough-sorter-supplier-conformance.md` 的验收分层。

**当前状态：** `COMPLETED — LOCAL ENGINEERING PASS / LOCAL MOCK INTEGRATION PASS`（2026-08-19）。Task 0—5
均已完成；当前有效证据绑定 Commit `d90d0df6`：QUALITY `3532 passed, 4 skipped`、计划锁定的核心 FAST owner
`21 passed`、Phase 8 selector HEAVY `362 passed, 0 skipped`、粗分机插件 E2E `10 passed, 0 skipped`。供应商一致性、
真实 WMS/设备现场联调和业务验收仍为 `NOT RUN / BLOCKED`，因此 Phase 8 总计划仍保持
`IN_PROGRESS — EXTERNAL BLOCKED`。本文档自此作为已完成的本机验收实施记录，不再作为待执行计划。

---

## 1. 架构裁决

### 1.1 可以用本机 Mock 完成什么

本机 Mock 可以完成两类验收，并应在报告中分别命名：

1. **仓库工程验收**：合同映射、幂等/冲突、事务、资源释放、重启与 prefork 等由既有核心测试所有者证明。
2. **本机 Mock 模拟联调**：使用真实 WES 镜像和真实基础设施，仅把开发环境不可达的 WMS/ECS HTTP 对端替换为合同级 Stub，证明粗分机业务路径能够跨进程闭环并安全停住。

这两类证据合并后，Phase 8 可报告：`LOCAL ENGINEERING PASS / LOCAL MOCK INTEGRATION PASS`。

### 1.2 本计划不能替代什么

以下验收不得因本计划转为 `PASS`：

- 真实 WMS 对接、供应商 ECS/网关一致性验收；
- PLC、设备动作、物理到位、安全互锁和真实时序；
- TEST/生产环境可达性、现场联调和业务方签字；
- 真实失败概率、网络抖动和厂商私有协议适配。

最终状态必须保留：`SUPPLIER CONFORMANCE NOT RUN`、`SITE INTEGRATION NOT RUN`、`BUSINESS ACCEPTANCE NOT RUN`。

### 1.3 严格边界

- **基础能力**：`src/app/`、`src/celery_app/` 和核心测试负责可靠执行、持久化、幂等、资源冻结/释放、prefork 与恢复。
- **业务能力**：`workline_plugins/rough_sorter/` 负责粗分机场景编排和插件级模拟联调。
- **Mock**：只存在于 `workline_plugins/rough_sorter/tests/e2e/`；不得进入生产依赖注入、配置、镜像或数据库。
- **供应商能力**：私有协议和真机验收留在供应商 ECS/网关边界，不在 WES 核心仓库复制。

```text
外部输入 / Mock               WES 基础能力                         粗分机业务能力
SCAN ----------------------> API / Inbox / Evidence -----------> Decision
WMS WAIT <------------------ confirmation <--------------------- Admission
WMS ACCEPT ----------------> 新 operation identity ------------> DeviceCommand
ECS ACK -------------------> ACKNOWLEDGED ----------------------> 等待，不释放
ECS CALLBACK --------------> result evidence ------------------> 下一业务决策
WMS RECORDED --------------> confirmation ---------------------> CLOSED
```

图中 Mock 不是生产对端，ACK 不是物理完成；基础能力测试和业务能力测试各自证明本层责任，不能互相替代。

## 2. 冻结变更面

预期只修改以下文件：

- `workline_plugins/rough_sorter/tests/e2e/test_business_loop.py`
- `docs/integration/rough-sorter-joint-acceptance.md`
- `docs/integration/rough-sorter-supplier-conformance.md`
- `docs/superpowers/plans/2026-08-03-rough-sorter-plugin-convergence.md`
- `docs/superpowers/plans/2026-08-18-rough-sorter-local-mock-acceptance-completion.md`（本计划文档）
- `docs/superpowers/README.md`

明确不修改：

- `src/`、Alembic migration、数据库 schema、生产配置和 Docker 部署组合；
- `tests/` 下已有核心测试的断言和所有权；
- `docs/architecture/heavy-test-impact.toml`；
- `docs/hardware/`；
- `VERSION`、`CHANGELOG.md` 和发布元数据。

如果新增 E2E 首先暴露生产缺陷，立即停止本计划：保留失败证据，单独建立 Bug 修复计划并按 TDD 修改生产代码。不得在“验收补齐”中顺手改变业务语义。

## 3. 验收矩阵与完成条件

| 层级 | 本计划证据 | 完成状态 |
| --- | --- | --- |
| 基础合同与可靠执行 | 复用既有核心 FAST/HEAVY 所有者；核对可执行树指纹 | `PASS` |
| 粗分机正常闭环 | 现有真实镜像 E2E | `PASS` |
| WMS `WAIT` 安全停住并新身份重试 | 新增真实镜像 E2E | `PASS` |
| ECS ACK 后等待 CALLBACK、不重放 | 新增真实镜像 E2E | `PASS` |
| 供应商 ECS/网关一致性 | 不在本机 Mock 范围 | `NOT RUN` |
| 现场物理联调 | 开发环境不可达 | `NOT RUN / BLOCKED` |
| 业务验收 | 需要业务方与真实设备 | `NOT RUN / BLOCKED` |

完成本计划必须同时满足：

- 新增的两个 E2E 在当前源码构建的真实 WES 镜像中通过；
- 插件领域测试通过，核心聚焦 FAST 证据有效；
- prefork/恢复 HEAVY 证据要么在相同基础可执行树指纹下有效复用，要么重新运行通过，不能以 `skipped` 代替；
- 文档准确区分代码、运行时、Mock、供应商、现场和业务验收；
- 最终 diff 没有生产代码、兼容层、通用 Mock 框架或无关清理。

---

## Task 0（已完成）：冻结工作树并准备当前源码镜像

**Files:**

- Verify: 冻结变更面中的全部文件
- Build only: `wes-backend:phase8-rough-sorter`

### Step 1：冻结工作树并拒绝范围外变化

Run:

```bash
git rev-parse HEAD
git status --short
git diff --name-only
git diff --cached --name-only
```

Expected: 记录 HEAD 和 staged/unstaged/untracked 清单；除已知用户现场与本计划冻结变更面外不得继续。不得 stash、reset、覆盖或顺手暂存无关变化。

### Step 2：核对并构建当前源码镜像

先比较本地镜像的源码标签与当前 HEAD/tree；镜像不存在或任一标签不匹配时，必须从当前工作树重新构建：

```bash
current_revision=$(git rev-parse HEAD)
current_tree=$(git rev-parse 'HEAD^{tree}')

docker image inspect wes-backend:phase8-rough-sorter \
  --format '{{.Id}} revision={{index .Config.Labels "org.opencontainers.image.revision"}} tree={{index .Config.Labels "com.zontec.wes.source-manifest"}}'

docker build \
  --build-arg WES_VCS_REVISION="$current_revision" \
  --build-arg WES_SOURCE_TREE="$current_tree" \
  -t wes-backend:phase8-rough-sorter .
```

仅在标签已精确匹配时跳过 `docker build`。构建后再次 `docker image inspect`，记录 image ID、revision 和 source-tree；随后由现有 `_assert_current_image()` 在每个 E2E 启动前做机器校验。旧镜像、仅同名镜像或手工口头确认均不能作为当前源码证据。

Task 1 至 Task 3 的完整插件 E2E 结束前不要穿插 Commit；任何 Commit 都会改变 HEAD/tree 并使镜像标签证据失效，必须先重新执行本步再运行后续 E2E。

---

## Task 1（已完成）：用真实镜像补齐 WMS `WAIT → ACCEPT` 模拟联调

**Files:**

- Modify: `workline_plugins/rough_sorter/tests/e2e/test_business_loop.py`
- Test: `workline_plugins/rough_sorter/tests/e2e/test_business_loop.py`

### Step 1：先做保持绿灯的测试夹具整理

本任务是测试验收能力扩展，不改变生产行为，因此不得为了流程制造“缺 helper”的人工 RED。先在同一测试文件内抽取两处立即复用的私有 helper：

- `_measurement_scan()`：返回现有测试里的扫描 payload；
- `_DockerStack.wait_query(sql, expected, timeout=API_TIMEOUT_SECONDS)`：复用现有轮询和诊断风格等待数据库可观察状态。

`wait_query` 保持单一职责：轮询 `query()`，达到期望即返回，超时只附加 `diagnostics()`；不引入场景 DSL 或通用等待框架。

Run:

```bash
uv run --project workline_plugins/rough_sorter pytest \
  workline_plugins/rough_sorter/tests/e2e/test_business_loop.py::test_installed_plugin_closes_one_material_through_public_ingress_and_real_workers \
  -q
```

Expected: 原有正常闭环继续通过。若此时失败，只修正本步机械整理，不进入新场景。

### Step 2：加入最小 Stub 分支和 WMS WAIT 验收场景

在同一测试文件内增加最小状态字段，不抽象为通用场景框架：

```python
def _released_event() -> threading.Event:
    event = threading.Event()
    event.set()
    return event


@dataclass(slots=True)
class _BoundaryState:
    api_url: str = ""
    admission_results: list[str] = field(default_factory=lambda: ["ACCEPT"])
    admission_retry_after_ms: int = 500
    admission_accept_release: threading.Event = field(default_factory=_released_event)
    ecs_callback_release: threading.Event = field(default_factory=_released_event)
    wms_requests: list[dict[str, Any]] = field(default_factory=list)
    ecs_commands: list[dict[str, Any]] = field(default_factory=list)
    callback_errors: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)
```

在 `_WmsStubHandler` 处理 `inbound.material.admission_decide@v1` 时，在锁内消费 `admission_results`；`WAIT` 必须返回正式合同字段：

```python
if admission_result == "WAIT":
    response = common | {
        "code": "DECIDED",
        "data": {
            "result": "WAIT",
            "reason_code": "CELL_PENDING",
            "retry_after_ms": self.state.admission_retry_after_ms,
        },
    }
```

当脚本结果为 `ACCEPT` 时，在写回响应前执行
`self.state.admission_accept_release.wait(timeout=API_TIMEOUT_SECONDS)`；默认事件已释放，现有正常闭环不变。新测试使用未释放事件确定性地截停第二次请求，先检查新 `operation_id` 和零 `DeviceCommand`，再放行 `ACCEPT`，避免依赖 500ms 竞态窗口。

增加 `test_wms_wait_creates_new_due_operation_without_device_command_then_closes()`，使用现有 `_DockerStack`、真实数据库、API 和 Worker，严格断言：

- 第一次 `WAIT` 是已完成业务响应，等待期间 `DeviceCommand` 数量为 `0`；
- 到期 follow-up 使用新的 `operation_id`，两次确认身份均可追踪；
- 放行第二次 `ACCEPT` 后沿既有路径到达 `CLOSED`；
- 不断言数据库主键或不可观察的内部实现。

### Step 3：运行新场景和正常闭环回归

Run:

```bash
uv run --project workline_plugins/rough_sorter pytest \
  workline_plugins/rough_sorter/tests/e2e/test_business_loop.py::test_wms_wait_creates_new_due_operation_without_device_command_then_closes \
  workline_plugins/rough_sorter/tests/e2e/test_business_loop.py::test_installed_plugin_closes_one_material_through_public_ingress_and_real_workers \
  -q
```

Expected: `2 passed`，无 callback error，正常闭环的 WMS/ECS 次序保持不变。若测试夹具已经能够确定性驱动场景但新测试暴露生产行为缺陷，立即停止本计划并建立独立 Bug/TDD 修复任务；不得在验收计划内改生产代码或放宽断言。

### Step 4：提交边界

仅在 Task 3 完整插件 E2E 已结束且用户另行授权 Commit 后执行：

```bash
git add workline_plugins/rough_sorter/tests/e2e/test_business_loop.py
git commit -m "test(rough-sorter): 验证 WMS WAIT 模拟闭环"
```

---

## Task 2（已完成）：用真实镜像补齐 ECS `ACK → 延迟 CALLBACK` 不重放证明

**Files:**

- Modify: `workline_plugins/rough_sorter/tests/e2e/test_business_loop.py`
- Test: `workline_plugins/rough_sorter/tests/e2e/test_business_loop.py`

### Step 1：增加确定性的 callback 闸门和 Beat 观察 helper

在 `_EcsStubHandler._callback_success()` 中，用测试状态控制既有 callback 线程：

```python
def _callback_success(self, command: dict[str, Any]) -> None:
    if not self.state.ecs_callback_release.wait(timeout=API_TIMEOUT_SECONDS):
        with self.state.lock:
            self.state.callback_errors.append("timed out waiting to release ECS callback")
        return
```

上述闸门插入现有 `time.sleep(0.4)` 之前，其后的 SUCCESS callback 构造和发送代码原样保留。默认 `_released_event()` 已置位，因此现有正常闭环行为不变。不得向生产配置增加“延迟 callback”开关。

在 `_DockerStack` 内增加测试私有 `log_occurrences(name, marker)` 和 `wait_log_occurrences(name, marker, minimum, timeout=API_TIMEOUT_SECONDS)`；二者只读取现有完整容器日志，按完整日志行中的 marker 计数并在超时时附加 `diagnostics()`。不要新建日志框架。

### Step 2：增加 ACK 后跨过真实 Beat 周期的验收场景

增加 `test_ecs_ack_does_not_replay_command_while_callback_is_withheld()`。测试必须按以下可观察顺序执行：

1. 使用未释放的 `ecs_callback_release` 启动真实 API、PostgreSQL、Redis 和带 `-B` 的 WES Worker；
2. 等待数据库出现 `ACKNOWLEDGED`，记录第一次 ECS `command_code`；
3. 记录 Worker 日志中 `Task src.celery_app.tasks.device_command.dispatch_device_commands_batch succeeded` 完成标记的当前次数；
4. 用 `wait_log_occurrences()` 等待该完成标记至少增加 `1`，即实际完成一次 10 秒 Beat 扫描，而不是只看到 task received 或固定 `sleep(1)`；
5. 断言数据库 `status || ':' || attempt_count` 仍为 `ACKNOWLEDGED:1`，ECS Stub 仍只收到第一次的同一 `command_code`；
6. 放行 CALLBACK，等待材料到达 `CLOSED`，并在 `finally` 中始终释放事件和关闭容器。

该测试只证明统一 ECS 合同层的关键门：HTTP `200/ACCEPTED` 只形成 ACK 证据，不触发资源释放，也不重放等价命令；匹配 CALLBACK 后才继续业务闭环。它不证明供应商设备真的执行了动作。

### Step 3：运行新场景聚焦验证

Run:

```bash
uv run --project workline_plugins/rough_sorter pytest \
  workline_plugins/rough_sorter/tests/e2e/test_business_loop.py::test_ecs_ack_does_not_replay_command_while_callback_is_withheld \
  -q
```

Expected: 通过；日志证明至少发生一次真实 Beat dispatch，释放闸门前状态为 `ACKNOWLEDGED:1` 且只有一个 `command_code`，释放后材料最终 `CLOSED`。固定睡眠、未观察到 Beat、或只检查最终 `CLOSED` 均不得接受。

### Step 4：提交边界

仅在 Task 3 完整插件 E2E 已结束且用户另行授权 Commit 后执行：

```bash
git add workline_plugins/rough_sorter/tests/e2e/test_business_loop.py
git commit -m "test(rough-sorter): 验证设备 ACK 后不重放"
```

---

## Task 3（已完成）：闭合分层测试所有权，不复制核心能力测试

**Files:**

- Test: `workline_plugins/rough_sorter/tests/`
- Test: `tests/contracts/wms_adapter/test_inbound_adapter.py`
- Test: `tests/runtime/execution/test_wms_confirmation_service.py`
- Test: `tests/runtime/transport/test_transport_service.py`
- Test: `tests/runtime/transport/test_transport_outcome.py`
- Test: `tests/runtime/transport/test_transport_acceptance_edges.py`
- Test: `tests/deployment/test_execution_worker_startup.py`
- Evidence only: `tests/integration/test_celery_async_runtime_postgresql.py`
- Evidence only: `tests/integration/execution/test_decision_processing_postgresql.py`
- Evidence only: `tests/e2e/device_command/test_device_command_production_wiring.py`

### 场景、分层和唯一测试所有者

| 场景 | 层级 | 唯一主要所有者 | 关键断言 |
| --- | --- | --- | --- |
| WMS `WAIT → ACCEPT` 跨进程闭环 | 插件 E2E | `test_business_loop.py::test_wms_wait_creates_new_due_operation_without_device_command_then_closes` | WAIT 零命令、新身份、最终 CLOSED |
| ECS `ACK → CALLBACK` | 插件 E2E | `test_business_loop.py::test_ecs_ack_does_not_replay_command_while_callback_is_withheld` | 跨过 Beat 后仍 ACKNOWLEDGED:1、单命令 |
| Admission WAIT | 插件单元 | `test_material_and_admission.py::test_admission_wait_does_not_create_a_device_command` | WAIT 不创建设备命令 |
| 无可用格口 | 插件单元 | `test_device_and_target.py::test_no_available_cell_requests_stable_replacement_plan_without_device_command` | 稳定替换计划、零命令 |
| Target WAIT | 插件单元 | `test_device_and_target.py::test_target_wait_keeps_material_at_outlet` | 物料留在出口 |
| 设备 FAILED/UNKNOWN | 插件单元 | `test_device_and_target.py::test_failed_or_unknown_device_result_never_replays_equivalent_action` | 不重放等价动作 |
| 放行门未关闭 | 插件单元 | `test_placement_and_replacement.py::test_open_release_gate_creates_no_rack_move_and_does_not_claim_recovery` | 不创建移库、不抢恢复 |
| 在途放置未确认 | 插件单元 | `test_placement_and_replacement.py::test_active_placement_without_confirmation_defers_rack_release` | 不释放货架 |
| 放行门关闭 | 插件单元 | `test_placement_and_replacement.py::test_closed_release_gate_creates_two_independent_stable_rack_moves` | 两个独立稳定移库 |
| 新货架成功 | 插件单元 | `test_transport_and_recovery.py::test_new_rack_matching_success_retries_target_without_waiting_for_old_rack` | 不等待旧货架即可重试 |
| 新货架失败/未知 | 插件单元 | `test_transport_and_recovery.py::test_new_rack_failure_or_unknown_blocks_target_and_reconciles` | 阻塞 target 并进入对账 |
| 人工恢复 ABORT | 插件单元 | `test_transport_and_recovery.py::test_recovery_abort_closes_without_deleting_or_inventing_position` | 关闭但不伪造位置 |
| 人工恢复 CONTINUE | 插件单元 | `test_transport_and_recovery.py::test_recovery_continue_uses_typed_continuation_and_is_deterministic` | typed continuation 且确定性 |
| 人工恢复 DEFER | 插件单元 | `test_transport_and_recovery.py::test_recovery_explicit_defer_keeps_the_same_recovery_evidence_rebuildable` | 同一恢复证据可重建 |
| WMS/Transport duplicate/conflict | 核心 FAST | Step 2 列出的 WMS Confirmation/Transport owner | 同身份幂等、变载荷冲突 |
| DeviceCommand ACK/结果/未知 | 核心 FAST | Step 2 列出的 DeviceCommand owner | ACK 只持证、CALLBACK 关闭、未知不误放行 |

同一场景只以表中 owner 为主要证据；其他测试只提供相邻层合同证据，不复制场景，也不能代替该 owner。

### Step 1：运行插件业务领域回归

Run:

```bash
uv run --project workline_plugins/rough_sorter pytest \
  workline_plugins/rough_sorter/tests \
  --ignore=workline_plugins/rough_sorter/tests/e2e \
  -q

uv run --project workline_plugins/rough_sorter pytest \
  workline_plugins/rough_sorter/tests/e2e \
  -q
```

Expected: 两组全部通过。Task 1、Task 2 只跑各自聚焦场景；本步是插件非 E2E 与 E2E 各唯一一次完整运行。这里是粗分机业务能力所有者，不进入核心默认 pytest/QUALITY/HEAVY selector。

### Step 2：运行基础合同的聚焦 FAST 所有者

Run:

```bash
uv run pytest \
  tests/contracts/wms_adapter/test_inbound_adapter.py::test_adapter_maps_wait_to_typed_follow_up_plan \
  tests/contracts/wms_adapter/test_inbound_adapter.py::test_business_wait_completes_original_and_atomically_creates_due_follow_up \
  tests/runtime/execution/test_wms_confirmation_service.py::test_same_operation_identity_and_request_is_idempotent_but_payload_cannot_change \
  tests/runtime/execution/test_wms_confirmation_service.py::test_delivery_unknown_reuses_identity_and_only_safe_retry_returns_pending \
  tests/runtime/execution/test_wms_confirmation_service.py::test_wait_is_a_completed_response_and_conflicting_response_reconciles \
  tests/runtime/transport/test_transport_service.py::test_same_client_request_is_idempotent_but_changed_payload_conflicts \
  tests/runtime/transport/test_transport_service.py::test_delivery_unknown_enters_reconciling_and_keeps_resource \
  tests/runtime/transport/test_transport_outcome.py::test_same_event_is_idempotent_and_changed_payload_conflicts \
  tests/runtime/device_command/test_dispatch_service.py::test_dispatch_result_is_fenced_into_reliable_state \
  tests/runtime/device_command/test_evidence_service.py::test_result_evidence_is_only_authority_that_closes_acknowledged_command \
  tests/runtime/device_command/test_reconciliation_service.py::test_reconcile_one_distinguishes_not_sent_from_delivery_unknown \
  tests/runtime/transport/test_transport_acceptance_edges.py::test_known_partial_failure_forms_failed_outcome_and_releases_resources \
  tests/deployment/test_execution_worker_startup.py::test_device_command_beat_contract_matches_production_configuration \
  tests/deployment/test_execution_worker_startup.py::test_device_command_worker_readiness_requires_child_probe_after_parent_ready \
  -q
```

Expected: 全部通过。参数化 case 数量以 pytest 实际收集为准，不硬编码 passed 数。这些测试证明基础合同，不得被插件 E2E 取代。

### Step 3：核对 prefork/事务 HEAVY 证据是否仍绑定当前基础可执行树

历史 HEAVY 只能在“完整可执行树未漂移”且“原始 manifest/CI artifact 可追溯”两项同时成立时复用。先核对 `git status --short`：除本计划冻结变更面和执行前已记录的用户现场外，任何 untracked、staged 或 unstaged 可执行文件都使复用条件不成立。

再比较发布基线 `c579b18a` 到当前工作树的基础可执行面：

```bash
git diff --name-only c579b18a -- \
  src tests scripts deployment migrations \
  workline_plugins/rough_sorter/src \
  workline_plugins/rough_sorter/fixtures \
  workline_plugins/rough_sorter/tests \
  workline_plugins/rough_sorter/pyproject.toml \
  docs/architecture/heavy-test-impact.toml \
  main.py pyproject.toml uv.lock Dockerfile 'docker-compose*.yml'
```

Expected: 输出只能是本计划新增的 `workline_plugins/rough_sorter/tests/e2e/test_business_loop.py`；任何其他路径都视为基础可执行树漂移并进入 fresh HEAVY 分支。不得通过缩窄 allowlist 隐藏测试、脚本、配置、fixture、迁移或共享依赖变化。

树未漂移后，还必须定位 `c579b18a` 对应的原始 HEAVY manifest/CI artifact，并记录 artifact 地址或归档路径、commit、环境、所选 node 列表和结果。`CHANGELOG.md` 中的汇总数字、口头描述或“历史曾绿”不满足可追溯要求。两项条件均成立时，才可复用以下证据，并在验收报告中明确写“基础可执行树指纹未变，复用指定 artifact”，不能写成“本轮 fresh 运行”：

- prefork 两子进程各自 runtime/engine；
- `max-tasks-per-child` 重建；
- `TERM` 温和退出释放连接；
- `QUIT` 后幂等恢复；
- PostgreSQL rack fence/placement snapshot；
- 已知部分失败形成失败 outcome 后才释放资源。

只要可执行树有漂移、原始 artifact 缺失或 artifact 与 `c579b18a` 不匹配，就不得复用。重新冻结变化后，按 `docs/architecture/heavy-test-impact.toml` 生成 selector manifest，并在隔离 PostgreSQL/Redis 环境执行 manifest 中的受影响 HEAVY。针对本计划验收边界，manifest 必须能够解释以下 owner 是否被选择；若相关却未选择，先修正 mapping，不得手工臆造结果：

- `tests/e2e/device_command/test_device_command_production_wiring.py::test_real_broker_ecs_callback_worker_and_postgresql_close_command`；
- `tests/integration/test_celery_async_runtime_postgresql.py` 中 prefork、`max-tasks-per-child`、`TERM` 和 `QUIT` owners；
- `tests/integration/execution/test_decision_processing_postgresql.py` 中 rack release snapshot 和 rack fence owners。

缺少可复用 artifact 时，以 Phase 8 首个提交的父提交为 selector base，生成并执行完整 Phase 8 HEAVY manifest；这不是手工拼接测试清单：

```bash
uv run scripts/select_heavy_tests.py --base 'ab02f42f^'
./scripts/run_selected_heavy_local.sh --base 'ab02f42f^'
```

若 selector 输出未覆盖上列相关 owner，或基础设施无法 fresh 运行，验收门保持 `BLOCKED`；不得改成直接运行手工 pytest 列表绕过唯一 mapping 真源。

不得用插件 `solo` Worker E2E、`skipped`、历史汇总描述或不含精确 node 的 artifact 冒充 prefork/事务/真实 broker 证据。

### Step 4：记录证据快照

记录以下信息，供 Task 4 写入验收文档：

- `git rev-parse HEAD`；
- `git status --short`；
- 当前镜像 source revision/image ID（由 E2E provenance 断言输出）；
- 上述命令、通过数量、是否 fresh 或指纹复用；
- 本机 Docker/PostgreSQL/Redis/Celery 版本或镜像标识；
- 所有未运行的外部边界及原因。

---

## Task 4（已完成）：更新当前验收真源，保留外部阻塞

**Files:**

- Modify: `docs/integration/rough-sorter-joint-acceptance.md`
- Modify: `docs/integration/rough-sorter-supplier-conformance.md`
- Modify: `docs/superpowers/plans/2026-08-03-rough-sorter-plugin-convergence.md`
- Include: `docs/superpowers/plans/2026-08-18-rough-sorter-local-mock-acceptance-completion.md`
- Modify: `docs/superpowers/README.md`

### Step 1：更新联合验收报告

在 `docs/integration/rough-sorter-joint-acceptance.md` 中把结果拆成独立行：

```text
仓库工程验收                PASS
本机 Mock 模拟联调          PASS
供应商 ECS/网关一致性       NOT RUN
现场物理联调                NOT RUN / BLOCKED
业务验收                    NOT RUN / BLOCKED
```

补充两条新 E2E 的命令、结果和可观察证据，并明确：

- API、数据库、Redis、Worker/Beat 与当前源码镜像是真实运行组件；
- 只有 WMS/ECS HTTP 对端是合同级 Mock；
- 插件 E2E 使用 `solo` 只验证业务闭环，prefork 由 Task 3 的基础 HEAVY 所有者证明；
- ACK 不是物理完成，CALLBACK 才允许继续；
- Mock PASS 不等于供应商、现场或业务验收。

### Step 2：更新供应商一致性边界

在 `docs/integration/rough-sorter-supplier-conformance.md` 中增加“仓库已具备的模拟验收证据”，但总状态继续保持 `NOT RUN`。原有 PLC、真机、安全互锁、时序和私有协议缺口不得删除或弱化。

### Step 3：更新 Phase 8 计划状态

在 `docs/superpowers/plans/2026-08-03-rough-sorter-plugin-convergence.md` 的 Task 9 Step 4 中拆分：

- `[x]` 本机 Mock：正常闭环、WMS WAIT 安全停住、ECS ACK 后等待 CALLBACK 且不重放；
- `[x]` 仓库所有者：其余 duplicate/conflict、unknown、rack release 和 recovery 场景有对应测试证据；
- `[ ]` 供应商/现场：真实 WMS、ECS/网关、PLC、物理动作和业务签字。

不得勾选整个 Task 9，也不得把 Phase 8 写成“全部完成”。

### Step 4：更新计划索引摘要

在 `docs/superpowers/README.md` 中将 Phase 8 摘要更新为：本机工程验收和 Mock 模拟联调已完成；外部供应商/现场/业务验收仍阻塞。

### Step 5：做纯文档相称检查

Run:

```bash
git diff --check -- \
  docs/integration/rough-sorter-joint-acceptance.md \
  docs/integration/rough-sorter-supplier-conformance.md \
  docs/superpowers/plans/2026-08-03-rough-sorter-plugin-convergence.md \
  docs/superpowers/plans/2026-08-18-rough-sorter-local-mock-acceptance-completion.md \
  docs/superpowers/README.md

rg -n "Phase 8|Mock|供应商|现场|业务验收|NOT RUN|BLOCKED" \
  docs/integration/rough-sorter-joint-acceptance.md \
  docs/integration/rough-sorter-supplier-conformance.md \
  docs/superpowers/plans/2026-08-03-rough-sorter-plugin-convergence.md \
  docs/superpowers/README.md
```

Expected: `git diff --check` 无输出；四个当前真源的状态一致，没有把外部验收写成 `PASS`。纯文档部分不新增测试代码或文档正文断言。

### Step 6：提交边界

仅在用户另行授权 Commit 后执行：

```bash
git add \
  docs/integration/rough-sorter-joint-acceptance.md \
  docs/integration/rough-sorter-supplier-conformance.md \
  docs/superpowers/plans/2026-08-03-rough-sorter-plugin-convergence.md \
  docs/superpowers/plans/2026-08-18-rough-sorter-local-mock-acceptance-completion.md \
  docs/superpowers/README.md
git commit -m "docs(rough-sorter): 记录本机 Mock 分层验收"
```

本任务不产生新的过期过程文档，因此无需归档；若实施时发现被当前报告取代的过程文档，先列出引用和归档目标，单独获得授权后按 SHA-256 清单移至 `../archive_docs/wes_backend/`。`docs/hardware/` 永不纳入清理。

---

## Task 5（已完成）：最终门禁、Review 与交付结论

**Files:**

- Verify: all files in the frozen change surface

### Step 1：冻结最终 diff

Run:

```bash
git status --short
git diff --stat
git diff -- \
  workline_plugins/rough_sorter/tests/e2e/test_business_loop.py \
  docs/integration/rough-sorter-joint-acceptance.md \
  docs/integration/rough-sorter-supplier-conformance.md \
  docs/superpowers/plans/2026-08-03-rough-sorter-plugin-convergence.md \
  docs/superpowers/plans/2026-08-18-rough-sorter-local-mock-acceptance-completion.md \
  docs/superpowers/README.md
```

Expected: 只有冻结变更面；若出现 `src/`、migration、配置、`docs/hardware/` 或无关文件，停止并查明来源。

### Step 2：运行最终有效验证

先核对 Task 3 记录的三组结果仍绑定当前可执行树：插件非 E2E、插件 E2E、核心聚焦 FAST。Task 4 仅修改人类文档，不使这些证据失效；不得为形式再次运行相同测试。

最终代码/测试快照冻结后只运行尚未执行的完整 QUALITY 和文档检查：

```bash
./scripts/git-quality-gate.sh --profile quality
git diff --check
```

Expected: 全部通过。QUALITY 不包含插件测试，所以 Task 3 的两组插件结果必须保留为独立证据；这里强调证据复用，不重复执行同一目录。若 Task 3 之后生产代码、测试、脚本、配置或环境发生变化，只刷新受影响的证据。

### Step 3：核对 HEAVY selector 边界

在获得 Commit 授权并完成显式路径 staging 后运行：

```bash
uv run scripts/select_heavy_tests.py --scope staged
```

Expected: 粗分机插件测试和纯文档不选择核心 HEAVY，这是测试所有权设计，不是漏测。Task 3 的基础 HEAVY 指纹核对仍必须有证据。

### Step 4：做一次主 Review

固定 base、head、staged/unstaged/untracked 范围后，使用项目约定的一个主 Review 流程检查：

- Stub 是否只实现当前测试需要的两个分支；
- `WAIT`、ACK、CALLBACK 的可观察语义是否与公开合同一致；
- 是否存在时间窗口伪绿灯或 callback 线程错误未上浮；
- 是否重复了核心基础测试或越过插件测试所有权；
- 文档是否把本机 Mock 夸大为供应商、现场或业务验收；
- 是否误改生产代码、发布元数据或 `docs/hardware/`。

生产测试代码修复后，在同一轮完成旧意见闭环和 fresh full Review；纯文档措辞修复只做定向复核。

### Step 5：形成交付结论

最终只允许使用以下结论：

```text
Phase 8 仓库工程验收：PASS
Phase 8 本机 Mock 模拟联调：PASS
Phase 8 供应商一致性：NOT RUN
Phase 8 现场联调：NOT RUN / BLOCKED（开发环境生产路径不可达）
Phase 8 业务验收：NOT RUN / BLOCKED
```

这意味着“外部验收仍阻塞”被拆成可管理的真实边界，而不是被 Mock 消除。Commit、Push、PR、Merge、Deploy 仍是独立授权，本计划完成不自动授权任何一步。

---

## GSTACK REVIEW REPORT

| Review | Date | Round | Result | Notes |
| --- | --- | --- | --- | --- |
| Eng Review | 2026-08-19 | 1 | CLEAR | 9 项已接受意见已写入；镜像、Beat、测试所有权、HEAVY 复用和证据失效门已闭合 |
| Completion Sync | 2026-08-19 | 1 | COMPLETE | Task 0—5 已完成；本机工程与 Mock 联调证据已写入当前验收真源，外部阻塞保持不变 |

**VERDICT:** COMPLETED — `LOCAL ENGINEERING PASS / LOCAL MOCK INTEGRATION PASS`；本机 Mock 只关闭仓库工程验收和
模拟联调，不关闭供应商、现场或业务验收。

NO UNRESOLVED DECISIONS WITHIN LOCAL MOCK ACCEPTANCE SCOPE
