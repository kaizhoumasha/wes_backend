# 人工拣料旧排空异常退出实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: 使用 `superpowers:executing-plans` 按本计划顺序实施。推荐主 Agent 原生执行；任务共享 execution、成员关联及工作线事务，不拆 Subagent 或 worktree。每步用 checkbox 记录结果。

**Goal:** 独立后续 PickingTask 不再被旧 drain 的整线 reservation 阻塞；符合条件的旧排空自动异常退出，原执行身份、可靠义务及迟到事实保留。

**Architecture:** 在 manual-picking 插件增加排空 execution 与必要的原 Return 成员关联，复用现有 Repository、driver、WorkLine 锁及宿主可靠交付。请求创建、成员冻结及资格校验同事务；事实按原归属消费，正常完成和异常退役分别决定业务重试资格。宿主与 SDK 不导入具体插件，不新增外部 operation、状态机平台或物理容量围栏。

**Tech Stack:** 现有 Python 3.13+、SQLModel/SQLAlchemy、PostgreSQL、Alembic、pytest/pytest-asyncio、Celery/Redis；不增加依赖。

**Spec:** [已确认 SPEC](../specs/2026-10-09-manual-picking-drain-exception-retirement-design.md)，D1–D5 全部批准。工程评审 T1–T4 保留原编号；本计划任务 A–F 是实际执行切片，不另开设计。

**状态与授权:** PLAN READY，未执行。当前授权仅制定计划；不修改生产代码、测试、配置、迁移或服务器，不 Commit/Push/Ship/Deploy。计划里的命令都是后续实施步骤，不是本轮通过证据。

## Global Constraints

- execution 状态仅 `ACTIVE`、`COMPLETED`、`EXCEPTION_RETIRED`，不改变 Transport/WmsConfirmation 枚举。
- reason_code 仅 `DRAIN_ACTION_TERMINAL_EXCEPTION`、`DRAIN_RESULT_OVERDUE`、`DRAIN_RESULT_UNKNOWN`，优先级依此顺序；同类 blocker 按冻结执行身份稳定排序。
- 超时只触发本地异常退出，不写 Transport FAILED；新任务不证明旧动作终态。只有 §4.2 的 READY、有效独立后续任务和确定 blocker 全部成立才退役。
- 已持久化可靠义务继续沿原 identity/正文技术恢复；匹配迟到事实先留存，不能覆盖更新的因果事实。
- 正常离场任务不会被 RCS 取消；保留现有对象级异常恢复，不设计新的正常取消流程。
- `COMPLETED` 不重开排空；原离场业务重试仍需直接 Requirement 有效、权威目标未满足、原 Action 明确终态。`EXCEPTION_RETIRED` 禁止本次 drain 的业务后继及业务重试。
- 原 `BinLineReturn.id` 关联从创建 return_batch 义务时生效；两个请求入口都阻止 ACTIVE/退役的未决原成员被其它批次再次申请。同箱码合法新轮次使用新 ID。
- ACTIVE 的确定 `NO_BATCH` 依原合同换面继续，不变成同面轮询；未知响应保留原身份及成员依赖。退役的未闭合原成员继续保留归属。
- 同 rack/face 既有分配门禁保持；同一未闭合物理 FIFO 不越序，不能用 SQL 排除旧成员后跨越队头。其他独立物理动作仍可提交 ECS/RCS。
- 本地退出不证明地点 EMPTY、物理成功或 WMS reservation 已释放；不增加人工按钮、填单、补报或数据库删除恢复路径。
- 使用 `timezone.now_for_db()` 保存时间；模型沿 `EnterpriseMixin`/`DataTableMixin`、BIGINT 外键规范，不重复审计/乐观锁 mixin。
- 插件 FAST、PostgreSQL、真实 worker 由插件测试拥有并单独运行；核心 QUALITY/HEAVY 不替代插件验证。

## Review Focus

1. 请求提交后、响应前跨 rack/face 并发：同一 Return.id 只进入一项未决分配义务；B 的 PostgreSQL 竞争测试证明。
2. 退役与迟到 READY 同时发生、货架面被新任务复用：按原 execution 消费，不产生新 BIN_MOVE；B/D 的竞态测试证明。
3. 正常完成被终态过滤误伤、取消结果到达但目标已满足：保留原离场已有异常恢复，已满足目标不重试；C 的状态对照测试证明。
4. worker 在提交后崩溃、通知丢失或旧结果迟到：扫描能重新发现 ACTIVE，退役不反转，原义务不丢失；E 的真实 worker 测试证明。
5. 存量迁移身份不唯一、整体闭合历史删除、同箱码重新使用：不猜关联，不复活历史前驱，不清除未闭合依赖；A/F 的迁移及清理测试证明。

## 基线、保护现场与执行顺序

仓库根：`/Users/kaizhou/codeDev/wes_backend`；分支 `develop`；本轮读取 HEAD `99ffe143c4ad32a5851728963623e1b3cd72669d`。磁盘 Alembic 唯一 head 为 `a16c9b2e7d04`。实施开始重新确认基线，普通代码漂移记录到清单；发现与已批准合同矛盾时先明确矛盾，不能静默改设计。

| 已有文件 | 当前状态 | SHA-256 |
| --- | --- | --- |
| `TODOS.md` | 用户已有 modified | `989b42c9c71d38b79a39818756420b498d262a84048db05ef33b5bfef027e8d5` |
| `tests/runtime/execution/test_rack_inbound_window.py` | 用户已有 modified，本任务不改 | `de8f987e108bed68babf93272a5b9554fc6bfdf49f529ef37c371af0975cf537` |
| `workline_plugins/manual-picking/tests/test_rack_cycle_postgresql.py` | 用户已有 modified；与后续测试范围重叠，保留原补丁 | `11a87d7bc3e5b8fb0d109d12bfec4d4d218c4fb9355cfce4b20c9da612472fb3` |
| 已确认 SPEC | untracked，评审成果保留 | `ccc4c16e9c30750cb1b04b1e7837faf7b51750b0fefb4a99bb3cfc1fda5f8e5f` |

制定计划的收尾检查中，上表四个既有文件已转为 staged，内容 SHA-256 均未变化；本轮没有执行 git add、Commit 或修改 index，不恢复或覆盖该暂存状态。表中状态是开始时基线，实施前以最新 status/HEAD 为准。

单一 owner 顺序执行 `准备 → A → B → C → D → E → F`。A 的 schema/当前执行接口先完成；B 将 T1/T3/T4 一起传播；C 完成 T2；D 才拆除整线注入并接通退出扫描。各切片结束只跑相应测试，最终 F 闭合全集。不在未知签名传播尚未完成时反复运行整目录测试。当前不创建工作区或修改上述文件。

## 文件与职责清单

以下插件路径以 `workline_plugins/manual-picking/` 为根；“新增”是后续实施产物。

| 文件 | 动作与职责 | 所属切片 |
| --- | --- | --- |
| `src/manual_picking/application/drain_execution_model.py` | 新增 execution 与必要的原成员关联模型；单一状态真源 | A/B |
| `src/manual_picking/application/drain_execution_repository.py` | 新增当前 execution、原关联、关闭及原候选资格查询；不做外部 I/O | A/B |
| `src/manual_picking/application/drain_repository.py` | current 从当前运行记录选取；保留原 Evidence 定位和 Transport 身份核验 | A/C |
| `src/manual_picking/application/drain_flow.py` | 请求冻结原关联、退役条件重验；保持正常 READY/WAIT/NO_BATCH 编排 | B/D |
| `src/manual_picking/application/batch_flow.py` | 两个 return_batch 分支核验原成员资格；不改变 inbound 路径 | B |
| `src/manual_picking/application/scan_flow.py` | READY 同事务登记 execution；原批次归属消费及两终态恢复资格 | A/B/C |
| `src/manual_picking/application/batch_result.py` | 消费 drain 结果时按冻结原 Return 身份核验；已创建动作不重复创建 | B |
| `src/manual_picking/application/batch_driver.py` | 在现有 WorkLine 扫描事务内退役/正常闭合，统一停止后继 | A/D |
| `src/manual_picking/application/__init__.py` | 导出新增 Repository；不添加无用聚合导出 | A |
| `src/manual_picking/application/bin_line/return_repository.py` | 保持原物理 READY/FIFO 查询与签名；仅需要按冻结 IDs 锁读时添加限定方法 | B |
| `tests/test_drain_execution.py`、`tests/test_drain_execution_postgresql.py` | 新增模型/Repository/资格及专属迁移证明 | A/B/F |
| 既有 `tests/test_drain_repository.py`、`test_drain_flow.py`、`test_batch_flow.py`、`test_scan_flow.py`、`test_source_progression.py`、`test_transport_outcome.py` | 对应对象资格和回归断言 | A–D |
| 既有 `tests/test_rack_cycle_postgresql.py`、`test_business_loop.py`、`rack_cycle_support.py` | 复用真实装配、独占库、锁序与 worker harness；只增加本能力必要夹具 | B/D/E |

宿主/部署及治理文件：

| 文件 | 动作与边界 |
| --- | --- |
| `deployment/plugin_models.py` | 显式注册新插件模型；include_plugin_table 与模型加载名单同步，插件未启用时不纳入 metadata |
| `deployment/plugin_composition.py` | 注入同一 DrainExecutionRepository 给插件消费者；去掉 manual 的 `drains.is_reserved` 赋值；保持通用 blocker 端口 |
| `src/app/wms_integration/return_buffer_drain/service.py` | 增加按 operation 定点读取；复用当前 typed 解码、Evidence/摘要/owner 校验；不新增 operation 或通用调用入口 |
| `tests/contracts/wms_adapter/return_buffer_drain/test_service.py` | 只证明定点读取的基础身份与发布资格，不导入插件 |
| `tests/deployment/test_core_plugin_installation.py` | 基础装配、插件选择、保留通用端口；具体退出流程由插件证明 |
| `tests/runtime/wms_integration/outbound_picking/test_prepare_service.py`、`tests/contracts/wms_adapter/outbound_picking/test_plan_activation_service.py`、`tests/runtime/execution/test_fact_processor.py` | 既有通用 blocker 测试保持，防止为拆一个注入而删公共能力 |
| `migrations/versions/` 下 Alembic 生成的新 revision | 新表/约束/索引、存量运行链初始化；实际 revision ID/文件名在 A 生成后立即写入执行清单，down_revision 从实施时唯一 head 取值 |
| `docs/architecture/heavy-test-impact.toml` | 新迁移精确映射；现有宿主/部署映射复用；插件测试不加入核心 selector |
| `docs/contracts/wms-outbound-picking-task-integration-requirements.md` §9.2.3、`docs/contracts/wms-manual-outbound-picking-integration-requirements.md` 对应引用、`docs/architecture/file_index.md` | 只同步本能力合同及索引；不改 wire 示例或增加 API |
| `TODOS.md` | 实施完成后只更新本能力既有条目，保护当前 dirty 改动；计划阶段不改 |

无需修改 `src/wes_plugin_sdk/`、wire/OpenAPI、Transport/DeviceCommand 枚举、Celery 注册/队列/Beat 或前端。宿主 prepare/activation/FactProcessor 生产实现当前可保留：变化在 Composition 注入，只有源码证明确有遗漏才限域修改并补清单。

## 准备：冻结高风险变更面

- [ ] 记录 git status、HEAD、staged/unstaged/untracked 和相关文件指纹；与上述基线比较，保护原 dirty。若直接追加现有 dirty 测试无法保持单一 owner，执行时再使用独立 worktree，不先覆盖或重置。
- [ ] 按项目 GitNexus 流程检查索引；只在 stale 时刷新一次。对计划内函数 upstream impact 批量查询并缓存，覆盖 current/for_evidence、drain/batch 请求、scan 结果/重试、driver、reader 和 Composition。HIGH/CRITICAL 说明调用面，不自动增加授权门槛。
- [ ] 固定变更清单：直接调用者、fixture/fake 消费者、必要归一化/摘要身份、测试 owner、HEAVY mapping、migration/索引、验证环境及无关 dirty 指纹。定点 reader 的直接消费者包含 drain_repository；既有 transport_outcome 使用 read，不改变物理结果发布资格。
- [ ] 一次枚举 `.current`、`for_evidence`、`ready_prefix_for_update`、两个请求分支及 `_resolve_drain_decision` 的消费者；只给本切片所需 fake 增加 ID/关联，不把 bin_code 当 Return 身份。
- [ ] 准备独占 PostgreSQL/Redis；实施阶段才启动。记录 readiness、插件启用值、数据库/worker 作用域；没有环境时先完成可做的 FAST，不能运行一轮仅得到 skip 的名义集成测试。

## A：当前排空执行记录、正常闭合与 schema（G1/G2/G12）

**Files:** 文件清单中的 execution 模型/Repository（包括 B 定义的原成员关联 schema）、drain_repository、scan_flow、batch_driver、application 导出、定点 reader、deployment/plugin_models、新 migration 与对应测试。

**Interfaces:** 新增接口属于插件 application，参数来自已验证 typed record，不解析原始 JSON。

```python
# drain_execution_repository.py：均为 async methods
ensure_ready_in_session(db: AsyncSession, *, decision: ReturnBufferDrainRecord) -> ManualPickingDrainExecution
active_for_workline(db: AsyncSession, workline_id: int) -> ManualPickingDrainExecution | None
for_evidence(db: AsyncSession, *, workline_id: int, evidence_id: int) -> ManualPickingDrainExecution | None
close_in_session(db: AsyncSession, execution: ManualPickingDrainExecution, *, status: Literal["COMPLETED", "EXCEPTION_RETIRED"], closed_at: datetime, reason_code: str | None = None, blocker_client_request_id: str | None = None, trigger_picking_task_id: int | None = None) -> bool
# src/app/wms_integration/return_buffer_drain/service.py，现有 reader 增加
for_operation(db: AsyncSession, *, workline_id: int, operation_id: str) -> ReturnBufferDrainRecord | None
```

保留 `DrainRepository.current(db, workline_id) -> ReturnBufferDrainRecord | None`、`for_evidence(db, workline_id, evidence_id)` 和 `transport` 的既有签名；构造函数增加 keyword-only `executions` 注入，不增加兼容 wrapper。

- [ ] **RED：** 在 `test_drain_execution.py`/`test_drain_repository.py` 加 `test_ready_creates_execution_once`、`test_terminal_execution_never_becomes_current`（两终态参数化）、`test_no_fallback_to_closed_predecessor`。核心断言：

```python
assert first.id == replay.id
assert first.status == "ACTIVE"
assert terminal.status in {"COMPLETED", "EXCEPTION_RETIRED"}
assert await repository.current(db, workline_id) is None
```

另在核心 reader 测试增加 `test_for_operation_validates_frozen_owner_and_evidence`：错误 owner、内容漂移拒绝，未发布结果不冒充可用 READY；查询仅命中原 operation，不返回更晚记录。

- [ ] **核验 RED：** 执行下方 FAST 命令 1；失败必须是缺少新能力/错误 current 行为，不是依赖或导入配置。
- [ ] **DEV：** 同一 schema 切片创建 `ManualPickingDrainExecution` 与 B 定义的 `ManualPickingDrainReturnMember` 两个模型/表；B 只实现成员读写与消费，不再追加另一轮 schema。表名分别为 `wes_biz.manual_picking_drain_executions`、`wes_biz.manual_picking_drain_return_members`，与 deployment 注册名单一致。execution 复用 SPEC §5 的字段；drain_operation_id 唯一，索引支持 workline/status 与精确 Evidence。状态、关闭时间/原因/触发引用合法性用约束表达；ACTIVE 关闭字段为空，正常关闭不带 blocker/trigger，异常关闭带已确认引用与固定 reason。
- [ ] **DEV：** READY 在 `ManualPickingScanFlow.apply_in_session` 的原 WorkLine 事务内登记 execution；幂等重放不改终态。定点 reader 复用原校验；current 只从 ACTIVE execution 获得 READY，不再用历史尾部推导或回退前驱。WAIT/发送未决沿原可靠记录恢复，不能被误登为 READY execution；只读取仍有直接运行依赖的记录，不借闭合 READY 恢复 current。
- [ ] **DEV：** 正常闭合从 driver 按已批准的各 reservation 离场接纳语义写 COMPLETED，不改 Transport。更新模型注册与 include_plugin_table；新增 Repository 在 application 导出。
- [ ] **DEV：** migration 增加 schema 及存量初始化。迁移前限定原 WorkLine/drain/decision/binding/未闭合义务链，冻结唯一运行身份；明确各架离场接纳才写 COMPLETED，其余保留 ACTIVE。关联只由唯一可证明的原请求、Evidence、Return/Transport binding 建立；原 Return 身份无法唯一确定时 preflight 报错，不按历史时间/当前箱码猜测，不在关联缺失时启用新候选选择。此错误通过现有软件证据定位，不增加现场填单或人工确认流程。
- [ ] **GREEN：** FAST 命令 1；独占库增加 `test_execution_constraints_and_identity_concurrency`、`test_runtime_chain_backfill_preserves_unclosed_references`、`test_upgrade_downgrade_reupgrade_drain_schema`，验证幂等、约束、注册、存量身份及迁移。整体闭合记录才可清理；未闭合原 Transport/原离场恢复所需引用继续保留。

**完成条件:** READY current 不依赖已闭合历史；新增 execution 不要求 WorkLine 新唯一限制，若迁移出现多个无法证明唯一当前 owner 的运行候选，按原身份核验报错，不能用 MAX(id)/最近时间选一个或复活前驱。WAIT/技术义务保留；新表及迁移在干净临时库可验证，尚不拆整线注入。

## B：T1 + T3 + T4 请求身份、原成员及退役响应（G7–G9/U3）

**Files:** execution 模型/Repository、drain_flow、batch_flow、scan_flow、batch_result、必要 Return 定点读取、Composition 注入；插件 test_scan_flow/test_batch_flow/test_drain_flow 及 PostgreSQL 测试。

**Interfaces:** 同一 Repository 维护本能力关联，不另建预占服务。关联 schema 已在 A 一次创建；本切片实现其读写。

```python
# DrainExecutionRepository 的 async methods
freeze_return_members_in_session(db: AsyncSession, *, execution_id: int, return_batch_operation_id: str, return_ids: tuple[int, ...]) -> None
members_for_return_batch(db: AsyncSession, *, workline_id: int, operation_id: str) -> tuple[ManualPickingDrainReturnMember, ...]
eligible_return_prefix(db: AsyncSession, *, workline_id: int, rows: tuple[BinLineReturn, ...]) -> tuple[BinLineReturn, ...]
# ReturnRepository 必要的定点读取，保留 ready_prefix_for_update 原签名
by_ids_for_update(db: AsyncSession, *, workline_id: int, return_ids: tuple[int, ...]) -> tuple[BinLineReturn, ...]
```

`ManualPickingDrainReturnMember` 与 execution 放同一模型文件：execution_id、return_batch_confirmation_id、return_id、sequence_no。FK 指向原 execution/WmsConfirmation/BinLineReturn，operation_id 从原 Confirmation 取得，不重复保存正文、结果或箱码；每个请求的 sequence 与 Return 唯一。按固定 `BIN_RETURN_BATCH_OPERATION + operation_id` 解析 Confirmation 后关联，禁止靠当前 rack/face 推断。sequence_no 只保留原候选顺序，不引入新 wire。

- [ ] **RED / T1：** `test_retired_drain_ready_is_consumed_without_bin_move`、`test_old_batch_owner_survives_reused_rack_face`：原 execution 退役后返回 APPLIED 的业务消费结果，Evidence 可靠留存，Transport.create 未调用；重复消费不新建、不改写已有动作，不反复进入无意义 RECONCILING。
- [ ] **RED / T3：** `test_drain_request_freezes_return_execution_ids`、`test_retired_pending_members_are_not_selected_by_new_batch`、`test_same_bin_new_return_id_does_not_inherit_old_membership`。冻结顺序与原请求一致，旧关联只约束原 ID，不拦合法新 ID；`test_same_batch_replay_rejects_changed_return_ids` 验证同 operation 的关联只能幂等重放同一执行成员，换 ID/顺序明确冲突。
- [ ] **RED / T4：** `test_active_pending_drain_member_is_not_requested_on_other_face` 同时覆盖 drain 请求与 batch_flow 的两个 return 分支；既有同面门禁仍有效；inbound 可独立继续。用调用记录断言同一 Return.id 未进入第二项新请求，不用“队列非空”冒充分配证明。
- [ ] **核验 RED：** FAST 命令 2 的新增节点；三类缺口必须分别出现预期失败，再一起实现共享关联。
- [ ] **DEV：** 在已持有 WorkLine 锁的 `return_in_session` 创建可靠义务后同事务冻结原 execution/Return.id。scheduler 原返回值为 None，不为此改其签名或建立新 outbox；Repository 从同事务原 Confirmation 定点关联，commit 前禁止外部发送。
- [ ] **DEV：** `eligible_return_prefix` 读取有界 READY 前缀的必要关联：原 drain 请求未知/未决时（ACTIVE 或退役）不给其它批次再次申请；退役原未闭合成员保持归属；ACTIVE 的确定 NO_BATCH 不锁死后续合法换面。遇到被直接依赖阻止的成员只保留其前面的可执行前缀，不跳过去选后面的同一 FIFO 成员；不添加 Return 枚举，不更改物理到位顺序。
- [ ] **DEV：** `batch_flow.advance_in_session` 的 no-progress/allow_inbound=False 分支和正常 interleave 分支都执行相同资格查询；drain 请求也执行。统一注入同一 Repository，不复制资格规则。`decide_in_session` 的数量统计、active_rack_face 与 driver 的 READY 预检查核对语义：不得把已属旧未决请求的成员算成一次等价新分配，不改变原计数/容量合同。
- [ ] **DEV：** scan_flow 先按原 batch operation 解析关联及 execution，再决定消费资格。退役结果在当前位置/新 source-face 校验之前结束后继创建；ACTIVE 路径仍核验原进场事实及冻结候选，batch_result 对 drain 请求按原 Return.id 读取，禁止当前同箱码替代。无关联的普通 PickingTask 批次沿原合同，已知原 drain 关联缺失/漂移明确对账，不降级为普通批次。
- [ ] **GREEN / PostgreSQL：** `test_cross_face_requests_serialize_original_members`、`test_request_and_membership_rollback_together`、`test_retirement_and_ready_response_serialize`、`test_active_no_batch_releases_request_for_next_face`：用现有 locked_line 与 pg_blocking_pids 证明竞争次序；第二请求不能重复使用原 Return，回滚后无义务或孤立关联。验证 NO_BATCH 不同面继续、未知响应不换 identity/依赖；原同面终态/已知终位门禁保持。
- [ ] **GREEN：** FAST 命令 2 与集成命令 4 对应节点；新增纯模型/查询测试放 test_drain_execution.py，真实事务放 test_drain_execution_postgresql.py；保留既有 test_rack_cycle_postgresql.py 的 Confirmation/WorkLine 锁序回归。

**完成条件:** T1/T3/T4 同一事务切片闭合；请求、退役和迟到消费均用原身份。旧未知搬运不被换身份等价重发。

## C：T2 正常完成与异常退役的原离场恢复（G10/U4）

**Files:** drain_repository、scan_flow `_resolve_drain_decision`/`_retry_terminal_rack`；插件 test_scan_flow、test_drain_repository、test_transport_outcome。

**Interfaces:** 沿 A 的 `for_evidence` 定位原 execution，沿现有 `_goal_already_satisfied` 和 ReliableRackTransportCreator；不加新的恢复端点或 wrapper，不改变原结果 publisher 的身份合同。

- [ ] **RED：** `test_completed_drain_preserves_original_departure_retry`、`test_retired_drain_never_retries_rack_action`（进场/旋转/离场参数化）、`test_completed_departure_with_satisfied_goal_does_not_retry`、`test_terminal_drain_replay_does_not_reopen_execution`。断言：

```python
assert retired.status == "EXCEPTION_RETIRED"
rack_creator.create.assert_not_awaited()  # retired 或目标已满足
assert retried_transport_id != original_transport_id  # 仅合法明确终态业务重试
assert execution.status == "COMPLETED"  # 原离场恢复不重开整次排空
```

- [ ] **核验 RED：** FAST 命令 3 的新增节点；保留当前换 current 后原离场恢复和目标核验的已有测试，不直接改成“不重试”。
- [ ] **DEV：** 事实定位与业务资格分开。原 Evidence 仍可定位；EXCEPTION_RETIRED 拒绝业务重试，COMPLETED 仅允许原离场已有恢复。目标已满足、直接 Requirement 无效或原动作未明确终态不新建 Action；原技术重试继续归基础可靠执行层。
- [ ] **GREEN：** FAST 命令 3；原 TransportOutcomePublisher 对退役/正常完成仍接受匹配结果，原因果投影不覆盖更新事实。未完成引用不因 execution.closed_at 被清理。

**完成条件:** T2 无正常取消流程扩展；正常原对象恢复与异常停后继各有独立证据。

## D：异常触发、持久扫描和独立任务准入（G3–G6/U1/U2）

**Files:** drain_flow、batch_driver、execution Repository、deployment/plugin_composition、test_drain_flow/test_source_progression/test_rack_cycle_postgresql；三个宿主通用 blocker 测试保留。

**Interfaces:** `ManualPickingDrainFlow.retire_exception_in_session(db: AsyncSession, line: WorkLine, now: datetime) -> bool`。调用方持有 WorkLine authority 锁；方法只写本地 execution，不取消外部动作。触发任务与 blocker 的限定查询由插件 Repository 拥有。

- [ ] **RED：** `test_exception_retirement_requires_ready_independent_task_and_blocker`：逐一覆盖 QUEUED/PREPARING/EXECUTING 的有效后续任务、三类 reason 和期限边界。`test_drain_does_not_retire_without_current_basis` 覆盖无任务/任务取消/未 READY/期限前/正常进展/发送未知未接纳，全部不退出。
- [ ] **RED：** `test_independent_task_prepares_while_old_drain_is_active`、`test_plan_and_facts_are_not_blocked_by_manual_drain` 在插件真实 Composition 验证三入口；同对象依赖只停该步骤。`test_retirement_stops_uncreated_actions_on_all_reserved_racks` 覆盖多架多面，已提交的身份/正文保留。
- [ ] **核验 RED：** FAST 命令 2 中 test_drain_flow/test_source_progression 节点；三入口真实装配使用集成命令 4，不把宿主 fake 当 Composition 证明。
- [ ] **DEV：** `_advance_drain` 在 decide/后继前调用退役重验；扫描已有 `PickingTaskPlanActivationService.activate_batch` 的活动插件工作线，不依赖有 EXECUTING 任务或 Redis 通知，复用 `advance_completed_in_session`。不得新增 Beat 或仅靠内存标志。
- [ ] **DEV：** 同事务重读 execution、后续任务的有效/取消事实、原直接依赖及 blocker；Task Repository 现有 get_active_for_workline 只含 PREPARING/EXECUTING，不能用它漏掉 QUEUED，插件需限定查询该线三种状态。任务按原队列优先序/唯一 ID 稳定选择，blocker 按 reason 优先级和冻结身份稳定选择。期限使用原 result_deadline_at，不新设超时参数：对于仍 ACCEPTED 的动作，只有 `now > result_deadline_at` 且无匹配最终结果才命中 overdue，期限前/恰好等于期限均不命中；已经 RECONCILING 的确定未知走 unknown 条件。blocker 必须来自原 drain binding 或 B 的原 return_batch 关联，不能由同架其它独立任务/位置投影推断。明确失败仍属于现有可业务重试分支时不能命中 terminal-exception，只有插件按原直接依据确认不再自动业务重试才退出；增加这一反向测试。
- [ ] **DEV：** 写 EXCEPTION_RETIRED 时冻结 closed_at/reason/blocker/trigger，停止原 drain 未提交的进场/旋转/返料/离场及业务重试；窗口解除沿现有原绑定业务窗口完成，不把非到位事实写成空位，不释放 WMS reservation，不批量关闭 Transport。正常 drain 与独立任务保持共存。
- [ ] **DEV：** 删除 Composition 中 manual `prepare_workline_reserved = drains.is_reserved` 注入；保留 prepare/plan activation/FactProcessor 的通用 workline_reserved 参数和其它消费者。唯一源码现状无其它生产调用者后才移除 is_reserved 这个失去用途的插件方法；相应旧导出/注释/测试更新，不删除公共端口。
- [ ] **GREEN：** PostgreSQL `test_retirement_rechecks_cancelled_task_and_arriving_result`、`test_duplicate_scans_close_once_with_stable_reason`、多架测试与三入口验证；FAST 命令 2、宿主命令 6。真实查询验收 trigger/blocker/reason，重复扫描不反转、不重复计数。没有匹配最终结果与“已有明确终态但尚未消费”分开：先重放确定 Evidence，不把可重放确定事实误判成超期未知。

**完成条件:** 退出可由持久扫描重发现；已拆的整线注入不会把未知原对象依赖也拆掉。

## E：Mock good case / bad case、真实 worker 与历史清理（G11/U1–U4）

**Files:** 插件 test_business_loop.py、test_drain_execution_postgresql.py、必要 rack_cycle_support.py；生产扫描复用 D，不新建 worker wiring。

**Interfaces:** 复用 `rack_database`、`worker_for`、`TransportBrokerWorker`、`BusinessServer` 和既有 ACTIVATE/DISPATCH/APPLY/PUBLISH/EXECUTE 任务名。只有本能力必需的受控响应/崩溃夹具可增加；不写新的 worker/outbox 框架。

- [ ] **RED：** `test_real_worker_retires_overdue_drain_and_prepares_next_task`（U1）；`test_real_worker_preserves_normal_drain_with_independent_task`（U2）；`test_real_worker_consumes_late_ready_without_reallocating_old_members`（U3）；`test_restart_and_missing_wakeup_preserve_retired_state_and_obligations`（U4）。分别断言新任务准备状态、旧 execution 状态、原 Transport/Confirmation identity、无新增旧后继及实际 worker task 执行记录。
- [ ] **核验 RED：** 实施到相关切片时逐节点验证缺失行为；若 A–D 已使某个 E2E 首跑通过，记录已有行为证据，不制造失败或改断言，保证 A–D 已有对应 RED。
- [ ] **GREEN：** 集成命令 5：提交后崩溃再起 worker；不发送加速通知，调用现有持久扫描；延迟匹配响应然后重放；两种竞态提交顺序分别验收。用实际任务结果和数据库对象证明，不用 HTTP 200/ACK/health 代替完成。
- [ ] 按下方 Mock 矩阵完成 good case 和 bad case；复用 A–D 的主要测试 owner，E 只证明真实装配、持久扫描及 worker 链路，不复制一套 Repository 全矩阵。既有 Mock 基线使用命令 8–9；新退役场景使用命令 1–5 中对应节点。每项分别记录 PASS/FAIL/SKIP/未执行，不用旧基线通过替代新功能验收。
- [ ] **GREEN：** `test_purgeable_drain_history_is_not_runtime_dependency`：清理测试库中已整体闭合链后创建新任务及同码合法新 Return，运行 current/请求/恢复；另断言未闭合原义务及原离场恢复引用仍存在。WMS reservation 拒绝由 BusinessServer 返回真实拒绝语义，证明不伪造可用容量。
- [ ] 退出 worker/server、关闭连接后才销毁本例独占库；沿 close_transport_test_resources 清理，不影响其它 Compose 项目。

**完成条件:** 插件 worker 证据独立于核心 HEAVY；本轮不声称 RCS/ECS 现场验收。

## Mock good case / bad case 验收矩阵

本节按用户确认补入计划，**实施后执行，本轮未运行**。WMS 决策及 Transport 接纳/权威结果由既有 Mock 端口注入；业务 Service、Repository、Evidence 应用和持久扫描使用生产路径。FAST 验证局部分支，PostgreSQL 验证事务/唯一约束/竞争，真实 worker 验证任务交付与重启。三类证据分别记录，不能互相替代；不增加通用 replay runner 或新的模拟协议。

**既有基线与数据来源：** [GOOD_CASES](../../../tests/mock/GOOD_CASES.md) 的 KT16 六箱现场归档只用作场景和数据取样依据，尚未接入可执行 replay runner。`tests/mock/data/kt16_manual_picking_good_case.json` 是另一历史任务的 fixture；命令 8 的既有测试只验证 32 项 Mock 决策、重复响应和事件转发/解析，回调发送被替换为受控端口，不能证明真实 WES 业务闭环。真正的正常排空基线使用 `test_business_loop.py::test_completed_task_drains_fifo_through_real_worker`。既有 [BAD_CASES](../../../tests/mock/BAD_CASES.md) 的异常回归用命令 9 定点执行；直接构造状态的局部测试不等于丢回调全链路验收。

新案例复用 `test_business_loop.py` 的 `BusinessServer`、`worker_for`、`callback` 和 `rack_cycle_support.py`。每例在隔离库中生成自己的 WorkLine、PickingTask、execution、operation 和 Return.id；不向服务器重发历史请求。超期分支使用受控时间与原 `result_deadline_at`，不靠长时间等待；完整流程用匹配的 typed Evidence 推进，不能把手工写成功状态作为验收证据。正常离场不注入 RCS 取消；异常恢复只对照现有合同明确允许的终态分支。

| 案例 | 准备与 Mock 输入 | 必须验收的行为 | 对应要求、主要 owner / 命令 |
| --- | --- | --- | --- |
| M-G1 正常排空完成 | READY 排空，多箱按首次 SCAN4 顺序；逐个注入匹配的到位/放箱结果，离场正常接纳；再重放同身份结果 | 正常完成条件满足后 execution 仅一次 COMPLETED；每个原动作身份唯一，重复结果不新增 Transport；仅 ACK/ACCEPTED 不伪造物理完成 | G2；A 的正常闭合测试 + E 既有 `test_completed_task_drains_fifo_through_real_worker` / 1、5 |
| M-G2 正常 ACTIVE 与独立任务并行 | 原 drain 尚在正常期限内，新有效 PickingTask 到来；原对象仍有直接搬运依赖 | 不退役；新任务正常准备/plan/fact 推进，只有原依赖步骤等待；三入口均不恢复整线 reservation | G5/U2；D 三入口真实装配 + E `test_real_worker_preserves_normal_drain_with_independent_task` / 2、4、5 |
| M-G3 ACTIVE 的确定 NO_BATCH | 请求冻结原成员后返回当前合同的确定 NO_BATCH，按既有换面逻辑再次评估 | 不退役，不增加同面轮询；原成员资格依既有确定响应闭合语义处理，后续合法请求仍保持 FIFO 和同面门禁 | T3/T4/G8；B `test_drain_flow`、`test_batch_flow` / 2 |
| M-G4 同箱码合法新轮次 | 旧 execution 已退役，其原成员未闭合；按现有准入产生同 bin_code 的合法新 Return.id；另跑整体可清理历史移除的变体 | 旧关联继续约束原 ID；合法新 ID 不继承旧关联。新轮次仍需自己的权威事实；清除整体闭合历史后结果相同 | T3/G8/G12；B `test_same_bin_new_return_id_does_not_inherit_old_membership` + E 历史清理测试 / 2、4 |
| M-B1 已接纳结果超期/确定未知/终态异常 | READY 旧 drain + 有效独立后续任务；分别注入原绑定 ACCEPTED 无最终结果且超期、RECONCILING 确定未知、已确认不可再业务重试的明确终态异常；另测期限前/等于期限、未 READY、取消后续任务、仍可业务重试 | 正向只一次 EXCEPTION_RETIRED，reason/blocker/trigger 正确，新任务继续；反向均不退役。超期不写 FAILED，不释放 WMS reservation，不换身份等价重发 | G3/G4/U1；D `test_exception_retirement_requires_ready_independent_task_and_blocker`、反向测试 + E U1 / 2、4、5 |
| M-B2 退役后迟到 READY、重复结果与货架面复用 | 原 return_batch 已提交；先退役，再复用 rack/face 开始独立任务；发送原 operation 的 READY 并重复发送 | 原 Evidence 可靠保存/消费；按原 execution 和 Return.id 归属处理，不创建旧 BIN_MOVE、不改当前新任务事实、不反复无意义对账 | T1/G7/U3；B `test_retired_drain_ready_is_consumed_without_bin_move`、`test_old_batch_owner_survives_reused_rack_face` + E U3 / 2、4、5 |
| M-B3 请求待决时跨 rack/face 重复分配 | ACTIVE 请求提交但响应未到；另一 rack/face 同时从 drain 和 batch_flow 两个 return 分支申请；再跑退役后仍未决变体 | 同一原 Return.id 只进入一项未决分配义务；请求与成员冻结同事务；入箱等独立动作可继续，同面既有门禁仍有效 | T3/T4/G8/G9；B `test_active_pending_drain_member_is_not_requested_on_other_face`、PostgreSQL 竞争测试 / 2、4 |
| M-B4 原队头未闭合与未知分配 | 原关联队头处于当前合同的阻塞状态，后续已有 READY；Mock 保持原请求未知，再以原身份技术重放；同 operation 变更成员 IDs/顺序另测 | 不用 SQL 过滤越过未闭合 FIFO 队头；未知时身份/正文/成员顺序不变，内容漂移冲突；其它独立队列只按自己的依据推进 | T3/T4/G8/G9；B FIFO、未知响应及 `test_same_batch_replay_rejects_changed_return_ids` / 2、4 |
| M-B5 两终态的原离场恢复对照 | 相同原离场异常分别关联 COMPLETED / EXCEPTION_RETIRED；对照直接 Requirement 有效/取消、目标已满足/未满足、原 Action 明确终态/仍未知；注入匹配迟到原结果 | COMPLETED 不重开排空，满足原资格才保留既有对象级业务重试；EXCEPTION_RETIRED 不业务重试或新建后继；两者可靠技术恢复/事实留存均不丢，目标已满足不重试、迟到旧结果不覆盖新因果 | T2/G10/U4；C `test_scan_flow`、`test_transport_outcome`，E 迟到结果变体 / 3、5 |
| M-B6 退役与响应/取消并发、提交失败 | 同一 WorkLine 下分别安排响应先提交/退役先提交，扫描时后续任务取消；请求/成员冻结事务注入回滚；重复扫描 | 锁内重读当前依据；先重放确定 Evidence，再判断超期；状态不反转，原成员不出现半提交，原请求只创建一次，原因稳定 | G1/G3/G7/G9；A/B PostgreSQL + D `test_retirement_rechecks_cancelled_task_and_arriving_result`、`test_duplicate_scans_close_once_with_stable_reason` / 4 |
| M-B7 重启、丢通知与可靠义务重提 | 业务提交后 worker 崩溃；不发加速唤醒，再起 worker 并运行现有持久扫描；原响应/物理结果迟到且重复 | 不靠内存状态恢复；ACTIVE 被重新发现，退役保持，旧可靠义务沿原身份/正文重提，未创建旧后继保持为零，合法独立新任务继续 | G11/U4；E `test_restart_and_missing_wakeup_preserve_retired_state_and_obligations` / 5 |
| M-B8 WMS 拒绝资源接纳 | 本地退役后合法新业务申请，BusinessServer 按现合同返回 reservation/接纳拒绝；随后正常许可另跑 good 变体 | 保留真实拒绝与原身份，不把本地退役解释成物理空位或 WMS reservation 已释放；按该 operation 的现有合同处理，不新增 fallback | 所有权边界；E BusinessServer 场景 / 5 |

- [ ] A–D 实施时先在对应主要 owner 建立表内新行为的 RED；E 为相同场景补真实装配和 worker 证据，复用已有断言/helper。M-G3、M-B4 的既有合同语义若已通过，记录基线，不制造失败或改变 FIFO 定义。
- [ ] 最终报告逐例记录：案例 ID、T1–T4/G/U、准确测试 nodeid、故障注入点、原/新身份、状态及 Evidence、禁止新增动作的计数、命令、结果、环境、当前可执行树 fingerprint。已创建动作与未创建后继分别断言；没有物理结果时保留未知。
- [ ] M-G1–G4 和 M-B1–B8 均须有对应证据；M-B3/M-B6 必须是真实 PostgreSQL 竞争/事务，M-B7 必须是真实 worker。仅既有命令 8–9 通过、Mock 返回 200 或被 skip，都不能将新退役机制标记验收通过。

## F：迁移终验、测试所有权、合同同步与最终 Review

- [ ] 同步 outbound §9.2.3 与人工出库引用：替换“无业务表或字段”、旧链不可绕过/全线 reservation 语义；写明 T1–T4、正常/异常恢复边界和 WMS reservation 所有权。SRS 第 0 章保持，file_index 只更新本能力入口；无新 API，wire/OpenAPI 不变。
- [ ] 对新 migration 写精确 HEAVY mapping：复用 `tests/integration/test_initial_schema_baseline_postgresql.py` 的通用迁移基础证明；新插件表、存量及业务状态由插件专属 PostgreSQL 测试显式覆盖。保留 deployment/plugin_models、plugin_composition、定点 reader 的现有映射，禁止把 workline_plugins/** 改为核心收集范围。执行 selector 后才知道本快照真正必选集合。
- [ ] 独占 PostgreSQL 同一实例内使用不同干净逻辑库验证空库→当前 head、`a16c9b2e7d04` 的存量 fixture→新 head→down_revision→新 head。所有启用插件的目标模型均已迁移；禁用插件的 metadata 不额外含插件表。自测降级只在独占临时库，运行库不得直接回退删除数据。
- [ ] 固定当前可执行树 fingerprint 与验证环境；最终 FAST 为所有实际受影响 owner，另闭合下表直接/间接消费者。变更 tests/support 或 fixture 时一并枚举消费者，不凭全目录失败发现漏传播。
- [ ] 残留扫描：无 `prepare_workline_reserved = drains.is_reserved`，无 drain current 的历史尾部判断，无按当前 rack/face 授权旧 drain 响应；原历史 reader 在其它直接运行依赖的用途若仍有消费者保留，不盲删。冻结 wire 无字段漂移，原 Transport/Command/Confirmation 身份不被改写。
- [ ] 执行文档引用、`git diff --check`、任务列表和测试所有权检查；最终一次 full Review 同时验证十条不变量、T1–T4、G1–G12/U1–U4、迁移及身份。修复后原 reviewer 将意见闭环与 fresh Review 合为一轮，不另叠重复全审。
- [ ] 当前高风险/schema 范围执行一次有效快照的完整 QUALITY、selector 必选核心 HEAVY 与插件集成/worker；已通过且未失效的相同内容不重跑。失败先分类并定向诊断，修复后刷新受影响的最终必选门禁。
- [ ] 回验 dirty 指纹和新增文件清单，记录 `IMPLEMENTED — VERIFIED` 或具体缺失证据；不自动 Commit、Push、更新 VERSION/CHANGELOG、Ship、Merge 或部署。用户后续授权 Commit 时再按 staged scope/hook 执行，不预先暂存本任务之外的文件。

## 验证命令与环境

以下命令均在仓库根执行并使用 `rtk`。实施时先确认 `uv` 环境已安装项目与 manual-picking 插件；pytest 从插件 tests 目录显式收集，不靠核心 testpaths。命令 1–3/6/8–9 是 FAST，不连真实服务；命令 4–5 仅在环境已就绪时执行。

1. **A / G1–G2、reader：**

```bash
rtk uv run pytest workline_plugins/manual-picking/tests/test_drain_execution.py workline_plugins/manual-picking/tests/test_drain_repository.py tests/contracts/wms_adapter/return_buffer_drain/test_service.py -q
```

2. **B/D / T1、T3、T4 与触发编排：**

```bash
rtk uv run pytest workline_plugins/manual-picking/tests/test_drain_execution.py workline_plugins/manual-picking/tests/test_drain_flow.py workline_plugins/manual-picking/tests/test_batch_flow.py workline_plugins/manual-picking/tests/test_scan_flow.py workline_plugins/manual-picking/tests/test_source_progression.py -q
```

3. **C / T2 与真实结果接收的 FAST：**

```bash
rtk uv run pytest workline_plugins/manual-picking/tests/test_drain_repository.py workline_plugins/manual-picking/tests/test_scan_flow.py workline_plugins/manual-picking/tests/test_transport_outcome.py -q
```

4. **A/B/D/F / PostgreSQL：** 环境中显式配置 `RUN_WORKLINE_INTEGRATION=1`、`INTEGRATION_DATABASE_URL`、`INTEGRATION_REDIS_URL`、`ENABLED_WORKLINE_PLUGINS=["manual-picking"]`。连接仅指向本任务独占临时 PostgreSQL/test admin 库，账号可建库；Redis 使用独占实例/测试 DB。`rack_database`/`temporary_database` 派生独立逻辑库，migration 使用 `ALEMBIC_DATABASE_URL` 指向那个临时库，不升级共享入口库。

```bash
rtk uv run pytest workline_plugins/manual-picking/tests/test_drain_execution_postgresql.py workline_plugins/manual-picking/tests/test_rack_cycle_postgresql.py -q -rs --junitxml=reports/drain-retirement-postgresql.xml
```

5. **E / 真实 worker：** 除命令 4 环境外，检查 Redis ping、数据库 readiness、冻结插件版本可用及 worker 启停。复用 TransportBrokerWorker 启动真实 worker 和 BusinessServer；WMS 仅是软件 Mock，不冒充物理验收。

```bash
rtk uv run pytest workline_plugins/manual-picking/tests/test_business_loop.py -q -rs --junitxml=reports/drain-retirement-worker.xml
```

6. **宿主通用消费者、装配：**

```bash
rtk uv run pytest tests/runtime/wms_integration/outbound_picking/test_prepare_service.py tests/contracts/wms_adapter/outbound_picking/test_plan_activation_service.py tests/runtime/execution/test_fact_processor.py tests/deployment/test_core_plugin_installation.py -q
```

7. **F / 拓扑与最终门禁：** 拓扑仅在新增测试/owner 清单传播后运行；本任务未暂存的实际差异使用 unstaged，Commit 授权后才切 staged。当前 selector 的 unstaged 同时读取 tracked diff 与 untracked 文件；核验输出确实包含新 migration，不能因漏 mapping 接受 NONE，也不为运行门禁擅自暂存用户文件。

```bash
rtk uv run pytest tests/architecture/test_suite_topology_guardrail.py tests/architecture/test_core_plugin_test_ownership_guardrail.py -q
rtk proxy ./scripts/git-quality-gate.sh --profile quality
rtk uv run scripts/select_heavy_tests.py --scope unstaged
rtk proxy ./scripts/run_selected_heavy_local.sh --scope unstaged
rtk git diff --check
```

8. **既有 Mock good case 基线：** 只回放现有 fixture 的 Mock 决策/事件合同，不承担 M-G1 的 WES 业务闭环。显式清除默认 pytest addopts，只收集这个节点。

```bash
rtk proxy env PYTHONPATH=.:workline_plugins/manual-picking/src uv run pytest tests/mock/test_wms_transport_mock_server.py::test_kt16_good_case_replays_recorded_decisions_and_completion_events -q -o addopts='' --junitxml=reports/drain-retirement-mock-good-baseline.xml
```

9. **既有 Mock bad case 基线：** 旧异常响应、SCAN3 异常重入、Return 原子替换、READY 队列及当前 CTU03 直接依赖；具体覆盖以收集的 nodeid 为准。使用隔离 SQLite/模拟端口，不冒充完整 RCS/ECS 故障链。实施前确认实际收集范围，最终执行一次有效快照；此集合仅含直接相关既有场景。

```bash
rtk proxy env PYTHONPATH=.:workline_plugins/manual-picking/src uv run pytest workline_plugins/manual-picking/tests/test_batch_repository.py workline_plugins/manual-picking/tests/test_scan_flow.py workline_plugins/manual-picking/tests/test_return_model.py workline_plugins/manual-picking/tests/test_source_progression.py -k 'mock_bad_case or scan3_recovery_replaces or test_return_fifo_isolates_unready_bin_and_rejects_second_open_bin' -q -o addopts='' --junitxml=reports/drain-retirement-mock-bad-baseline.xml
```

核心 local HEAVY runner 创建/清理自己的隔离环境；插件命令 4–5 必须单独执行，其 Redis/数据库不会自动继承 runner 子进程环境。同一迁移链与插件集成复用独占 PostgreSQL 实例和 readiness、使用独立逻辑库；不能把某一库的污染状态复用于迁移 autogenerate/升级验收。

**通过标准:** Mock 矩阵各案例及对应节点全部通过、应执行 integration/worker 场景无 skip；最终 selector 集合逐项通过或证明 NONE 覆盖完整；存量身份/升级降级重升/清理断言成立；源码和 frozen wire 无旧资格残留；证据绑定当前 fingerprint/环境。每份证据记录命令、结果、节点/manifest、环境与快照，FAIL/SKIP/未执行分开记录。

## 覆盖矩阵与最终调用点闭合

| SPEC/评审要求 | owner 与验收测试 | 执行切片/命令 |
| --- | --- | --- |
| G1 execution 唯一、严格身份 | test_drain_execution + PostgreSQL constraints | A / 1、4 |
| G2 两终态非 current、不回前驱 | test_drain_repository + 清理集成 | A/F / 1、4 |
| G3 三类 blocker 正反谓词 | test_drain_flow 参数化 | D / 2 |
| G4 无任务/取消/无 READY/期限前/发送未知 | test_drain_flow 参数化 | D / 2 |
| G5 锁内重验、多架停后继 | PostgreSQL 重验/双扫描/多架测试 | D / 4 |
| G6 三入口只移除 manual 注入 | 插件真实 Composition；宿主通用 blocker 保留 | D / 4、6 |
| G7 请求/原 execution/Return.id 原子关联 | rollback/identity 测试 | B / 2、4 |
| G8 退役迟到 READY/新面复用/重复消费 | test_scan_flow + 退出/响应竞争 | B / 2、4 |
| G9 跨面未决防重、退役防重、同码新 ID、NO_BATCH | 两请求入口 FAST + PostgreSQL 竞争 | B / 2、4 |
| G10 两终态原离场恢复、目标满足、迟到因果 | test_scan_flow/test_transport_outcome | C / 3 |
| G11 重启/丢通知/提交后崩溃 | test_business_loop 真实 worker | E / 5 |
| G12 干净迁移/存量/整体历史清理 | test_drain_execution_postgresql | A/F / 4 |
| U1 旧 ACCEPTED 超期 + 独立任务 | real_worker_retires_overdue_drain_and_prepares_next_task | E / 5 |
| U2 正常 drain + 新任务、直接依赖不扩大 | real_worker_preserves_normal_drain_with_independent_task | E / 5 |
| U3 请求后退役/原成员/迟到响应 | real_worker_consumes_late_ready_without_reallocating_old_members | E / 5 |
| U4 重启/丢通知/迟到物理结果 | restart_and_missing_wakeup_preserve_retired_state_and_obligations | E / 5 |

直接/间接回归还包括插件 `test_batch_repository.py`、`test_return_model.py`、`test_return_rack_progression.py`、`tests/integration/test_plan_activation_return_rack_postgresql.py`，以及 fixture 被传播影响的其它节点。实施准备时枚举后记录精确节点；签名保持且内容未受影响的测试不为安心反复全跑，未知 owner 必须补查再完成门禁。

## 当前交付与后续执行入口

本轮只交付本文件；SPEC、生产代码、测试、配置、服务器及既有 dirty 均保持。计划已逐项对照 SPEC §1–11、T1–T4、G1–G12/U1–U4，并检查接口名称、测试文件、迁移父 revision、通用 blocker 消费者及插件测试所有权。按用户后续确认增加 Mock good case / bad case 验收矩阵与既有基线命令，仍为实施后执行；本轮没有运行 Mock/pytest 或其它门禁。

后续实施推荐原生顺序执行，由主 Agent 负责写入与验证；最终一次 full Review。用户明确授权实施后再进入 Execution Lock、运行上述步骤；任何 Commit、Ship 或部署仍按后续交付目标授权。本计划不把设计通过当成测试或部署通过。
