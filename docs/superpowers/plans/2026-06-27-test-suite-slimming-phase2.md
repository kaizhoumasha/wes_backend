# 测试套件瘦身 Phase 2 执行计划

**目标:** 在第一阶段 guardrail 已落地后，继续收敛测试套件的结构成本和默认回归成本。

**边界:** 本阶段优先做行为等价的结构收敛，不删除业务覆盖。每个任务以定向 pytest、collect、ruff 和 GitNexus detect-changes 验证。

## Task 1: 清空 `tests/` 根目录遗留测试

- 将 `tests/test_*.py` 按职责归位到 `tests/core/`、`tests/database/`、`tests/sys/`、`tests/api_auth/`、`tests/deployment/`、`tests/utils/`、`tests/admin/`、`tests/contracts/`。
- 修正移动后依赖 `__file__` 深度的仓库根路径。
- 将 `ROOT_LEVEL_TEST_FILE_ALLOWLIST` 收紧为空。
- 验证拓扑 guardrail、移动文件定向测试和默认 collect。

## Task 2: 收紧默认快速回归边界

- 将 `tests/integration/` 纳入默认排除目录，显式运行集成测试。
- 更新 `tests/README.md` 的默认快速回归说明、目录归属矩阵和当前基线。
- 验证 pyproject、README 与 guardrail 一致。

## Task 3: 拆分优先级最高的重测试文件

- 优先处理 mock/API 绑定最重的 `tests/api/test_callback_api.py`。
- 按 callback result、event、external、validation/diagnostic 等职责拆分到多个文件。
- 保持测试函数语义不变，优先移动和轻量 helper 提取，不改变生产代码。
- 验证 callback API 测试组、默认 collect 和相关 WorkLine runtime 聚焦测试。

## Task 4: 收尾

- 更新 `tests/README.md` 当前治理基线。
- 运行 `ruff format` / `ruff check` 覆盖触达测试文件。
- 运行 `rtk gitnexus detect-changes`。
- 分任务提交，避免暂存无关用户改动。
