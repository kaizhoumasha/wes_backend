# Phase4 Runtime Readiness 实施计划

## 目标

根据 Phase4 SPEC 实施可测试的运行时读模型与基础事实能力：

- CellReservation 目标生命周期。
- RuntimeLocationEvent append-only 位置事实。
- MaterialLocationQuery 运行时查询服务。
- WorklineActiveObjects 作业线当前对象视图。

Wave2/Wave3 降级为本机开发环境 MOCK 验收，不做生产接入。Phase1 callback admission 已关闭；本项目未发布，当前开发/测试默认使用 MOCK closure，真实 artifact 不再作为当前开发/测试推进阻塞项。入库热路径与 SMT/NG/WMS 闭环的生产热路径继续保持 gated：只有 Phase2 `runtime_status` 兼容投影收尾完成，且发布前显式通过 `scripts/check_phase3_closure_gate.py --closure-profile production ...` 后，才允许接入线上写路径。

2026-07-04 追加进展：Phase2 `runtime_status` 已引入 `WorkLineRuntimeStatusProjectionService` 作为 runtime/orchestration 兼容投影服务，并完成 LOW 风险写入点、HIGH 风险 safety estop 与 CRITICAL 风险 dispatch ACK exhausted 写入点收敛。`src/app` 中对 `WorkLine.runtime_status` 的真实写入已集中到兼容投影服务；其它命中仅为读取、局部变量或 device runtime projection。

2026-07-04 追加进展 2：补齐 Phase4 P0/Wave1 开发/测试缺口：MaterialLocationQuery API facade 增加 workline active object 第 6 入口；CellReservation 增加 `correlation_id`、`evidence_json`、reservation_key correlation/source_event 覆盖、TTL 只释放过期 `PLANNED` 的 service/repository 路径；MaterialLocationQuery 的 CellReservation evidence 映射同步带出 correlation/provider/source_version。

2026-07-04 追加进展 3：深化 Wave2/Wave3 本机 MOCK 验收，不接生产热路径：WMS mock 增加 `full_box_exchange`、`change_rack_face` 与 `runtime-hold-release-preview` 合同；`tests/mock/phase4` 覆盖满箱交换有/无需求分流、`CHANGE_RACK_FACE` 独立履约、已满箱对象排除逐件候选，以及 RuntimeHold 只释放声明 scope。

2026-07-04 追加进展 4：补齐 sorter inbound preview 级本机 MOCK 合同：粗分机正常流拆分本地物理事实与 WMS 同步状态；分拣机入库覆盖 SCAN1/2/3、join gate、NG/RuntimeHold 路由、扫码平台预取默认 0 与显式开启 validator；CTU 父子批次视图覆盖父成功但子项缺失、重复 sequence、未 resolve placeholder 和部分失败进入 `RECONCILING`。

2026-07-04 追加进展 5：新增 `scripts/check_phase4_runtime_readiness_gate.py`，把 Phase4 开发/测试 readiness 固化为可执行门禁：默认 `development-mock` profile 验证 SPEC 状态、Wave2/Wave3 本机 MOCK 合同、生产热路径未开启；`production` profile 在未显式生产接入前返回 `PHASE4_PRODUCTION_HOT_PATH_NOT_ENABLED`。该 gate 已接入 `./scripts/git-quality-gate.sh --profile quality`。

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
- [x] P0 迁移：新增 RuntimeLocationEvent 表，扩展 reservation enum/index/correlation/evidence 字段。
- [x] P0 service：扩展 reservation repository/service 与 RuntimeLocationEvent model/repository/service；覆盖 TTL 只释放过期 `PLANNED`，不释放 `RECONCILING`。
- [x] MaterialLocationQuery TDD：覆盖来源优先级、冲突 RECONCILING、package/bin、external reference、workline active object API facade 第 6 入口。
- [x] WorklineActiveObjects TDD：覆盖 OK、冲突、transient window、RuntimeHold freeze scope。
- [x] API TDD：覆盖 material location query 与 workline active objects facade。
- [x] CellReservation evidence/idempotency 收敛：reservation_key 覆盖 object/correlation/target cell/source_event，evidence 保留 trace/correlation/source_event/provider/source_version，并进入 MaterialLocationQuery evidence。
- [x] Wave2 预备合同：sorter characterization-to-target mapping 明确 PKG binding 与库存事务 port 归属。
- [x] Wave3 预备合同：SMT/NG/WMS 对账覆盖 NG evidence、本地事实缺失、WMS 拒绝、目标箱回写失败、重复/乱序 callback、source_version drift、RuntimeHold scope-only release。
- [x] Wave2 本机开发环境 MOCK 验收：用 WMS/ECS mock 验证 PKG binding、库存事务、ECS callback、粗分机正常流 preview、分拣机入库 join gate、扫码平台预取 validator、CTU 父子视图、满箱交换前置分流、换面独立履约和逐件候选排除口径，不做生产接入。
- [x] Wave3 本机开发环境 MOCK 验收：用 WMS reconciliation mock 验证冲突/乱序/版本漂移和 RuntimeHold scope-only release 场景，不做生产接入。
- [x] Phase4 runtime readiness gate：`scripts/check_phase4_runtime_readiness_gate.py` 默认开发/测试 MOCK profile 通过，production profile 明确阻塞，并接入 quality profile。
- [x] Phase2 兼容投影第一步：引入 `WorkLineRuntimeStatusProjectionService`，迁移 LOW 风险写入点。
- [x] Phase2 兼容投影收尾：单独处理 HIGH 风险 safety estop / dispatch ACK exhausted 写入点。
- [ ] Wave2 生产热路径：production closure profile 与上线确认未通过，未实施。
- [ ] Wave3 生产热路径：Wave2 生产稳定性、production closure profile 与上线确认未通过，未实施。

## 验收命令

- `uv run pytest tests/workline_runtime/test_bin_cell_reservation_target_lifecycle.py tests/workline_runtime/test_runtime_location_event_service.py tests/workline_runtime/test_material_location_query_service.py tests/workline_runtime/test_workline_active_objects_service.py tests/api/test_phase4_read_model_routes.py tests/contracts/test_phase4_design_docs.py tests/contracts/test_phase3_ops_contract_docs.py -q`
- `uv run pytest tests/api/ -q`
- `uv run pytest tests/mock/phase4 -q`
- `uv run python scripts/check_phase4_runtime_readiness_gate.py`
- `uv run pytest --collect-only -q -o addopts='' | tail -5`
- `git diff --check`
- `./scripts/git-quality-gate.sh --profile quality`
- GitNexus detect changes

## 当前门禁结论

P0/Wave1 已可本地验证，且 MaterialLocationQuery API 第 6 入口、CellReservation TTL、reservation evidence/idempotency 口径已补齐。Wave2/Wave3 已降级为本机开发环境 MOCK 验收；Phase1 callback admission 已在 callback API 热路径关闭。Phase3 closure 在当前开发/测试范围默认走 MOCK closure，真实 artifact 不再作为当前推进阻塞项。Phase2 `runtime_status` 兼容投影收尾已完成，开发/测试范围的 Phase4 runtime readiness gate 已关闭；生产热路径仍不得自动接入，发布前必须显式通过 `scripts/check_phase3_closure_gate.py --closure-profile production ...`，并重新确认 Wave2 稳定性与上线门禁。
