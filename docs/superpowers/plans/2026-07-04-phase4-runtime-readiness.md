# Phase4 Runtime Readiness 实施计划

## 目标

根据 Phase4 SPEC 实施可测试的运行时读模型与基础事实能力：

- CellReservation 目标生命周期。
- RuntimeLocationEvent append-only 位置事实。
- MaterialLocationQuery 运行时查询服务。
- WorklineActiveObjects 作业线当前对象视图。

Wave2/Wave3 降级为本机开发环境 MOCK 验收，不做生产接入。Phase1 callback admission 已关闭；本项目未发布，当前开发/测试默认使用 MOCK closure，真实 artifact 不再作为当前开发/测试推进阻塞项。入库热路径与 SMT/NG/WMS 闭环的生产热路径继续保持 gated：只有 Phase2 `runtime_status` 兼容投影收尾完成，且发布前显式通过 `scripts/check_phase3_closure_gate.py --closure-profile production ...` 后，才允许接入线上写路径。

2026-07-04 追加进展：Phase2 `runtime_status` 已引入 `WorkLineRuntimeStatusProjectionService` 作为 runtime/orchestration 兼容投影服务，并完成 LOW 风险写入点、HIGH 风险 safety estop 与 CRITICAL 风险 dispatch ACK exhausted 写入点收敛。`src/app` 中对 `WorkLine.runtime_status` 的真实写入已集中到兼容投影服务；其它命中仅为读取、局部变量或 device runtime projection。

## 范围边界

- 允许实施 P0 与 Wave1 的 runtime/read-model 能力。
- 不删除 legacy，不执行 Phase5 drop。
- 不新增 RCS/AGV/CTU direct provider adapter。
- 不迁移 `MaterialUnit` 表归属；本轮只新增 material API facade 与查询 service。
- API 层只调用 service，不直接访问 repository 或 database。
- Wave2/Wave3 只允许通过 `tests/mock/phase4` 和本机 WMS/ECS mock 做合同验收；mock 验收不得绕过 Phase 2 residual gate 或 production closure profile 进入生产热路径，Phase1 callback admission 证据需保持绿灯。

## 任务状态

- [x] Baseline：`tests/contracts/test_phase4_design_docs.py` 通过。
- [x] Phase3 closure gate 记录：当前开发/测试默认使用 MOCK closure；`uv run python scripts/check_phase3_closure_gate.py` 无 artifact 可通过，生产发布前再显式运行 `--closure-profile production`。
- [x] Reservation TDD：覆盖 RECONCILING 冻结、owner mismatch、普通失败释放不越过冻结态。
- [x] RuntimeLocationEvent TDD：覆盖 append-only、幂等、按 object/correlation/external reference 查询。
- [x] P0 迁移：新增 RuntimeLocationEvent 表，扩展 reservation enum/index。
- [x] P0 service：扩展 reservation repository/service 与 RuntimeLocationEvent model/repository/service。
- [x] MaterialLocationQuery TDD：覆盖来源优先级、冲突 RECONCILING、package/bin、external reference。
- [x] WorklineActiveObjects TDD：覆盖 OK、冲突、transient window、RuntimeHold freeze scope。
- [x] API TDD：覆盖 material location query 与 workline active objects facade。
- [x] Wave2 预备合同：sorter characterization-to-target mapping 明确 PKG binding 与库存事务 port 归属。
- [x] Wave3 预备合同：SMT/NG/WMS 对账覆盖 NG evidence、本地事实缺失、WMS 拒绝、目标箱回写失败、重复/乱序 callback、source_version drift、RuntimeHold scope-only release。
- [x] Wave2 本机开发环境 MOCK 验收：用 WMS/ECS mock 验证 PKG binding、库存事务和 ECS callback 口径，不做生产接入。
- [x] Wave3 本机开发环境 MOCK 验收：用 WMS reconciliation mock 验证冲突/乱序/版本漂移场景，不做生产接入。
- [x] Phase2 兼容投影第一步：引入 `WorkLineRuntimeStatusProjectionService`，迁移 LOW 风险写入点。
- [x] Phase2 兼容投影收尾：单独处理 HIGH 风险 safety estop / dispatch ACK exhausted 写入点。
- [ ] Wave2 生产热路径：production closure profile 与上线确认未通过，未实施。
- [ ] Wave3 生产热路径：Wave2 生产稳定性、production closure profile 与上线确认未通过，未实施。

## 验收命令

- `uv run pytest tests/workline_runtime/test_bin_cell_reservation_target_lifecycle.py tests/workline_runtime/test_runtime_location_event_service.py tests/workline_runtime/test_material_location_query_service.py tests/workline_runtime/test_workline_active_objects_service.py tests/api/test_phase4_read_model_routes.py tests/contracts/test_phase4_design_docs.py tests/contracts/test_phase3_ops_contract_docs.py -q`
- `uv run pytest tests/api/ -q`
- `uv run pytest tests/mock/phase4 -q`
- `uv run pytest --collect-only -q -o addopts='' | tail -5`
- `git diff --check`
- `./scripts/git-quality-gate.sh --profile quality`
- GitNexus detect changes

## 当前门禁结论

P0/Wave1 已可本地验证。Wave2/Wave3 已降级为本机开发环境 MOCK 验收；Phase1 callback admission 已在 callback API 热路径关闭。Phase3 closure 在当前开发/测试范围默认走 MOCK closure，真实 artifact 不再作为当前推进阻塞项。Phase2 `runtime_status` 兼容投影收尾已完成，开发/测试范围的 Phase4 runtime readiness gate 已关闭；生产热路径仍不得自动接入，发布前必须显式通过 `scripts/check_phase3_closure_gate.py --closure-profile production ...`，并重新确认 Wave2 稳定性与上线门禁。
