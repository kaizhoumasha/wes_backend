# Runtime BLOCK 会话人工挂起持久化设计

## 背景

在沙箱验证 `business_key=b09b133beac18408` 时，`MEASUREMENT_REEL` 命令已完成，`COMMAND_RESULT` inbox 也已处理完成，但对应 Session 仍停留在 `WAITING_DEVICE_RESULT`：

- Session：`SES_49f1148cc21e403b`
- 当前状态：`WAITING_DEVICE_RESULT`
- 当前等待命令：`CMD-20260529-MEASUREMENT_REEL-998CC783`
- 命令状态：`COMPLETED`
- Timeline：已写入 `MANUAL_HOLD`
- 阻塞原因：`ROUGH_SORTER_MEASUREMENT_PAYLOAD_INVALID`

这说明业务插件已经通过 `RuntimeIntent.BLOCK` 判定需要人工处理，但 Session 表未持久化为 `MANUAL_HOLD`。

## 根因

`RuntimeIntentEffectApplier._apply_block()` 当前只完成了两类副作用：

1. 通过 `workline_session_lifecycle_service.manual_hold()` 修改内存中的 Session 对象。
2. 写入 `MANUAL_HOLD` timeline。

但它没有显式更新 `workline_sessions` 表。由于运行时存在异步处理与 ORM 对象状态边界，单纯修改内存对象不足以保证 Session 行落库。

同类问题此前已在命令等待态和完成态中出现，并分别通过 repository 显式持久化修复。本次问题属于同一类：关键运行时状态 transition 缺少显式落库。

## 目标

当插件返回 `RuntimeIntent.BLOCK` 时，Session 必须被持久化为 `MANUAL_HOLD`，并与 timeline、command、inbox 状态保持一致。

## 非目标

- 不改变 `RuntimeIntent.BLOCK` 的业务语义。
- 不新增 runtime hold 记录。
- 不把该场景改为 `FAILED` 或 runtime reconciliation。
- 不重构全部 Session 状态机。
- 不改变白皮书中的 Command → ACK → Result 协议。

## 状态约定

`RuntimeIntent.BLOCK` 的目标状态统一为 `MANUAL_HOLD`。

落库后 Session 应满足：

- `status = MANUAL_HOLD`
- `current_wait_type = null`
- `waiting_since = null`
- `deadline_at = null`
- `current_wait_timeout_seconds = null`
- `awaiting_command_id = null`
- `ended_at = null`
- `failure_domain = intent.block_scope`
- `failure_code = intent.reason_code`
- `failure_message = intent.message`

命令状态不回退。若触发 BLOCK 的 Result 已完成，命令保持 `COMPLETED` 或 `FAILED`，由 Result 写入路径决定。

## 方案

采用最小修复方案：补齐 BLOCK → MANUAL_HOLD 的显式持久化。

### Repository

在 `WorklineSessionRepository` 增加人工挂起持久化方法，职责是把指定 Session 原子更新为 `MANUAL_HOLD` 并清空等待字段。

该方法接收：

- `session_id`
- `occurred_at`
- `failure_domain`
- `failure_code`
- `failure_message`

该方法只负责 Session 行更新，不写 timeline，不创建 runtime hold。

### Runtime Effect

在 `RuntimeIntentEffectApplier._apply_block()` 中：

1. 保留现有 lifecycle 调用，维护内存对象一致性。
2. 保留现有 failure 字段赋值。
3. 调用 repository 显式持久化 `MANUAL_HOLD`。
4. 保留现有 `MANUAL_HOLD` timeline 写入。

推荐顺序为先持久化 Session，再写 timeline。这样如果 timeline 成功存在，Session 状态也应已经完成落库。

## 数据流

1. 设备回传 `COMMAND_RESULT`。
2. `WorklineOperationService.submit_sandbox_result()` 标记命令完成并写 inbox。
3. inbox processor 调用插件。
4. 插件返回 `RuntimeIntent.BLOCK`，例如 `ROUGH_SORTER_MEASUREMENT_PAYLOAD_INVALID`。
5. Runtime effect 将 Session 持久化为 `MANUAL_HOLD`。
6. Runtime effect 写 `MANUAL_HOLD` timeline。
7. inbox 标记为 `PROCESSED`。

最终状态应为：

- inbox：`PROCESSED`
- command：终态，不回退
- session：`MANUAL_HOLD`
- timeline：存在 `MANUAL_HOLD`

## 错误处理

如果 Session 持久化失败，当前 inbox 处理应失败并由现有 inbox 失败/重试机制接管。不能只写 timeline 而让 Session 保持等待态。

如果 timeline 写入失败，也应保持现有异常传播行为，不吞掉错误。

## 测试设计

新增或增强 `tests/workline_runtime/test_runtime_intent_effects.py` 中的 BLOCK intent 测试：

- 输入：`RuntimeIntent.block(scope=MATERIAL, reason_code="MATERIAL_BLOCKED", message="物料需要人工处理")`
- 断言内存对象状态为 `MANUAL_HOLD`
- 断言等待字段清空
- 断言 failure 字段写入
- 断言 `db.execute` 被调用，证明存在显式持久化
- 断言 timeline payload 保留 `suggested_action` 与 evidence

保留现有命令等待态、完成态、沙箱 Result 路由测试。

## 验收标准

使用同类测量 payload invalid 场景复测：

1. `MEASUREMENT_REEL` 命令完成。
2. `COMMAND_RESULT` inbox 处理完成。
3. 插件返回 BLOCK。
4. Session 状态为 `MANUAL_HOLD`。
5. `current_wait_type / awaiting_command_id / deadline_at` 均为空。
6. Timeline 存在 `MANUAL_HOLD`。
7. 命令保持 `COMPLETED`。

自动化验证：

- 相关 runtime intent 测试通过。
- workline operation / runtime reconciliation 相关回归测试通过。
- Ruff check 和 format check 通过。

## 风险

风险较低。变更范围集中在 `RuntimeIntent.BLOCK` 的状态持久化，不改变插件决策、设备协议、Result 合同或命令状态机。

主要风险是与人工恢复流程的状态预期不一致。当前代码和测试均把 BLOCK 映射为 `MANUAL_HOLD`，因此该修复与既有语义一致。

## 回滚

如果修复引入异常，可回滚 repository 新方法与 `_apply_block()` 调用。回滚后系统会恢复到 timeline 可写但 Session 可能停留等待态的旧行为。

## 架构评审决议 (Engineering Review Decision)

在 2026-05-29 的工程设计评审（/plan-eng-review）中，针对 BLOCK 挂起状态持久化的方案设计做出了以下决议：

### 1. 核心工程决策 (D1)

- **决议**：**直接落库模式 (选项 A)**。
- **考量**：
  - 遵循当前 `RuntimeIntentEffectApplier` 已有的 `persist_command_result_wait` 和 `persist_completed` 的就近显式落库设计模式，避免在服务层引入过度包装。
  - 符合 YAGNI 规则，专注于解决当前的数据库状态转换遗漏，控制回归测试风险。

### 2. 接口与约定变更

#### 2.1 Repository 层
在 `WorklineSessionRepository` 中实现 `persist_manual_hold`：
- **方法签名**：`async def persist_manual_hold(self, db: AsyncSession, *, session_id: int, occurred_at: Any, failure_domain: str | None, failure_code: str | None, failure_message: str | None) -> None:`
- **原子更新值**：
  - `status = SessionStatus.MANUAL_HOLD`
  - `current_wait_type = None`
  - `waiting_since = None`
  - `deadline_at = None`
  - `current_wait_timeout_seconds = None`
  - `awaiting_command_id = None`
  - `ended_at = None`
  - `failure_domain = failure_domain`
  - `failure_code = failure_code`
  - `failure_message = failure_message`

#### 2.2 Side Effect 应用层
在 `RuntimeIntentEffectApplier._apply_block` 中：
- 导入并调用 `WorklineSessionRepository().persist_manual_hold` 执行原子更新，传入从内存 session 中同步的 failure 属性。
- 确保持久化操作在发布 `TimelineActionType.MANUAL_HOLD` 之前被 `await` 完成。

### 3. 分步实施与验证计划

1. **第一步 (数据层)**：在 `src/app/workline/repositories/session_repository.py` 中，参考 `persist_completed` 补全 `persist_manual_hold` 数据库更新操作。
2. **第二步 (控制层)**：在 `src/workline_runtime/runtime_intent_effects.py` 的 `_apply_block` 中导入并调用 repository 的新接口。
3. **第三步 (测试验证)**：
   - 运行单元测试：`uv run pytest tests/workline_runtime/test_runtime_intent_effects.py`。
   - 检查代码风格与格式：`uv run ruff check .` & `uv run ruff format .`。

