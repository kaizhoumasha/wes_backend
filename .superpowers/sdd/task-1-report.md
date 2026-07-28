# Task 1 实施报告：冻结 WMS 全工厂 35 项合同

## 结论

Task 1 已完成。WMS 北向目标合同现由 19 项 QUERY、16 项 EFFECT 的 operation-specific typed Definition
组成；各业务域显式导出静态 `OPERATIONS`，顶层 registry 只做静态组合。Provider operation manifest、
conformance requirement、业务场景覆盖与 Mock fixture 均由该 registry 派生并 fail closed。

## 主要变更

- 新增静态 Definition、预算、分页、completion mode、lane 与唯一 registry；注册表导入时强制恰好 35 个唯一
  identity。
- 为 35 项 operation 分别建立严格 request/result model，禁止 extra 字段；所有 EFFECT 使用 POST。
- Q19 冻结为无副作用 POST QUERY，包含六合一码、测量、correlation identity、typed result 与 7 项拒绝码闭集。
- GRN 收敛为 PO 行级记录，删除 `WmsGrnItem`、`list_grn_items`、`item_count`；事件身份同步为
  `grn_id + po_number + po_item + material_code`。
- E01–E07/E15/E16 固定 `SYNC_RESULT`，E08–E14 固定 `ASYNC_TASK`；Definition 仅允许异步项声明 status 能力。
- E11 请求不再携带 WES 选择的空箱；E12/E13 使用批量 typed request/result，冻结批次成员、FIFO 候选、
  candidate digest、接纳成员和逐箱结果字段。
- 增加业务场景覆盖 manifest、旧 transport identity 到 E08–E14 的迁移清单；目标 manifest 不注册旧
  transport identity，也不提供 alias/facade/fallback。
- Mock validator 从 registry 解析模型与拒绝码；新增 35 组 request/result fixture，缺少任一项即合同测试失败。
- 修订北向合同、Mock 能力、可观测性、外部 profile 与全工厂业务蓝图；callback 只保留 hint 语义，南向
  `PICK ACK → 下一北向；PICK result → SCAN；SCAN result → WES 决策；PUT result → 最终事实` 已冻结。

## TDD 证据

1. RED：首次运行 registry 合同测试失败，原因为
   `src.app.wms_integration.operation_registry` 不存在。
2. GREEN：实现静态 registry 与 typed contracts 后，35 项唯一 identity 合同通过。
3. RED：补充 Q19 未声明拒绝码测试后，旧实现未抛 `ValidationError`。
4. GREEN：将 Q19 `reason_code` 收窄为 7 项 Literal 闭集后测试通过。

## GitNexus

- 修改前已对所有涉及的现有生产 symbol 执行 upstream impact analysis。
- `WmsGrnInfo`、`WmsDocumentPort`、`WmsFulfillmentPort`、`WmsGrnReceivedEvent` 等为 LOW。
- `FullBoxExchangeOperationRequest`、`WmsEffectStatusRequest` 为 MEDIUM；本任务未改动旧 runtime status
  接线。
- `WmsProviderOperationBinding` 为 HIGH、`build_active_wms_provider_profile` 为 CRITICAL；首轮未修改，
  第二轮审查明确要求 active Provider 直接消费 35 项 registry 后，在用户授权范围内完成静态组合修复，
  未实现 T2/T5 endpoint compiler/dispatcher。
- 索引刷新后运行 `gitnexus detect-changes --scope all`：21 files / 82 symbols，0 affected processes，
  risk level LOW。

## 验证

- `uv run pytest tests/contracts/wms_integration tests/wms_integration tests/architecture/test_wms_7_ports_contract.py -q`
  加 fulfillment 边界最终复核 → 391 passed。
- `uv run pytest tests/mock/test_wms_northbound_contract.py -q` → 37 passed。
- 针对 catalog、Mock、process naming 与 fulfillment 边界复核 → 62 passed。
- `uv run pytest tests/architecture/test_test_suite_topology_guardrail.py -q` → 6 passed。
- `uv run pytest --collect-only -q -o addopts=''` → 4249 tests collected。
- `./scripts/git-quality-gate.sh --profile quality` → PASS（Ruff format/check、Bandit、runtime guardrails
  365 passed、legacy absence、process naming、import-linter、architecture、topology）。

## 后续边界

本任务只冻结目标合同与静态派生资产，不提前切换运行时 endpoint compiler/dispatcher。现有旧 runtime
full-box/status/transport 执行路径由 T2/T5 按本 registry 迁移；它们不属于目标 Provider manifest，也不能作为新
operation 的兼容入口。

## 第二轮审查修复

- active Provider profile、runtime index、SLO 与 signal registry 改为直接派生唯一 35 项
  `WMS_OPERATIONS`；删除并行的 index builder 和生成脚本。
- 保留既有 QUERY/EFFECT transport 的必要读取兼容，但目标 Provider binding 的 operation 对象严格引用
  registry 原对象；旧 target code 仅用于已投产三项 transport 的冻结 binding，不进入目标 manifest。
- Mock completion 口径收紧为：9 项 `SYNC_RESULT` 直接返回 200 typed result，拒绝 status query 与
  callback hint；仅 E08–E14 返回 202/200 共用 ACK 并开放独立 status/hint。
- E12 ACK 必须接纳完整冻结批次；E13 ACK 只能接纳有序前缀；两者均要求非空、唯一成员与 canonical
  SHA-256 digest。terminal result 必须与 ACK 和原请求的 sequence、route、bin 身份逐项对应。
- Mock Server 与 feasibility probe 直接遍历新 registry/fixture，未知 path fail closed；删除旧三项异步
  语义，不提供旧 payload 或 identity fallback。

## 第二轮 TDD 与验证

1. RED：active Provider 仍只有旧 4 项，且运行时 index 仍依赖并行 builder。
2. GREEN：Provider/index 精确覆盖 35 项并直接引用 registry；builder/codegen 已删除。
3. RED：9 项同步 EFFECT 返回 202，仍可查询 status/hint。
4. GREEN：同步 EFFECT 直接 200 typed result；E08–E14 独占异步状态机。
5. RED：E12/E13 可接纳重复成员、非前缀/部分 ACK，且 terminal 未校验成员闭环。
6. GREEN：新增 7 项批量 ACK 合同测试，覆盖完整批次、有序前缀、digest 和 terminal 对应。

最终验证：

- `uv run pytest tests/contracts/wms_integration tests/mock/test_wms_northbound_contract.py
  tests/mock/test_wms_mock_server.py tests/runtime/orchestration/test_northbound_operation_observability.py -q`
  → 421 passed。
- `uv run pytest tests/contracts/wms_integration/test_wms_batch_ack_contract.py -q` → 7 passed。
- `./scripts/git-quality-gate.sh --profile quality` → PASS（Bandit 0 issue、runtime guardrails 365 passed、
  process naming 11 passed、architecture/import-linter/topology 全通过）。
- `gitnexus detect-changes --scope all` → 16 indexed files / 172 symbols / 10 affected processes，
  risk HIGH；命中范围为预期的 QUERY observability 和 Mock status 流程，无 T2/T5 runtime
  endpoint compiler/dispatcher。

## 第三轮审查修复

- E02 删除 DELETE/旧 envelope，只保留 registry 冻结的
  `POST /inventory/reservations/release` typed request/result。
- Mock 启动时从唯一 registry 一次性注册 35 条明确 route；删除请求期 catch-all、重复 typed handler 与
  `/fulfillment/package-binding`、`/inventory/reserve`、`/outbound/confirm` 等旧 alias 路径。
- Q14 改为静态 GET route 并严格消费 `material_code`，响应直接符合 typed result，不再接受
  `material_id`/`sku` alias 或旧 envelope。
- Provider External HTTP binding、旧三项 author-time gateway 合同统一使用 registry canonical
  target code；删除 `_LEGACY_EFFECT_TARGET_CODES`。本节替代第二轮报告中“旧 target code 读取兼容”的
  临时结论。
- E13 `candidate_digest` 改为绑定 `workline_id`、`queue_code` 与有序冻结候选的 canonical SHA-256；
  篡改 digest 或仅重排候选均在 request model 校验阶段 fail closed。

## 第三轮 TDD 与验证

1. RED：E02 仍返回旧 envelope，DELETE 路由仍存在；35 项 route 未标记为 registry 静态注册，Q14
   `material_code` 请求返回 422。
2. GREEN：35 项 operation 全部通过启动期显式 route 注册，E02/Q14 均直接通过对应 typed model。
3. RED：3 个 External HTTP binding 仍接受旧 target code；3 条旧 alias 路径仍注册。
4. GREEN：binding 仅接受各 operation 的 canonical target code，旧 alias 路径与旧 target code 检索为零。
5. RED：E13 接受伪造 digest 与候选重排。
6. GREEN：正确 digest 通过，伪造 digest 与重排候选均触发 `ValidationError`。

最终验证：

- `uv run pytest tests/contracts/wms_integration tests/mock/test_wms_northbound_contract.py
  tests/mock/test_wms_mock_server.py tests/runtime/orchestration/test_northbound_operation_observability.py -q`
  → 426 passed。
- `./scripts/git-quality-gate.sh --profile quality` → PASS（Ruff format/check、Bandit 0 issue、runtime
  guardrails 365 passed、process naming 11 passed、architecture/import-linter/topology 全通过）。
- worktree `gitnexus detect-changes --scope all` → 13 indexed files / 62 symbols / 2 affected processes，
  risk MEDIUM；仅命中 Q14 Mock 查询/HMAC 流程，未实现 T2/T5 endpoint compiler/dispatcher。

## Final Review 修复

- 删除 E03/E07 的 status binding 与 hint 资格；E11 不再属于旧固定三项集合。status contract、scanner、
  reducer 与 hint router 统一从 35 项 registry 派生 E08–E14 精确集合，集合外 fail closed。
- 通用 status request 先按对应 Definition 校验冻结 EFFECT payload，再从 request/result 共享标量字段派生
  correlation；`COMPLETED` 按 registry 的 operation-specific result model 解析，不再保留旧三项 parser、
  identity alias 或 fallback。
- 区分 16 项 EFFECT 的通用幂等分类与 7 项异步 status 分类，避免 E03/E07 同步化后丢失提交幂等校验。
- 删除 Mock 的 `/api/wms/rack-operation`、`/api/wms/transport-request`、
  `/api/wms/legacy/full-box-exchange` 及其终态 callback；删除只验证这些兼容行为的测试，未注册 path 保持
  `404`。
- 删除旧三项 status replay fixture 和基于 E07 preparation 的 PostgreSQL status 集成测试。E08–E14 的
  PostgreSQL dispatcher 接线留给 T5，本任务未增加桥接、双写或临时兼容。
- 删除 `timeout_seconds`、`endpoint_path`、`retry_policy` Definition alias；QUERY transport、黑盒探针
  与测试直接读取 `deadline_seconds`、`path_template`、`max_attempts`、`backoff_seconds`。
- 业务蓝图改为 E08–E14 的 `typed ACK → status query → typed terminal result`，callback 只保留
  `WMS_EFFECT_STATUS_HINT`；同步更新北向验收/可行性文档并修复合同尾随空白。

## Final Review TDD 与验证

1. RED：新增五项合同测试，分别证明旧固定三项 status 分类、Definition alias、业务蓝图旧终态、
   E08 hint 拒绝和 Mock 旧 route 仍可达。
2. GREEN：五项合同全部转绿；旧三项专用 status parser/handler/fixture 引用归零，Mock 旧 path 仅保留
   “未注册”断言。
3. 扩大回归发现 QUERY 测试仍把旧 runtime contract 注入新 transport，以及 reducer/outbox 测试仍以 E07
   代表异步 status；测试 fixture 分别迁移到 registry Definition 与 E08。
4. 默认全集首次运行发现四项旧架构假设；更新为 19 QUERY、16 EFFECT、9 sync/7 async、35 Provider
   bindings，并刷新零遗留报告后全部转绿。

最终验证：

- 受影响 WMS/runtime/Mock/contract 回归 → `582 passed`。
- `uv run pytest tests/ -q` → `4272 passed, 5 skipped`。
- `uv run pytest tests/architecture/test_test_suite_topology_guardrail.py -q` → `6 passed`。
- `uv run pytest --collect-only -q -o addopts=''` → `4277 tests collected`。
- `uv run ruff format --check .`、`uv run ruff check .`、`git diff --check` → PASS。
- `./scripts/git-quality-gate.sh --profile quality` → PASS（Bandit 0 issue、runtime guardrails 365 passed、
  process naming 11 passed、import-linter、architecture、topology 全通过）。
- worktree `gitnexus detect-changes --scope staged` → 41 indexed files / 135 symbols / 6 affected processes，
  risk HIGH；命中 QUERY transport 与 Mock/合同流程，属于已授权 Final Review 修复范围，未实现 T2/T5。

## Release Review 修复

- 旧 rack/handling/single-layer transport 与旧 terminal callback 全部 fail closed；生产源码中的旧 identity /
  callback type 只允许出现在 migration manifest，删除 FullBox 旧 callback 正向消费与 transport completed
  event surface。
- 物理删除 E03/E07/E11 的重复 author-time capability、port、adapter、handler 与 PostgreSQL 正向测试；
  E03/E07 同步执行器和 E11 新选箱执行器在 T5 落地前明确拒绝，不创建旧 SystemOutbox，也不提供 alias、
  fallback 或 optional bypass。
- E12/E13 status request 新增强制 `frozen_ack`；request、ACK、terminal result 必须逐项匹配 operation、
  idempotency、批次成员、顺序与业务字段，通用 request 无 ACK 时 fail closed。
- Mock `/api/wms` surface 精确等于 35 项 registry route；调试资源迁至 `/debug/wms`。Q19 POST 与 GET
  统一执行 HMAC、时间窗、nonce replay 与 body 签名校验。
- external contract profile、runtime readiness、legacy matrix/absence ledger 同步为当前 registry 和
  fail-closed 边界；清理已删除测试的旧 evidence path 与派生统计。
- heavy integration/resilience 测试移除已删除 typed EFFECT imports；通用 EXTERNAL_HTTP 冻结/崩溃合同
  改用测试专用 identity，不再复活旧 transport producer。

## Release Review TDD 与验证

1. RED：新增 removal guardrail、E12/E13 冻结 ACK/parser、35 路由 surface、Q19 POST HMAC 合同后，
   分别暴露旧生产 identity/callback、无 ACK batch parser、额外 `/api/wms` 路由和 POST 未验签。
2. GREEN：六项 release finding 聚焦回归 `109 passed`；原默认全集暴露的 33 项陈旧正向测试和 evidence
   引用全部收敛为 absence/fail-closed 合同，对应回归 `132 passed`。
3. 默认全集首次复验为 `4137 passed, 5 skipped, 2 failed`，仅剩派生文档旧计数；同步 ledger/matrix
   后 closure 合同 `33 passed`，最终全集全部通过。

最终验证：

- `uv run pytest tests/ -q` → `4139 passed, 5 skipped`（4144 collected）。
- `uv run pytest tests/architecture/test_test_suite_topology_guardrail.py -q` → `6 passed`。
- `uv run pytest --collect-only -q -o addopts=''` → `4144 tests collected`。
- `uv run pytest tests/contracts/wms_integration/test_release_removal_guardrails.py
  tests/contracts/wms_integration/test_wms_batch_ack_contract.py tests/mock/test_wms_mock_server.py -q`
  → `109 passed`。
- `uv run python scripts/generate_runtime_extensions.py --check`、`uv run ruff format --check .`、
  `uv run ruff check .`、`git diff --check` → PASS。
- `./scripts/git-quality-gate.sh --profile quality` → PASS（Bandit 0 issue、runtime guardrails 365 passed、
  business legacy absence、process naming 11 passed、import-linter、architecture、topology 全通过）。
- 受影响 heavy tests 14 项可完整收集；本机未配置 `INTEGRATION_DATABASE_URL`，实际执行为 11 skipped，
  3 项在数据库初始化前由 heavy harness 以 `missing_url` 拒绝，未产生代码断言失败。
- 当前 worktree GitNexus MCP 因 LadybugDB storage version 不兼容无法读取；重建索引后使用同版本 CLI
  `detect-changes --scope all` 成功检测 61 indexed files / 141 symbols / 15 affected processes，risk HIGH。
  命中旧 rack/handling/single-layer transport 与 callback/WMS 合同流程，属于已授权 Release Review
  fail-closed/删除范围；未实现 T2/T3/T5。

## Release Re-review 修复

- 外部 WMS/RCS callback 在 ingress、normalizer 与 orchestration 三层统一 fail closed；只允许四类普通事件和
  `WMS_EFFECT_STATUS_HINT`。旧 rack/bin/handling/full-box/transport terminal callback 在写入
  RuntimeInbox 前即被拒绝，且不再调用 rack/handling lifecycle。
- removal guard 扩展为完整旧 callback family、transport identities、facade symbol/file、活跃文档和
  operation allow-set 一致性检查；删除旧 transport facade、rack/handling gateway 与旧正向 facade 测试。
- Handling、Rack 与单层货架尚未迁移的生产入口改为所属领域的明确 migration-required error；不保留异常后
  的不可达 compatibility 实现。
- Mock 以唯一 registry 参数化覆盖 9 项 `SYNC_RESULT` 的直接终态，以及 E08–E14 各自的 ACK/status/hint；
  删除把多个 operation 覆盖成 E08 的假参数化和旧 rack terminal producer。E11 fixture 不包含
  `empty_box_id`。
- 分拣预览不再读取 `scanner_platform_state` 或计算 `source_arm_prefetch_capacity`；下一次北向取料只由
  `southbound_pick_acknowledged` 触发。Mock、合同、业务文档与 legacy ledger 同步为 ACK 因果。
- 活跃 WMS/RCS 文档只发布 35 项 registry、四类普通事件和 E08–E14 status hint；legacy mapping 测试改用
  当前 Provider profile 与 canonical operation identity。

## Release Re-review TDD 与验证

1. RED：参数化旧 callback family 后，旧 ingress/orchestration 仍写 RuntimeInbox 并调用 lifecycle；
   Mock 的多组参数实际覆盖为同一个 E08；预览仍依赖平台 FREE/预取字段。
2. GREEN：三层 callback allow-set、Mock registry 参数化、ACK-only 预览与 facade 物理删除完成；直接调用
   orchestration 也无法绕过 ingress。
3. 默认全集首轮暴露 22 个旧 lifecycle/Event Port/ledger 假设；修复后第二轮为
   `4159 passed, 5 skipped, 9 failed`，剩余均为跨域 import 和派生 ledger 统计；清理跨域 import 并刷新
   账本后第三轮全部通过。

最终验证：

- `uv run pytest` → `4168 passed, 5 skipped`（4173 collected）。
- 显式 Mock 重测试 → `88 passed`；callback/removal 聚焦回归 → `111 passed`，最终 removal guard
  → `8 passed`。
- `uv run pytest tests/architecture/test_test_suite_topology_guardrail.py -q` → `6 passed`。
- `uv run pytest --collect-only -q -o addopts=''` → `4173 tests collected`。
- `uv run ruff format --check .` → 1078 files already formatted；`uv run ruff check .`、`git diff --check`
  → PASS。
- `./scripts/git-quality-gate.sh --profile quality` → PASS（Bandit 0 issue、runtime contracts 365 passed、
  business legacy final、process naming 11 passed、import-linter、architecture enforced、topology 全通过）。
- worktree `gitnexus detect-changes --scope all` → 45 files / 149 symbols / 8 affected processes，risk HIGH；
  命中 callback、rack/handling/single-layer fail-closed 与 sandbox callback 流程，属于用户已授权的
  Release Re-review 范围；未实现 T2/T3/T5。
