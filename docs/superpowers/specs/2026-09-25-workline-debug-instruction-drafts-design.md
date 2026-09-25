# WorkLine 联调指令草稿（Debug Instruction Drafts）设计

**状态：已批准**

**日期：2026-09-25**

## 1. 目标与边界

前端计划把现有的 `ops/transport-debug/`（Transport 自动联调）重写成一个通用"联调中心"：选一条 WorkLine，加载它的默认联调指令，编辑、优化，再执行，同时观测 `Event → Command → Result`（或 Transport 的 Task/Step）链路。目前有两种联调场景，指令形状完全不同：

- **ECS_TEST**（`docs/superpowers/specs/2026-09-24-workline-ecs-test-mode-design.md`）：一组"来源设备→目标设备→固定 `task_type`/`params`"的规则，已经存在 `WorkLine.runtime_config_json.ecs_test_rules` 里，随 START 冻结进 `device_contracts`，由 `DeviceEvidenceService._process_event()` 在每次真实事件到达时**实时读取**用于建命令。
- **Transport 自动联调**（`docs/superpowers/specs/2026-09-02-transport-debug-auto-run-design.md`、`src/app/transport_debug/`）：货架、分面料箱、进出料口、扫码设备等参数，目前完全是 `CreateTransportDebugRun` 请求里的一次性字段，WorkLine 上不持久化任何"默认指令"。

本设计只解决一件事：**给两种场景加一个可以随时编辑、跟运行状态无关的"草稿"层**，供前端联调中心读取、编辑、预填表单。草稿本身不驱动任何设备行为；驱动设备行为的仍然是已经上线、测试过的两条既有链路（ECS_TEST 的 STOP→改配置→START 冻结；Transport 的 `create_run` 一次性参数）。

**非目标**：
- 不改变 `_process_event`、`_start_ecs_test`、`create_run` 的任何现有行为、校验或返回契约。这两条链路已经在 PR #276（ECS_TEST）里合并并跑过真实 Postgres 集成测试和一次外部代码评审（含一次 P1 并发修复），本设计不重新打开它们。
- 不要求统一两种模式的指令数据结构；两个模式各自的草稿字段各自的形状，只共享"随时可编辑、跟 WorkLine 存在关系"这一点。
- 不做前端联调中心本身的设计（属于后续的独立子项目，见"执行选择"一节）。
- 不引入新的运行模式（`WorkLineRunMode` 不变）。

## 2. 合同依据

- ECS_TEST 现有实现：`src/app/workline/domain/ecs_test.py`（`EcsTestRule`/`parse_ecs_test_rules`）、`src/app/workline/services/workline_start_service.py`（`_start_ecs_test`）、`src/app/device/services/device_evidence_service.py`（`_process_event` 实时读取 `runtime_config_json.ecs_test_rules`）、`src/app/workline/services/workline_service.py`（`_reject_active_configuration_update` 阻止活动线写 `runtime_config_json`）。
- Transport 自动联调现有实现：`src/app/transport_debug/debug_run_contracts.py`（`CreateTransportDebugRun`）、`src/app/transport_debug/debug_run_service.py`（`create_run`）。
- `WorkLine.runtime_config_json` 字段本身的用途是"工作线运行时配置（重试、超时、会话归属等）"（`src/app/workline/models/workline.py`），草稿写入这个字段符合其既有定位。

## 3. 数据模型

草稿存在 `WorkLine.runtime_config_json` 的两个新 key 下，跟现有的 `ecs_test_rules`（生效层，不动）完全独立：

```json
{
  "ecs_test_rules": [ /* 现有生效配置，不动 */ ],
  "debug_instruction_drafts": {
    "ecs_test": {
      "rules": [
        {
          "source_device_code": "STATION_SCAN1",
          "target_device_code": "STATION_SCAN1",
          "task_type": "MOVE_FORWARD",
          "params": {"source": {"location_id": "STATION_SCAN1"}}
        }
      ]
    },
    "transport_debug": {
      "rack_id": "510056",
      "face_groups": [
        {"face": "90", "bins": [{"bin_code": "A000001922", "slot_id": "510056A3F2C101"}]}
      ],
      "test_mode": false,
      "workstation": "KT16",
      "infeed_position": "CNV0301",
      "outfeed_position": "CNV0302",
      "scan_device_codes": ["STATION_SCAN9", "STATION_SCAN10", "STATION_SCAN11", "STATION_SCAN12"]
    }
  }
}
```

以上是**讨论示例，非最终字段合同**——具体字段沿用各自现有的 `EcsTestRule`/`CreateTransportDebugRun` 形状，实现时以那两处代码为准。

- `debug_instruction_drafts.ecs_test`：结构校验复用现有 `parse_ecs_test_rules`（只做结构校验，不做设备归属/在线校验——这些校验属于"应用"步骤，即现有 START 流程，不属于草稿本身）。
- `debug_instruction_drafts.transport_debug`：新增一个纯函数校验器，复用 `CreateTransportDebugRun.__post_init__` 里已有的校验规则（面值、料箱数量、扫码设备数量等），但不要求 `workline_code`（草稿已经挂在具体 WorkLine 上，不需要重复）。
- 两个 key 都是可选的；一条 WorkLine 可以同时有两种草稿（草稿不代表当前 `run_mode`，纯粹是"存起来以后要用"）。
- 草稿没有版本号/乐观锁；两个人同时编辑同一条草稿后保存，后写入的覆盖先写入的（跟聊天草稿一样，不是关键业务数据，冲突了大不了重新编辑一次）。

## 4. API 设计

新增两个独立读写端点，**不复用**现有的 `PUT /work_lines/{id}`（那个端点整体替换 `runtime_config_json`，会把另一半草稿或 `ecs_test_rules` 冲掉）：

```
GET /work_lines/{id}/debug-instruction-drafts/ecs-test
PUT /work_lines/{id}/debug-instruction-drafts/ecs-test
GET /work_lines/{id}/debug-instruction-drafts/transport
PUT /work_lines/{id}/debug-instruction-drafts/transport
```

- 不要求 `is_active=False`；不要求 `version` 乐观锁（草稿不是关键配置，见上）。
- `PUT` 的实现是"读取该 WorkLine 行 → 只替换 `runtime_config_json.debug_instruction_drafts.{mode}` 这一个子 key → 写回"，不触碰 `runtime_config_json` 里的其它 key（包括生效中的 `ecs_test_rules`）。需要在行锁（`get_for_update`）保护下做读-改-写，避免并发写导致互相覆盖对方没碰过的 key。
- `GET` 找不到草稿时返回空（`null` 或 `{}`），不是 404——草稿本来就允许不存在。
- 两个端点各自的权限沿用 WorkLine 配置读写的既有权限体系（具体权限名在实现阶段对照 `OPS_PERMISSIONS`/`RequirePermission` 现有命名约定确定，不在本设计里预先分配）。

**"应用"不是新接口**：前端联调中心把草稿加载进表单、编辑完，点"应用"按钮时：
- ECS_TEST：前端照现有流程调用 `PUT /work_lines/{id}`（写 `run_mode`+`runtime_config_json.ecs_test_rules`，要求停线）+ `POST /operations/worklines/{id}/start`。草稿只是帮忙把表单填好，不改变这两个既有调用的契约。
- Transport：前端照现有流程调用 `POST /operations/transport-debug-runs`（`create_run`，具体路径以现有实现为准）。草稿只是帮忙把请求体填好。

## 5. 与现有代码的关系（明确不动的部分）

- `_process_event`、`_start_ecs_test`、`create_run`、`_reject_active_configuration_update`、`ux_device_commands_execution_identity` 等 PR #276 交付的所有生效路径：**零改动**。
- `WorkLineRunMode` 枚举：不变。
- 现有 `WorkLineUpdate`/`WorkLineService.update` 的乐观锁和"活动线不能改 `runtime_config_json`"门禁：不变，草稿走全新的、完全独立的端点和 service 方法，不经过这条门禁。

## 6. 校验规则

- `ecs_test` 草稿：结构不合法时 `PUT` 返回 4xx，错误信息复用 `parse_ecs_test_rules` 抛出的 `ValueError` 文案。
- `transport_debug` 草稿：结构不合法时同样返回 4xx，复用 `CreateTransportDebugRun.__post_init__` 的校验语义（新写一个不依赖 `workline_code` 的校验函数，逻辑与其保持一致，避免后续两处校验漂移——如果实现时发现能直接复用 `CreateTransportDebugRun` 本身校验大部分字段并单独处理 `workline_code`，优先复用而不是复制一份校验逻辑）。

## 7. 验收标准

- 新增 FAST 测试覆盖：两个模式的 `PUT`/`GET` 往返、结构校验拒绝、并发写不冲掉对方的 key、`is_active=True` 时依然允许读写草稿。
- 新增/复用 Postgres 集成测试确认：草稿写入不影响 `ecs_test_rules` 的现有 STOP 门禁和 START 冻结行为（即：写草稿不会意外触发 `_reject_active_configuration_update`，也不会被那条门禁挡住）。
- 回归：PR #276 的全部现有测试（FAST + 列出的 Postgres 集成测试）保持全绿，证明本次改动确实没有触碰生效路径。
- `heavy-test-impact.toml` 补上新文件/新增方法的精确 mapping。

## 8. 风险与非目标核对

- **风险**：如果实现时图省事，把草稿的读写直接接到现有 `WorkLineService.update`/`_reject_active_configuration_update` 路径上（而不是新建独立端点+方法），会导致"随时可编辑"这个核心诉求失效，还可能意外放宽或破坏现有的活动线保护。实现前必须先确认新端点完全绕开那条门禁，而不是修改门禁本身。
- **非目标确认**：本设计不涉及前端；不涉及"实时改规则、下一个事件立刻生效"这种效果（那个方案在讨论中被明确否决）；不涉及给 Transport 联调引入类似 ECS_TEST 的"冻结"机制（Transport 的 `create_run` 本来就没有冻结步骤，草稿只是预填表单，不新增冻结逻辑）。

## 执行选择

本设计对应"通用联调中心"项目的子项目 1（后端）。子项目 2（前端联调中心重写）依赖本设计交付的两个新端点，待本子项目实现并合并后再单独走一轮 brainstorming/spec 流程。
