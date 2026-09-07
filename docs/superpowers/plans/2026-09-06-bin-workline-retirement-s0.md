# BinExecution / Epoch 退役 S0 范围清单

状态：用户已确认的 START、设备派发/证据、执行处理、WMS owner、Transport 和 rough_sorter 核心迁移已实施；最终代码评审、QUALITY、核心 HEAVY、迁移及插件镜像端到端验证通过，已进入后端 PR #211。前端正式冻结等待后端合并后的 clean develop；S3B、部署及现场验收仍为后续边界。
实施输入：[SPEC](../specs/2026-09-06-bin-code-and-station-driven-flow-design.md)。
后端 HEAD：`8b00a0cb2e21a9e13dee7ee2a9f5da270bce9fba`；SPEC SHA-256：`0816e35ca83376a731ffa235d4256e7374ad45c11f590529ffe1be343fbd903b`。
初始 staged diff SHA-256：`ef88ea520c24106776f4c29255662d42d3ac3639c64ef4d4194f93a475087e65`；当时仅 SPEC 已暂存。实施开始时用户已暂存本清单及 SPEC，实施未改写 index。
前端 HEAD：`dcf0f61b3184dca84d1ca3ccd1ff015355865e2f`；工作区干净。

## 1. 风险与当前证据

GitNexus 索引为 bf98b4fe，落后当前 HEAD 三个提交。下表只作影响导航，已用当前 rg 文件清单补核，不能作为 fresh 全符号图谱。
| 符号 | 图谱风险 | 直接消费者 |
| --- | --- | --- |
| LineRunEpoch | CRITICAL | 41 |
| BinExecution | MEDIUM | 7 |
| WorkLineStartService | MEDIUM | 7 |
| PositionProjectionService | MEDIUM | 6 |
| DeviceDispatchService | LOW | 4 |
| DecisionApplier | MEDIUM | 5 |

LineRunEpoch 影响 START、DeviceCommand 派发/证据、FactProcessor/DecisionApplier、WMS owner、Transport 投影及 rough_sorter。
这是既有生产职责迁移，不能推迟到 S3B。该影响链已获用户确认；各内聚切片生产补丁前补齐拟改方法级 impact 或当前调用链降级证据。

## 2. 承接规则与切片

| 原职责 | 唯一承接 | 核心测试 owner |
| --- | --- | --- |
| START Epoch 请求重放 | WorkLine version；旧版本 409，客户端重读 | tests/api/test_workline_start_api.py；tests/workline/test_workline_start_service.py；START PostgreSQL |
| Epoch 设备/位置绑定 | WorkLine 当前配置；命令冻结必要发送合同 | tests/runtime/device_command/；tests/integration/device_command/ |
| Epoch 插件路由 | WorkLine 当前插件；切换前已有义务全部闭合 | tests/runtime/execution/；rough_sorter 自有 tests/ |
| BinExecution 位置授权 | WorkLine 准入、冻结 Transport 成员、对象锁及位置事实 | tests/runtime/execution/test_position_projection.py；tests/integration/execution/test_execution_constraints.py |
| Bin/Epoch confirmation owner | 真正任务或 WorkLine，唯一 owner 约束 | tests/runtime/execution/test_wms_confirmation_service.py；tests/integration/wms_adapter/ |
| Epoch 清线与关闭 | WorkLine 原子检查/停用；工作人员确认物理清线，无记录机制 | tests/integration/workline_capabilities/test_workline_configuration_postgresql.py |
| Transport bin_id | 内部 bin_code；外部 container_id 保留 | tests/runtime/transport/；tests/integration/transport/ |

顺序：S1 内部 Transport/联调 DTO 改名 → S3A WorkLine/设备/执行/WMS/插件关联迁移 → schema 与生成物闭合。
S3A 共享 SDK、schema、composition，由同一实施 owner 顺序修改，主 Agent 负责集成、文档和最终验证；S3B 站点等待/FIFO 为后续独立业务。

## 3. wire / 摘要 / 幂等清单

- START request_id 退出；请求携带 version，返回 WorkLine 状态/新版本，成功/冲突语义按 SPEC D1。
- `inbound.material.admission_decide@v1` 删除 `line_run_epoch_id`；data 保留 `material_execution_id`、`material_trace_id`、`six_in_one`、`measurements`、`shape_result`、`workline_code`、`source_position`。可靠 owner 仍为原 MaterialExecution，不新增业务身份。
- `outbound.bin.return_batch@v1` 删除 `line_run_epoch_id`；data 保留 `workline_code`、`rack_id`、`rack_face`、`return_candidates`。owner 迁入请求所属 WorkLine，创建义务前锁定该 WorkLine 并校验准入及编码匹配；FIFO 与精确成员校验继续按原合同执行。
- 两个 operation 的名称、方向、响应联合、`(operation, operation_id)` 幂等和重试规则保持原合同；严格 DTO 拒绝已删除字段，不提供替代 Epoch 字段或别名。
- SDK protocols/wms_operations/wms_types 中的 Epoch 字段同步退出，rough_sorter Facts/handlers/application 全部迁移；保留 MaterialExecution 自身身份。
- Transport request_json、submit_snapshot、debug_run DTO/evidence/state machine 含 bin_id；内部改名会改变规范化摘要输入，技术重试仍使用原冻结载荷及请求身份。
- Epoch configuration/topology digest 删除其运行代际用途；必要配置校验迁入唯一 WorkLine 入口，禁止复制摘要框架。
- 原 migration 文件保留历史链；用仓库命令生成新 revision，验证干净库完整迁移链，不修改旧 revision 以伪造零残留。
- Operation inventory、Transport OpenAPI、前端生成类型、API 示例按对应切片同步；废弃词残留须按历史迁移/通用 container_id/object_id 白名单解释。

## 4. 文件证据清单

下表为精确词扫描加六个 upstream 的文件并集，包含测试及历史材料；不是承诺每个文件都要修改。行号为空表示图谱间接消费者。
| 文件 | 分类 | 命中行（前 12 项） | SHA-256 前缀 |
| --- | --- | --- | --- |
| `deployment/plugin_composition.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 图谱间接 | `45667e1efe43` |
| `docs/architecture/SRS.md` | 合同/文档/生成物同步 | 146, 334, 398, 451, 452, 483, 485, 486, 507, 508, 559, 568 | `a6e7d6f44569` |
| `docs/architecture/device-command-contract.md` | 合同/文档/生成物同步 | 39, 43, 59, 72, 146 | `c7168d209712` |
| `docs/architecture/file_index.md` | 合同/文档/生成物同步 | 49 | `15a6c122acfa` |
| `docs/architecture/legacy-cleanup-matrix.csv` | 合同/文档/生成物同步 | 11, 12, 13, 14, 45, 48, 137 | `1b9b597aaed1` |
| `docs/contracts/device-annexes/rough-sorter-device-contract.md` | 合同/文档/生成物同步 | 46, 47, 53 | `30e6badeaf89` |
| `docs/contracts/transport-fulfillment-contract.md` | 合同/文档/生成物同步 | 127, 146, 175, 190, 304, 355, 356, 447, 632 | `a7fe8a692cb4` |
| `docs/contracts/wms-inbound-putaway-integration-requirements.md` | 合同/文档/生成物同步 | 32, 33, 48, 87, 91, 203, 215, 257, 260, 337, 348, 376 | `e3b3b1804fe5` |
| `docs/contracts/wms-manual-outbound-picking-integration-requirements.md` | 合同/文档/生成物同步 | 44, 64, 68, 117, 120, 125, 126, 134, 140, 216, 239, 257 | `94f523b90f42` |
| `docs/contracts/wms-outbound-picking-task-integration-requirements.md` | 合同/文档/生成物同步 | 263, 798, 803, 832, 893, 925, 1449, 1771, 1772 | `b9cabc83d3d2` |
| `docs/contracts/wms-rough-sorter-inbound-integration-requirements.md` | 合同/文档/生成物同步 | 46, 102 | `e472e1390730` |
| `migrations/env.py` | 历史迁移，保留链；新 revision 删除目标结构 | 37, 78, 79 | `6d884a0df59b` |
| `migrations/versions/20260831_1531_f9c7c2e5f501_建立最终初始数据库基线.py` | 历史迁移，保留链；新 revision 删除目标结构 | 1295, 1319, 1765, 1767, 1779, 1793, 1798, 1803, 1816, 1836, 1861, 1867 | `5fe45010fba7` |
| `migrations/versions/20260904_1437_ff5d0af61f91_扩展_pickingtask_prepare_领取合同.py` | 历史迁移，保留链；新 revision 删除目标结构 | 25, 39, 47, 49, 62, 85, 91, 109, 116 | `c1ce795bde47` |
| `migrations/versions/20260905_1102_b42147d0d086_添加工作线插件选择并放开同角色设备.py` | 历史迁移，保留链；新 revision 删除目标结构 | 86, 100 | `b71a9b1475ac` |
| `migrations/versions/20260907_0427_5098dc1b2b63_add_epoch_owner_to_wms_confirmation.py` | 历史迁移，保留链；新 revision 删除目标结构 | 23, 28, 36, 46, 48, 63, 68 | `ac9d885f4619` |
| `scripts/verify_core_plugin_installation.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 图谱间接 | `2d37cd702f2e` |
| `src/app/device/composition.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 图谱间接 | `a936c9c92ad5` |
| `src/app/device/contracts.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 22 | `b12cc269d55e` |
| `src/app/device/models/command.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 92, 184, 188, 220 | `2e4507c85069` |
| `src/app/device/repositories/__init__.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 图谱间接 | `aa86e0a241c6` |
| `src/app/device/repositories/command_repository.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 12, 74, 79, 91, 100, 134, 141, 152, 157, 314, 317 | `2f9f32f2ef21` |
| `src/app/device/services/__init__.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 图谱间接 | `0f8bcc83405b` |
| `src/app/device/services/device_command_service.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 43, 98, 130, 132, 138, 140, 246, 256, 279, 323, 389, 426 | `5bf400b5ad84` |
| `src/app/device/services/device_context_service.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 图谱间接 | `ec4a431e9694` |
| `src/app/device/services/device_dispatch_service.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 20, 52, 53, 67, 119, 129, 137, 288, 297, 309 | `c442fcf5c349` |
| `src/app/device/services/device_evidence_service.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 46, 170, 259, 318, 319, 321, 501, 543, 544 | `0676baec6111` |
| `src/app/device/v1/command.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 图谱间接 | `bf2ed2e6f29c` |
| `src/app/device/v1/ecs_callback.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 图谱间接 | `1175aba126af` |
| `src/app/device/v1/reconciliation.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 图谱间接 | `5f33197d27e9` |
| `src/app/execution/__init__.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 图谱间接 | `37c14a26049f` |
| `src/app/execution/composition.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 图谱间接 | `1e6c0db50c93` |
| `src/app/execution/locks.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 17, 18, 21, 22 | `532a83e1f961` |
| `src/app/execution/models/__init__.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 3, 20 | `dc869ddb9a5f` |
| `src/app/execution/models/bin_execution.py` | 删除定义；必要职责迁入 WorkLine | 22, 32, 37, 42, 44, 57 | `15d02d4d30cd` |
| `src/app/execution/models/inbound_evidence.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 131 | `cd112c6d2406` |
| `src/app/execution/models/material_execution.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 98, 102, 115 | `e39e56ac9f14` |
| `src/app/execution/models/position_projection.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 24, 25, 29, 37, 38 | `70e8543af6be` |
| `src/app/execution/models/transport_decision_binding.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 22, 27, 38, 46, 57, 61 | `db4fa582c786` |
| `src/app/execution/models/wms_confirmation.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 38, 40, 71, 83 | `ae9c2a0ea5d4` |
| `src/app/execution/repositories/__init__.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 图谱间接 | `290853e1a239` |
| `src/app/execution/repositories/bin_execution_repository.py` | 删除定义；必要职责迁入 WorkLine | 1, 11, 12, 16, 18, 20, 23, 26, 27, 29, 30, 34 | `dfe581151191` |
| `src/app/execution/repositories/inbound_evidence_repository.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 17, 146, 149, 153 | `52f4f7b7c6f5` |
| `src/app/execution/repositories/material_execution_repository.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 50, 59 | `ee28ca4622e4` |
| `src/app/execution/repositories/position_projection_repository.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 11, 13, 21, 24, 27, 28, 29, 31, 32, 33, 65, 67 | `f9c00c66c992` |
| `src/app/execution/repositories/transport_decision_binding_repository.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 22, 28, 31, 34, 41, 49, 61, 69 | `4c2bb54f4691` |
| `src/app/execution/services/__init__.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 图谱间接 | `d0f1682c8a4f` |
| `src/app/execution/services/bin_execution_service.py` | 删除定义；必要职责迁入 WorkLine | 1, 8, 18, 22, 24, 26, 28, 30, 32, 40, 58, 60 | `6990bf5bf266` |
| `src/app/execution/services/decision_applier.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 52, 60, 63, 75, 81, 90, 246, 261, 305, 310, 316, 326 | `17902ffdc2c9` |
| `src/app/execution/services/fact_builder.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 28, 40, 85, 151, 174 | `31e9de601e8e` |
| `src/app/execution/services/fact_processor.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 25, 64, 66, 281, 282, 283, 284, 285, 286, 289, 290, 291 | `043055411207` |
| `src/app/execution/services/inbound_evidence_service.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 93, 137, 149, 196 | `23dd2b779eb4` |
| `src/app/execution/services/material_execution_service.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 40, 66, 84, 100, 110, 120, 137 | `8bf1cd8da610` |
| `src/app/execution/services/position_projection_service.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 11, 22, 24, 26, 38, 75, 78, 79, 83, 84, 92, 94 | `e0ac3e33efc9` |
| `src/app/execution/services/wms_confirmation_service.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 100, 197, 199, 204, 219, 221, 227, 231, 242, 244, 379, 383 | `01520aa9f288` |
| `src/app/resource/services/active_rack_snapshot_service.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 107, 338, 447, 484 | `972d6ecaf583` |
| `src/app/resource/services/relation_service.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 315 | `2dc4358ee7c8` |
| `src/app/runtime/orchestration/models/runtime.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 192, 603 | `c697f9cf2fb2` |
| `src/app/runtime/orchestration/services/query/workline_active_objects_service.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 图谱间接 | `27da54bcd246` |
| `src/app/runtime/orchestration/services/trace/trace_resource_view_builder.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 22 | `35d6bc34d745` |
| `src/app/runtime/orchestration/services/trace/trace_response_builder.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 194 | `36698901fb1b` |
| `src/app/transport/__init__.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 图谱间接 | `65f87f00b7e9` |
| `src/app/transport/composition.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 图谱间接 | `d6ddf6b5706c` |
| `src/app/transport/contracts.py` | 料箱改名；保留外部 container_id，Epoch 关联另迁移 | 137, 138, 143, 144, 208, 213, 227, 229, 233, 234, 237, 320 | `6784928dcd55` |
| `src/app/transport/debug_run_contracts.py` | 料箱改名；保留外部 container_id，Epoch 关联另迁移 | 38, 42, 79, 80, 81 | `185d0b9afaf9` |
| `src/app/transport/debug_run_evidence.py` | 料箱改名；保留外部 container_id，Epoch 关联另迁移 | 29, 62, 63, 68, 77, 151, 157 | `a372de16e1b9` |
| `src/app/transport/debug_run_repository.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 图谱间接 | `de2bf11599ea` |
| `src/app/transport/debug_run_service.py` | 料箱改名；保留外部 container_id，Epoch 关联另迁移 | 492, 495, 498, 535, 538, 540, 544, 549, 550, 653, 656, 794 | `b3cc3df13d54` |
| `src/app/transport/debug_run_state_machine.py` | 料箱改名；保留外部 container_id，Epoch 关联另迁移 | 71, 73, 159 | `7f5525dbf431` |
| `src/app/transport/service.py` | 料箱改名；保留外部 container_id，Epoch 关联另迁移 | 1055, 1058, 1529, 1534, 1535, 1559, 1564, 1565, 1568, 1569, 1601, 1640 | `9363e3007d38` |
| `src/app/transport/submit_snapshot.py` | 料箱改名；保留外部 container_id，Epoch 关联另迁移 | 45, 58, 63 | `1e0bba278d5a` |
| `src/app/transport/v1/debug_runs.py` | 料箱改名；保留外部 container_id，Epoch 关联另迁移 | 59, 86, 173 | `f04592686b23` |
| `src/app/transport/v1/tasks.py` | 料箱改名；保留外部 container_id，Epoch 关联另迁移 | 123, 133, 135, 287, 309, 316, 394, 403, 405 | `547fa180d83b` |
| `src/app/wms_adapter/inbound_material/wire.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 135 | `9ffa381a0cc4` |
| `src/app/wms_adapter/outbound_picking/return_batch_typed.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 18 | `00df468b6e8a` |
| `src/app/wms_adapter/outbound_picking/return_batch_wire.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 35 | `b6aafb942022` |
| `src/app/wms_adapter/v1/events.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 图谱间接 | `93e60c52a07d` |
| `src/app/wms_integration/outbound_picking/models/picking_task.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 49, 51, 116 | `9c4af80ffa2c` |
| `src/app/wms_integration/outbound_picking/repositories/plan_delta_repository.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 15, 51, 63 | `1042c7efc26d` |
| `src/app/wms_integration/outbound_picking/repositories/prepare_eligibility_repository.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 13, 25, 36, 47, 48, 68 | `9047c0194ff2` |
| `src/app/wms_integration/outbound_picking/services/picking_task_confirmation_owner.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 58, 59 | `326f70015f1f` |
| `src/app/wms_integration/outbound_picking/services/picking_task_issued.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 图谱间接 | `cc2a147267ab` |
| `src/app/wms_integration/outbound_picking/services/picking_task_prepare.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 28, 75, 148, 181 | `370edf02f4e6` |
| `src/app/wms_integration/outbound_picking/services/return_batch_owner.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 4, 5, 10, 13, 18, 23, 24, 29, 31 | `ac1cc649f9c3` |
| `src/app/workline/epoch_activation.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 1, 9, 30, 46, 47, 51, 52 | `f837ea35d00d` |
| `src/app/workline/epoch_digest.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 1 | `dd5a8382d845` |
| `src/app/workline/models/__init__.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 7, 40, 41, 42, 43 | `e15dbed7ed3a` |
| `src/app/workline/models/line_run_epoch.py` | 删除定义；必要职责迁入 WorkLine | 18, 25, 32, 43, 51, 52, 55, 62, 69, 74, 87, 105 | `b7a62c9ca2bd` |
| `src/app/workline/models/start.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 27, 28 | `24452983f822` |
| `src/app/workline/plugin_routing.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 64, 105 | `7a3e7bfb928f` |
| `src/app/workline/repositories/__init__.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 8, 13 | `45465c6f2488` |
| `src/app/workline/repositories/line_run_epoch_repository.py` | 删除定义；必要职责迁入 WorkLine | 1, 14, 15, 16, 17, 23, 24, 28, 32, 42, 47, 50 | `eaae6d79c6af` |
| `src/app/workline/repositories/workline_repository.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 10, 23, 95, 97, 107, 130, 142, 157, 172, 191, 220, 239 | `55935c67fff6` |
| `src/app/workline/services/__init__.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 14, 30 | `4f52cffb5aa1` |
| `src/app/workline/services/line_run_epoch_service.py` | 删除定义；必要职责迁入 WorkLine | 1, 13, 15, 21, 22, 30, 33, 35, 42, 43, 44, 45 | `98c04a089e0a` |
| `src/app/workline/services/plane_service.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 图谱间接 | `dbad8db0c242` |
| `src/app/workline/services/safety_service.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 图谱间接 | `3348297cfa25` |
| `src/app/workline/services/workline_configuration_service.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 20, 111 | `1d246469d5da` |
| `src/app/workline/services/workline_service.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 图谱间接 | `51e59cd11794` |
| `src/app/workline/services/workline_start_service.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 13, 20, 21, 23, 44, 52, 54, 61, 62, 63, 64, 66 | `5777558ff481` |
| `src/app/workline/v1/__init__.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 图谱间接 | `f002a5772045` |
| `src/app/workline/v1/operation.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 101, 162, 163 | `45f62370eed1` |
| `src/app/workline/v1/workline.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 图谱间接 | `24c7e6afe514` |
| `src/celery_app/async_runtime.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 图谱间接 | `8f3d6d33f702` |
| `src/celery_app/tasks/device_command.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 图谱间接 | `0fe35a4327f5` |
| `src/celery_app/tasks/execution.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 24, 33 | `81ad71f3c4b1` |
| `src/celery_app/tasks/transport.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 图谱间接 | `982cc6ac6002` |
| `src/celery_app/tasks/wms_confirmation.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 图谱间接 | `818818901357` |
| `src/register.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 图谱间接 | `fede46d8b44c` |
| `src/wes_plugin_sdk/src/wes_plugin_sdk/protocols.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 21, 28, 60, 71 | `31515772927b` |
| `src/wes_plugin_sdk/src/wes_plugin_sdk/wms_operations.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 42, 54, 230, 238 | `904cd52d51b0` |
| `src/wes_plugin_sdk/src/wes_plugin_sdk/wms_types.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 82, 92, 280, 287 | `578bc39e7d32` |
| `tests/README.md` | 承接测试/fixture；不得直接删除行为断言 | 47 | `35246b397014` |
| `tests/api/test_device_ecs_callbacks.py` | 承接测试/fixture；不得直接删除行为断言 | 图谱间接 | `c556921957e1` |
| `tests/api/test_device_reconciliation_api.py` | 承接测试/fixture；不得直接删除行为断言 | 图谱间接 | `002fbe373fe0` |
| `tests/api/test_transport_debug_runs.py` | 承接测试/fixture；不得直接删除行为断言 | 105 | `e157d42b9ce4` |
| `tests/api/test_transport_tasks.py` | 承接测试/fixture；不得直接删除行为断言 | 132, 145, 147, 579 | `18d363ed3710` |
| `tests/api/test_workline_routes.py` | 承接测试/fixture；不得直接删除行为断言 | 图谱间接 | `d0a3cab830e2` |
| `tests/api/test_workline_start_api.py` | 承接测试/fixture；不得直接删除行为断言 | 10, 53, 54, 56, 66, 137, 138, 251, 281, 282, 316 | `de946c25c0d2` |
| `tests/architecture/test_plugin_sdk_boundary_guardrail.py` | 承接测试/fixture；不得直接删除行为断言 | 359, 370, 382, 437 | `17eca98dcdbe` |
| `tests/architecture/test_transport_boundaries.py` | 承接测试/fixture；不得直接删除行为断言 | 图谱间接 | `6a04b5d910d0` |
| `tests/architecture/test_workline_service_shim_contract.py` | 承接测试/fixture；不得直接删除行为断言 | 236, 278 | `2523b4754f2d` |
| `tests/contracts/wms_adapter/inbound_material/support.py` | 承接测试/fixture；不得直接删除行为断言 | 69 | `26f61a720aa8` |
| `tests/contracts/wms_adapter/inbound_material/test_typed.py` | 承接测试/fixture；不得直接删除行为断言 | 20 | `2402262239eb` |
| `tests/contracts/wms_adapter/inbound_material/test_wire_acceptance.py` | 承接测试/fixture；不得直接删除行为断言 | 55, 120, 249, 263 | `04077ec97470` |
| `tests/contracts/wms_adapter/outbound_picking/test_confirmation_owner.py` | 承接测试/fixture；不得直接删除行为断言 | 19, 36, 66, 91 | `f6d4f6bbc6e9` |
| `tests/contracts/wms_adapter/outbound_picking/test_plan_delta_service.py` | 承接测试/fixture；不得直接删除行为断言 | 65 | `b11f61b05513` |
| `tests/contracts/wms_adapter/outbound_picking/test_prepare_eligibility.py` | 承接测试/fixture；不得直接删除行为断言 | 70, 93 | `57759e65f72d` |
| `tests/contracts/wms_adapter/outbound_picking/test_prepare_service.py` | 承接测试/fixture；不得直接删除行为断言 | 86, 217 | `b77f185f171f` |
| `tests/contracts/wms_adapter/outbound_picking/test_return_batch.py` | 承接测试/fixture；不得直接删除行为断言 | 21, 113, 182, 184, 187, 189, 200 | `c186318f943a` |
| `tests/contracts/wms_adapter/outbound_picking/test_work_plan.py` | 承接测试/fixture；不得直接删除行为断言 | 42 | `e99be35156f3` |
| `tests/contracts/wms_adapter/test_transport_openapi.py` | 承接测试/fixture；不得直接删除行为断言 | 39 | `5934ad5a38df` |
| `tests/core/test_schema_json_nullability.py` | 承接测试/fixture；不得直接删除行为断言 | 图谱间接 | `9d1ff6c1d4a5` |
| `tests/core/test_schema_reference_id_types.py` | 承接测试/fixture；不得直接删除行为断言 | 16, 22, 23, 24 | `322373c8cb88` |
| `tests/deployment/test_core_plugin_installation.py` | 承接测试/fixture；不得直接删除行为断言 | 图谱间接 | `82cc33bf79e4` |
| `tests/deployment/test_device_command_startup.py` | 承接测试/fixture；不得直接删除行为断言 | 图谱间接 | `1f705ac1b92e` |
| `tests/deployment/test_execution_worker_startup.py` | 承接测试/fixture；不得直接删除行为断言 | 13, 336 | `a0d09a6060db` |
| `tests/e2e/device_command/test_device_command_production_wiring.py` | 承接测试/fixture；不得直接删除行为断言 | 19, 116, 119, 191, 193, 228, 229, 242, 243, 260 | `00b4d1c453a4` |
| `tests/e2e/transport/test_transport_production_wiring.py` | 承接测试/fixture；不得直接删除行为断言 | 148 | `2b353ff8a614` |
| `tests/integration/device_command/test_device_command_constraints.py` | 承接测试/fixture；不得直接删除行为断言 | 28, 30, 31, 34, 50, 51, 63, 64, 79, 83, 102, 125 | `54a7c5bd98e9` |
| `tests/integration/device_command/test_event_command_blocking_reconciliation_postgresql.py` | 承接测试/fixture；不得直接删除行为断言 | 32, 183, 234, 235, 247, 248, 278, 290, 381, 388, 390, 620 | `727589b04f62` |
| `tests/integration/execution/test_decision_processing_postgresql.py` | 承接测试/fixture；不得直接删除行为断言 | 63, 64, 65, 66, 202, 207, 216, 224, 232, 256, 280, 281 | `a6f398fccdd3` |
| `tests/integration/execution/test_execution_constraints.py` | 承接测试/fixture；不得直接删除行为断言 | 15, 44, 50, 59, 60, 75, 82, 92, 101, 116, 118, 124 | `9635259cfdb5` |
| `tests/integration/fixtures/initial_schema_final_manifest.json` | 承接测试/fixture；不得直接删除行为断言 | 1 | `9b47ad6a40fb` |
| `tests/integration/fixtures/initial_schema_old_chain_catalog.json` | 承接测试/fixture；不得直接删除行为断言 | 1 | `77214740a6fd` |
| `tests/integration/fixtures/initial_schema_transition_disposition.json` | 承接测试/fixture；不得直接删除行为断言 | 1 | `5ce70024332c` |
| `tests/integration/test_celery_async_runtime_postgresql.py` | 承接测试/fixture；不得直接删除行为断言 | 519, 541, 542, 565, 566, 583, 616 | `3ce60a320458` |
| `tests/integration/test_optimistic_lock.py` | 承接测试/fixture；不得直接删除行为断言 | 图谱间接 | `c348bca6baf8` |
| `tests/integration/test_release_operational_readiness_postgresql.py` | 承接测试/fixture；不得直接删除行为断言 | 33, 115, 128, 129, 154, 155, 169, 209, 257, 361, 399, 521 | `7dba8a16f074` |
| `tests/integration/test_transport_fulfillment_queue.py` | 承接测试/fixture；不得直接删除行为断言 | 图谱间接 | `852b37199b07` |
| `tests/integration/transport/test_dark_transport_loop.py` | 承接测试/fixture；不得直接删除行为断言 | 116, 123, 137 | `dfb5e3409f6d` |
| `tests/integration/transport/test_transport_debug_auto_run.py` | 承接测试/fixture；不得直接删除行为断言 | 129, 148, 163, 182, 332, 374, 378, 386, 397, 400, 417, 611 | `45ddd67c44c2` |
| `tests/integration/transport/test_transport_debug_reset.py` | 承接测试/fixture；不得直接删除行为断言 | 202, 241, 323, 353 | `9ec681469663` |
| `tests/integration/transport/test_transport_debug_run_real_loop.py` | 承接测试/fixture；不得直接删除行为断言 | 104, 123, 135, 164, 190, 337, 363, 375, 387, 408, 429, 443 | `677b6cfe1e41` |
| `tests/integration/transport/test_transport_debug_run_recovery.py` | 承接测试/fixture；不得直接删除行为断言 | 38 | `16a9aa87078c` |
| `tests/integration/transport/test_transport_debug_run_repository.py` | 承接测试/fixture；不得直接删除行为断言 | 487 | `ba08fb890f7b` |
| `tests/integration/transport/test_transport_debug_run_schema.py` | 承接测试/fixture；不得直接删除行为断言 | 图谱间接 | `8717db95e5a8` |
| `tests/integration/transport/test_transport_debug_run_service.py` | 承接测试/fixture；不得直接删除行为断言 | 43 | `46fa48eb69ad` |
| `tests/integration/transport/test_transport_evidence_transaction.py` | 承接测试/fixture；不得直接删除行为断言 | 827, 833, 851, 1009, 1015, 1024 | `8d07c7b034c8` |
| `tests/integration/transport/test_transport_schema.py` | 承接测试/fixture；不得直接删除行为断言 | 图谱间接 | `2df17a21688a` |
| `tests/integration/wms_adapter/outbound_picking/confirmation_support.py` | 承接测试/fixture；不得直接删除行为断言 | 16, 17, 75, 76, 85, 112, 143 | `436f2ad043ce` |
| `tests/integration/wms_adapter/outbound_picking/test_departure_production_wiring.py` | 承接测试/fixture；不得直接删除行为断言 | 85 | `f0036c42d07d` |
| `tests/integration/wms_adapter/outbound_picking/test_issued_postgresql.py` | 承接测试/fixture；不得直接删除行为断言 | 图谱间接 | `20f584b535a4` |
| `tests/integration/wms_adapter/outbound_picking/test_material_decide_production_wiring.py` | 承接测试/fixture；不得直接删除行为断言 | 94 | `108c828656f3` |
| `tests/integration/wms_adapter/outbound_picking/test_plan_delta_postgresql.py` | 承接测试/fixture；不得直接删除行为断言 | 30, 69, 70, 101, 161, 187 | `749f664b2381` |
| `tests/integration/wms_adapter/outbound_picking/test_plan_delta_production_wiring.py` | 承接测试/fixture；不得直接删除行为断言 | 42 | `28cef802686e` |
| `tests/integration/wms_adapter/outbound_picking/test_prepare_postgresql.py` | 承接测试/fixture；不得直接删除行为断言 | 29, 30, 31, 83, 84, 102, 103, 113, 114, 240, 251, 262 | `9e9f4afb6df8` |
| `tests/integration/wms_adapter/outbound_picking/test_queue_changed_postgresql.py` | 承接测试/fixture；不得直接删除行为断言 | 20, 193, 194, 209 | `0b34a1fe02eb` |
| `tests/integration/wms_adapter/outbound_picking/test_return_batch_production_wiring.py` | 承接测试/fixture；不得直接删除行为断言 | 17, 18, 21, 76, 77, 85, 95, 106, 116, 123, 124, 128 | `8dd4530038b1` |
| `tests/integration/wms_adapter/outbound_picking/test_schema.py` | 承接测试/fixture；不得直接删除行为断言 | 55, 93, 139, 142 | `c496ca3a7519` |
| `tests/integration/wms_adapter/outbound_picking/test_source_empty_production_wiring.py` | 承接测试/fixture；不得直接删除行为断言 | 81 | `b755ca95d48c` |
| `tests/integration/wms_adapter/outbound_picking/test_work_plan_production_wiring.py` | 承接测试/fixture；不得直接删除行为断言 | 70 | `eb3332776852` |
| `tests/integration/wms_adapter/test_inbound_confirmation_postgresql.py` | 承接测试/fixture；不得直接删除行为断言 | 31, 47, 48, 66, 75, 98, 107, 140, 161, 169, 344, 383 | `c0cd5ea54cc3` |
| `tests/integration/wms_adapter/test_transport_callback_receipts.py` | 承接测试/fixture；不得直接删除行为断言 | 图谱间接 | `275ade4c8a95` |
| `tests/integration/workline_capabilities/test_line_run_epoch_activation_postgresql.py` | 承接测试/fixture；不得直接删除行为断言 | 1, 14, 15, 18, 19, 20, 23, 24, 45, 46, 58, 59 | `d484ebda5c91` |
| `tests/integration/workline_capabilities/test_unfinished_execution_snapshot_postgresql.py` | 承接测试/fixture；不得直接删除行为断言 | 13, 28, 52, 53, 72, 73, 91, 101, 108, 110, 112, 121 | `fbe6874a7095` |
| `tests/integration/workline_capabilities/test_workline_configuration_postgresql.py` | 承接测试/fixture；不得直接删除行为断言 | 19, 21, 187, 188, 202, 203, 215, 225, 235, 298, 299, 316 | `773bfc4850cf` |
| `tests/integration/workline_capabilities/test_workline_start_postgresql.py` | 承接测试/fixture；不得直接删除行为断言 | 16, 17, 22, 23, 24, 25, 61, 73, 147, 151, 158, 174 | `83fd9b527216` |
| `tests/runtime/device_command/test_composition.py` | 承接测试/fixture；不得直接删除行为断言 | 图谱间接 | `fb40a8e11513` |
| `tests/runtime/device_command/test_device_command_model.py` | 承接测试/fixture；不得直接删除行为断言 | 17 | `22ea2d781a9e` |
| `tests/runtime/device_command/test_device_command_service.py` | 承接测试/fixture；不得直接删除行为断言 | 27, 65, 74, 114, 121, 123, 124, 194, 195, 197, 212, 225 | `3c41d63fdd36` |
| `tests/runtime/device_command/test_dispatch_admission.py` | 承接测试/fixture；不得直接删除行为断言 | 13, 16, 17, 19, 36 | `49d3fa32a1e7` |
| `tests/runtime/device_command/test_dispatch_service.py` | 承接测试/fixture；不得直接删除行为断言 | 13, 80, 84, 89, 171, 186, 187, 189, 240, 280, 310, 333 | `f171d1b27ff3` |
| `tests/runtime/device_command/test_evidence_service.py` | 承接测试/fixture；不得直接删除行为断言 | 240, 335, 487, 499, 555, 594, 785, 798, 824, 943, 968, 1059 | `1d61782ea53e` |
| `tests/runtime/device_command/test_manual_reconciliation_service.py` | 承接测试/fixture；不得直接删除行为断言 | 101, 104, 150, 216, 330, 339, 395 | `390e3a6ce5a6` |
| `tests/runtime/device_command/test_reconciliation_service.py` | 承接测试/fixture；不得直接删除行为断言 | 41 | `63a4966ea538` |
| `tests/runtime/execution/test_bin_execution.py` | 承接测试/fixture；不得直接删除行为断言 | 1, 22, 23, 25, 26, 27, 29, 30, 32, 33, 57, 58 | `81832018a4b1` |
| `tests/runtime/execution/test_decision_applier.py` | 承接测试/fixture；不得直接删除行为断言 | 24, 79, 92, 112, 115, 119, 121, 161, 165, 167, 169, 327 | `460c776b81e3` |
| `tests/runtime/execution/test_fact_builder.py` | 承接测试/fixture；不得直接删除行为断言 | 26, 40, 56, 227, 228, 250, 282 | `3aae7647673f` |
| `tests/runtime/execution/test_fact_processor.py` | 承接测试/fixture；不得直接删除行为断言 | 31, 89, 91, 99, 103, 105, 107, 109, 112, 113, 158, 362 | `56c67f80f97c` |
| `tests/runtime/execution/test_inbound_evidence_repository.py` | 承接测试/fixture；不得直接删除行为断言 | 图谱间接 | `723d69a71a66` |
| `tests/runtime/execution/test_inbound_evidence_service.py` | 承接测试/fixture；不得直接删除行为断言 | 95, 105, 171 | `3bc17d602c8a` |
| `tests/runtime/execution/test_material_execution.py` | 承接测试/fixture；不得直接删除行为断言 | 54, 60, 88, 126, 166, 278, 312 | `80ed599b4651` |
| `tests/runtime/execution/test_plugin_sdk_wms_operations.py` | 承接测试/fixture；不得直接删除行为断言 | 30 | `7cb6c07d8241` |
| `tests/runtime/execution/test_position_projection.py` | 承接测试/fixture；不得直接删除行为断言 | 28, 30, 34, 35, 37, 38, 41, 42, 66, 82, 83, 103 | `7f8eee3aff17` |
| `tests/runtime/execution/test_transport_decision_binding.py` | 承接测试/fixture；不得直接删除行为断言 | 16, 39 | `c258995aff5c` |
| `tests/runtime/execution/test_wms_confirmation_dispatch.py` | 承接测试/fixture；不得直接删除行为断言 | 140, 313, 382, 512, 546, 551, 578 | `cdaed00b40b8` |
| `tests/runtime/execution/test_wms_confirmation_service.py` | 承接测试/fixture；不得直接删除行为断言 | 238, 276, 282 | `893989904be1` |
| `tests/runtime/transport/conftest.py` | 承接测试/fixture；不得直接删除行为断言 | 图谱间接 | `e4296efa8653` |
| `tests/runtime/transport/test_transport_acceptance_edges.py` | 承接测试/fixture；不得直接删除行为断言 | 203, 209, 832, 838, 1079, 1109 | `b151f572eaae` |
| `tests/runtime/transport/test_transport_composition.py` | 承接测试/fixture；不得直接删除行为断言 | 图谱间接 | `a306a0008f2e` |
| `tests/runtime/transport/test_transport_debug_run_advancement.py` | 承接测试/fixture；不得直接删除行为断言 | 271, 272, 345, 358, 607, 624, 694, 695, 718, 719 | `37acd189b1b1` |
| `tests/runtime/transport/test_transport_debug_run_contracts.py` | 承接测试/fixture；不得直接删除行为断言 | 15, 16, 54, 63, 70 | `94d5e4cd1bdf` |
| `tests/runtime/transport/test_transport_debug_run_evidence.py` | 承接测试/fixture；不得直接删除行为断言 | 59, 77, 92 | `b71babd3595c` |
| `tests/runtime/transport/test_transport_debug_run_service.py` | 承接测试/fixture；不得直接删除行为断言 | 207 | `1dc61344501b` |
| `tests/runtime/transport/test_transport_debug_run_state_machine.py` | 承接测试/fixture；不得直接删除行为断言 | 51, 52, 57, 311 | `1c00036c744f` |
| `tests/runtime/transport/test_transport_execution_authority.py` | 承接测试/fixture；不得直接删除行为断言 | 12, 15, 16, 21, 22 | `98bbd2491f93` |
| `tests/runtime/transport/test_transport_observability.py` | 承接测试/fixture；不得直接删除行为断言 | 图谱间接 | `cb2945dee20d` |
| `tests/runtime/transport/test_transport_outcome.py` | 承接测试/fixture；不得直接删除行为断言 | 910, 916 | `3ca15738a8b2` |
| `tests/runtime/transport/test_transport_outcome_revision.py` | 承接测试/fixture；不得直接删除行为断言 | 59 | `1869824373da` |
| `tests/runtime/transport/test_transport_reconciling_facts.py` | 承接测试/fixture；不得直接删除行为断言 | 图谱间接 | `23094d3730c9` |
| `tests/runtime/transport/test_transport_service.py` | 承接测试/fixture；不得直接删除行为断言 | 562, 568, 616, 627, 635, 648, 659, 717, 735, 741 | `c7d3de013b47` |
| `tests/runtime/transport/test_transport_submit_fencing.py` | 承接测试/fixture；不得直接删除行为断言 | 图谱间接 | `d8eea24a7873` |
| `tests/support/ecs_uniform_wire.py` | 承接测试/fixture；不得直接删除行为断言 | 图谱间接 | `409259b4949e` |
| `tests/support/sqlmodel_metadata.py` | 承接测试/fixture；不得直接删除行为断言 | 10, 13, 18, 21 | `c4d9c197ad3e` |
| `tests/support/transport_callbacks.py` | 承接测试/fixture；不得直接删除行为断言 | 图谱间接 | `c53498ab5855` |
| `tests/support/transport_projections.py` | 承接测试/fixture；不得直接删除行为断言 | 11, 29, 43, 64, 67, 81, 83, 84 | `dd5d0063d6bb` |
| `tests/workline/test_epoch_activation.py` | 承接测试/fixture；不得直接删除行为断言 | 1, 10, 11, 14, 16, 17, 22, 25, 26, 27, 32, 40 | `cfc86e552868` |
| `tests/workline/test_epoch_digest.py` | 承接测试/fixture；不得直接删除行为断言 | 6, 13, 16, 17, 18, 30, 31, 32, 105, 109, 114, 124 | `41573b4cd4a1` |
| `tests/workline/test_line_run_epoch.py` | 承接测试/fixture；不得直接删除行为断言 | 1, 9, 10, 13, 15, 22, 26, 32, 46, 47, 48, 49 | `41739339b4e2` |
| `tests/workline/test_plugin_routing.py` | 承接测试/fixture；不得直接删除行为断言 | 70, 92, 107 | `eead0b7eb2b5` |
| `tests/workline/test_workline_configuration_service.py` | 承接测试/fixture；不得直接删除行为断言 | 560, 582, 601 | `127bf67b7e97` |
| `tests/workline/test_workline_start_service.py` | 承接测试/fixture；不得直接删除行为断言 | 13, 14, 18, 29, 38, 39, 40, 42, 47, 48, 50, 119 | `27cbac2b5ac1` |
| `tests/workline_runtime/test_workline_active_objects_service.py` | 承接测试/fixture；不得直接删除行为断言 | 图谱间接 | `9a3a7893804b` |
| `workline_plugins/rough_sorter/src/rough_sorter/application/business_blocker.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 图谱间接 | `683043a72f24` |
| `workline_plugins/rough_sorter/src/rough_sorter/application/device_facts.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 97, 110, 164 | `62aa3dd91dc8` |
| `workline_plugins/rough_sorter/src/rough_sorter/application/factory.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 17, 74, 117, 189, 190, 213, 218, 246, 247, 294 | `10bd47dbde1e` |
| `workline_plugins/rough_sorter/src/rough_sorter/application/persistence.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 33, 45, 47, 49, 52, 53, 85, 88, 102, 105, 112, 127 | `658cc8ffd3f4` |
| `workline_plugins/rough_sorter/src/rough_sorter/application/start_plan.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 10, 11, 78, 85, 108, 117, 120, 139 | `573aeca5dd5d` |
| `workline_plugins/rough_sorter/src/rough_sorter/application/transport.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 94, 113, 152, 162, 170 | `aaa963c38abc` |
| `workline_plugins/rough_sorter/src/rough_sorter/application/transport_recovery_facts.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 136, 142, 281, 326 | `3f300d7368d1` |
| `workline_plugins/rough_sorter/src/rough_sorter/application/values.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 图谱间接 | `b7bb4f7392f0` |
| `workline_plugins/rough_sorter/src/rough_sorter/application/wms_facts.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 176, 296, 325, 432, 438, 447 | `a5b1ded5009c` |
| `workline_plugins/rough_sorter/src/rough_sorter/application/wms_follow_up.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 图谱间接 | `ea384e2958b9` |
| `workline_plugins/rough_sorter/src/rough_sorter/application/wms_recovery.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 128, 176 | `622b0351f459` |
| `workline_plugins/rough_sorter/src/rough_sorter/facts.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 134, 195, 218 | `18aa3ef4c42b` |
| `workline_plugins/rough_sorter/src/rough_sorter/handlers/_guards.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 41, 44 | `369e05a21f41` |
| `workline_plugins/rough_sorter/src/rough_sorter/handlers/admission_decided.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 33 | `8ff2db177be2` |
| `workline_plugins/rough_sorter/src/rough_sorter/handlers/device_position_confirmed.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 36 | `b9676486a0b1` |
| `workline_plugins/rough_sorter/src/rough_sorter/handlers/material_evidence_ready.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 28, 30 | `1f43b3a1ebbe` |
| `workline_plugins/rough_sorter/src/rough_sorter/handlers/recovery_decided.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 61 | `d307721b4741` |
| `workline_plugins/rough_sorter/src/rough_sorter/handlers/replacement_plan_decided.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 42 | `dfa08bea6a36` |
| `workline_plugins/rough_sorter/src/rough_sorter/handlers/target_decided.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 36 | `6dd896a75d95` |
| `workline_plugins/rough_sorter/src/rough_sorter/handlers/transport_outcome_published.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 33 | `aea32339f580` |
| `workline_plugins/rough_sorter/src/rough_sorter/wms_requests.py` | 现有能力承接/关联迁移；按切片精确确定符号 | 42 | `48569b0bf31a` |
| `workline_plugins/rough_sorter/tests/conftest.py` | 承接测试/fixture；不得直接删除行为断言 | 23, 31 | `e6d7192a8da7` |
| `workline_plugins/rough_sorter/tests/integration/test_decision_processing_postgresql.py` | 承接测试/fixture；不得直接删除行为断言 | 49, 50, 51, 52, 68, 77, 78, 136, 137, 145, 151, 152 | `34cd677feac5` |
| `workline_plugins/rough_sorter/tests/test_material_and_admission.py` | 承接测试/fixture；不得直接删除行为断言 | 51 | `59a322caaefa` |
| `workline_plugins/rough_sorter/tests/test_plugin_startup.py` | 承接测试/fixture；不得直接删除行为断言 | 25, 26, 27, 28, 64, 65, 67, 79, 80, 81, 110, 111 | `dabbde2cd5c6` |
| `workline_plugins/rough_sorter/tests/test_start_plan.py` | 承接测试/fixture；不得直接删除行为断言 | 图谱间接 | `a50dce614e8c` |
| `workline_plugins/rough_sorter/tests/test_wms_follow_up.py` | 承接测试/fixture；不得直接删除行为断言 | 40 | `b935df736ccc` |
| `workline_plugins/rough_sorter/tests/test_wms_recovery.py` | 承接测试/fixture；不得直接删除行为断言 | 69, 85 | `5bc26ea08dee` |

## 5. 前端及 HEAVY

前端直接命中：`src/views/admin/worklines/components/WorkLineStartDialog.vue`、`src/views/admin/worklines/composables/useWorkLineStart.ts`。
另须检查 API module、OpenAPI 生成类型与 START 测试；先后端合同，再前端生成与调用方。
当前候选文件命中的 HEAVY 并集共 47 项，仅用于所有权导航，不是最终执行 manifest；最终由真实 diff 的 selector 决定。

- `tests/e2e/device_command/test_device_command_production_wiring.py`
- `tests/e2e/transport/test_transport_production_wiring.py`
- `tests/integration/device_command/test_device_command_constraints.py`
- `tests/integration/device_command/test_event_command_blocking_reconciliation_postgresql.py`
- `tests/integration/execution/test_decision_processing_postgresql.py`
- `tests/integration/execution/test_execution_constraints.py`
- `tests/integration/test_authorization_bootstrap_postgresql.py`
- `tests/integration/test_celery_async_runtime.py`
- `tests/integration/test_celery_async_runtime_postgresql.py`
- `tests/integration/test_celery_prefork_harness_cleanup.py`
- `tests/integration/test_dev_seed_initial_data_postgresql.py`
- `tests/integration/test_initial_schema_baseline_postgresql.py`
- `tests/integration/test_optimistic_lock.py`
- `tests/integration/test_transport_fastapi_lifespan.py`
- `tests/integration/test_transport_fulfillment_queue.py`
- `tests/integration/transport/test_dark_transport_loop.py`
- `tests/integration/transport/test_transport_debug_auto_run.py`
- `tests/integration/transport/test_transport_debug_reset.py`
- `tests/integration/transport/test_transport_debug_run_recovery.py`
- `tests/integration/transport/test_transport_debug_run_repository.py`
- `tests/integration/transport/test_transport_debug_run_schema.py`
- `tests/integration/transport/test_transport_debug_run_service.py`
- `tests/integration/transport/test_transport_evidence_transaction.py`
- `tests/integration/transport/test_transport_repository.py`
- `tests/integration/transport/test_transport_schema.py`
- `tests/integration/wms_adapter/outbound_picking/test_arrival_report_production_wiring.py`
- `tests/integration/wms_adapter/outbound_picking/test_batch_confirmation_postgresql.py`
- `tests/integration/wms_adapter/outbound_picking/test_departure_production_wiring.py`
- `tests/integration/wms_adapter/outbound_picking/test_inbound_batch_production_wiring.py`
- `tests/integration/wms_adapter/outbound_picking/test_issued_postgresql.py`
- `tests/integration/wms_adapter/outbound_picking/test_material_decide_production_wiring.py`
- `tests/integration/wms_adapter/outbound_picking/test_plan_delta_postgresql.py`
- `tests/integration/wms_adapter/outbound_picking/test_plan_delta_production_wiring.py`
- `tests/integration/wms_adapter/outbound_picking/test_prepare_postgresql.py`
- `tests/integration/wms_adapter/outbound_picking/test_prepare_production_wiring.py`
- `tests/integration/wms_adapter/outbound_picking/test_queue_changed_postgresql.py`
- `tests/integration/wms_adapter/outbound_picking/test_return_batch_production_wiring.py`
- `tests/integration/wms_adapter/outbound_picking/test_schema.py`
- `tests/integration/wms_adapter/outbound_picking/test_source_empty_production_wiring.py`
- `tests/integration/wms_adapter/outbound_picking/test_work_plan_production_wiring.py`
- `tests/integration/wms_adapter/test_inbound_confirmation_postgresql.py`
- `tests/integration/wms_adapter/test_transport_callback_receipts.py`
- `tests/integration/workline_capabilities/test_line_run_epoch_activation_postgresql.py`
- `tests/integration/workline_capabilities/test_unfinished_execution_snapshot_postgresql.py`
- `tests/integration/workline_capabilities/test_workline_configuration_postgresql.py`
- `tests/integration/workline_capabilities/test_workline_start_postgresql.py`
- `tests/mock/test_wms_transport_mock_server.py`

候选未映射项（不得臆造 NONE；先判定是否实际修改，再闭合映射）：

- `src/app/runtime/orchestration/services/trace/trace_resource_view_builder.py`
- `src/app/workline/services/plane_service.py`

## 6. 实施门禁进度

- [x] CRITICAL 影响范围确认；实际改动方法的 impact 或当前精确调用链记录按内聚切片维护。
- [x] 上述两个已实现 WMS wire 的逐项目标合同已冻结；站点事实和入库跨执行 FIFO 仅门禁 S3B。
- [x] 按直接测试导入和 fixture/helper 闭合各内聚切片消费者，并完成独立断言语义审阅。
- [x] 新/删文件最终 HEAVY mapping、迁移链及必需真实 worker 验证按最终快照执行。
- [ ] 后端 PR 合并并取得 clean develop 后，执行前端正式 `contract:freeze` 和生成类型同步。

S1 后端 Transport 箱码切片聚焦验证 414 passed；前端 S1 + START 在真实候选合同下 94 tests passed、type check 通过，后续 START 配置调整的相关 16 项再次通过。
前端正式冻结要求后端 clean develop，当前条件未满足；候选补丁仅作开发验证，未改写正式来源记录，不代表前后端已正式同步。
资源快照与验空挂载已去掉 bin_id 别名，资源域 46 passed。
独立评审发现的历史 OLD_OUT 围栏和结果发布竞态已修复；同一 reviewer 完成意见闭环与当前完整快照复核，无新增可行动代码问题。
最终生产提交：`176971bab862cdbcd65d3f04ac7fa7f1a3c7087b`，源码树：`4aaf8fb413d5dbbbbf7a78969a0f68c2673b9709`。本节记录最终证据，前文初始清单及指纹保留原值。
WMS HTTP 返回时 owner 已失效的结果仍保存 Evidence 并进入 `RECONCILING`；START 接受合法初始 `version: 0`。
Transport 结果 publisher 复用宿主 `db`，避免 worker 单连接池内二次申请连接超时；任务行锁保持到 Evidence 同事务提交，提交后才 enqueue。

| 最终验证 | 结果 | 本机证据 |
| --- | --- | --- |
| QUALITY | 3200 passed、5 skipped，门禁通过 | `/tmp/wes-ship-publisher-commit-20260907.log` |
| 核心 HEAVY | 最终 selector 56 个文件，517 passed、零跳过 | `/tmp/wes-ship-heavy-complete-20260907.log` |
| 迁移 | 新鲜空库从 base 完整升级到 `93deacda8c9c` 成功 | 专属 PostgreSQL 干净逻辑库 |
| 插件 FAST | rough_sorter 173 passed；manual_bin_processing 20 passed | 插件各自 FAST 入口 |
| rough_sorter PostgreSQL | 7 passed | `/tmp/wes-ship-plugin-pg-publisher-20260907.log` |
| rough_sorter 镜像 E2E | 两个独立组 10 + 2 = 12 passed、零跳过；镜像 `wes-backend:ship-176971bab862` 绑定上述生产提交及源码树 | `/tmp/wes-ship-plugin-e2e-complete-a-20260907.log`、`/tmp/wes-ship-plugin-e2e-complete-b-20260907.log`；`reports/rough-sorter-e2e.xml` |
| 独立 Review | CLEAR，无待处理 finding | `/tmp/wes-ship-review-20260907.md` |

测试环境：专属 PostgreSQL/Redis Compose 项目使用独立逻辑库；核心与插件验收分开运行。FAST 的 5 项跳过分别为 4 项需真实 API 凭据的签名检查及 1 项需预构建生产镜像的检查，不作为通过证据。
QUIT countdown retry 间歇问题复现后，测试补齐了 `RETRY` 状态前置条件；原退出时限、同 task id 接管及幂等终态断言保留，最终真实 worker 验证通过。此次修改的是测试前置条件，生产 Celery 信号与停机机制未改动。

本机 ASGI/浏览器已验证 Swagger、WorkLine 空页和 Transport diagnostics smoke。测试镜像另观察到 Loguru 文件轮转 `OSError 22`，不在本次修复范围；不据此宣称所有日志无误。
正式交付剩余边界：前端 `scripts/lib/backend-checkout.ts` 要求后端是干净的 `develop`；用户已确认先交付后端 PR，再执行前端正式 `contract:freeze`。候选前端补丁只完成开发验证，尚未正式冻结；S3B 站点等待/FIFO 仍为后续业务。本轮尚未 Merge、Deploy、供应商一致性或现场业务验收，本机测试与 smoke 不替代这些验收。
