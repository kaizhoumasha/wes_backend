# SANDBOX MOCK Catalog 统一设计

## 目标

将 SANDBOX 默认 Event 样例、内置 `SandboxWmsInventoryClient`、外部 `tests/mock/wms_mock_server.py`
统一到同一套可维护的 MOCK/SANDBOX catalog，避免粗分机默认 happy path 与 WMS MOCK 支持数据漂移。

## 背景

当前存在三套隐含数据来源：

- `src/app/workline/services/operation_service.py` 写死 `SCAN_COMPLETED` 默认 payload，物料为
  `HHPN=620100L00-011-G`、`LotCode=8904936031`、`PkgID=SVYU00125TP4LCR02_2`。
- `tests/mock/wms_mock_server.py` 只支持 `sku=CAP001`、`lot_no=LOT-A` 的库存查询。
- `src/workline_runtime/services.py` 中的 `SandboxWmsInventoryClient` 对任意请求都返回一条成功库存。

这会导致本地内置 SIMULATION、外部 MOCK WMS、前端 SANDBOX 默认模板行为不一致。默认样例可能在一个环境通过，在另一个环境没有库存。

## 设计决策

采用 `src` 内共享 Python catalog 作为单一事实源。

新增一个轻量 catalog 模块，建议路径为：

- `src/workline_runtime/sandbox_catalog.py`

该模块只表达测试/SANDBOX 所需的确定性业务样例，不扩展成通用模拟平台。

核心职责：

- 定义粗分机默认 happy path 六合一码。
- 定义 WMS MOCK 物料与库存行。
- 提供构造 `SCAN_COMPLETED` 默认 payload 的函数。
- 提供按 `sku + lot_no` 查询库存行的函数。
- 提供转换为 WMS mock server seed 字典的函数，保持 mock server API 输出不变。

## 数据合同

默认粗分机 happy path 必须满足：

- `payload.data.HHPN` 等于 WMS 查询中的 `sku`。
- `payload.data.LotCode` 等于 WMS 查询中的 `lot_no`。
- `payload.data.PkgID` 是完整且稳定的包号，用于生成粗分机业务键。
- `payload.data.DateCode`、`MfrPN`、`Qty` 齐全，使 `barcode_decision_service` 返回 OK。

建议默认样例：

- `HHPN=CAP001`
- `MfrPN=V0001-CAP-0402`
- `Qty=100`
- `DateCode=20260409`
- `LotCode=LOT-A`
- `PkgID=PKG-CAP001-LOT-A-001`
- `location=ARM01`

WMS 库存样例至少包含：

- happy path：`sku=CAP001`、`lot_no=LOT-A`、`available_qty > 0`
- 无库存路径：未知 `sku` 或未知 `lot_no` 返回空列表

可在后续需求中添加更多场景，例如库存存在但可用量为 0、不同仓库、不同货主。当前不提前实现未被流程消费的复杂场景。

## 组件变更

### Catalog 模块

`src/workline_runtime/sandbox_catalog.py`：

- 暴露默认粗分机样例 payload 构造函数。
- 暴露库存查询函数，输入为 `sku`、可选 `lot_no`、可选 `warehouse_code`、可选 `owner_code`。
- 暴露 mock server 所需 seed 字典或转换函数。
- 使用普通 `dict` 和 `Decimal`/数字边界清晰的值，避免引入数据库或 HTTP 依赖。

### SANDBOX 模板

`src/app/workline/services/operation_service.py`：

- `SCAN_COMPLETED` 默认 payload 从 catalog 生成。
- `_get_default_payload_template()` 继续负责补 `event_type`、`timestamp`、`device_code`。
- 其它 Event 模板暂不迁移，除非与 catalog 明确相关。

### 内置 Sandbox WMS

`src/workline_runtime/services.py`：

- `SandboxWmsInventoryClient.query_inventory()` 改为查询 catalog。
- 命中时返回 catalog 中的库存行。
- 未命中时返回 `items=[]`，不再对任意物料生成成功库存。
- 保留 `reason_code=SANDBOX_WMS_INVENTORY`，消息根据命中/未命中表达清楚。

### 外部 MOCK WMS

`tests/mock/wms_mock_server.py`：

- `MOCK_MATERIALS`、`MOCK_INVENTORY` 从 catalog 派生或直接引用转换结果。
- `/api/wms/inventory/query` 的 POST/GET 响应格式保持不变。
- 已有 WMS mock 测试改为断言 catalog happy path。

### 前端

前端不需要硬编码改动。

`wes_frontend` 当前从 `/sandbox/templates` 获取 `payload_template`，并原样提交到 `/sandbox/events`。只要后端模板更新，前端 SANDBOX 默认值会自然同步。

## 数据流

1. 前端进入 SANDBOX，选择工作线和设备。
2. 前端请求 `/api/v1/workline/operations/sandbox/templates`。
3. 后端根据粗分机 manifest 的 `supported_events` 生成 `SCAN_COMPLETED` 模板。
4. `SCAN_COMPLETED.payload_template.data` 来自 catalog happy path。
5. 前端提交 `/sandbox/events`。
6. 后端创建 `DEVICE_EVENT` inbox，并补 `event_type`、`sandbox_mode=true`。
7. 粗分机插件从 `payload.data` 解析六合一码。
8. 测量成功后粗分机按 `HHPN/LotCode` 查询 WMS。
9. 内置 `SandboxWmsInventoryClient` 或外部 WMS mock 都从同一 catalog 返回一致库存。

## 错误处理

- 未知 `sku` 或 `lot_no` 不抛异常，返回空库存列表。
- 粗分机继续沿用现有 WMS 校验逻辑处理空库存，不在 catalog 层决定业务 NG 文案。
- catalog 数据缺字段应由单元测试发现；运行时不做复杂动态校验。

## 测试策略

必须覆盖以下路径：

- SANDBOX `SCAN_COMPLETED` 默认模板使用 catalog happy path 数据。
- 默认模板的 `HHPN/LotCode` 能在 catalog WMS 库存中命中。
- `SandboxWmsInventoryClient` 对 happy path 返回库存行，对未知物料返回空列表。
- `tests/mock/wms_mock_server.py` POST/GET 查询 happy path 返回同一库存行。
- 粗分机默认模板经过 `handle_scan_completed` 后能生成测量命令。
- 现有 `ruff` 检查通过。

## 验收标准

- 默认 SANDBOX 粗分机 Event 样例在内置 SIMULATION WMS 和外部 MOCK WMS 中语义一致。
- 默认 happy path 不依赖“任意库存都成功”的兜底行为。
- 未知物料不会被内置 `SandboxWmsInventoryClient` 错误放行。
- 前端无需改动即可拿到新的默认 payload。
- 测试明确防止 SANDBOX 模板、内置 sandbox WMS、外部 WMS mock 再次漂移。

## 不在本次范围

- 不实现完整 WMS 业务模拟平台。
- 不改真实 WMS typed port 合同。
- 不改粗分机业务流程和 WMS 校验判定逻辑。
- 不新增前端 SANDBOX 编辑器功能。
- 不调整数据库 seed 或 Jenkins 部署流程。

## 风险与缓解

- 风险：`SandboxWmsInventoryClient` 从任意成功变为按 catalog 命中，可能暴露现有测试对宽松行为的依赖。
  缓解：测试失败时优先修正测试输入，让其使用 catalog 支持数据；不恢复任意成功行为。
- 风险：`tests/mock/wms_mock_server.py` 直接导入 `src` 模块后，独立运行时需要项目根目录在 `sys.path`。
  缓解：该 mock server 已有项目根目录注入逻辑，应保持并测试直接导入路径。
- 风险：catalog 变成杂乱样例堆。
  缓解：只添加当前流程需要的样例，每个样例必须有命名用途和测试覆盖。
