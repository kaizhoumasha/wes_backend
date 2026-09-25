# WORKLINE ECS_TEST Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 WORKLINE 在 `ECS_TEST` 下由连续真实 `SCAN_COMPLETED` 事件可靠触发预配置的单条固定 ECS 命令，完成 Event → Command → Result 关联，且不产生 WMS 业务或插件 Decision。

**Architecture:** 复用 WORKLINE 的启停和设备归属、InboundEvidence 的持久接收、DeviceCommand 的原身份派发与 Result 处理。测试规则作为 WORKLINE 运行配置冻结，设备事件 worker 在同一事务内创建测试命令并消费 Event；共享业务入口按模式隔离，ECS 负责物理接纳与互斥。

**Tech Stack:** Python 3.13、FastAPI、SQLModel/SQLAlchemy、PostgreSQL、Alembic、Celery、pytest。

**Spec:** `docs/superpowers/specs/2026-09-24-workline-ecs-test-mode-design.md`

## Global Constraints

- 每个新且可区分的真实 `SCAN_COMPLETED` 最多产生一条固定动作；重报只关联原 Event/Command。WES 不主动产生事件、循环发送、按扫码内容选路或编排后继。
- 来源与目标均为同一 WORKLINE 所拥有的 Device；`task_type`/`params` 是配置时冻结的常量，Event `data` 只用于原事件证据和身份。
- `ECS_TEST` 不创建新的 WMS operation、业务对象或插件 Decision；旧业务义务先闭合，旧身份重报仍保持原幂等答复。
- 命令可能已被 ECS 接纳时保留 `command_code` 与冻结载荷；ACK、本地超时和 `RECONCILING` 都不等于物理终态。当前 `TIMED_OUT` 只用于调用 ECS 提交接口前到期的命令，不与已发送后结果未知混用。不同真实事件仍可独立提交，由 ECS 决定物理接纳。
- 两份 `docs/hardware/` 设备说明书只提供动作和事件场景；生产下发使用已批准白皮书的四个固定接口。现场启用前必须取得设备附录对 `SCAN_COMPLETED` 身份稳定性、目标 `task_type`/`params`、Result 字段的确认。
- 真实 ECS 启动测试线前，用同源背靠背相同扫码和原事件重报样本核对身份唯一性与重报稳定性；缺少设备证据时只报告本地链路验证，不报告现场连续运行验收。
- 保护现有 dirty 文件。当前交付目标没有 Commit、Push 或 Deploy；执行阶段只修改和验证工作区。生产符号修改前按 AGENTS.md 做 GitNexus upstream impact，最终关闭直接/间接测试与 HEAVY mapping 所有权。

## Review Focus

1. 两个不同真实扫码的 `data` 相同且毫秒时间相同时，当前 wire 是否能区分？Task 1 记录设备附录与报文样本的可用性，真实 ECS START 前必须取得证据；Task 4 验证相同 wire 重报只发一次，缺口未闭合时不得标现场验收 PASS。
2. Event 入库后 worker 崩溃或通知丢失，是否仍能创建原身份命令？Task 2 用持久扫描重入测试覆盖。
3. 两个来源同时指向一个目标时，是否各有独立命令且无 WES 设备占槽？Task 2 覆盖。
4. 已发送后超时的原 `command_code` 是否留在 `RECONCILING` 并由迟到 Result 收敛？未发送的 `TIMED_OUT` 是否不伪装成已执行？Task 3 覆盖，不能创建替代命令。
5. 业务插件草稿仍在 WORKLINE、Transport debug-run 又匹配扫码设备时，是否会触发 drain、插件 Decision 或两条调试命令？Task 1 与 Task 3 覆盖。

## 变更面与文件职责

| 所有者 | 文件 | 职责 |
| --- | --- | --- |
| WORKLINE 合同与配置 | `src/app/workline/models/workline.py`、`domain/run_mode.py`、新增 `domain/ecs_test.py`、`services/workline_service.py`、`services/workline_configuration_service.py` | 新模式和规则校验；停用与负载门禁；业务插件草稿不等于活动插件。 |
| START/绑定 | `src/app/workline/services/workline_start_service.py`、`repositories/workline_repository.py` | 测试线不要求插件；冻结来源/目标设备执行合同；供 Event 准入和命令创建读取。 |
| Event/Command | `src/app/device/services/device_evidence_service.py`、`device_command_service.py`、`models/command.py`、`src/app/device/composition.py` | 原 Event 身份到唯一测试 Command；固定载荷；已发送后未知结果沿 `RECONCILING` 闭合，未提交 ECS 的 `TIMED_OUT` 保持未接纳语义。 |
| 业务隔离 | `src/app/wms_integration/outbound_picking/services/picking_task_issued.py`、`picking_task_plan_delta.py`、`picking_task_plan_activation.py`、`return_batch_owner.py`、`rack_departure_owner.py`、`src/app/transport_debug/debug_run_service.py` | 核对当前仅凭 `is_active` 放行的 WMS 入口；只在新业务准入点拒绝测试线，旧身份义务保留原处理；禁止 debug-run 抢占测试来源。测试 Event 标记 `IGNORED` 后不会进入 FactProcessor。 |
| 迁移与合同 | `migrations/versions/20260924_1800_e5c57e570001_add_ecs_test_run_mode.py`、`docs/integration/third_party_integration_whitepaper.md`、`docs/architecture/heavy-test-impact.toml` | 扩展 WORKLINE 模式约束；明确 `is_debug=true` 与活动测试线冲突；精确 HEAVY 选择。执行时确认 Alembic head 仍为当前 `b6b5d9240f51`，如有并行迁移先更新 down revision。 |
| 验收 | `docs/runbooks/workline-ecs-test-acceptance.md` | 用已有 Evidence/Command/Result 记录核对连续运行，区分 WES 技术闭环、ECS Result 与现场物理观察。 |

## Task 1：WORKLINE 模式、规则与启动

**Files:** 修改 `src/app/workline/models/workline.py`、`domain/run_mode.py`、`services/workline_service.py`、`services/workline_configuration_service.py`、`services/workline_start_service.py`、`repositories/workline_repository.py`；新增 `src/app/workline/domain/ecs_test.py` 与上述 Alembic revision；测试 `tests/workline/test_workline_start_service.py`、`test_workline_configuration_service.py`、`tests/integration/workline_capabilities/test_workline_start_postgresql.py`。

**Interfaces:** `runtime_config_json.ecs_test_rules` 是来源设备唯一映射，每项 `source_device_code: str, target_device_code: str, task_type: str, params: dict`。`src/app/workline/domain/ecs_test.py` 导出 `EcsTestRule` 与 `parse_ecs_test_rules(config: Mapping[str, object]) -> tuple[EcsTestRule, ...]`，供 START 和 Event worker 共同使用。`WorkLineRepository.get_active_binding_for_device()` 和 `get_binding_for_command_creation()` 在测试模式下返回由本线 Device 冻结的 `WorkLineDeviceBinding`，业务模式保持插件角色语义。`WorkLineStartService.start()` 对 `ECS_TEST` 走独立配置/连通性路径，并将 `plugin_version`、`flow_mode` 清空。

- [ ] **Step 0 — 现场前置证据:** 登记滚筒线和粗分机设备附录对事件身份的保证，以及同源背靠背相同扫码、原事件重报的报文样本是否已取得；缺少时不阻止本地实现，但真实 ECS START 和现场验收保持未通过。
- [ ] **Step 1 — RED:** 增加测试：缺规则、重复来源、跨线/停用设备、目标不支持 `task_type`、活动线修改规则分别拒绝；无插件的测试线可 START，同源/异源目标都冻结 Endpoint，`list_bindings()` 与 `get_binding_for_command_creation()` 能查到来源和目标；业务线仍要求插件；测试线 STOP 不触发插件 drain；worker startup 只校验活动业务线插件。

```python
async def test_ecs_test_start_freezes_cross_device_binding(service, line, source, target):
    line.run_mode = WorkLineRunMode.ECS_TEST
    line.runtime_config_json = {"ecs_test_rules": [{"source_device_code": source.device_code,
        "target_device_code": target.device_code, "task_type": "MOVE_FORWARD", "params": {}}]}
    started = await service.start(db, workline_id=line.id, version=line.version)
    assert started.plugin_version is None
    assert target.device_code in started.device_contracts
    bindings = await repository.list_bindings(db, line.id)
    assert {item.device_code for item in bindings} == {source.device_code, target.device_code}
    assert (await repository.get_binding_for_command_creation(
        db, workline_id=line.id, device_code=target.device_code)) is not None
```

- [ ] **Step 2 — verify RED:** `uv run pytest tests/workline/test_workline_start_service.py tests/workline/test_workline_configuration_service.py -q`，新增案例应因 `ECS_TEST` 未定义及未冻结测试绑定而失败。
- [ ] **Step 3 — DEV:** 用现有运行配置 JSON 严格校验规则对象和设备归属；START 批量读取本线 Device 和 ECS capabilities，冻结来源/目标 binding；`WorkLineRepository.list_bindings()` 对 `ECS_TEST` 直接枚举 `device_contracts` 的设备码构造 binding，内部 `device_role` 取设备码，不从 `config.device_bindings` 取插件角色映射；业务模式保持原逻辑。停用前让待处理 Event/Result 与 `RECONCILING` 测试命令阻止模式切换，尚未提交 ECS 即到期的 `TIMED_OUT` 按原终态处理。数据库迁移只扩展 `worklinerunmode` CHECK，不改历史行与其它枚举。

```python
if workline.run_mode == WorkLineRunMode.ECS_TEST:
    rules = parse_ecs_test_rules(workline.runtime_config_json)
    bindings = await self._build_ecs_test_bindings(db, workline, rules)
    workline.plugin_version = None
    workline.flow_mode = None
    contracts = {}
    for binding in bindings:
        contract = asdict(binding)
        for key in ("workline_id", "device_code", "device_role"):
            contract.pop(key)
        contracts[binding.device_code] = contract
    workline.device_contracts = contracts
```

- [ ] **Step 4 — GREEN:** 重跑 Task 1 两组 FAST；在独占干净临时 PostgreSQL 库运行 Alembic 当前 base→head 和 `tests/integration/workline_capabilities/test_workline_start_postgresql.py`，不得把环境 skip 算通过。
- [ ] **Step 5 — review:** 旧业务插件草稿仍可恢复为正常 START；活动测试线不被 `list_active_plugin_identities()` 误报为缺插件；所有来源/目标查到相同 WORKLINE 冻结合同。

## Task 2：真实 Event 到唯一固定 Command

**Files:** 修改 `src/app/device/services/device_evidence_service.py`、`device_command_service.py`、`models/command.py`、`composition.py`；按现有模型需要在 `repositories/command_repository.py` 添加精确测试身份查询；测试 `tests/runtime/device_command/test_evidence_service.py`、`test_device_command_service.py`、`tests/e2e/device_command/test_device_command_production_wiring.py`。

**Interfaces:** 新内部引用 `ECS_TEST` 的 `(workline_id, target_device_code, event.source_identity)` 约束唯一命令。`DeviceCommandService.create_ecs_test_command_in_session(db: AsyncSession, *, evidence: InboundEvidence, rule: EcsTestRule) -> DeviceCommandHandle` 先取原身份命令，缺席时读取 Task 1 的目标冻结 binding 并调用 `create_command_in_session()`。`DeviceEvidenceService` 的 WorkLine 读取端口增加 `get_for_update(db, workline_id)`；经 `ecs_test_command_service` 端口接入命令方法。Event worker 在同一事务创建命令并把 Event 标记 `IGNORED` 以避开 FactProcessor。首次创建时冻结 `deadline_at`，精确重入先取原 Command。命令派发与结果继续走现有 worker。

- [ ] **Step 1 — RED:** 增加 Event→Command 测试：单事件一条命令、精确重报零新增、不同事件同目标各一条、异源到目标、`params` 不受 Event `data` 改变、无匹配规则零命令、非 `SCAN_COMPLETED` 的合法测试 Event 留证且零命令、worker 提交后通知丢失由扫描继续。断言所有测试 Event 均不进入业务 FactProcessor。

```python
assert command.execution_ref_type == "ECS_TEST"
assert command.device_code == configured_target
assert command.params == configured_params
assert command.execution_ref_id == evidence.source_identity
assert evidence.apply_status == InboundEvidenceApplyStatus.IGNORED
```

- [ ] **Step 2 — verify RED:** `uv run pytest tests/runtime/device_command/test_evidence_service.py tests/runtime/device_command/test_device_command_service.py -q`；新增案例应先失败。
- [ ] **Step 3 — DEV:** 在既有 `DeviceEvidenceService._process_event()` 中用 `evidence.workline_id` 加锁读取 WORKLINE；对 `ECS_TEST` 优先于现有 `event.is_debug`/业务 `mark_applied` 分支处理，匹配 `SCAN_COMPLETED` 时创建固定命令，其它合法测试 Event 只标记 `IGNORED`，两者都不唤醒 FactProcessor。创建命令与 Event `IGNORED` 同事务，复用 `DeviceCommandService.create_command_in_session()` 和现有事务后唤醒。对本次身份已持久化的命令先返回冻结快照，避免重算 deadline；不复制 Adapter、worker、retry 或物理互斥。

```python
workline = await self._worklines.get_for_update(db, evidence.workline_id)
if workline is not None and workline.run_mode == WorkLineRunMode.ECS_TEST:
    rule = next((item for item in parse_ecs_test_rules(workline.runtime_config_json)
                 if item.source_device_code == event.device_code), None)
    wake_device_commands = False
    if event.event_type == "SCAN_COMPLETED" and rule is not None:
        outcome = await self._ecs_test_commands.create_ecs_test_command_in_session(
            db, evidence=evidence, rule=rule)
        wake_device_commands = outcome.created and outcome.status is CommandStatus.PENDING
    await self._processing.mark_ignored(db, evidence, processed_at=processed_at)
    return False, wake_device_commands, None
```

- [ ] **Step 4 — GREEN:** 重跑 Task 2 FAST 和 `uv run pytest tests/e2e/device_command/test_device_command_production_wiring.py -q`（只有真实依赖可用时执行，跳过不算通过）；核对 Celery 注册、队列路由、序列化和真实 worker 能领取 Event、Command、Result。
- [ ] **Step 5 — review:** Event 接收提交早于 ACK，命令发送晚于持久化；重报不产生第二个 `command_code`；不同事件不被 WES 的同设备未终态状态串行阻塞。

## Task 3：Result 闭合、WMS 与现有调试互斥

**Files:** 修改 `src/app/device/services/device_evidence_service.py`、`src/app/transport_debug/debug_run_service.py`、`src/app/wms_integration/outbound_picking/services/picking_task_issued.py`、`docs/integration/third_party_integration_whitepaper.md`；逐一核对 `picking_task_plan_delta.py`、`picking_task_plan_activation.py`、`return_batch_owner.py`、`rack_departure_owner.py` 的新业务准入分支，仅对实际放行测试线新业务的路径加模式检查。测试 `tests/runtime/device_command/test_evidence_service.py`、`tests/api/test_device_ecs_callbacks.py`、`tests/integration/transport_debug/test_transport_debug_auto_run.py`、`tests/integration/wms_adapter/outbound_picking/test_issued_postgresql.py` 和受影响 WMS owner 的既有测试。

**Interfaces:** `ECS_TEST` Result 只按原 `command_code` 闭合对应 Command，不唤醒业务 Decision。活动测试来源不进入 `EVENT_DEBUG`；显式 `is_debug=true` 按修订的公共合同拒绝。Transport debug-run 与测试线来源设备有交集时，双方 START/create_run 都拒绝。WMS 新业务准入按 run mode 返回原合同的明确冲突结果，旧身份重报照原幂等结果。

- [ ] **Step 1 — 合同:** 在白皮书写明活动 `ECS_TEST` 来源显式 `is_debug=true` 的拒绝响应及零命令语义，保留其它场景的 `EVENT_DEBUG`；不改四个固定路径和顶层 wire。新增对应公共回调合同测试。
- [ ] **Step 2 — RED:** 增加测试：ACK 不标成功；同一 Result 精确重报不重复闭合；不同命令的 Result 不交叉；已提交 ECS 的 `DISPATCHING`/`ACKNOWLEDGED` 结果未知时进入 `RECONCILING`，匹配迟到 Result 收敛原命令；提交前到期的 `PENDING`/已领取 `DISPATCHING` 进入 `TIMED_OUT` 且不伪造设备终态；活动测试线拒绝新 WMS issued/prepare；插件草稿不运行 Decision；Transport debug-run 与测试来源交集双向拒绝；显式 `is_debug=true` 零命令。

```python
assert result_evidence.command_code == original_command.command_code
assert original_command.status == CommandStatus.SUCCEEDED
assert new_commands_for_same_event == 0
assert new_wms_confirmations == 0
```

- [ ] **Step 3 — verify RED:** `uv run pytest tests/runtime/device_command/test_evidence_service.py -q`，新增案例应先失败；仅在所需外部依赖就绪时运行 integration，跳过不算通过。
- [ ] **Step 4 — DEV:** 核对共享派发超时分类与 Result 应用路径：已发送后未知结果保持 `RECONCILING` 并收敛原身份，未发送的 `TIMED_OUT` 不改成已执行；保留冲突 Result 证据。按 WorkLine run mode 拦截实际新业务准入。测试 Event 已标记 `IGNORED`，确认现有 FactProcessor 不消费它，无需新增分流。Transport debug-run 与测试 START 双向检查来源设备交集；公共 Event 入口按合同拒绝测试来源的显式 debug 标记。旧身份 WMS 重报保持其原 ACK/冲突语义。

```python
if line.run_mode == WorkLineRunMode.ECS_TEST:
    evidence.apply_status = InboundEvidenceApplyStatus.RECONCILING
    return PickingTaskIssuedPersistenceResult(
        "CONFLICT", _timestamp_ms(evidence.received_at), "STATE_CONFLICT"
    )
```

- [ ] **Step 5 — GREEN:** 重跑受影响 FAST；按实际依赖运行 `tests/integration/transport_debug/test_transport_debug_auto_run.py`、`tests/integration/wms_adapter/outbound_picking/test_issued_postgresql.py` 和真实 worker 接线；核对新 Command、WMS Confirmation、MaterialExecution、PickingTask、插件 Decision 的计数符合测试模式边界。
- [ ] **Step 6 — review:** 与原命令无关的独立事件仍可推进；`RECONCILING`/`TIMED_OUT` 不被当作失败许可；`is_debug` 合同变化有白皮书和针对性合同测试支撑。

## Task 4：合同、观察与最终验收证据

**Files:** 修改 `docs/architecture/heavy-test-impact.toml`；新增 `docs/runbooks/workline-ecs-test-acceptance.md`；需要 API 文档时只更新既有 WORKLINE OpenAPI 示例；聚焦测试 owner 为 Tasks 1–3 的文件和 selector 合同。

**Interfaces:** 不新增测试调度器或统计主表。验收查询沿 `InboundEvidence.source_identity → DeviceCommand.execution_ref_id → command_code → Result Evidence` 重建事件、命令、Result；记录 WES 接收、ECS ACK、匹配终态及现场物理观察为不同事实。

- [ ] **Step 1:** 对照 Task 3 已更新的白皮书与两份供应商原件，记录设备附录尚待确认的流水线/粗分机 `task_type`、`params`、Result 和事件唯一性；真实 ECS START 前取得同源背靠背相同扫码、原事件重报的报文样本。缺少供应商证据时继续本地验证，但不推定现场验收通过。
- [ ] **Step 2:** Runbook 写明启动配置示例、关闭旧业务义务、连续真实事件观察时段、重复事件注入方法、Event/Command/Result 原身份关联查询、WMS/插件零新增核对与结果记录表；记录多个来源同时指向同一目标时 ECS 的 ACK、拒绝、排队、超时和 Result。API 示例必须包含具体请求与响应，未冻结字段标注“讨论示例/非合同”。
- [ ] **Step 3:** 为新增/变更的生产模块、迁移和真实 worker 资产在 `heavy-test-impact.toml` 增加精确 mapping；运行 `uv run scripts/select_heavy_tests.py --scope unstaged`，核对 manifest 覆盖对应 owner，不用 `heavy_tests=[]` 掩盖未知风险。
- [ ] **Step 4:** 固定最终可执行树与环境指纹，闭合直接/间接 FAST、QA、HEAVY、迁移链所有权；做一次最终 full Review；运行 `git diff --check`、聚焦 FAST、必要 QUALITY、selector 选中的 HEAVY 与干净逻辑库 base→head。后续补丁只刷新被失效的证据。
- [ ] **Step 5:** 真实 ECS 连续测试使用现场批准窗口和获批设备附录，记录时段、对端版本、总事件数、重复数、命令/ACK/Result/失败/未知数及现场观察。只有所有新事件可区分、重报无重复物理动作、WMS/插件零新业务、原身份 Result 闭合和现场设备记录共同成立，才报告目标完整验收；Mock 或 HTTP 200 只作为各自层的证据。

## 执行选择

这些任务共享 WORKLINE 模式、设备证据与命令身份，同一路径的跨任务修改较多。推荐当前 Agent 按 `superpowers:executing-plans` 顺序实施，在任务边界做聚焦复核，最后一次完整 Review；若你明确希望分工，再按项目 Subagent 协议选择独立任务。计划获批后不重新设计已冻结事项。
