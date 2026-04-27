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

## [ERR-20260310-001] rbac_cached_permission_list_null_marker_check

**Logged**: 2026-03-10T17:54:24+0800
**Priority**: high
**Status**: resolved
**Area**: backend

### Summary
缓存空值标记判断直接对任意缓存载荷做集合成员测试，遇到列表权限载荷时触发 `TypeError`。

### Error
```text
TypeError: unhashable type: 'list'
```

### Context
- Command/operation: RBAC `verify_permission()` -> `get_user_permissions()` -> `get_cached_value()`
- Input: Redis 中的权限缓存值为列表，如 `["*"]`
- Trigger: `is_null_cache_value()` 对 `list` 执行 `value in LEGACY_CACHE_NULL_MARKERS`

### Suggested Fix
仅对字符串值执行空标记判断，其它结构化缓存值交由调用方的 parser 处理，并增加权限列表缓存回归测试。

### Metadata
- Reproducible: yes
- Related Files: src/database/cache_helpers.py, tests/test_cache_helpers.py, tests/test_rbac_cache_invalidation.py

### Resolution
- **Resolved**: 2026-03-10T17:54:24+0800
- **Commit/PR**: n/a
- **Notes**: RBAC 缓存中的 `["*"]`、`["menu:view"]` 等列表值不再被空值判断提前打断。

---

## [ERR-20260323-001] local_api_verification_in_codex_sandbox

**Logged**: 2026-03-23T17:16:00+08:00
**Priority**: medium
**Status**: resolved
**Area**: infra

### Summary
在当前 Codex 环境里验证本地 API 时，`curl` 与 `uv run pytest` 容易先被代理变量和沙箱权限阻断，导致初始排查信号失真。

### Error
```text
curl: (7) Failed to connect to 127.0.0.1 port 7890 after 0 ms: Couldn't connect to server
error: Failed to initialize cache at `/Users/kaizhou/.cache/uv`
nc: connectx to 127.0.0.1 port 8001 (tcp) failed: Operation not permitted
```

### Context
- Command/operation: 本地 `curl` 调用 `http://127.0.0.1:8001/...`，以及 `PYTHONPATH=. uv run pytest -q ...`
- Environment: Codex `workspace-write` sandbox + shell 环境带 `all_proxy=http://127.0.0.1:7890`
- Trigger:
  - 未显式取消代理时，`curl` 会先尝试走 `127.0.0.1:7890`
  - 未提权时，访问本机监听端口或读取 `~/.cache/uv` 可能被沙箱拒绝

### Suggested Fix
在当前环境验证本地服务时，优先采用以下策略：

- `env -u all_proxy -u ALL_PROXY -u http_proxy -u HTTP_PROXY -u https_proxy -u HTTPS_PROXY curl ...`
- 对 `uv run pytest` 和本机端口访问，直接使用已批准前缀或提权执行
- 在确认接口行为前，先区分“代理/沙箱失败”与“应用本身失败”

### Metadata
- Reproducible: yes
- Related Files: AGENTS.md
- See Also: ERR-20260306-001

### Resolution
- **Resolved**: 2026-03-23T17:16:00+08:00
- **Commit/PR**: n/a
- **Notes**: 本次已确认无代理 `curl` 和提权 pytest 是当前环境下的稳定验证路径，并拿到了真实接口响应。

---

## [ERR-20260425-001] github_cli_land_pr_noninteractive_gotchas

**Logged**: 2026-04-25T10:49:23Z
**Priority**: medium
**Status**: resolved
**Area**: infra

### Summary
GitHub CLI 的 PR checks 字段和非交互 merge 参数与预期不一致，导致 land 流程中途失败后重试。

### Error
```text
Unknown JSON field: "status"
Available fields: bucket, completedAt, description, event, link, name, startedAt, state, workflow

--merge, --rebase, or --squash required when not running interactively
```

### Context
- Command/operation:
  - `gh pr checks 10 --json name,state,status,conclusion`
  - `gh pr merge 10 --auto --delete-branch`
- Environment: Codex 非交互 shell + GitHub CLI
- Trigger:
  - 当前 `gh pr checks` 不支持 `status` / `conclusion` 字段，需使用 `state`、`completedAt`、`description`、`link`、`workflow` 等字段。
  - 非交互合并时，即使用 `--auto`，该 CLI 版本也要求显式指定 `--merge`、`--rebase` 或 `--squash`。

### Suggested Fix
Land PR 时使用当前兼容命令：

```bash
gh pr checks <pr> --json name,state,completedAt,description,link,workflow
gh pr merge <pr> --squash --delete-branch
```

如果仓库有强制检查或 merge queue，再根据 `gh pr view --json mergeStateStatus,mergeable,state` 结果决定是否等待。

### Metadata
- Reproducible: yes
- Related Files: n/a
- See Also: none

### Resolution
- **Resolved**: 2026-04-25T10:46:30Z
- **Commit/PR**: #10
- **Notes**: PR #10 已用 `gh pr merge 10 --squash --delete-branch` 成功合并，并通过 `git fetch origin --prune` 清理远端引用。

---

## [ERR-20260425-002] gstack_review_checklist_path_missing

**Logged**: 2026-04-25T10:49:23Z
**Priority**: low
**Status**: resolved
**Area**: infra

### Summary
`/ship` 文档引用的 gstack review checklist 路径在当前安装中不存在，需要回退读取已安装的 `gstack-review/SKILL.md`。

### Error
```text
sed: /Users/kaizhou/SynologyDrive/github/gstack/.agents/skills/gstack-review/checklist.md: No such file or directory
```

### Context
- Command/operation: ship pre-landing review 阶段读取 checklist
- Environment: 当前 gstack skills 安装路径
- Trigger: skill 文档引用 `.agents/skills/gstack/review/checklist.md`，但实际可用文件是 `gstack-review/SKILL.md` 和相关 agent 配置。

### Suggested Fix
当 checklist 文件缺失时，不要跳过 pre-landing review；改为读取 `gstack-review/SKILL.md` 中的核心 review 规则，并显式说明 fallback。

### Metadata
- Reproducible: yes
- Related Files: /Users/kaizhou/SynologyDrive/github/gstack/.agents/skills/gstack-review/SKILL.md

### Resolution
- **Resolved**: 2026-04-25T10:35:00Z
- **Commit/PR**: n/a
- **Notes**: 本次通过 fallback 完成 review，并发现/修复了设备派发并发风险。

---
