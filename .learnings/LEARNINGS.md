# Learnings

Corrections, insights, and knowledge gaps captured during development.

**Categories**: correction | insight | knowledge_gap | best_practice
**Areas**: frontend | backend | infra | tests | docs | config
**Statuses**: pending | in_progress | resolved | wont_fix | promoted | promoted_to_skill

## Status Definitions

| Status | Meaning |
|--------|---------|
| `pending` | Not yet addressed |
| `in_progress` | Actively being worked on |
| `resolved` | Issue fixed or knowledge integrated |
| `wont_fix` | Decided not to address (reason in Resolution) |
| `promoted` | Elevated to CLAUDE.md, AGENTS.md, or copilot-instructions.md |
| `promoted_to_skill` | Extracted as a reusable skill |

## Skill Extraction Fields

When a learning is promoted to a skill, add these fields:

```markdown
**Status**: promoted_to_skill
**Skill-Path**: skills/skill-name
```

---

## [LRN-20260307-001] best_practice

**Logged**: 2026-03-07T01:10:00+08:00
**Priority**: high
**Status**: resolved
**Area**: backend

### Summary
先验证“当前事实状态”再给修复建议，是减少无效改动和误判的关键流程。

### Details
本次问题定位过程中，用户已先行修复 `where(not Menu.is_deleted)` 导致的空菜单问题。  
通用教训是：在进入补丁动作前，先确认“代码当前态 + 数据当前态 + 路由当前态”，否则容易重复修复、误判根因或提出过时建议。

### Suggested Action
对线上/联调问题统一执行三步前置核验：
1. 读当前代码（不是凭历史上下文）；
2. 查当前数据（关键关联表和过滤条件）；
3. 验当前接口行为（响应结构与业务数据分开看）。

### Metadata
- Source: user_feedback
- Related Files: src/app/admin/repositories/menu_repository.py, src/app/admin/v1/menu.py
- Tags: root-cause, verification, stale-context, review-gate
- Pattern-Key: process.verify_current_state_before_fix
- Recurrence-Count: 1
- First-Seen: 2026-03-07
- Last-Seen: 2026-03-07

### Resolution
- **Resolved**: 2026-03-07T01:10:00+08:00
- **Commit/PR**: n/a
- **Notes**: 后续所有评审先输出“当前状态核验结果”再给修复建议。

---

## [LRN-20260307-002] best_practice

**Logged**: 2026-03-07T01:10:00+08:00
**Priority**: high
**Status**: resolved
**Area**: frontend

### Summary
初始化场景要区分“未加载”和“加载后为空”，用显式状态位比用数组长度判断更稳健。

### Details
登录后菜单请求重复的根因是：多个入口都用 `menuTree.length === 0` 作为触发条件，无法表达“已经加载过但结果为空”。  
引入 `hasLoaded / isMenuLoaded` 后，能够稳定区分状态，避免重复请求与时序竞争。

### Suggested Action
在前端初始化链路统一采用状态机思路：
1. `idle`（未加载）
2. `loaded_empty`（加载完成但空）
3. `loaded_nonempty`（加载完成有数据）
4. `failed`（失败）
并将触发条件从“数据内容”迁移到“状态语义”。

### Metadata
- Source: conversation
- Related Files: /Users/kaizhou/SynologyDrive/works/wes_frontend/src/composables/useMenu.ts, /Users/kaizhou/SynologyDrive/works/wes_frontend/src/layouts/DefaultLayout.vue
- Tags: state-management, initialization, duplicate-request, kiss
- Pattern-Key: frontend.init_state_semantics_over_data_shape
- Recurrence-Count: 1
- First-Seen: 2026-03-07
- Last-Seen: 2026-03-07

### Resolution
- **Resolved**: 2026-03-07T01:10:00+08:00
- **Commit/PR**: n/a
- **Notes**: 后续初始化逻辑统一优先使用状态位判断。

---

## [LRN-20260306-004] correction

**Logged**: 2026-03-06T14:44:12Z
**Priority**: medium
**Status**: resolved
**Area**: config

### Summary
在当前编辑器环境中，`python.defaultInterpreterPath` 使用相对路径 `.venv/bin/python` 可能无法被解析。

### Details
用户反馈将解释器路径从 `${workspaceFolder}/.venv/bin/python` 调整为 `.venv/bin/python` 后，仍出现 “Could not resolve interpreter path '.venv/bin/python'”。  
结论：该环境对默认解释器路径更稳妥的写法是仓库内 `.venv` 的绝对路径。

### Suggested Action
将 `.vscode/settings.json` 的 `python.defaultInterpreterPath` 固定为：
`/Users/kaizhou/SynologyDrive/works/wes_backend/.venv/bin/python`。

### Metadata
- Source: user_feedback
- Related Files: .vscode/settings.json, .vscode/launch.json
- Tags: vscode, python, interpreter, path-resolution
- See Also: none
- Pattern-Key: config.vscode_python_interpreter_absolute_path_when_relative_fails
- Recurrence-Count: 1
- First-Seen: 2026-03-06
- Last-Seen: 2026-03-06

### Resolution
- **Resolved**: 2026-03-06T14:44:12Z
- **Commit/PR**: n/a
- **Notes**: 默认解释器改为绝对路径，调试配置继续使用 `${command:python.interpreterPath}` 跟随已选解释器。

---

## [LRN-20260306-001] best_practice

**Logged**: 2026-03-06T11:01:23Z
**Priority**: high
**Status**: resolved
**Area**: config

### Summary
前后端联动改动必须按“契约一致性”做双端闭环验证，不能只验单端逻辑。

### Details
在本项目中，权限与认证属于跨仓（前端+后端）协同域。  
若只在单端修复，容易出现“接口行为已变但调用时序未统一”或“前端状态清理完成但服务端状态仍残留”的灰区。  
项目级正确做法是：先定义契约，再做双端验证，再给最终可提交流水线结论。

### Suggested Action
建立固定评审清单：  
1. 接口输入输出契约核对  
2. 调用顺序与异常分支核对  
3. 双端最小回归测试核对  
4. 提交前“一致性结论”输出

### Metadata
- Source: conversation
- Related Files: src/app/auth/v1/auth.py, src/app/auth/services/auth_service.py, /Users/kaizhou/SynologyDrive/works/wes_frontend/src/api/services/token-refresh.ts
- Tags: contract, cross-repo, review, release-gate
- Pattern-Key: process.cross_repo_contract_validation
- Recurrence-Count: 1
- First-Seen: 2026-03-06
- Last-Seen: 2026-03-06

### Resolution
- **Resolved**: 2026-03-06T11:01:23Z
- **Commit/PR**: staged (local)
- **Notes**: 形成可复用的双端一致性评审流程。

---

## [LRN-20260306-002] best_practice

**Logged**: 2026-03-06T11:01:23Z
**Priority**: medium
**Status**: resolved
**Area**: tests

### Summary
提交前验证应采用“分层最小集”：静态检查 + 关键路径定向测试 + 跨端接口检查。

### Details
全量测试在项目里成本高、噪音大，容易影响评审效率；仅靠局部自测又容易漏掉回归。  
更稳妥的是固定三层验证：  
1) 静态规则（ruff/eslint）  
2) 与改动直接相关的后端测试集（按模块定向）  
3) 前后端调用链关键点检查（认证、权限、状态清理）

### Suggested Action
将此三层验证固化到提交模板，评审输出必须给出已执行命令和通过结论。

### Metadata
- Source: conversation
- Related Files: pyproject.toml, tests/auth/test_auth.py, tests/test_rbac_cache_invalidation.py
- Tags: verification, qa, review, submission
- Pattern-Key: process.layered_pre_submit_validation
- Recurrence-Count: 1
- First-Seen: 2026-03-06
- Last-Seen: 2026-03-06

### Resolution
- **Resolved**: 2026-03-06T11:01:23Z
- **Commit/PR**: staged (local)
- **Notes**: 已按三层验证输出“可提交/不可提交”结论。

---

## [LRN-20260306-003] best_practice

**Logged**: 2026-03-06T11:01:23Z
**Priority**: medium
**Status**: resolved
**Area**: docs

### Summary
架构/原则类评审（DRY/KISS/SOLID/YAGNI）必须和功能性结论分开陈述，避免混淆阻塞级别。

### Details
当原则建议和功能缺陷混在一起时，容易造成“问题很多但都不阻塞”的表达失真。  
项目级评审应先列阻塞问题，再列非阻塞优化，并明确每项影响范围与提交建议。

### Suggested Action
统一评审输出结构：  
1. Blocking findings（按严重度）  
2. Non-blocking improvements（原则/风格/维护性）  
3. 最终提交流水线结论

### Metadata
- Source: conversation
- Related Files: AGENTS.md
- Tags: review, principles, governance
- Pattern-Key: process.blocking_vs_nonblocking_review
- Recurrence-Count: 1
- First-Seen: 2026-03-06
- Last-Seen: 2026-03-06

### Resolution
- **Resolved**: 2026-03-06T11:01:23Z
- **Commit/PR**: n/a
- **Notes**: 后续评审统一采用阻塞/非阻塞分层表达。

---
