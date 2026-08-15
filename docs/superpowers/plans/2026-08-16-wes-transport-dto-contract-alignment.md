# WES Transport DTO 接口契约定向对齐实施计划

> **实施入口：** 使用 `wes-implementation` 直接模式按任务切片执行；可观察行为严格遵循 RED → GREEN → REFACTOR。本文不授权 Commit、Push、PR、Merge、Deploy 或生产迁移。

**Goal:** 在不重做 Phase 1～Phase 7、不启动 Phase 8 或数据库迁移基线重置的前提下，使 Transport 当前设计、跨系统合同、WMS 接入说明、Python DTO、FastAPI、序列化、OpenAPI、持久化幂等和测试证据一致。

**Architecture:** 保留 Transport 核心四个领域方法及内部 `BinMove.bin_id`、`BinExchangePair`、`TransportMember.object_id`，只在 `src/app/wms_adapter/` 将内部模型转换为 WMS 接口契约。T1 保存并发送同一份冻结请求体字节；T2/T3 使用一个带数据库唯一约束的回调收据作为 `operation + operation_id` 唯一身份 owner，合法 evidence 与首次响应在同一事务保存，可关联的非法请求只保存收据和首次 422，不产生 evidence。

**Tech Stack:** Python 3.13、FastAPI、SQLModel/SQLAlchemy、Alembic、PostgreSQL、Pytest、OpenAPI 3.0.3、Ruff、GitNexus。

**Status:** `Implemented`。Transport v0.2 定向对齐和本地验证已完成；工作区保持未提交，WMS 提供方实现、双方联调、部署和业务验收不在本次完成声明内。

## 1. 全局约束

- 当前系统未发布，直接替换旧接口契约；不提供 alias、shim、v2、双读、双写、兼容 wrapper 或旧数据迁移。
- 严格保持 `API → Service → Repository → Database`；WMS ACL 只位于 `src/app/wms_adapter/`。
- T1 是 WES 调用 WMS 的接口，服务端 OpenAPI 由 WMS 提供；WES 的独立 OpenAPI 只拥有 T2/T3 的 `POST /api/v1/wms/events`。
- `TransportTask` 与 `DeviceCommand` 保持并行；不修改 ECS、供应商私有协议、业务插件或 `docs/hardware/`。
- 三份当前态文档只在代码、测试和机器可读契约全部通过验证后更新为 `ALIGNED`。
- 新 Alembic revision 只承载本次 Transport 回调收据、摘要命名和冻结请求体列；由 generator 生成随机 revision ID，不删除或压缩历史 revision chain。
- 开发/测试 Transport 数据允许清理；不为旧行编写回填、兼容列或双字段过渡。
- 计划中的验证不包含 Commit；所有最终证据绑定本分支最终未提交工作树快照。

## 2. 合同—代码—测试—OpenAPI—阶段归属差异矩阵

| 对齐项 | 当前文档要求 | 当前代码与测试证据 | 当前 OpenAPI | 结论 | 最小修改面与测试 owner | 风险 / 阶段 |
| --- | --- | --- | --- | --- | --- | --- |
| T1 货架 | `RACK_MOVE/RACK_ROTATE` 均为 `source + target + target_face`；成功同时匹配位置和朝向 | `MoveRackRequest`、`TransportPort.move_rack()`、`TransportService.move_rack()` 缺少 `target_face`；`RACK_ROTATE` 出站仍用 `position`；旧测试通过 | WES 不拥有 T1 服务端 OpenAPI | 不一致 | `transport/contracts.py`、`service.py`、`submit_snapshot.py`；`tests/runtime/transport/` 与 `tests/contracts/wms_adapter/` | MEDIUM / Phase 4 |
| T1 料箱 | `BIN_MOVE/BIN_EXCHANGE` 统一 `moves[].container_id + source + target`；交换为 2/4 个互不重叠二元环；按 `container_id` 排序 | `BIN_MOVE` 出站为 `moves[].bin_id`；`BIN_EXCHANGE` 出站为 `exchange_pairs`；内部 pair 可保留 | WES 不拥有 T1 服务端 OpenAPI | 不一致 | `submit_snapshot.py` 只做 ACL 展开和排序；保留内部领域类；WMS Adapter FAST 合同承接 | MEDIUM / Phase 4 |
| T1 ACK | 删除 429、`BUSY`、`retry_after_ms` | `TransportSubmitCode`、Adapter 和 Service 仍接受 BUSY 并使用动态等待；旧测试保护该行为 | T2/T3 OpenAPI 不表达 T1 ACK | 不一致 | `transport/contracts.py`、`transport_adapter.py`、`service.py`；Adapter 与 runtime 测试承接 | MEDIUM / Phase 4 |
| 重试与投递不确定 | 仅 `NOT_SENT`、明确 503 固定等待 2 秒；可能写出后进入 `DELIVERY_UNKNOWN → RECONCILING`；复用同一冻结请求体字节 | delivery state 区分能力已存在；重试状态基本存在，但每次由 JSON 对象重新编码，未持久化最终请求体字节 | 不适用 | 部分一致 | TransportTask 保存冻结请求体；WmsClient 增加受控 JSON bytes 发送入口；core Client 测试与 Transport 集成测试分别承接 | MEDIUM / Phase 3 边界回归 + Phase 4 |
| T2 | 使用 `container_id`；货架不发送 T2 | validator、Service、测试和 OpenAPI 仍用 `bin_id` | 静态与运行时 schema 使用 `bin_id` | 不一致 | `transport_wire.py`、`service.py`、`transport_openapi.py`、静态 JSON；合同/API/集成测试 | MEDIUM / Phase 4、6 |
| T3 | 货架结果直接使用 `rack_id`；料箱 `results[].container_id`；两族严格 DTO | 当前所有 kind 统一 `results[].object_id`；成功仅对 `RACK_ROTATE` 校验朝向 | 当前 schema 为通用 `results[].object_id` | 不一致 | 接口业务载荷校验器、内部 ACL 归一化、成功条件、OpenAPI；合同/API/runtime 测试 | MEDIUM / Phase 4、6 |
| 可关联回调拒绝 | 解析出 `operation + operation_id` 后保存完整消息信封摘要和首次 422；同摘要稳定重放、异摘要冲突；数据库并发约束 | Handler 在 DTO 校验失败时直接返回 422；`transport_evidence` 只能保存合法 task/evidence，且未保存首次 HTTP/code；旧测试明确断言“不持久化” | OpenAPI 只描述响应形状，不能证明持久化 | 不一致，现有 schema 无法表达 | 新 `TransportCallbackReceipt` + Repository；Handler 作为 WMS ACL Service 原子保存 receipt/evidence；`tests/integration/wms_adapter/` 承接并发、事务、回滚 | MEDIUM / Phase 4、6；需要定向 migration |
| 摘要范围和命名 | `data_digest` 只覆盖 data；`message_digest` 覆盖完整消息信封；`request_body_digest` 覆盖最终 HTTP 请求体 | `submit_payload_digest` 与 evidence `payload_digest` 实际覆盖完整消息信封；名称误导；任务 `payload_digest` 是内部请求幂等摘要 | 不适用 | 不一致 | 直接重命名为 `request_digest`、`submit_request_body_digest`、`message_digest`；不保留旧属性 | MEDIUM / Phase 4；需要定向 migration |
| FastAPI facade | 唯一 `/api/v1/wms/events`；route 只做有界读取、认证和调用 Service | route 唯一且无 Repository 直连；runtime 缺席时仍即时构造未持久化 422 | 运行时拥有 T2/T3 path | 部分一致 | 保持单 route；runtime 缺席只返回 503 或预关联空响应，不能伪造已持久化 422；API 测试承接 | LOW / Phase 6 |
| 静态 OpenAPI | WES 文件只拥有 T2/T3，并与 FastAPI schema 一致 | builder 与静态文件已只有 `/api/v1/wms/events`，但字段仍是旧 T2/T3；Transport 合同末节对 T1 所有权表述过时 | path 所有权正确，DTO 错误 | 部分一致 | 更新 builder、静态 JSON、合同所有权措辞；机器合同测试承接 JSON，不测试 Markdown 正文 | LOW / Phase 4、6 |
| Phase 1/2/3/5/7 回归 | 只验证受影响边界，不重写 | 测试治理、基础 HTTP、WmsClient、零插件、DeviceCommand/ECS 均有独立 owner | 不适用 | 需回归 | 拓扑 guardrail、core outbound HTTP、WMS Client、退役插件缺席、DeviceCommand 边界测试 | LOW / 边界回归 |

## 3. 架构决策

### 3.1 T1 冻结请求体

- `build_submit_data()` 是内部领域对象到 WMS `data` 的唯一 ACL：货架输出两族固定字段；料箱把内部 `bin_id` 映射为 `container_id`，把 `BinExchangePair` 展开为二元环 moves，并按 `container_id` 排序。
- 首次创建任务时使用与 WmsClient 相同的严格 UTF-8 JSON 编码形成最终请求体字节，持久化请求体及 `request_body_digest`。
- Provider 发送和技术重试只读取冻结请求体，不重新生成 timestamp、排序或默认字段；WmsClient 的 bytes 入口只负责基础 HTTP，不解释 Transport。

### 3.2 回调身份与 evidence 分离

- 新增 `TransportCallbackReceipt`，以数据库唯一约束 `operation + operation_id` 裁决全部可关联回调，保存 `message_digest`、完整消息信封、首次 HTTP/code/timestamp/data。
- 合法 T2/T3 在同一事务写入 receipt 与 `TransportEvidence`；非法 DTO 只写 receipt 并保存首次 422；`503` 不写 receipt。
- 同身份同摘要：首次 `RECEIVED` 转为 `DUPLICATE`，首次 `REJECTED/CONFLICT` 原样重放；同身份不同摘要返回 409；不触发业务副作用。
- `transport_task_id + outcome_revision` 继续由数据库唯一约束裁决。不同消息身份复用同一版本时保存当前身份的首次冲突 receipt，不保存第二份 evidence。

### 3.3 两族回调 DTO 到内部模型

- T2 `container_id` 在 ACL 边界映射为内部成员 `object_id`。
- T3 货架顶层 `rack_id/status/...` 在 ACL 边界归一化为一个内部成员结果；料箱 `results[].container_id` 归一化为内部成员结果列表。
- 核心结果应用继续面向 `TransportMember.object_id`；货架和料箱成功都校验冻结 target，货架成功额外校验冻结 `target_face`。

## 4. 实施任务切片

### Task 1: T1 货架、料箱 DTO 与冻结请求体

**Files:**

- Modify: `src/app/transport/contracts.py`
- Modify: `src/app/transport/submit_snapshot.py`
- Modify: `src/app/transport/service.py`
- Modify: `src/app/wms_adapter/client.py`
- Modify: `src/app/wms_adapter/transport_adapter.py`
- Test: `tests/runtime/transport/`
- Test: `tests/contracts/wms_adapter/`
- Test: `tests/contracts/wms_adapter/test_client.py`

**切片与验证：**

1. RED：`MoveRackRequest`/Service 缺少 `target_face`，货架 T1 业务载荷不是统一结构。
2. GREEN：显式接收并冻结 `target_face`；RACK_MOVE 与 RACK_ROTATE 输出 `source + target + target_face`。
3. RED：料箱 T1 业务载荷仍出现 `bin_id/exchange_pairs`，排序不稳定。
4. GREEN：ACL 输出统一 `moves[].container_id`，交换为 2/4 个二元环并稳定排序；内部 pair 保留。
5. RED：相同冻结任务重试未证明复用同一 bytes。
6. GREEN：保存并发送冻结请求体 bytes，摘要覆盖最终 HTTP 请求体。
7. 运行受影响 runtime、WMS Adapter 和 WmsClient 聚焦测试。

### Task 2: ACK、固定重试与投递不确定状态

**Files:**

- Modify: `src/app/transport/contracts.py`
- Modify: `src/app/transport/service.py`
- Modify: `src/app/wms_adapter/transport_adapter.py`
- Test: `tests/contracts/wms_adapter/test_transport_adapter.py`
- Test: `tests/contracts/wms_adapter/test_transport_adapter_qa_regressions.py`
- Test: `tests/runtime/transport/test_transport_service.py`

**切片与验证：**

1. RED：429/BUSY/retry_after_ms 仍可解析和安排动态重试。
2. GREEN：删除旧 enum、字段和分支；未知 429 进入 `DELIVERY_UNKNOWN`。
3. RED/GREEN：分别覆盖 `NOT_SENT`、503、写出后超时/断连和三次预算；只有前两者固定 2 秒重试。

### Task 3: T2/T3 两族 DTO、内部 ACL 与 OpenAPI

**Files:**

- Modify: `src/app/wms_adapter/transport_wire.py`
- Modify: `src/app/wms_adapter/transport_openapi.py`
- Modify: `src/app/transport/service.py`
- Modify: `docs/contracts/openapi/wes-wms-transport.openapi.json`
- Test: `tests/contracts/wms_adapter/test_transport_wire_acceptance.py`
- Test: `tests/contracts/wms_adapter/test_transport_openapi.py`
- Test: `tests/api/test_qa_regression_transport_openapi.py`
- Test: `tests/runtime/transport/test_transport_outcome*.py`

**切片与验证：**

1. RED/GREEN：T2 只接收 `container_id`，拒绝 `bin_id`。
2. RED/GREEN：货架 T3 直接使用 `rack_id`；料箱 T3 使用 `results[].container_id`；拒绝通用 `object_id` 和单元素 rack results。
3. RED/GREEN：两族外部 DTO 归一化为内部成员，完整成员、位置、朝向和 outcome revision 不变量保持。
4. 更新运行时和静态 OpenAPI；证明静态 artifact 等于 builder，path 仅为 WES 所有的 T2/T3。

### Task 4: 可关联非法回调的持久化幂等

**Files:**

- Modify: `src/app/transport/models.py`
- Modify: `src/app/transport/repository.py`
- Modify: `src/app/wms_adapter/transport_event_handler.py`
- Modify: `src/app/transport/composition.py`
- Generate/Modify: `migrations/versions/` 下由 `uv run alembic revision -m "对齐 Transport 回调收据与冻结请求体"` 生成的唯一 revision 文件
- Modify: `docs/architecture/heavy-test-impact.toml`
- Test: `tests/contracts/wms_adapter/test_transport_event_handler.py`
- Test: `tests/integration/wms_adapter/`
- Test: `tests/integration/transport/test_transport_schema.py`

**切片与验证：**

1. RED：首次可关联非法 DTO、相同内容重放、不同内容冲突、并发重放和事务失败均不满足目标。
2. 使用 Alembic generator 创建定向 revision；增加 receipt 唯一约束和范围明确的摘要/响应字段，直接重命名误导列，不保留兼容列。
3. GREEN：Handler 先建立消息身份，再校验 DTO；receipt/evidence 同事务；非法 DTO 无 evidence；并发由唯一约束收敛。
4. 在新鲜隔离 PostgreSQL 上验证 upgrade head、约束、并发、回滚和无跳过集成测试。

### Task 5: FastAPI、生产基线与当前态文档收敛

**Files:**

- Modify if required: `src/app/wms_adapter/v1/events.py`
- Modify: `docs/superpowers/specs/2026-08-14-wes-wms-transport-dto-design.md`
- Modify: `docs/contracts/transport-fulfillment-contract.md`
- Modify: `docs/integration/wes-wms-interface-requirements.md`
- Test: `tests/api/test_wms_transport_events.py`
- Test: `tests/e2e/transport/test_transport_production_wiring.py`

**切片与验证：**

1. RED/GREEN：route 只做 facade；runtime 不可用时不产生无法持久化的终局 422。
2. 验证唯一 route、真实 composition、broker/worker/HTTP/PostgreSQL 生产基线，不把通过写成联调或业务验收。
3. 仅在最终代码与全部验收通过后，把三份当前态文档的实现状态改为准确状态，收敛 OpenAPI 所有权和术语；保留 `ReviewRequired` 附录。

## 5. 阶段归属

- Phase 4：领域请求、冻结请求体、WMS ACL、ACK、T2/T3、回调 receipt/evidence 与定向 schema 是主要实施范围。
- Phase 6：重新验证唯一 FastAPI route、composition、Celery 任务、PostgreSQL 持久化和静态/运行时 OpenAPI。
- Phase 1：只验证测试拓扑、FAST/HEAVY 所有权和 selector mapping。
- Phase 2：只回归基础 outbound HTTP delivery state；不修改其业务语义。
- Phase 3：只为 WmsClient 增加发送冻结 JSON bytes 的最小入口并做独立合同回归。
- Phase 5：只回归退役插件缺席，不恢复任何 plugin consumer。
- Phase 7：只回归 DeviceCommand/ECS 与 Transport 平行边界，不修改其代码和合同。
- Phase 8：不实施；真实业务插件继续留待后续阶段。
- Phase 11：不启动；本次只追加一条普通定向 revision，不压缩、删除或重建 migration baseline。

## 6. 测试策略与最终门禁

每个切片先运行失败测试并记录失败原因，再完成最小实现和聚焦 GREEN。最终快照执行：

1. 受影响的 `tests/runtime/transport/`、`tests/contracts/wms_adapter/`、`tests/api/` 和 WmsClient/core 聚焦测试。
2. `tests/integration/wms_adapter/` 与受影响的 `tests/integration/transport/`，真实 PostgreSQL、并发与事务测试不得 skip。
3. `uv run pytest tests/architecture/test_suite_topology_guardrail.py tests/architecture/test_core_plugin_test_ownership_guardrail.py -q`。
4. `uv run pytest --collect-only -q -o addopts=''`。
5. 对全部变更 Python 文件运行 `uv run ruff format --check` 与 `uv run ruff check`。
6. `uv run scripts/select_heavy_tests.py --scope unstaged`，随后运行 manifest 中全部 HEAVY。
7. `./scripts/git-quality-gate.sh --profile quality`。
8. Markdown 引用、OpenAPI artifact、术语残留、归档边界和 `git diff --check`。
9. `gitnexus_detect_changes({scope: "all"})`；未授权暂存或提交时不伪造 staged 证据。

## 7. 验收标准

- T1 外部请求体、T2/T3 外部回调、ACK 和 OpenAPI 中无旧字段或兼容分支。
- T1 重试复用相同 `operation_id`、timestamp、冻结请求体 bytes 与 `request_body_digest`。
- 只有 `NOT_SENT` 和明确 503 固定 2 秒自动重试；投递不确定进入对账。
- 可关联非法回调的首次 422、重复、冲突、并发与事务回滚均有 PostgreSQL 证据。
- 货架成功同时匹配冻结位置和目标面；料箱结果完整覆盖冻结成员。
- 静态 OpenAPI 只表达 WES 提供的 T2/T3；T1 出站通过合同测试验证并记录需 WMS 提供服务端 OpenAPI。
- 最终 diff 不包含 DeviceCommand/ECS、业务插件、完整 migration baseline reset、`docs/hardware/` 或兼容层。
- 结论只证明本地代码和生产基线验证，不宣称部署、WMS/RCS 联调、供应商一致性或业务验收。

## 8. 风险和停止条件

- 用户未确认本次定向 schema 扩展前，不修改生产代码、模型或 migration。
- 后续 GitNexus `HIGH` 影响链按用户授权记录范围后继续；`CRITICAL` 仍立即停止并报告目标符号、直接调用者和受影响流程。
- 无法用一张 receipt 表和数据库唯一约束同时裁决合法、非法与并发回调身份时停止，不退化为进程锁或跨表唯一性猜测。
- WmsClient 无法在不破坏基础传输边界的前提下发送冻结请求体 bytes 时停止，不在 Adapter 绕过 Client。
- 新鲜 PostgreSQL 无法安全验证定向 revision 时停止，不执行生产迁移，不启动 Phase 11。
- 三份当前态文档出现用户当前指令不能裁决的新外部字段冲突时停止并请求裁决。
