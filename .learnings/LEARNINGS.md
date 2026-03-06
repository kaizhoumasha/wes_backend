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
