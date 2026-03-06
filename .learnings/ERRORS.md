# Errors

Command failures, exceptions, and unexpected behaviors.

**Priority**: critical | high | medium | low
**Areas**: frontend | backend | infra | tests | docs | config
**Reproducible**: yes | no | unknown

---

## [ERR-20260306-001] uv_pytest_sandbox_cache_permission

**Logged**: 2026-03-06T11:01:23Z
**Priority**: medium
**Status**: resolved
**Area**: infra

### Summary
测试执行环境与工具缓存路径存在权限边界不一致，导致“可运行命令”在受限环境下失败。

### Error
```text
error: failed to open file `/Users/kaizhou/.cache/uv/sdists-v9/.git`: Operation not permitted (os error 1)
```

### Context
- Command/operation: `uv run pytest`（任何依赖用户级缓存的执行）
- Environment: workspace-write sandbox
- Trigger: 工具链默认读取 `~/.cache/uv`，超出当前沙箱读权限

### Suggested Fix
将“测试命令是否需要提权”前置为项目级执行策略，并维护白名单前缀规则，避免中途失败后反复重跑。

### Metadata
- Reproducible: yes
- Related Files: AGENTS.md

### Resolution
- **Resolved**: 2026-03-06T11:01:23Z
- **Commit/PR**: n/a (运行策略调整)
- **Notes**: 已改用提权执行 pytest，后续同类命令可复用批准前缀。

---

## [ERR-20260306-002] test_output_signal_noise

**Logged**: 2026-03-06T11:01:23Z
**Priority**: low
**Status**: pending
**Area**: tests

### Summary
测试日志噪音过高，影响评审效率与问题定位速度。

### Error
```text
pytest 输出包含大量 debug/sql 日志，关键信息（失败点/断言）可读性下降。
```

### Context
- Command/operation: `pytest -q` 定向执行
- Environment: 本地开发与联调评审
- Trigger: 默认日志级别与 SQL/debug 输出开启

### Suggested Fix
在测试配置中为常规回归场景设置更低日志级别（如 warning），仅在排障时临时升高。

### Metadata
- Reproducible: yes
- Related Files: pyproject.toml

---
