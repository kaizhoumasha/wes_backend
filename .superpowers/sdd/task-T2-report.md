# T2 实施报告：WMS typed operation Foundation 合同

## 状态与范围

T2 已实现并通过本任务门禁。变更只建立四个真实 WMS operation 的 typed Foundation：Port 领域合同、operation transport 合同、Provider profile 组合、入站 callback 合同、conformance manifest、确定性 generated index，以及三个 EFFECT 到既有 `DispatchEnvelope` 的领域 gateway。

本任务没有迁移 QUERY/EFFECT 运行路径，没有修改 `SystemCapabilityDefinition`、`WmsTypedPortService` 或旧 `ExternalContractProfile`，也没有删除缓存、字符串 `Port.method` 路径、旧专用 capability、WMS endpoint 配置或任何旧数据。

## 实施内容

### 四个独立 operation 合同

四个稳定 identity 只在 `src/app/wms_integration/ports/` 的相应 operation 模块定义：

- `wms.inventory.query_inventory@v1`
- `wms.inventory.confirm_inbound@v1`
- `wms.fulfillment.notify_pkg_binding@v1`
- `wms.fulfillment.full_box_exchange@v1`

每个 operation 都有独立、冻结、`extra="forbid"` 的 request/result 与独立 Protocol。库存与入库数量使用 `Decimal`；没有 `Decimal → float → Decimal` 往返。

### Provider ACL 与缺省事实

新增库存查询 Provider DTO 和唯一 adapter 映射函数。Provider DTO 使用与领域 snapshot 不同的名称；`available_qty` 直接映射为领域 `Decimal`。Provider 未提供的 warehouse、库位、owner、source version 等事实保持 `None`，不补 `UNKNOWN`、空字符串或其它猜测值。

### operation catalog/profile/callback

`WmsOperationContract` 为每个 operation 固定 mode、request/result model、endpoint、HTTP method、预算、retry policy 与所需出站 auth scheme。`ExternalContractProfile` 只包含 provider/version/environment identity；operation binding 和 `OutboundAuthProfile` 采用组合模型，credential 只保存版本化 reference，不进入 operation identity。

三个 EFFECT 各自声明 `InboundCallbackContract`，并直接复用对应 Port result model；QUERY 不声明 callback。production binding 显式拒绝 `NONE` auth，且 binding auth scheme 必须匹配 operation contract。

### EFFECT gateway

三个 EFFECT 各有独立领域 gateway：

- `ConfirmInboundDispatchGateway`
- `NotifyPackageBindingDispatchGateway`
- `FullBoxExchangeDispatchGateway`

gateway 只做 typed request 到既有 `DispatchEnvelope` 的单次 canonical payload 映射，不执行 I/O、不写 outbox、不实现 reducer/retry/dispatcher。可选字段为 `None` 时从 payload 中省略，不伪造缺省值；Decimal quantity 以无损十进制字符串进入 JSON 投影。

### conformance manifest 与 generated index

构建期 `WMS_CONFORMANCE_MANIFEST` 单独承载 fixture root 与 required cases，不进入运行时 profile。`WmsOperationIndexBuilder` 从 typed Provider profile 派生 identity、contract digest 与只读 `MappingProxyType` index；`scripts/generate_wms_operation_index.py --check` 验证生成物零漂移。generated index 不做目录扫描、动态 import 或手工维护第二份 identity。

## TDD 证据

### RED

先新增最小 contract/architecture 测试并运行：

```bash
uv run pytest \
  tests/contracts/wms_integration/test_typed_operation_foundation.py \
  tests/architecture/test_northbound_wms_typed_operation_boundaries.py -q
```

结果：`11 failed, 1 passed`。失败均明确指向 T2 operation modules、Provider mapper、EFFECT gateway、profile/manifest/generated index 尚不存在；唯一通过的是既有 `SystemCapabilityDefinition` 已保持 transport-agnostic。

### GREEN

实现最小 Foundation 后运行同一命令，结果：`12 passed in 0.22s`。

扩大回归首次发现 Pydantic manifest 类型必须在运行时可解析：`157 passed, 1 failed`；修复精确的运行时 import 后，同组结果为 `158 passed in 9.43s`。

## GitNexus

- 生产变更全部为新文件，没有修改索引中的既有函数、类或方法。
- 对格式/类型调整涉及的新符号按规定运行 impact；由于新符号尚未进入 GitNexus 索引，`OperationConformanceRequirement`、`WmsOperationIndexBuilder` 及两个新测试符号均返回 `Target not found`、`impactedCount=0`、`risk=UNKNOWN`。
- `SystemCapabilityDefinition` 影响已知为 HIGH，因此本任务完全未修改该类；只新增架构测试证明其字段不含 endpoint、HTTP、auth、payload builder 或 dispatch factory。
- staged `detect-changes` 按指定 branch/repo 执行，输出 `No changes detected.`。当前 GitNexus 索引不识别本任务全部新增、尚未提交的文件；结合 staged name-status 可确认 27 个文件均为新增，没有既有 symbol 变更。该限制已作为 concern 保留，不把 `No changes detected` 解释为没有 staged diff。

## 验证

- generated index：`uv run python scripts/generate_wms_operation_index.py --check` → `count=4`，digest `3782ce41aee2041d88328816b23fa29338259b3484dbe3cae6234705afd639e1`。
- 目标与相关回归：`158 passed in 9.43s`。
- 完整 architecture + WMS/System Capability contracts：`495 passed, 1 skipped in 137.82s`。
- 默认收集审计：`3471 tests collected in 1.36s`。
- `./scripts/git-quality-gate.sh --profile quality`：通过；Ruff format/check clean、Bandit 0 issue、runtime contract guardrails `348 passed`、process naming `11 passed`、import-linter 1 个合同 kept、架构门禁 0 violation。
- `git diff --check`：通过。

## 文件

- `src/app/wms_integration/ports/*_operation.py`：四个唯一领域 operation 合同。
- `src/app/wms_integration/adapters/query_inventory_operation_adapter.py`：Provider DTO 与唯一库存 ACL 映射。
- `src/app/runtime/system_capabilities/wms/contracts.py`：profile/operation/auth/callback 组合合同。
- `src/app/runtime/system_capabilities/wms/inventory/`：query_inventory、confirm_inbound author-time contract 与入库 gateway。
- `src/app/runtime/system_capabilities/wms/fulfillment/`：notify_pkg_binding、full_box_exchange contract 与 gateway。
- `src/app/runtime/system_capabilities/wms/provider_catalog.py`：Provider profile author-time 组合真源。
- `src/app/runtime/system_capabilities/wms/conformance_manifest.py`：构建期 conformance 清单。
- `src/app/runtime/system_capabilities/wms/operation_index_builder.py` 与 `generated_operation_index.py`：确定性只读索引。
- `scripts/generate_wms_operation_index.py`：生成/漂移检查入口。
- `tests/contracts/wms_integration/test_typed_operation_foundation.py`：typed contract 行为测试。
- `tests/architecture/test_northbound_wms_typed_operation_boundaries.py`：单一 schema、Definition/profile/mapper/index 架构边界。

## 未做项与 concern

- 旧 QUERY 缓存、`WmsTypedPortService`、字符串 capability/profile 字段和粗分机专用 capability 保持原状；由 T3–T7 迁移/删除，本任务未建立 alias 或兼容 adapter。
- 本任务只冻结 EFFECT request→`DispatchEnvelope` 的纯映射；未写 SystemOutbox、dispatch attempt、canonical payload bytes、typed transport result、reducer、reconciliation 或 callback 热路径，这些属于 T8+。
- manifest 已声明统一 conformance required cases，但 operation 级 scripted fixtures、真实 adapter 完整试卷与 staging live report 属于 T5/T6，不在 T2 实现。
- `DispatchEnvelope` 目前只支持 `payload_json`；canonical payload bytes 是后续 T8a 的跨 outbox 变更，本任务没有提前修改该高扩散合同。

## 评审修复（2026-07-21）

本轮只修复 T2 评审发现，没有迁移 T3+ QUERY/EFFECT 路径，也没有新增兼容 alias、字符串 dispatch 或双运行层：

- `ProviderQueryInventoryResponseDTO.items` 与 `InventoryQueryOperationResult.items` 改为必填；缺失字段判为 malformed，显式 `[]` 仍归一化为合法空 tuple。
- `WmsProviderProfile` 先校验每个绑定 EFFECT 恰有一个 callback、拒绝重复，再要求 callback 引用 binding 中同一对象或完整相等的 `WmsOperationContract`；仅 identity 相同但 transport contract 不同会被拒绝。
- `WmsRetryPolicy.backoff_seconds` 的每个元素均为有限正数，明确拒绝 NaN、`+inf`、`-inf`，并继续受 `max_attempts <= 10` 与元素数量约束。
- 删除三个 EFFECT gateway 的测试专用 `REQUEST_MODEL_MODULE` 字符串入口；合同测试直接参数化 gateway/request 类型。

### TDD 证据

- RED：新增评审回归测试后运行 typed Foundation 合同，结果 `6 failed, 11 passed`；失败分别证明两个 `items` 缺失、重复 callback、同 identity 错配 contract、NaN/+inf 尚未被拒绝。
- GREEN：最小实现后同一文件 `17 passed`；typed Foundation + T2 架构边界 `22 passed`。
- 扩大合同/架构回归：WMS integration、System Capability 与相关 WMS 架构测试 `175 passed`。

### 验证与影响分析

- GitNexus impact：Provider DTO、领域 query result、profile validator 与 gateway 测试为 LOW；`WmsRetryPolicy`、`WmsProviderProfile` 为 MEDIUM（各 7 个直接 import、0 条 execution flow）；无 HIGH/CRITICAL。
- GitNexus detect changes（all）：7 个文件、15 个符号、0 条 execution flow，风险 LOW；gateway 仅删除模块常量，未映射为函数/类符号变更。
- generated index check：`count=4`，digest 保持 `3782ce41aee2041d88328816b23fa29338259b3484dbe3cae6234705afd639e1`。
- 测试拓扑：`6 passed`；默认收集审计：`3481 tests collected`。
- `./scripts/git-quality-gate.sh --profile quality`：通过；Ruff、Bandit、runtime contract guardrails、process naming、import-linter、架构门禁与测试拓扑均通过。
- `git diff --check`：通过。
