# prepare 宿主 Coordinator 与插件 Policy 实施计划

**Status:** ReviewRequired。根据工程评审范围决策，从工作线角色归属计划独立拆出；尚未完成独立评审，不是实施授权。

**边界：** 基础与业务分离、复用已有可靠能力、未发布系统直接替换且不保留兼容路径；遵循 `AGENTS.md` 与 `tests/README.md`。
先固定当前源码、合同、dirty 和测试所有者；不得把原计划的历史基线或验证清单当作当前通过证据。

## 实施范围

**出口：** 核心不再判断 manual_bin_processing 身份与人工选线规则；宿主保留 prepare 的可靠事务 Coordinator，插件只拥有无副作用
typed Policy。既有 prepare 共享合同和暗构建保持不变。
此切片可以独立评审；不作为角色配置的先决条件，不扩大为 Phase 12 全业务交付。

**文件与 owner：**

- 将 `src/app/wms_integration/outbound_picking/services/picking_task_prepare.py` 收敛为宿主 `PickingTaskPrepareCoordinator`：继续拥有事务、
  锁顺序、PickingTask 绑定、`WmsConfirmationLifecycleService`、typed prepare intent 落库和提交后唤醒，不迁入插件。
- 将 `repositories/prepare_eligibility_repository.py` 的人工准入策略迁到插件
  `application/prepare_policy.py`；Policy 只接收 Coordinator 提供的不可变事实快照并返回 typed 候选/拒绝结果，不访问 Repository、数据库、
  HTTP 或队列。若缺少纯事实查询，只在宿主基础 Repository 增加最小查询，业务判定留插件。
- `PickingTaskPrepareCoordinator.prepare_next_for_workline(workline_id, *, now)` 保持现有事务和可靠义务调用方式，复用
  PickingTaskRepository、WmsConfirmationLifecycleService、TaskQueueGateway 和 `wms_operations.outbound_picking_task_prepare(...)`；
  直接替换旧 Service 名称/导出和调用点，无兼容 import。
- wire/parser/adapter 与 PickingTask model/repository 留原域，`outbound_picking/composition.py` 不增加 prepare 生产接线。

**步骤与验收：**

- [ ] 先固定 prepare 现有外部合同、三 owner confirmation 身份、暗构建守卫与直接调用点；发现合同冲突只报告，不改 ACK。
- [ ] RED：建立插件 Policy 主测试 owner，覆盖人工上下文准入、无任务和候选选择；核心 Coordinator owner 覆盖重复领取、业务事务、
  typed prepare intent 及提交后唤醒；
  明确承接旧测试每项语义，再做 owner 迁移，不先删除旧测试。
- [ ] DEV：移动具体策略，保留并收敛宿主 Coordinator，直接更新 imports/fixtures；删除旧业务常量与只为旧路径服务的 helper，
  不增加通用任务调度器或 operation registry。
- [ ] GREEN：插件测试承接通过后删除对应旧业务测试；核心继续验证 wire/adapter、通用确认身份及 schema 约束。
- [ ] 保持暗构建：无新公共 route/OpenAPI 激活、Celery/Beat 触发、现场 WorkLine START 或真实设备动作。

**测试拆分：**

- `tests/contracts/wms_adapter/outbound_picking/test_prepare_eligibility.py` 的人工判断迁到
  `workline_plugins/manual_bin_processing/tests/test_prepare_policy.py`；`test_prepare_service.py` 中事务、锁、绑定、typed intent 和唤醒断言
  继续由核心 Coordinator owner 承接。
- `tests/integration/wms_adapter/outbound_picking/test_prepare_postgresql.py` 继续拥有真实事务、锁、领取和 confirmation 可靠性；插件只增加
  Policy 的纯测试，不复制 PostgreSQL 或共享可靠性矩阵。共享 confirmation/schema 行为保留其核心 owner，
  不整文件盲移，也不复制可靠性测试矩阵。
- `test_prepare_wire.py`、`test_prepare_adapter.py` 留核心；更新
  `tests/architecture/test_outbound_picking_prepare_activation_guardrail.py` 与 HEAVY 映射，不把插件路径放回核心 selector。

## 验证与交付

生产变更按高风险 TDD；闭合调用点和承接测试后，执行适用 QUALITY、精确 HEAVY 与真实事务回归。
纯文档拆分不编写或运行测试。Commit、Push、Merge、Deploy 分别授权；本计划不激活 prepare/plan_delta。
