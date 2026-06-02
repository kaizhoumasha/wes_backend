# 沙箱清理设备运行态修复设计

## 背景

在 `http://localhost:5173/runtime/sandbox/51` 执行“清理沙箱数据”后，测试粗分机入料机械臂仍显示 `ERROR`。这会让用户误以为沙箱已清理，但工作线仍处于不可继续调试的状态。

当前后端清理逻辑已经删除 SIMULATION 工作线的沙箱运行时图，包括 Session、Inbox、Outbox、DeviceCommand、Runtime Hold、Timeline、Diagnostic、Dispatch Attempt、Safety Incident 等数据，并重置工作线运行状态。

问题在于设备运行态清理范围过窄：只有当设备的 `current_command_id` 指向本次被删除的沙箱命令时，才会把设备恢复为 `IDLE` 并清空 `error_code`。如果设备本身已经是 `ERROR`，但没有当前命令，或者当前命令不在本次沙箱选择集内，`ERROR` 会残留。

## 根因证据

- `SandboxCleanupRepository._clear_cyclic_refs` 只按 `current_command_id in selection.commands` 重置设备。
- 运行监控工作线汇总直接统计绑定设备中的 `Device.device_status == "ERROR"`。
- `DeviceCommandGateway` 在设备为 `ERROR` 时拒绝后续命令派发，错误码为 `DEVICE_ERROR_STATE`。
- START 准入流程依赖设备回到可启动条件，残留 `ERROR` 会继续阻断调试闭环。
- `DeviceRuntimeStatePolicy` 要求 `IDLE/RUNNING/ERROR/OFFLINE/MAINTENANCE` 与 `current_command_id/error_code/maintenance_mode` 保持合法组合；删除沙箱命令前必须先断开设备指针，避免留下悬空 `current_command_id`。
- GitNexus 影响分析复核：当前索引曾显示 stale，直接按类方法名查询未命中；使用候选 UID 复核 `_clear_cyclic_refs` 上游影响为 LOW，仅直接影响 `execute_cleanup`。实现前建议先刷新索引或在 PR 中附上复核命令输出。

## 目标

执行 SIMULATION 工作线的“清理沙箱数据”后，该工作线应回到可重新开始沙箱调试的状态：

- 旧沙箱待处理、历史、Hold、诊断等运行时记录不可见。
- 由沙箱调试产生或残留的设备 `ERROR/RUNNING/current_command_id` 运行态投影不再阻断下一轮调试。
- 不修改工作线、设备、能力、通信配置等主数据或配置字段；只允许修正运行态投影。
- 不影响 AUTO 工作线或其它工作线绑定设备。

## 非目标

- 不新增前端按钮或新的“重置设备状态”接口。
- 不删除货架、库位、库存等无法唯一归属为沙箱链路的主投影。
- 不把所有设备状态都无条件改成 `IDLE`。
- 不改变 AUTO 工作线的清理或运行态治理语义。

## 设计决策

采用两阶段、保守设备运行态修正策略：

1. 先断开待删除沙箱命令引用：
   - 对当前 SIMULATION 工作线绑定的设备，如果 `current_command_id in selection.commands`，清空 `current_command_id`。
   - 这一步不等于设备复位；它只保证删除 `DeviceCommand` 前不会留下悬空指针。
2. 再处理可由 sandbox cleanup 接管的运行态投影：
   - 对当前 SIMULATION 工作线绑定的设备，如果不处于 `MAINTENANCE/OFFLINE`，且满足以下任一条件，则恢复为 `IDLE`：
     - `device_status == ERROR`
     - `device_status == RUNNING`
     - `current_command_id` 非空
   - 恢复为 `IDLE` 时清空：
     - `current_command_id`
     - `error_code`
     - `maintenance_mode` 保持或显式确保为 `False`
3. 对 `MAINTENANCE/OFFLINE` 采用状态保留策略：
   - 保留 `device_status` 和 `error_code`。
   - 如果 `current_command_id` 指向本次待删除沙箱命令，仍要清空 `current_command_id`，以满足设备运行态合法组合。
   - 不把 `MAINTENANCE/OFFLINE` 自动改成 `IDLE`。

保留 `MAINTENANCE` 的原因是它代表人工维护，不应被清理按钮解除。保留 `OFFLINE` 的原因是它可能代表真实连通性问题，清理沙箱数据不应把不可达设备伪装为可用。

`ERROR` 的处理边界：本次接受“SIMULATION 工作线 + 二次确认”作为沙箱运行态归属代理，清理该工作线绑定设备上的 `ERROR`。这会清掉该沙箱线上的人工故障标记风险，但符合“清理沙箱数据后能重新开始调试”的用户预期。若后续需要区分真实硬件故障和沙箱派生错误，应新增错误来源或 error_code allowlist 设计，不塞进本次小修。

状态决策表：

```text
当前设备状态         current_command_id 指向待删命令   cleanup 后状态
------------------  -------------------------------  ----------------------------
ERROR               任意                              IDLE, current_command_id=None, error_code=None
RUNNING             任意                              IDLE, current_command_id=None, error_code=None
IDLE                是                                IDLE, current_command_id=None, error_code=None
IDLE                否                                保持不变
MAINTENANCE         是                                MAINTENANCE, current_command_id=None, error_code 保留
MAINTENANCE         否                                保持不变
OFFLINE             是                                OFFLINE, current_command_id=None, error_code 保留
OFFLINE             否                                保持不变
AUTO 工作线设备      任意                              保持不变
```

## 架构与数据流

入口保持不变：

1. 前端 `SandboxWorkbenchPage` 调用 sandbox cleanup dry-run。
2. 用户二次确认后，前端调用执行清理。
3. API 层只调用 `SandboxCleanupService`，不直接访问数据库。
4. Service 校验工作线存在、为 SIMULATION、确认码匹配。
5. Repository 收集沙箱运行时选择集。
6. Repository 断开循环引用、重置设备运行投影、按依赖顺序物理删除沙箱运行时数据。
7. API 提交事务并发布延迟 SSE。
8. 前端清空本地沙箱列表并刷新。

设备运行态重置应继续留在 Repository 层，因为它是事务内数据投影修正，和现有 `_clear_cyclic_refs` 同属删除前的引用与运行态清理步骤。

```text
cleanup_workline
  ├─ lock SIMULATION WorkLine
  ├─ collect sandbox runtime selection
  ├─ _clear_cyclic_refs
  │   ├─ clear RuntimeHold/SystemOutbox/Session cyclic refs
  │   ├─ clear Device.current_command_id for commands being deleted
  │   └─ reset eligible bound devices to IDLE
  ├─ delete sandbox runtime graph
  └─ reset WorkLine runtime_status to STOPPED
```

## 现有能力复用

- 继续复用 `SandboxCleanupService.cleanup_workline` 的 SIMULATION 校验、确认码校验和事务边界。
- 继续复用 `SandboxCleanupRepository._clear_cyclic_refs` 作为删除前引用断开与运行态投影修正位置。
- 继续复用 `DeviceRuntimeStatePolicy` 的合法状态组合语义：`IDLE` 不能带 `current_command_id/error_code`，`OFFLINE/MAINTENANCE` 不能带 `current_command_id`。
- 验收继续通过 `RuntimeQueryService` 的工作线汇总和 `DeviceCommandGateway` 的命令治理结果证明用户可见阻断消失，不新增接口。

## 错误处理

- 非 SIMULATION 工作线继续返回现有业务错误。
- confirmation 不等于工作线编码时继续拒绝执行。
- 找不到工作线继续返回现有错误。
- 设备状态重置不单独引入新错误码；它在同一个事务内执行，失败时整体回滚。

## 测试策略

新增后端回归测试，覆盖当前 BUG：

- 创建 SIMULATION 工作线。
- 创建绑定设备，状态为 `ERROR`，`error_code` 有值，`current_command_id=None`。
- 创建最小沙箱运行时图，确保 cleanup 可执行。
- 执行 `sandbox_cleanup_service.cleanup_workline`。
- 断言该设备恢复为 `IDLE`，`error_code=None`，`current_command_id=None`。
- 通过工作线运行汇总断言 `error_device_count == 0`，证明用户可见的 `ERROR` 计数消失。

扩展现有执行清理测试，保持以下边界：

- 绑定 AUTO 工作线的对照设备不被修改。
- `MAINTENANCE` 设备不被 cleanup 改成 `IDLE`，但如果指向待删除 sandbox command，`current_command_id` 会被清空。
- `OFFLINE` 设备不被 cleanup 改成 `IDLE`，但如果指向待删除 sandbox command，`current_command_id` 会被清空。
- 原有通过 sandbox command 占用的 `RUNNING` 设备仍会被恢复为 `IDLE`。
- `RUNNING` 但 `current_command_id=None` 的沙箱绑定设备会被恢复为 `IDLE`，避免残留忙碌态阻断调试。
- 清理后再次走命令治理路径时，不再因为本地残留 `DEVICE_ERROR_STATE` 拒绝沙箱调试命令。

覆盖图：

```text
CODE PATHS                                           USER/OPS FLOWS
SandboxCleanupService.cleanup_workline()
  ├─ confirmation mismatch                           已有测试：不删除数据
  ├─ non-SIMULATION reject                           已有测试：拒绝清理
  ├─ collect_selection                               已有测试：沙箱运行图计数
  ├─ _clear_cyclic_refs
  │   ├─ current_command_id in commands -> IDLE       已有测试：命令占用设备释放
  │   ├─ ERROR + current_command_id=None -> IDLE      新增回归测试
  │   ├─ RUNNING + current_command_id=None -> IDLE    新增边界测试
  │   ├─ MAINTENANCE + selected command -> 清指针     新增不变量测试
  │   └─ OFFLINE + selected command -> 清指针         新增不变量测试
  └─ _reset_workline_runtime_state -> STOPPED         已有测试：不改 READY 语义

Runtime visibility
  ├─ error_device_count 清零                          新增汇总断言
  └─ DEVICE_ERROR_STATE 不再阻断下一轮命令             新增治理断言或服务级断言
```

聚焦验证命令：

```bash
rtk uv run pytest tests/workline_runtime/test_sandbox_cleanup_service.py -q
rtk uv run ruff check src/app/workline/repositories/sandbox_cleanup_repository.py tests/workline_runtime/test_sandbox_cleanup_service.py
rtk uv run ruff format --check src/app/workline/repositories/sandbox_cleanup_repository.py tests/workline_runtime/test_sandbox_cleanup_service.py
```

## 验收标准

- 执行“清理沙箱数据”后，测试粗分机入料机械臂残留的 `ERROR` 被清为 `IDLE`。
- 沙箱工作线汇总中的 `error_device_count` 不再因为该残留 ERROR 保持非零。
- 下一轮沙箱 START 或命令派发不再因为旧 `DEVICE_ERROR_STATE` 被阻断。
- AUTO 工作线设备状态不变。
- `MAINTENANCE/OFFLINE` 状态不被 cleanup 自动覆盖为 `IDLE`，但不会保留指向已删除沙箱命令的 `current_command_id`。
- cleanup 后设备运行态组合符合 `DeviceRuntimeStatePolicy` 约束。
- 新增回归测试失败于旧实现，修复后通过。

## 风险

- 如果某些 `ERROR` 不是沙箱残留，而是用户希望保留的人工故障标记，清理后会被清掉。该风险由 SIMULATION 工作线限定和二次确认降低。
- 如果 `OFFLINE` 也来自 mock 残留，本设计不会自动修复；需要通过 START 准入或设备连通性治理单独处理。
- 如果未来引入 SafetyZone 或物理共享设备模型，本设计只覆盖当前 `Device.work_line_id` 绑定语义，不承担跨工作线物理安全域复位。
- 当前计划文档曾写过清理后工作线恢复 `READY`，而现实现为 `STOPPED`。本设计不扩大该行为变更，避免把工作线状态语义和设备状态修复混在一次改动里。

## 实施边界

本次实现只修改：

- `src/app/workline/repositories/sandbox_cleanup_repository.py`
- `tests/workline_runtime/test_sandbox_cleanup_service.py`

不修改前端，不新增 API，不改迁移，不调整工作线 `STOPPED/READY` 语义。
