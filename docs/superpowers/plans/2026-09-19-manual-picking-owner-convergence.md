# Manual-picking owner convergence and contract alignment plan

## Goal

将人工出库合同 §8/§10 的测试 owner、状态和验收边界对齐到当前仓库事实；保留已有宿主基础能力与插件业务边界，使用现有 MOCK 业务 owner 验收人工 Bin，不创建不存在的占位测试文件或新的 SLA 机制。

## Constraints

- 宿主/SDK 只提供 Evidence、WmsConfirmation、DeviceCommand、Transport 和 worker 基础能力；人工 Bin 决策、结果解释、顺序和 NG 分支仍由 `manual-picking` 插件拥有。
- `drain_rack_decide` 的响应字段使用 `rack_face`（数组值），不保留 `rack_faces` 兼容别名。
- `WORK_REQUIRED` 外部等待 SLA 的告警、停新入线和独立 owner 不属于本阶段；只保持 point2 占用并 fail closed。
- integration 未启用时的 `skipped` 不是通过；真实 ECS/现场验收不由仓库测试代替。

## Review focus

- 当前 `test_business_loop.py` 已覆盖 drain、下一任务准备和来源离位补位；人工 Bin `NORMAL/NG` 业务语义已有 `test_scan_flow.py` MOCK owner，worker wiring 单独保留。
- §10 中原有若干 owner 路径不存在；应引用已有 wire/adapter/scan/completion owner，禁止通过创建空壳文件制造“有 owner”的假象。
- README 曾引用错误的 PostgreSQL 集成路径；合同 drain 示例和文字需统一为 `rack_face`。

## Engineering review result

**条件通过。** 方案边界和收敛方向正确，但真实 worker 任务不能假设存在现成的人工 Bin 装配入口。当前 `test_business_loop.py` 的 fixture 主要装配 Transport/Drain；执行 Task 3 前必须先确认已有 `InboundEvidence` 注入、WMS response dispatch、Celery fact application 和插件状态构造可以在同一 owner 内闭合。若不能复用这些入口，不新增第二套 E2E harness，保留 `PLANNED` 并报告阻塞。

Native execution 还必须保留工作区中已有的取消/排空变更（`batch_driver.py`、`batch_flow.py` 及其测试），不回滚、不重写、不把它们混入本方案的无关清理。

## Tasks

### 1. 收敛合同和 README owner（文档）

- [x] 更新 `docs/contracts/wms-manual-outbound-picking-integration-requirements.md` §8.4：明确真实 worker 现状、planned owner 和 skipped 边界。
- [x] 更新 §10：替换不存在的 owner，删除未实现的 external-wait SLA 声明，明确现场 `NOT ACCEPTED`。
- [x] 删除历史 T1–T7 未来式任务，改为本方案四步入口。
- [x] 更新 `workline_plugins/manual-picking/README.md` 的 owner 路径和 drain 字段。
- 验证：`git diff --check`；`rg` 确认不存在的 owner 路径不再出现在当前合同/README。

### 2. 对齐 drain fixture（测试资产）

- [x] 修改 `workline_plugins/manual-picking/tests/test_transport_outcome.py` 中 drain 响应 fixture：`racks[].rack_faces` → `racks[].rack_face`。
- [x] 运行：`uv run pytest workline_plugins/manual-picking/tests/test_transport_outcome.py -q`（20 passed）。
- [x] 扫描 manual-picking 测试和两个出库合同，确认 drain 语义只剩 `rack_face`；plan_delta 的 `rack_faces` 不在本任务范围内。

### 3. 收敛人工 Bin 验收 owner（MOCK；真实 worker wiring 分离）

- [x] 先在 `test_business_loop.py` 及 `rack_cycle_support.py` 中确认可复用的数据库 seed、公共 Event ingress、WMS response dispatch、`EXECUTE` worker 和插件应用入口；这一步未改生产代码。
- [x] 仅复用 `workline_plugins/manual-picking/tests/test_business_loop.py` 既有真实 worker 入口，不新增平行 E2E 包；本轮只修正生产装配参数与 fixture 必填字段。
- [x] 复用 `test_scan_flow.py` 既有 MOCK owner，覆盖 `WORK_REQUIRED → NORMAL`、`WORK_REQUIRED → NG`、稳定 DeviceCommand identity 和重复 completion 不产生第二条释放命令。
- [x] 保持断言在业务可观察结果：插件终态、point2 释放、命令数量/身份和 Evidence 状态；不复制共享 wire、幂等或数据库基础能力矩阵。
- [x] 真实 worker 仅保留为独立 wiring 证据；本轮已确认生产插件装配参数可注入，但不以本机 prefork 失败阻塞 MOCK 业务验收。

执行结果：已通过 SSH 隧道验证远端容器 PostgreSQL/Redis 可达，并完成 PostgreSQL preflight。已修复既有测试 fixture 中 `queued=True` 的必填 `workline_id`、将真实 worker 调用切换到生产 `prepare_picking_tasks_batch` owner，并在该测试上下文显式注入生产要求的 `ENABLED_WORKLINE_PLUGINS=["manual-picking"]`。最小用例已确认 `PREPARE` 返回 `1`；随后 `DISPATCH` 因本机 Celery prefork 子进程启动失败（`WorkerLostError`）未完成，但不阻塞既有 MOCK owner 验收。人工 Bin `WORK_REQUIRED → NORMAL/NG` 改由 `test_scan_flow.py` 既有 MOCK owner 承接；本轮测试临时 role 和临时数据库已清理。

### 4. 最终门禁与合同状态回写

- [x] 运行人工插件 FAST（含当前工作区 batch/source 回归）：116 passed。
- [x] 运行拓扑/所有权 guardrail：15 passed。
- [x] 代码或测试变更后按 `docs/architecture/heavy-test-impact.toml` 选择 HEAVY；selector 未选择可执行 HEAVY manifest。
- [x] 根据真实结果回写 §6/§8/§10；MOCK 业务验收闭合，真实 worker wiring 和现场验收仍单独保持未闭合。

## Acceptance

完成标准是：合同中的 owner 全部指向真实路径；drain 合同与 parser/fixture 使用同一 `rack_face` 字段；人工 Bin NORMAL/NG 至少有一条可重复的 MOCK owner；基础能力测试与插件业务测试边界清楚；所有未运行或未授权的真实 worker、设备和现场验收明确标注。

本计划只改变文档和既有测试/业务路径，未授权 Commit、Push、PR、Merge 或 Deploy。
