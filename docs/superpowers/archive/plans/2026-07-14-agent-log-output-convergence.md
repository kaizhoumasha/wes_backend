# Agent 日志输出收敛实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**目标：** 防止代码 Review、pytest 与 PostgreSQL 验收把无边界 DEBUG 日志写入 Agent 会话，同时保留完整的落盘诊断证据。

**架构：** pytest 默认启用文件描述符捕获；应用调试行为与日志阈值分离；验收子进程直接流式写入日志文件，失败异常只携带固定行数、固定字符数的脱敏尾部摘要。完整日志继续作为 artifact 保存。

**技术栈：** Python 3.13、pytest、Loguru、subprocess、TOML 配置。

---

### Task 1：收敛 pytest 与应用控制台日志

**文件：**

- 修改：`pyproject.toml`
- 修改：`src/core/logger.py`
- 新增：`tests/core/test_test_logging_contract.py`

- [x] 先写合同测试，锁定 pytest 默认参数不包含 `-s` 或 `--capture=no`。
- [x] 写日志测试，锁定 `APP_DEBUG=true` 时 sink 仍采用显式 `LOG_LEVEL`。
- [x] 运行定向测试并确认测试因旧行为失败。
- [x] 删除全局 `-s`，让 `setup_logger()` 始终以 `settings.LOG_LEVEL` 作为阈值；`APP_DEBUG` 仅控制格式和诊断细节。
- [x] 运行 `tests/core` 定向回归。

### Task 2：限制 RuntimeInbox 验收失败摘要

**文件：**

- 修改：`scripts/run_runtime_inbox_postgresql_acceptance.py`
- 修改：`tests/deployment/test_runtime_inbox_postgresql_acceptance_ci.py`

- [x] 先写失败合同测试，要求完整 stdout/stderr 流式保存、异常只包含脱敏且有界的日志尾部。
- [x] 运行定向测试并确认旧的 `capture_output=True` 行为无法满足合同。
- [x] 将 subprocess stdout/stderr 直接合并写入日志文件，避免在内存中累积完整输出。
- [x] 失败时只读取固定行数和固定字符数的日志尾部，脱敏后写入异常与 diagnostic。
- [x] 运行 acceptance runner 定向回归。

### Task 3：验收与评审闭环

- [x] 运行测试拓扑 guardrail 与变更测试目录。
- [x] 运行默认收集验证，确认 pytest 不再默认关闭捕获。
- [x] 运行 `./scripts/git-quality-gate.sh --profile quality`。
- [x] 运行 GitNexus detect changes，核对影响仅限日志初始化与 RuntimeInbox 验收工具链。
- [x] 进行规格符合性、代码质量和最终整体评审，修复所有 actionable findings。

## 验收标准

- 默认 `uv run pytest` 不再实时输出被测代码的 stdout/stderr。
- `APP_DEBUG=true` 不再隐式覆盖 `LOG_LEVEL`。
- 验收完整日志仍落盘，但 Python 进程不在内存中保存完整输出。
- 失败消息与 diagnostic 只包含脱敏、有界的日志尾部。
- 定向测试、测试拓扑及质量门禁全部通过。
