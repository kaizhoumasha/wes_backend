# Handling Domain 一致性架构规格

日期：2026-05-26

## 背景

当前代码库中，“料箱搬运域”（Handling Domain）和“货架操作域”（Rack Domain）已经完成解耦并升级为系统级域。
在最近一次架构重构中，货架操作域（Rack）成功地接轨了 `SystemOutboxEngine`，移除了 `Workline` 的强绑定，并引入了 `OperationCompletionPolicy`。
Handling 域在架构设计上是系统级操作的“先驱”，它早就拥有了 `HandlingOperation` 和 `SystemOutbox` 调用的雏形。但为了保证 WES 中台架构的高度一致性，我们必须用明确的规格文档将 Handling 的设计边界、命名规范以及与 Rack 的异同固化下来。

## 目标

1. **确立 Handling 为系统级操作域**：定义其为 WES 核心的中台服务，负责封装和管理料箱级别的移动（Move）与执行步骤（Step）。
2. **规范集成 System Outbox Engine**：固化 Handling 向 WMS/RCS/CTU 等外部硬件系统发送异步调用时，必须统一使用 `SystemOutbox`（`operation_domain="HANDLING"`）。
3. **明晰与 Rack 域的差异与边界**：明确不抽象公共 `OperationBaseService` 的理由，阐述为何 Handling 的 `completion_policy` 默认使用 `CALLBACK_TRUSTED`。
4. **清理遗留痕迹**：彻底清洗历史遗留的 `.pyc` 残留和测试用例中的旧命名（如 `WorklineHandling`、`WorklineOutbox` 等），确保代码纯净。

## 非目标

1. 不新建 `src/app/bin` 领域模型。料箱的搬运属性强于“主数据”属性，当前的 `src/app/handling` 边界已足够合理。
2. 不强制抽象出一个基类或通用框架来同时兼容 Handling 和 Rack。它们在底层业务属性上有差异（例如 Rack 需要关注库位容量投影，Handling 关注箱子点到点移动），强行抽象容易造成过度设计。
3. 本次一致性重构不涉及新增针对外部系统的 RESTful API（如 `/v1/handling`），保持其目前作为内部基础层为 Runtime、Celery callbacks 以及其他业务服务所调用的现状。

## 架构决策

### 1. Handling 是系统级操作域

`src/app/handling/` 是独立于特定工作线（Workline）存在的操作域，负责料箱物理操作事实。

核心模型分层：
- `HandlingOperation`：一次完整的料箱操作意图（可能包含多个箱子的搬运）。
- `HandlingMove`：一次点到点的料箱移动（如从输送线到货架）。
- `HandlingStep`：一次外部请求的具体派发步骤（一次 Move 可能包含多个请求和回调）。

外键依赖：
`workline_id`、`workline_code`、`material_session_id` 均为**可选（Optional）上下文**。允许在没有任何工作线介入的情况下（例如库存整理、盘点等操作）发起 `HandlingOperation`。

### 2. 依托 System Outbox Engine 发送外部请求

Handling 必须且只能通过 `src/app/sys/models/outbox.py` 定义的 `SystemOutbox` 来发送外部通信。
生成 Outbox 时的必要上下文：
- `operation_domain`: `HANDLING`
- `dispatch_type`: `EXTERNAL_HTTP`
- `target_type`: `HTTP_ENDPOINT`

### 3. 完成策略（Completion Policy）模型

`HandlingOperation` 的完成策略显式记录在 `completion_policy` 字段中。

| 策略 | 语义 | 默认使用方 |
| --- | --- | --- |
| `CALLBACK_TRUSTED` | 只要所需步骤（Required step）收到外部硬件成功的确切回调，即视为成功 | Handling |
| `RESOURCE_PROJECTION_REQUIRED` | 外部回调成功后，还需通过资源投影（例如 Rack Placements）核对一致才能成功 | Rack |
| `CALLBACK_PLUS_RECONCILIATION` | 回调成功先推进，发现异常对账失败后转为挂起（RECONCILING 或 hold） | 预留（满箱交换可演进至此） |

**决策**：Handling 域默认坚守 `CALLBACK_TRUSTED`。因为对于多数 CTU/WMS 搬运动作而言，回调成功即意味着指令在硬件层被安全执行。只有像货架移动这种牵一发而动全身、对库存视图影响极大的操作，才需要强制配置为 `RESOURCE_PROJECTION_REQUIRED`。

**满箱交换例外**：`FULL_BOX_EXCHANGE` / `RACK_BIN_EXCHANGE` 类操作仍属于 Handling 域，但不是“任意成功回调即恢复”的纯可信回调。现有生命周期规则已经对满箱交换保留对账边界：
- `PHYSICAL_COMPLETED` 或 `RESOURCE_PROJECTED` 回调缺少 `post_exchange_relations` 时，进入 `RECONCILING`，并将等待中的 Session 转为人工处理。
- 回调 `rack_release_id` 与等待上下文不一致时，进入 `RECONCILING` / manual hold，避免错误释放不属于当前等待操作的 Session。
- 后续更高版本可信终态（例如 `BUSINESS_COMPLETED` 且 `source_version` 更新）可以从 `RECONCILING` 推进到 `SUCCEEDED`。

这条例外目前由 callback lifecycle 分支实现；本次一致性补齐只固化文档和测试合同，不把满箱交换立即改造成显式 `CALLBACK_PLUS_RECONCILIATION` 策略。

### 4. 没有基类强耦合

不要为了 DRY（Don't Repeat Yourself）原则将 `RackOperationService` 和 `HandlingOperationService` 强行提炼为一个基类。
它们享有相似的生命周期概念，但内部状态推演机制（Rack 的容量检查 vs Handling 的分步移动追踪）完全不同。

## 验收标准

1. `src/app/handling` 及其测试用例中没有任何历史遗留废弃的 `WorklineOutbox`、`WorklineHandling` 的旧词汇。
2. `git ls-files '*.pyc' '*/__pycache__/*'` 返回空，证明没有 Python 缓存文件进入仓库跟踪；本地 ignored `__pycache__` 可按需清理，但不作为 PR 主要成果。
3. `HandlingOperation.completion_policy`（继承自 `HandlingOperationBase`）默认值由测试锁定为 `OperationCompletionPolicy.CALLBACK_TRUSTED`。
4. `uv run pytest tests/handling/` 单元测试通过，验证生命周期机制无故障。
5. Runtime、Callback、Session Resolver、SystemOutbox 的 focused 测试通过，确保 `SystemOutbox` 的接入和挂起/恢复逻辑健壮。
