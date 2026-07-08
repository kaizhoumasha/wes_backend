# Phase4 Runtime Readiness 实施计划

> **Completion status (2026-07-07):** DONE for Phase4 runtime readiness. Phase4 development/mock readiness、Wave2/Wave3 production-capable runtime path、evidence profile gate 与 composer 已闭合；Phase4 production evidence artifact 已由 `2026-07-07-phase3-production-artifacts.md` 生成并通过 production gate。Phase5 business cleanup 已随 PR #79 合并；真实生产发布仍需现场 evidence/canary 按发布流程执行。

## 目标

根据 Phase4 SPEC 实施可测试的运行时读模型与基础事实能力：

- CellReservation 目标生命周期。
- RuntimeLocationEvent append-only 位置事实。
- MaterialLocationQuery 运行时查询服务。
- WorklineActiveObjects 作业线当前对象视图。

Wave2/Wave3 后续目标是 production-capable runtime path，外部 provider 可替换。WES 代码只面向 provider contract、RuntimeIntent、RuntimeInbox、idempotency、timeout/retry、evidence 与 RuntimeHold/Reconciliation；外部是真设备、sandbox、MOCK 还是 simulator 由部署 wiring 与 evidence profile 区分，不进入业务代码分支。Phase1 callback admission 已关闭；本项目未发布，当前开发/测试默认使用 MOCK closure，真实 artifact 不再作为当前开发/测试推进阻塞项。发布前仍必须显式通过 `scripts/check_phase3_closure_gate.py --closure-profile production ...` 与 Phase4 evidence profile gate。

2026-07-04 追加进展：Phase2 `runtime_status` 已引入 `WorkLineRuntimeStatusProjectionService` 作为 runtime/orchestration 兼容投影服务，并完成 LOW 风险写入点、HIGH 风险 safety estop 与 CRITICAL 风险 dispatch ACK exhausted 写入点收敛。`src/app` 中对 `WorkLine.runtime_status` 的真实写入已集中到兼容投影服务；其它命中仅为读取、局部变量或 device runtime projection。

2026-07-04 追加进展 2：补齐 Phase4 P0/Wave1 开发/测试缺口：MaterialLocationQuery API facade 增加 workline active object 第 6 入口；CellReservation 增加 `correlation_id`、`evidence_json`、reservation_key correlation/source_event 覆盖、TTL 只释放过期 `PLANNED` 的 service/repository 路径；MaterialLocationQuery 的 CellReservation evidence 映射同步带出 correlation/provider/source_version。

2026-07-04 追加进展 3：深化 Wave2/Wave3 本机 MOCK 验收，作为 provider contract 语义基线：WMS mock 增加 `full_box_exchange`、`change_rack_face` 与 `runtime-hold-release-preview` 合同；`tests/mock/material_flow` 覆盖满箱交换有/无需求分流、`CHANGE_RACK_FACE` 独立履约、已满箱对象排除逐件候选，以及 RuntimeHold 只释放声明 scope。

2026-07-04 追加进展 4：补齐 sorter inbound preview 级本机 MOCK 合同：粗分机正常流拆分本地物理事实与 WMS 同步状态；分拣机入库覆盖 SCAN1/2/3、join gate、NG/RuntimeHold 路由、扫码平台预取默认 0 与显式开启 validator；CTU 父子批次视图覆盖父成功但子项缺失、重复 sequence、未 resolve placeholder 和部分失败进入 `RECONCILING`。

2026-07-04 追加进展 5：新增 `scripts/check_phase4_runtime_readiness_gate.py`，把 Phase4 开发/测试 readiness 固化为可执行门禁：默认 `development-mock` profile 验证 SPEC 状态、Wave2/Wave3 本机 MOCK 合同与 preview 边界。该 gate 已接入 `./scripts/git-quality-gate.sh --profile quality`。

2026-07-04 追加进展 6：新增 `Phase4SorterInboundPreviewService`，把粗分机正常流、分拣机 join gate、满箱交换前置分流、`CHANGE_RACK_FACE` 独立履约与 CTU 父子批次查询视图从 mock endpoint 语义沉淀为 runtime capability 级纯 preview service；该 service 不访问 DB、不发 WMS/ECS effect、不复用旧 plugin 入口，只用于开发/测试 MOCK 验收，并已纳入 Phase4 runtime readiness gate。

2026-07-04 追加进展 7：新增 `SmtNgWmsReconciliationPreviewService`，把 NG evidence、本地物理事实缺失、WMS reject、目标箱回写失败、重复/乱序 callback、source_version drift 与 RuntimeHold scope-only release 从 mock endpoint 语义沉淀为 runtime capability 级纯 preview service；该 service 不访问 DB、不发 WMS/NG/PDA effect、不复用旧 plugin 入口，只用于开发/测试 MOCK 验收，并已纳入 Phase4 runtime readiness gate。

2026-07-05 追加进展：新增 `Phase4SorterInboundRuntimeService` 与 `SmtNgWmsReconciliationRuntimeService`，把 Wave2/Wave3 从 preview 语义推进到 production-capable runtime path 的 plan builder：输出 `RuntimeIntent`、effect port contract、provider-contract evidence、RuntimeInbox evidence 与 RuntimeHold scope-only plan，不判断外部 provider 是真设备、sandbox、MOCK 还是 simulator。`scripts/check_phase4_runtime_readiness_gate.py` 新增 `development/simulator/site/production` evidence profile；profile 只改变证据要求，不改变 service 行为。新增 `scripts/compose_phase4_runtime_evidence_artifact.py`，用于生成可被 gate 验收的 Phase4 runtime evidence artifact，避免手写证据口径漂移。

2026-07-05 追加进展 2：Phase4 runtime evidence profile gate 已细分 `simulator/site/production` 证据要求。`simulator` 允许最小 provider-contract artifact；`site/production` 必须提供 `evidence_manifest`，引用 Wave2/Wave3 provider contract、effect dispatch trace、RuntimeInbox worker trace、RuntimeHold/ReconciliationRecord trace 与 Phase4 runtime benchmark evidence 文件。`production` profile 继续叠加 Phase3 production closure artifact 校验。composer 支持 `--evidence-dir` 组装 site/production artifact；证据文件本身属于 `reports/` 或 CI/deploy artifact，不提交到 git。

## 范围边界

- 允许实施 P0 与 Wave1 的 runtime/read-model 能力。
- 不删除 legacy，不执行 Phase5 drop。
- 不新增 RCS/AGV/CTU direct provider adapter。
- 不迁移 `MaterialUnit` 表归属；本轮只新增 material API facade 与查询 service。
- API 层只调用 service，不直接访问 repository 或 database。
- Wave2/Wave3 的 preview 与 mock 合同只作为语义基线；runtime capability builder 必须只面向 provider contract，不根据外部 provider 是否 MOCK / simulator / 真设备选择业务分支。
- evidence profile 只改变验收证据要求；`site/production` 缺 Phase4 evidence manifest、引用 evidence 文件、Phase3 production closure artifact 或 benchmark artifact 时，发布前 gate 保持红灯。

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
- [x] Wave2 本机开发环境 MOCK 验收：用 WMS/ECS mock 验证 PKG binding、库存事务、ECS callback、粗分机正常流 preview、分拣机入库 join gate、扫码平台预取 validator、CTU 父子视图、满箱交换前置分流、换面独立履约和逐件候选排除口径，作为 provider contract 语义基线。
- [x] Wave3 本机开发环境 MOCK 验收：用 WMS reconciliation mock 验证冲突/乱序/版本漂移和 RuntimeHold scope-only release 场景，作为 provider contract 语义基线。
- [x] Phase4 runtime readiness gate：`scripts/check_phase4_runtime_readiness_gate.py` 默认开发/测试 profile 通过，`simulator/site/production` profile 只改变 evidence 要求，并接入 quality profile。
- [x] Wave2 sorter inbound preview capability：`Phase4SorterInboundPreviewService` 覆盖本机 preview 级粗分机/分拣机/满箱交换/CTU 父子视图语义，并由 readiness gate 检查存在与非生产边界。
- [x] Wave3 SMT/NG/WMS reconciliation preview capability：`SmtNgWmsReconciliationPreviewService` 覆盖本机 preview 级冲突矩阵、重复 callback 幂等合并和 RuntimeHold scope-only release，并由 readiness gate 检查存在与非生产边界。
- [x] Phase2 兼容投影第一步：引入 `WorkLineRuntimeStatusProjectionService`，迁移 LOW 风险写入点。
- [x] Phase2 兼容投影收尾：单独处理 HIGH 风险 safety estop / dispatch ACK exhausted 写入点。
- [x] Wave2 runtime capability builder：`Phase4SorterInboundRuntimeService` 输出 `RuntimeIntent`、`WmsFulfillmentPort.notify_pkg_binding`、`WmsInventoryTransactionPort.confirm_inbound`、CellReservation/RuntimeLocationEvent evidence 与 join gate object-scope reconciliation plan。
- [x] Wave3 runtime capability builder：`SmtNgWmsReconciliationRuntimeService` 输出 RuntimeInbox 上游 callback evidence、重复 callback 幂等合并、WMS reject/source_version drift 等 RuntimeHold plan 与 scope-only release plan。
- [x] Wave2 evidence profile gate：site/production manifest 已要求 provider contract、effect dispatch trace、RuntimeIntentLog/DeviceCommand/WMS fulfillment 证据；实际 evidence 文件由 `reports/`、CI 或部署验收产物提供，不进入 git。
- [x] Wave3 evidence profile gate：site/production manifest 已要求 provider contract、RuntimeInbox worker trace、RuntimeHold/ReconciliationRecord 证据；实际 evidence 文件由 `reports/`、CI 或部署验收产物提供，不进入 git。

## 验收命令

- `uv run pytest tests/workline_runtime/test_bin_cell_reservation_target_lifecycle.py tests/workline_runtime/test_runtime_location_event_service.py tests/workline_runtime/test_material_location_query_service.py tests/workline_runtime/test_workline_active_objects_service.py tests/workline_runtime/test_sorter_inbound_preview_service.py tests/workline_runtime/test_smt_ng_wms_reconciliation_preview_service.py tests/workline_runtime/test_sorter_inbound_runtime_service.py tests/workline_runtime/test_smt_ng_wms_reconciliation_runtime_service.py tests/api/test_phase4_read_model_routes.py tests/contracts/test_phase4_design_docs.py tests/contracts/test_phase3_ops_contract_docs.py tests/contracts/test_phase4_runtime_readiness_gate.py -q`
- `uv run pytest tests/api/ -q`
- `uv run pytest tests/mock/material_flow -q`
- `uv run python scripts/compose_phase4_runtime_evidence_artifact.py --output reports/phase4/runtime-evidence-simulator.json --profile simulator --environment local-wms-ecs-simulator --generated-at 2026-07-05T00:00:00Z`
- `uv run python scripts/compose_phase4_runtime_evidence_artifact.py --output reports/phase4/runtime-evidence-site.json --profile site --environment field-dry-run --generated-at 2026-07-05T00:00:00Z --evidence-dir evidence/phase4-runtime`
- `uv run python scripts/check_phase4_runtime_readiness_gate.py`
- `uv run python scripts/check_phase4_runtime_readiness_gate.py --readiness-profile simulator --phase4-runtime-evidence-artifact reports/phase4/runtime-evidence-simulator.json`
- `uv run python scripts/check_phase4_runtime_readiness_gate.py --readiness-profile site --phase4-runtime-evidence-artifact reports/phase4/runtime-evidence-site.json`
- `uv run python scripts/check_phase4_runtime_readiness_gate.py --readiness-profile production --phase4-runtime-evidence-artifact reports/phase4/runtime-evidence-production.json --p0-e2e-artifact reports/phase3/phase3-p0-e2e.json --benchmark-artifact reports/phase3/phase3-production-benchmark.json`
- `uv run pytest --collect-only -q -o addopts='' | tail -5`
- `git diff --check`
- `./scripts/git-quality-gate.sh --profile quality`
- GitNexus detect changes

## 当前门禁结论

P0/Wave1 已可本地验证，且 MaterialLocationQuery API 第 6 入口、CellReservation TTL、reservation evidence/idempotency 口径已补齐。Wave2/Wave3 已具备 preview 语义基线与 production-capable runtime path 的 plan builder；代码只输出 `RuntimeIntent`、effect contract、RuntimeInbox evidence 与 RuntimeHold/Reconciliation plan，不关心外部 provider 是 MOCK、simulator 还是真设备。Phase1 callback admission 已在 callback API 热路径关闭。Phase3 closure 在当前开发/测试范围默认走 MOCK closure，真实 artifact 不再作为当前推进阻塞项。Phase2 `runtime_status` 兼容投影收尾已完成，开发/测试范围的 Runtime evidence readiness gate 已关闭；Runtime `site/production` evidence profile gate 与 artifact composer 已闭合。2026-07-07 已通过后续 evidence 计划生成 `reports/runtime/runtime-evidence-production.json` 并通过 Runtime production profile gate；发布前仍必须使用对应现场/CI evidence 重新验证，并执行部署 canary。
