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

## [LRN-20260405-001] config

**Logged**: 2026-04-05T21:45:00+08:00
**Priority**: medium
**Status**: resolved
**Area**: config

### Summary
项目测试需要手动设置 PYTHONPATH=. 才能运行，应该在 pyproject.toml 中配置

### Details
每次运行 pytest 时都需要手动设置 PYTHONPATH=. 环境变量，否则会出现模块导入错误。这是因为项目使用了 src/ 作为源码目录结构。

### Suggested Action
在 pyproject.toml 的 [tool.pytest.ini_options] 中添加 pythonpath = ["."]

### Resolution
- **Resolved**: 2026-04-05T21:45:00+08:00
- **Commit**: 6a987f8
- **Notes**: 在 pyproject.toml 中添加了 pythonpath 配置

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

---

## [LRN-20260329-001] correction

**Logged**: 2026-03-29T22:45:00+08:00
**Priority**: high
**Status**: resolved
**Area**: tests

### Summary
SMT mock 的 `/debug/*` 路由仍需保留，不能在“正式接口化”时一并删除。

### Details
本轮继续实现 allocation/agv mock 时，误把“正式接口优先”扩展成“移除现有 `/debug/*`”。
用户明确纠正：当前仓库的 SMT mock 需要保持“正式接口 + debug 接口”并存，`/debug/*` 依旧是开发联调时的有效入口，只是不应替代正式协议。

### Suggested Action
后续处理 mock 服务时遵循这条边界：
1. 正式接口必须完整、可独立联调；
2. `/debug/*` 可以保留，但只作为开发辅助入口；
3. 不再把“避免依赖 debug 接口”误解成“删除 debug 路由”。

### Metadata
- Source: user_feedback
- Related Files: tests/mock/smt_classifier/arm_mock.py, tests/mock/smt_classifier/pipeline_mock.py, tests/mock/smt_classifier/run_all.py
- Tags: correction, mock, debug-endpoints, contract-boundary

### Resolution
- **Resolved**: 2026-03-29T22:45:00+08:00
- **Commit/PR**: n/a
- **Notes**: 新增 mock 服务时保持正式接口与 `/debug/*` 同时存在。
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

## [LRN-20260309-001] best_practice

**Logged**: 2026-03-09T14:30:00+08:00
**Priority**: medium
**Status**: resolved
**Area**: frontend

### Summary
Vue 3 组件中重复执行的 `find` 操作应缓存到 computed 属性，避免热路径性能损耗。

### Details
在 ConditionEditorRow.vue 中，`fieldDataType` 和 `enumOptions` 两个 computed 属性都执行了 `props.fields.find(f => f.key === props.condition.field)`，每次渲染都会重复查找同一字段。

解决方案是引入 `currentField` computed 缓存字段定义，其他 computed 复用该结果：
```typescript
// ✅ 正确：缓存字段定义
const currentField = computed(() =>
  props.fields.find(f => f.key === props.condition.field)
)

const fieldDataType = computed(() =>
  currentField.value?.dataType || 'text'
)

const enumOptions = computed(() =>
  currentField.value?.options || []
)
```

性能影响：每次渲染从 2+ 次 `find`（O(n)）优化到 1 次。

### Suggested Action
代码审查时检查是否有：
1. 多个 computed 属性执行相同的 find/filter 操作
2. 事件处理器中重复执行 computed 中已有的查找

对于大型数组（50+ 元素），考虑使用 Map 将查找从 O(n) 优化到 O(1)。

### Metadata
- Source: simplify-and-harden
- Related Files: /Users/kaizhou/SynologyDrive/works/wes_frontend/src/components/search/ConditionEditorRow.vue
- Tags: performance, computed, caching, vue3
- Pattern-Key: frontend.cache_repeated_lookups_in_computed
- Recurrence-Count: 1
- First-Seen: 2026-03-09
- Last-Seen: 2026-03-09

### Resolution
- **Resolved**: 2026-03-09T14:30:00+08:00
- **Commit/PR**: staged (local)
- **Notes**: 已重构 ConditionEditorRow.vue 使用 currentField 缓存

---

## [LRN-20260309-002] best_practice

**Logged**: 2026-03-09T14:30:00+08:00
**Priority**: medium
**Status**: resolved
**Area**: frontend

### Summary
硬编码的字符串映射（如操作符标签、占位符）应提取到类型文件作为常量，便于复用和国际化。

### Details
ConditionEditorRow.vue 组件内部定义了 `getOperatorLabel()` 函数，包含 13 个操作符的中文标签映射。但相同映射逻辑已存在于 search-compiler.ts 的 `buildConditionLabel()` 中。

解决方案是将操作符标签提取到 `types/search.ts`：
```typescript
// ✅ 正确：在 types/search.ts 中定义
export const OPERATOR_LABELS: Record<SearchOperator, string> = {
  contains: '包含',
  equals: '等于',
  // ... 13 个操作符
} as const

export function getOperatorLabel(op: SearchOperator): string {
  return OPERATOR_LABELS[op]
}
```

好处：
1. 单一数据源（SSOT）
2. 可复用到其他组件
3. 便于未来国际化（i18n）
4. 减少 20+ 行组件代码

同样处理了 `INPUT_PLACEHOLDERS`、`BOOLEAN_LABELS` 等常量。

### Suggested Action
代码审查时检查：
1. 组件中是否有 Record<string, string> 类型的映射常量
2. 硬编码的字符串标签（如 '是'/'否'）是否应提取
3. 相同映射是否在多个文件中重复定义

### Metadata
- Source: simplify-and-harden
- Related Files: /Users/kaizhou/SynologyDrive/works/wes_frontend/src/types/search.ts, /Users/kaizhou/SynologyDrive/works/wes_frontend/src/components/search/ConditionEditorRow.vue
- Tags: types, constants, reuse, i18n
- Pattern-Key: frontend.extract_string_literals_to_type_constants
- Recurrence-Count: 1
- First-Seen: 2026-03-09
- Last-Seen: 2026-03-09

### Resolution
- **Resolved**: 2026-03-09T14:30:00+08:00
- **Commit/PR**: staged (local)
- **Notes**: 已提取 OPERATOR_LABELS、INPUT_PLACEHOLDERS、BOOLEAN_LABELS 到 types/search.ts

---

## [LRN-20260309-003] best_practice

**Logged**: 2026-03-09T14:30:00+08:00
**Priority**: low
**Status**: resolved
**Area**: frontend

### Summary
使用类型守卫函数（Type Guard）替代硬编码字符串比较，提高类型安全和可读性。

### Details
ConditionEditorRow.vue 中多处使用 `condition.operator === 'between'` 进行判断。代码审查建议使用类型守卫替代：
```typescript
// ✅ 正确：定义类型守卫
export function isBetweenOperator(op: SearchOperator): op is 'between' {
  return op === 'between'
}

// 使用时获得类型推断
if (isBetweenOperator(condition.operator)) {
  // TypeScript 自动推断 operator 类型为 'between'
}
```

好处：
1. 语义更清晰（`isBetweenOperator()` 比 `=== 'between'`）
2. 集中定义特殊操作符逻辑
3. 类型守卫提供类型收窄（type narrowing）
4. 便于重构（如将来重命名操作符）

### Suggested Action
代码审查时检查：
1. 特殊值判断是否应封装为类型守卫或谓词函数
2. 魔法字符串是否应提取为命名常量
3. 重复的条件判断是否可抽取为可测试的纯函数

### Metadata
- Source: simplify-and-harden
- Related Files: /Users/kaizhou/SynologyDrive/works/wes_frontend/src/types/search.ts, /Users/kaizhou/SynologyDrive/works/wes_frontend/src/components/search/ConditionEditorRow.vue
- Tags: typescript, type-guard, semantic
- Pattern-Key: frontend.use_type_guards_over_string_comparison
- Recurrence-Count: 1
- First-Seen: 2026-03-09
- Last-Seen: 2026-03-09

### Resolution
- **Resolved**: 2026-03-09T14:30:00+08:00
- **Commit/PR**: staged (local)
- **Notes**: 已添加 isBetweenOperator 类型守卫，组件已更新使用

---

## [LRN-20260309-004] knowledge_gap

**Logged**: 2026-03-09T10:31:00+08:00
**Priority**: high
**Status**: promoted
**Area**: backend

### Summary
项目 Mixin 系统存在组合继承层次，使用 `EnterpriseMixin` 时不应再显式继承 `AuditMixin` 或 `OptimisticLockMixin`，否则会导致 MRO 错误。

### Details
**问题现象**：
运行 Alembic 生成迁移时抛出 `TypeError: Cannot create a consistent method resolution order (MRO)`。

**根本原因**：
项目的 Mixin 组合存在以下继承链：
```
EnterpriseMixin = AuditableMixin + OptimisticLockMixin
AuditableMixin = AuditMixin + StandardMixin
AuditMixin → TimestampMixin → BaseMixin
```

当数据库表模型定义时：
```python
# ❌ 错误：重复继承导致 MRO 冲突
class User(UserBase, AuditMixin, EnterpriseMixin, SoftDeleteMixin, DataTableMixin, table=True):
```

这导致 `TimestampMixin` 和 `BaseMixin` 在 MRO 中多次出现，形成菱形继承，Python 的 C3 线性化算法无法确定一致的方法解析顺序。

**修复方案**：
移除重复的 Mixin，使用 `EnterpriseMixin` 的完整功能：
```python
# ✅ 正确：EnterpriseMixin 已包含 AuditMixin 和 OptimisticLockMixin
class User(UserBase, EnterpriseMixin, SoftDeleteMixin, DataTableMixin, table=True):
```

**受影响的模型**（7 个）：
- `User` - src/app/admin/models/user.py
- `Role` - src/app/admin/models/role.py
- `Permission` - src/app/admin/models/perm.py
- `Menu` - src/app/admin/models/menu.py
- `Device` - src/app/device/models/device.py
- `DeviceCommand` - src/app/device/models/command.py
- `WorkLine` - src/app/workline/models/workline.py

### Suggested Action
1. ~~更新 CLAUDE.md 添加 Mixin 使用规范~~ ✅ 已完成
2. 代码审查时检查是否有重复的 Mixin 继承
3. 考虑添加静态检查或 linter 规则检测此类问题

### Metadata
- Source: error
- Related Files: src/core/mixins/composite.py, src/app/admin/models/user.py, src/app/admin/models/role.py, src/app/admin/models/perm.py, src/app/admin/models/menu.py, src/app/device/models/device.py, src/app/device/models/command.py, src/app/workline/models/workline.py
- Tags: mixin, mro, inheritance, python, alembic
- Pattern-Key: backend.enterprise_mixin_already_contains_audit_and_lock
- Recurrence-Count: 1
- First-Seen: 2026-03-09
- Last-Seen: 2026-03-09

### Resolution
- **Resolved**: 2026-03-09T10:31:00+08:00
- **Promoted**: CLAUDE.md
- **Commit/PR**: staged (local)
- **Notes**: 已修复所有 7 个模型的 Mixin 继承，迁移脚本成功生成；已更新 CLAUDE.md 添加 Mixin 使用规范

---

## [LRN-20260310-005] correction

**Logged**: 2026-03-10T18:57:32+0800
**Priority**: high
**Status**: resolved
**Area**: backend

### Summary
服务层包装方法如果依赖下层通用更新逻辑做缓存失效，必须继续透传 `cache`，否则会留下静默的陈旧缓存。

### Details
在 `APIAppService.reset_validity_period()` 中，代码通过 `self.update(...)` 复用通用更新流程，但重构后遗漏了 `cache` 参数。表面上数据库更新成功，实际上三类缓存路径都被绕过了：
- `BaseService.update()` 不会执行详情缓存和列表缓存失效
- `APIAppService.update()` 不会执行 `get_by_app_id()` 的别名缓存失效
- API 鉴权侧可能继续读到过期的 `status` / `expires_at`

这个问题的危险点在于它不会直接报错，而是以 TTL 窗口内的业务陈旧数据形式暴露，容易在代码审查前漏掉。对这类“包装方法 -> 通用方法”的重构，不能只关注数据库写入路径，还要核对副作用参数是否完整透传。

本次修复是在 `reset_validity_period()` 调用 `self.update(...)` 时补回 `cache`，并增加回归测试，预热详情缓存、列表缓存和 `app_id` 别名缓存后验证三者都会被清除。

### Suggested Action
1. 以后重构服务层包装方法时，逐项核对 `cache`、事务对象、审计上下文等副作用参数是否透传
2. 对缓存敏感的业务方法补“先预热缓存再更新”的回归测试，而不是只断言数据库字段变化
3. 若方法语义上必须依赖缓存失效，优先统一走已有 `update/delete/restore` 通道，避免在包装层复制失效逻辑

### Metadata
- Source: user_feedback
- Related Files: src/app/api_auth/services/app_service.py, tests/test_api_app_service_cache.py
- Tags: cache, invalidation, service-layer, regression-test, api-auth
- Pattern-Key: backend.propagate_cache_to_wrapped_service_updates
- Recurrence-Count: 1
- First-Seen: 2026-03-10
- Last-Seen: 2026-03-10

### Resolution
- **Resolved**: 2026-03-10T18:57:32+0800
- **Commit/PR**: local workspace
- **Notes**: 已为 `reset_validity_period()` 补传 `cache`，并新增覆盖详情/列表/别名缓存失效的回归测试

---

## [LRN-20260310-006] correction

**Logged**: 2026-03-10T19:35:51+0800
**Priority**: high
**Status**: resolved
**Area**: backend

### Summary
为支持缓存命中时的 `dict -> schema` 转换而修改 `to_response()` 时，不能把任意 ORM/SQLModel `BaseModel` 都提前短路，否则会丢失关联字段。

### Details
`model_to_schema()` 在这个项目里不只是普通序列化工具，它还负责基于 SQLAlchemy inspect 判断关系是否已加载，并把已加载关联安全地映射到响应 schema。

我之前在 `BaseService.to_response()` 中加入了：
- `dict` 直接 `response_schema.model_validate(...)`
- 任意 `BaseModel` 也直接 `response_schema.model_validate(model.model_dump(...))`

第二条是错误的。项目的 ORM/SQLModel 模型本身也是 `BaseModel`，这样会把数据库查询得到的对象绕过 `model_to_schema()`，导致关联字段即使已经加载也不会被按响应 schema 正确转换，表现为“数据库查出来的对象也没有关联列”。

正确做法是：
- 只为缓存命中场景保留 `dict -> schema` 的快捷路径
- 如果对象已经是目标 schema，直接返回
- 其它 ORM/SQLModel 对象仍走 `model_to_schema()`

### Suggested Action
1. 以后给 `to_response()` 这类核心转换函数加快捷分支时，先区分“缓存字典”和“ORM 模型”两种输入来源
2. 变更公共转换函数时，至少补两类测试：数据库对象路径、缓存命中路径
3. 避免基于 `isinstance(obj, BaseModel)` 做过宽泛的分支判断，因为项目 ORM 模型同样满足这个条件

### Metadata
- Source: user_feedback
- Related Files: src/core/base_service.py, tests/test_base_service_cache.py
- Tags: response-schema, relation, sqlmodel, cache, regression-test
- Pattern-Key: backend.keep_model_to_schema_for_orm_relation_serialization
- Recurrence-Count: 1
- First-Seen: 2026-03-10
- Last-Seen: 2026-03-10

### Resolution
- **Resolved**: 2026-03-10T19:35:51+0800
- **Commit/PR**: local workspace
- **Notes**: 已移除 `to_response()` 中对任意 `BaseModel` 的短路，只保留 `dict` 与目标 schema 的快捷路径，并增加回归测试覆盖 ORM 对象路径

---

## [LRN-20260314-001] best_practice

**Logged**: 2026-03-14T00:00:00+08:00
**Priority**: high
**Status**: pending
**Area**: backend

### Summary
使用简化响应模型避免不必要的关联查询和 SQLAlchemy 异步关系加载问题

### Details
当 API 操作不需要返回关联数据时（如重置密码），应使用 `UserSimpleResponse` 而不是 `UserResponse`：

- `UserResponse` - 包含 `roles` 关系，需要额外查询
- `UserSimpleResponse` - 无关联关系，避免 `MissingGreenlet` 错误

错误示例：
```python
# 返回包含 roles 的响应，但用户对象未加载 roles 关系
return response_builder.success(data=UserResponse.model_validate(user))
# 结果：MissingGreenlet: greenlet_spawn has not been called
```

正确示例：
```python
# 使用简化模型，不需要关联数据
return response_builder.success(data=UserSimpleResponse.model_validate(user))
```

### Suggested Action
在 CLAUDE.md 中添加响应模型选择规则

### Metadata
- Source: user_feedback
- Related Files: src/app/admin/models/user.py, src/app/admin/v1/user.py
- Tags: sqlalchemy, async, response-model, performance
- Pattern-Key: response_model.simplified

---

## [LRN-20260314-002] correction

**Logged**: 2026-03-14T00:00:00+08:00
**Priority**: medium
**Status**: pending
**Area**: backend

### Summary
项目响应工具方法命名：`response_builder.success()` 而非 `response_success`

### Details
项目 `src/core/response/` 模块使用 `response_builder` 单例对象构建响应：

```python
# 正确
from src.core.response import response_builder
return cast("ResponseSchemaModel[T]", response_builder.success(data=...))

# 错误（不存在）
from src.core.response import response_success
```

### Suggested Action
无，项目已有正确的模式

### Metadata
- Source: error
- Related Files: src/core/response/__init__.py, src/core/response/response_util.py
- Tags: response, api, naming

---

## [LRN-20260314-003] correction

**Logged**: 2026-03-14T00:00:00+08:00
**Priority**: medium
**Status**: pending
**Area**: backend

### Summary
项目异常类命名：`NotFoundException` 而非 `ResourceNotFoundException`

### Details
项目 `src/core/exceptions.py` 中资源未找到的异常类是 `NotFoundException`：

```python
# 正确
from src.core.exceptions import NotFoundException
raise NotFoundException(f"用户 {user_id} 不存在")

# 错误（不存在）
from src.core.exceptions import ResourceNotFoundException
```

### Suggested Action
无，记住即可

### Metadata
- Source: error
- Related Files: src/core/exceptions.py
- Tags: exception, naming

---

## [LRN-20260314-004] best_practice

**Logged**: 2026-03-14T00:00:00+08:00
**Priority**: high
**Status**: pending
**Area**: backend

### Summary
Service 层更新操作必须通过 `self.update()` 而非 `self.repo.update()` 以确保缓存失效

### Details
`BaseService.update()` 会自动处理缓存失效，直接调用 `self.repo.update()` 会跳过这个逻辑：

```python
# 正确 - 通过 BaseService.update() 失效缓存
updated_user = await self.update(db, user_id, data, cache=cache)

# 错误 - 跳过缓存失效
updated_user = await self.repo.update(db, user_id, data)
```

`BaseService.update()` 内部实现：
```python
async def update(self, db, id, data, cache=None):
    result = await self.repo.update(db, id, data)
    if cache:
        await self.invalidate_cache(cache, id, invalidate_list=True)
    return result
```

### Suggested Action
在 CLAUDE.md 中添加 Service 层缓存失效规则

### Metadata
- Source: user_feedback
- Related Files: src/core/base_service.py
- Tags: cache, service-layer, architecture
- Pattern-Key: service.cache_invalidation

---

## [LRN-20260314-005] best_practice

**Logged**: 2026-03-14T00:00:00+08:00
**Priority**: medium
**Status**: pending
**Area**: backend

### Summary
乐观锁模型更新时需要传递 version 字段，管理员操作可由后端自动获取

### Details
继承 `OptimisticLockMixin` 的模型更新时需要 `version` 字段验证。对于管理员操作（前端不提供 version），后端可以先获取用户信息再更新：

```python
# 1. 先获取用户（包含 version）
user = await self.repo.get_by_id(db, user_id)

# 2. 更新时携带 version
updated_user = await self.update(
    db,
    user_id,
    {
        "hashed_password": hashed_password,
        "version": user.version,  # 乐观锁验证
    },
    cache=cache,
)
```

### Suggested Action
无特定建议，记住此模式即可

### Metadata
- Source: error
- Related Files: src/database/base_repository.py
- Tags: optimistic-lock, update, pattern
- Pattern-Key: optimistic_lock.auto_version

---

## [LRN-20260314-006] correction

**Logged**: 2026-03-14T10:00:00+08:00
**Priority**: high
**Status**: promoted
**Area**: backend

### Summary
状态机设计应使用成熟的 `transitions` 库，而非自研实现

### Details
在设计作业线插件的状态机时，我最初设计了一套自研的 `StateMachineDefinition` 协议和 `Transition` 数据类。用户指出这是"重复造轮子"，应该使用 Python 社区成熟的 `transitions` 库。

**transitions 库的优势**：
- 10+ 年历史，广泛使用
- 完整的回调机制（before/after/conditions/prepare）
- 内置 Graphviz 可视化支持
- 支持嵌套状态（HierarchicalMachine）
- 支持 queued 模式（并发安全）
- 社区维护，维护成本低

**依赖安装**：
```bash
uv add transitions
```

**使用示例**：
```python
from transitions import Machine

class PackingZoneStateMachine(Machine):
    @classmethod
    def get_states(cls) -> list[str]:
        return ['NEW', 'RUNNING', 'WAITING_DEVICE_RESULT', 'COMPLETED', 'FAILED']

    @classmethod
    def get_transitions(cls) -> list:
        return [
            ['start', 'NEW', 'RUNNING'],
            ['wait_device', 'RUNNING', 'WAITING_DEVICE_RESULT'],
            ['device_success', 'WAITING_DEVICE_RESULT', 'COMPLETED'],
            ['fail', '*', 'FAILED'],  # 通配符支持
        ]
```

### Suggested Action
设计状态机时，优先考虑使用 `transitions` 库，避免重复造轮子

### Metadata
- Source: user_feedback
- Related Files: docs/workline_plugin_architecture_design.md
- Tags: state-machine, python, transitions, dry
- Pattern-Key: backend.use_transitions_library_for_state_machine

### Resolution
- **Resolved**: 2026-03-14T10:00:00+08:00
- **Promoted**: CLAUDE.md
- **Notes**: 已更新设计文档，使用 transitions 库替代自研状态机

---

## [LRN-20260314-007] correction

**Logged**: 2026-03-14T10:00:00+08:00
**Priority**: high
**Status**: promoted
**Area**: backend

### Summary
设备拓扑设计应遵循 KISS 原则，使用简单的 `upstream_device_id` 字段，而非复杂的 JSON 配置

### Details
在设计设备拓扑时，我设计了一套复杂的 `DeviceTopologyConfig` JSON 配置，包含 `TopologyNode`、`TopologyEdge`、拓扑版本管理、拓扑快照等。用户指出这是"过度设计"。

**过度设计的信号**：
- 为简单场景设计复杂的数据结构
- 引入版本管理、快照等机制但实际需求不明确
- 配置项过多，维护成本高

**简化方案**：
```python
class Device(table=True):
    device_role: str           # SCANNER, ROBOT_ARM, XRAY
    role_index: int = 1        # 同角色序号
    upstream_device_id: int    # 上游设备ID（线性拓扑）
    workline_id: int           # 所属作业线
```

**数据示例**：
```
| id  | device_code    | device_role | role_index | upstream_device_id |
|-----|----------------|-------------|------------|-------------------|
| 101 | SCANNER_7_1    | SCANNER     | 1          | NULL              |
| 201 | ROBOT_ARM_7_1  | ROBOT_ARM   | 1          | 101               |
| 202 | ROBOT_ARM_7_2  | ROBOT_ARM   | 2          | 201               |
```

**教训**：
- WES 场景大部分是线性流程
- 简单的字段设计比复杂的 JSON 配置更易维护
- 符合 KISS 原则

### Suggested Action
设计数据模型时，优先考虑简单方案，避免过度抽象

### Metadata
- Source: user_feedback
- Related Files: docs/workline_plugin_architecture_design.md
- Tags: database-design, kiss, topology, over-engineering
- Pattern-Key: backend.simple_field_over_complex_json_config

### Resolution
- **Resolved**: 2026-03-14T10:00:00+08:00
- **Promoted**: CLAUDE.md
- **Notes**: 已简化设计，删除复杂的拓扑配置，改用 upstream_device_id 字段

---

## [LRN-20260317-003] correction

**Logged**: 2026-03-17T09:35:00Z
**Priority**: high
**Status**: promoted
**Category**: correction
**Area**: backend

### Summary
ModelFactory 的 `for_optimistic_update()` 和 `for_update()` 方法必须根据模型的 Mixin 组合正确选择

### Details

**错误**：
```python
class WorklineSession(
    WorklineSessionBase,
    DataTableMixin,      # ❌ 不包含 OptimisticLockMixin
    SoftDeleteMixin,
    table=True,
)

class WorklineSessionUpdate(ModelFactory(...).for_optimistic_update()):
    # ❌ 错误：没有 version 字段
```

**正确**：
```python
class WorklineSessionUpdate(ModelFactory(...).for_update()):
    # ✅ 正确：没有乐观锁时使用 for_update()
```

**Mixin 组合规则**：

| Mixin 组合 | 包含 OptimisticLockMixin | Schema 方法 |
|------------|------------------------|------------|
| `EnterpriseMixin` = `AuditMixin` + `OptimisticLockMixin` | ✅ 是 | `for_optimistic_update()` |
| `DataTableMixin` + `SoftDeleteMixin` | ❌ 否 | `for_update()` |

**原因**：
- `for_optimistic_update()` 期望模型有 `version` 字段
- Update Schema 会包含 `version: int` 作为必填字段
- 如果模型没有 `version` 字段，会导致验证失败或行为不正确

### Suggested Action
在代码审查时检查：
1. 模型是否继承 `OptimisticLockMixin`（直接或通过 `EnterpriseMixin`）
2. Update Schema 方法是否与 Mixin 组合匹配

### Metadata
- Source: user_feedback
- Related Files: 
  - src/app/workline/models/session.py
  - src/database/model_factory.py
  - src/core/mixins/composite.py
- Tags: model-factory, optimistic-lock, schema, consistency

### Resolution
- **Resolved**: 2026-03-17T09:35:00Z
- **Promoted**: CLAUDE.md
- **Commit**: 待提交
- **Notes**: 已修改 WorklineSessionUpdate 使用 `for_update()`，规则已添加到 CLAUDE.md "Update Schema 方法选择规则"

---

## [LRN-20260317-004] correction

**Logged**: 2026-03-17T09:30:00Z
**Priority**: high
**Status**: promoted
**Category**: correction
**Area**: backend

### Summary
数据库表的循环依赖应该在模型层解决，而不是修改迁移文件

### Details

**场景**：
- `WorklineInbox.session_id` → `WorklineSession.id`
- `WorklineSession.last_inbox_id` → `WorklineInbox.id`
- Alembic 无法自动解析表创建顺序

**错误做法**：手工修改迁移文件，分两步创建表和外键

**正确做法**：修改模型定义，移除导致循环的外键约束
```python
# ❌ 错误：保留外键约束
last_inbox_id: int | None = Field(
    default=None,
    foreign_key="wes_biz.workline_inbox.id",  # 循环依赖
)

# ✅ 正确：移除外键约束，保留字段用于追溯
last_inbox_id: int | None = Field(
    default=None,
    description="最后处理的 Inbox ID（便于重放）",
)
```

**原则**：
1. 循环依赖通常表明某些外键是辅助性的，不是核心业务逻辑
2. 辅助追溯字段可以不设外键约束
3. 修改模型后重新生成迁移，确保迁移文件可重现

### Suggested Action
设计外键关系时考虑：
- 是否是核心业务约束（需要外键）
- 是否只是辅助追溯字段（可以不设外键）
- 是否会造成循环依赖

### Metadata
- Source: user_feedback
- Related Files:
  - src/app/workline/models/session.py
  - src/app/workline/models/timeline.py
  - migrations/versions/20260317_0930_8f8180e751c3_create_workline_session_timeline_inbox_.py
- Tags: circular-dependency, foreign-key, alembic, migration
- See Also: LRN-20260317-003

### Resolution
- **Resolved**: 2026-03-17T09:30:00Z
- **Promoted**: CLAUDE.md
- **Commit**: 待提交
- **Notes**: 移除了 `WorklineSession.last_inbox_id` 和 `WorklineTimeline.related_inbox_id` 的外键约束，规则已添加到 CLAUDE.md "外键设计规则"

---


## [LRN-20260322-001] best_practice

**Logged**: 2026-03-22T11:32:42Z
**Priority**: high
**Status**: pending
**Category**: best_practice
**Area**: backend

### Summary
SQLModel 关系依赖的目标模型如果只放在 `TYPE_CHECKING` 中，测试导入顺序变化时可能导致 mapper 或外键表注册缺失。

### Details
这次快速回归里暴露了一个隐蔽问题：

- `src/app/device/models/device.py` 里的 `work_line: "WorkLine" = Relationship(...)`
- `src/app/device/models/command.py` / `src/app/device/models/event_log.py` 里依赖 `Device` 与 `wes_biz.work_lines`

原先这些目标模型只在 `TYPE_CHECKING` 中导入，静态类型没问题，但运行时并不会真正加载对应模块。结果是当测试以不同顺序导入模型时，SQLAlchemy 在配置 mapper / 解析外键时会报：

- `InvalidRequestError: expression 'WorkLine' failed to locate a name`
- `NoReferencedTableError: ... could not find table 'wes_biz.work_lines'`

本次的最小可用修复不是改业务逻辑，而是让这些关键目标模型在运行时也被导入，确保 mapper 和 metadata 已注册。对这类导入，`ruff` 的 `TC001` 会建议移回 type-checking block，但这里应保留运行时导入，并显式加 `# noqa: TC001` 说明原因。

### Suggested Action
对所有使用字符串关系名或跨模块外键的 SQLModel/SQLAlchemy 模型，检查目标模型是否真的会在运行时导入；如果注册顺序依赖运行时导入，不要机械遵循 `TC001`。

### Metadata
- Source: conversation
- Related Files: src/app/device/models/device.py, src/app/device/models/command.py, src/app/device/models/event_log.py, src/app/workline/models/workline.py
- Tags: sqlalchemy, sqlmodel, mapper, metadata, import-order, ruff, tc001
- See Also: LRN-20260314-001

---

## [LRN-20260323-001] best_practice

**Logged**: 2026-03-23T17:15:00+08:00
**Priority**: high
**Status**: resolved
**Category**: best_practice
**Area**: backend

### Summary
对启用了 `validate_assignment=True` 的 SQLModel 实例，不要直接给运行时动态挂载的关系属性赋值，应该走 SQLAlchemy 的关系赋值入口。

### Details
这次排查 `PUT /api/v1/users/{id}/assign-roles` 时，最初看到的是两类表象错误：

- `MissingGreenlet: greenlet_spawn has not been called`
- `ValidationError: roles -> Object has no attribute 'roles'`

真正的根因分成两层：

1. `assign_roles()` 最初使用 `get_by_id()` 获取用户，`user.roles = valid_roles` 会触发异步懒加载，导致 `MissingGreenlet`
2. 即便改成 `get_by_id_with_roles()` 预加载角色，`User` 继承链里仍然开启了 `validate_assignment=True`，而 `roles` 是在 `src/app/admin/models/__init__.py` 中通过 `relationship()` 运行时挂到模型上的，不属于 Pydantic 声明字段。于是直接执行 `user.roles = valid_roles` 时，Pydantic 会抛出 `Object has no attribute 'roles'`

本次可工作的修复组合是：

- 先通过 `get_by_id_with_roles()` 预加载当前用户角色，避免异步懒加载
- 使用 `sqlalchemy.orm.attributes.set_attribute(user, "roles", valid_roles)` 更新关系集合，而不是直接做 `user.roles = valid_roles`
- 用户响应模型中的 `roles` 使用 `RoleResponseSimple`，避免为普通用户接口隐式要求 `permissions` 关系

### Suggested Action
在所有为 SQLModel 实例更新多对多/一对多关系的 Service 中，优先检查：

1. 关系是否已预加载
2. 模型是否启用了 `validate_assignment=True`
3. 关系属性是否是运行时动态挂载而非 Pydantic 声明字段

若三者同时成立，禁止直接 `instance.relation = ...`，统一使用 SQLAlchemy 关系赋值入口并补回归测试。

### Metadata
- Source: conversation
- Related Files: src/app/admin/services/user_service.py, src/app/admin/models/__init__.py, src/app/admin/models/user.py, tests/test_user_service_assign_roles.py, tests/test_user_model.py
- Tags: sqlmodel, sqlalchemy, relationship, validate-assignment, async, missinggreenlet, pydantic
- See Also: LRN-20260314-001

### Resolution
- **Resolved**: 2026-03-23T17:15:00+08:00
- **Commit/PR**: local workspace
- **Notes**: `assign_roles()` 已改为预加载 `roles` 并使用 `set_attribute()` 更新关系，相关定向测试已补齐并通过。

---

## [LRN-20260323-002] correction

**Logged**: 2026-03-23T17:20:00+08:00
**Priority**: high
**Status**: resolved
**Category**: correction
**Area**: tests

### Summary
这个仓库执行 pytest 时，默认命令应为 `PYTHONPATH=. uv run pytest ...`，不能直接写成 `uv run pytest ...`。

### Details
本次用户明确纠正了一个重复性错误：我在仓库里多次先执行了 `uv run pytest ...`，随后才因为 `ModuleNotFoundError: No module named 'src'` 改成带环境变量的版本。

对这个仓库而言，`tests/conftest.py` 和应用代码都直接从 `src` 顶层包导入模块，因此在当前运行方式下，pytest 需要显式把仓库根目录加入 `PYTHONPATH`。正确命令应统一为：

```bash
PYTHONPATH=. uv run pytest -q ...
```

而不是：

```bash
uv run pytest -q ...
```

这不是一次性的命令修补，而是该仓库的测试执行约定。

### Suggested Action
后续在本仓库运行任何 pytest 命令时，默认使用 `PYTHONPATH=. uv run pytest ...`，不要再先尝试不带 `PYTHONPATH` 的版本。

### Metadata
- Source: user_feedback
- Related Files: AGENTS.md, tests/conftest.py
- Tags: pytest, pythonpath, test-runner, correction, repo-convention
- See Also: ERR-20260323-001

### Resolution
- **Resolved**: 2026-03-23T17:20:00+08:00
- **Commit/PR**: local workspace
- **Notes**: 已记录为仓库级测试约定；后续本仓库中的 pytest 命令将默认带 `PYTHONPATH=.`。
---
## [LRN-20260326-001] best_practice

**Logged**: 2026-03-26T15:40:00+08:00
**Priority**: high
**Status**: resolved
**Area**: tests

### Summary
Mock 服务必须使用 WES 标准事件类型，业务结果放在 data 字段，而非自建事件类型。

### Details
SMT 粗分机 E2E 测试中发现，Pipeline Mock 最初使用了内部事件类型（`SCAN_OK`, `SCAN_NG`），但 WES 回调接口只接受标准事件类型（`SCAN_COMPLETED`, `PROCESS_COMPLETED`），导致 422 错误。

正确做法：
- Mock 直接使用 WES 标准事件类型
- 扫码/检测结果通过 `data.result` 传递
- Mock 是 WES 规范的实现方，不是规范的定义方

### Suggested Action
Mock 开发规范：
1. 严格遵循硬件接口文档，不自建类型
2. 事件类型使用 WES 标准枚举（`SCAN_COMPLETED`, `PROCESS_COMPLETED`）
3. 业务结果放在 data 字段，不通过事件类型区分

### Metadata
- Source: error
- Related Files: tests/mock/smt_classifier/pipeline_mock.py
- Tags: mock, e2e-testing, event-types, wes-callback
- See Also: LRN-20260326-002

### Resolution
- **Resolved**: 2026-03-26T15:40:00+08:00
- **Commit/PR**: n/a
- **Notes**: 已在 pipeline_mock.py 中修正事件类型，使用 WES 标准类型

---

## [LRN-20260326-002] best_practice

**Logged**: 2026-03-26T15:40:00+08:00
**Priority**: high
**Status**: resolved
**Area**: tests

### Summary
macOS 上使用 multiprocessing spawn 模式时，必须显式传递环境变量给子进程。

### Details
macOS 必须使用 `spawn` 方式启动多进程（`fork` 与 Objective-C runtime 不兼容），但 spawn 模式下子进程不会自动继承父进程的环境变量。

解决方案：
1. 父进程从 `.env.e2e` 加载环境变量
2. 通过 `multiprocessing.Process(kwargs={"env_vars": ...})` 传递给子进程
3. 子进程在启动时设置这些环境变量

### Suggested Action
E2E 测试使用 multiprocessing 时：
1. 环境变量统一配置，使用 `.env.e2e` 文件
2. 子进程环境变量显式传递，特别是在 spawn 模式下
3. 子进程启动时从 kwargs 读取并设置环境变量

### Metadata
- Source: error
- Related Files: tests/e2e/smt_classifier/conftest.py, tests/mock/smt_classifier/run_all.py
- Tags: macos, multiprocessing, spawn, environment-variables, e2e-testing
- See Also: LRN-20260326-001

### Resolution
- **Resolved**: 2026-03-26T15:40:00+08:00
- **Commit/PR**: n/a
- **Notes**: 已实现 _load_env_from_file() 方法和 env_vars 传递机制

---

## [LRN-20260326-003] best_practice

**Logged**: 2026-03-26T15:40:00+08:00
**Priority**: medium
**Status**: resolved
**Area**: tests

### Summary
E2E 测试运行前必须确保数据库迁移已完成，数据初始化依赖于完整的数据库 schema。

### Details
运行 `seed_e2e_test_data.py` 时遇到错误：`column work_lines.plugin_key does not exist`，原因是数据库表结构缺少字段，需要先运行迁移。

正确顺序：
1. `uv run alembic upgrade head` - 运行数据库迁移
2. `uv run python scripts/data/seed_e2e_test_data.py` - 初始化 E2E 测试数据

### Suggested Action
在 E2E 测试文档和脚本中明确：
1. 数据库迁移先于数据初始化
2. `seed_e2e_test_data.py` 是幂等的，可以重复运行
3. 提供一键运行脚本确保顺序正确

### Metadata
- Source: error
- Related Files: scripts/data/seed_e2e_test_data.py, tests/e2e/smt_classifier/run_e2e_tests.sh
- Tags: e2e-testing, database-migration, alembic, data-seeding

### Resolution
- **Resolved**: 2026-03-26T15:40:00+08:00
- **Commit/PR**: n/a
- **Notes**: 已在 run_e2e_tests.sh 中确保正确的执行顺序

---

## [LRN-20260326-004] best_practice

**Logged**: 2026-03-26T15:40:00+08:00
**Priority**: high
**Status**: resolved
**Area**: tests

### Summary
E2E 测试需要完整的外部依赖：数据库、Redis、WES 服务、Mock 服务必须全部就绪。

### Details
E2E 测试不是单元测试，需要完整的外部依赖链：
1. PostgreSQL 数据库
2. Redis 缓存
3. WES 后端服务（uvicorn）
4. Mock 服务（Pipeline, ARM）

Mock 服务会回调 WES，如果 WES 未启动会导致测试失败。

### Suggested Action
创建完整的测试运行脚本：
1. 检查 WES 服务是否运行
2. 启动基础设施（docker-compose）
3. 运行迁移和种子数据
4. 运行 E2E 测试
5. 提供故障排查指南

### Metadata
- Source: error
- Related Files: tests/e2e/smt_classifier/run_e2e_tests.sh, tests/e2e/smt_classifier/README.md
- Tags: e2e-testing, infrastructure, dependencies, docker-compose

### Resolution
- **Resolved**: 2026-03-26T15:40:00+08:00
- **Commit/PR**: n/a
- **Notes**: 已创建完整的测试运行脚本 run_e2e_tests.sh 和详细文档

---

## [LRN-20260326-005] knowledge_gap

**Logged**: 2026-03-26T15:40:00+08:00
**Priority**: medium
**Status**: resolved
**Area**: backend

### Summary
API 认证凭证需要双向配置：WES 数据库中创建应用，Mock 服务配置相同凭证。

### Details
Mock 服务回调 WES 时返回 401/403 错误，原因是：
- WES 后端需要知道 Mock 使用的 app_id/app_secret
- Mock 需要知道 WES 的回调地址和凭证

解决方案：
1. WES 数据库中创建 API 应用（seed_e2e_test_data.py）
2. 分配回调权限（`api:callback:result`, `api:callback:event`）
3. Mock 从环境变量读取相同凭证（`.env.e2e`）

### Suggested Action
E2E 测试认证配置：
1. API 应用凭证双向配置（WES DB + Mock 环境变量）
2. 签名算法必须与 WES 完全一致
3. 使用 `.env.e2e` 统一配置环境变量

### Metadata
- Source: error
- Related Files: scripts/data/seed_e2e_test_data.py, tests/e2e/smt_classifier/setup_e2e_app.py
- Tags: api-authentication, hmac, callback, e2e-testing

### Resolution
- **Resolved**: 2026-03-26T15:40:00+08:00
- **Commit/PR**: n/a
- **Notes**: 已实现 setup_e2e_app.py 生成 .env.e2e，seed_e2e_test_data.py 创建 API 应用

---

## [LRN-20260411-001] insight

**Logged**: 2026-04-11T00:00:00+08:00
**Priority**: high
**Status**: resolved
**Category**: insight
**Area**: backend

### Summary
SMT 粗分机完整业务流程（INSPECTION_COMPLETED 是 Mock 专用）

### Details
查阅硬件文档 `docs/hardware/SMT粗分机接口调用说明书20260321-v1.md` 后，记录完整正常业务流程：

```
┌─────────┐     SCAN_COMPLETED      ┌─────────┐
│  ARM01  │ ─────────────────────▶  │  WES   │
│ (扫码)  │ ◀─────────────────────  │        │
└─────────┘     PICK_AND_PUT        └─────────┘
                   (检验OK)

┌─────────┐     result(callback)    ┌─────────┐
│  ARM01  │ ─────────────────────▶  │  WES   │
│ (检测)  │ ◀─────────────────────  │ 判定+   │ 尺寸/厚度检测
└─────────┘     PICK_AND_PUT        │ 派发   │ reel_diameter/reel_thickness
                                 └─────────┘

┌──────────┐    result(callback)   ┌─────────┐
│ PIPELINE │ ◀─────────────────    │  WES   │
│  01      │ ───────────────────▶   │        │
└──────────┘    MOVE_FORWARD       └─────────┘

┌─────────┐     result(callback)   ┌─────────┐
│  ARM02  │ ◀─────────────────     │  WES   │
│ (出料)  │ ───────────────────▶   │ 分配+   │ 料箱分配/AGV呼叫
└─────────┘    PICK_AND_PUT        └─────────┘
```

**完整步骤**：
1. **ARM01 扫码** → 上报 `SCAN_COMPLETED` → `/api/v1/callback/event`
2. **WES 校验** → 检验 OK，下发 `PICK_AND_PUT` 指令给 ARM01
3. **ARM01 执行+检测** → 放置到流水线进料位，执行尺寸检测+测厚 → 上报 result → `/api/v1/callback/result`
4. **WES 判定** → 根据 `reel_diameter`, `reel_thickness` 与 WMS 物料信息对比（TODO）
5. **检测 OK** → 下发 `MOVE_FORWARD` 指令给 PIPELINE01
6. **PIPELINE01 执行** → 完成后上报 result → `/api/v1/callback/result`
7. **WES 料箱分配** → 无合适料箱则呼叫 AGV，有料箱时下发 `PICK_AND_PUT` 给 ARM02
8. **ARM02 出料** → 从流水线出料位抓取放置到料箱 → 上报 result → `/api/v1/callback/result`

**关键发现**：
- INSPECTION_COMPLETED 不是真实硬件协议事件
- 检测数据（reel_diameter/reel_thickness）通过 PICK_AND_PUT 命令结果返回
- WES 需要与 WMS 物料基础信息对比（TODO：暂返回模拟数据）

### Suggested Action
- 保留 INSPECTION_COMPLETED 处理器用于 E2E 测试
- 后续实现真实 WMS 物料信息对比逻辑

### Metadata
- Source: investigation
- Related Files:
  - docs/hardware/SMT粗分机接口调用说明书20260321-v1.md
  - src/workline_plugins/smt_classifier/plugin.py
- Tags: hardware-contract, smt-classifier, workflow, wms-comparison

### Resolution
- **Resolved**: 2026-04-11T00:00:00+08:00
- **Notes**: 完整业务流程已记录

---

## [LRN-20260413-001] best_practice

**Logged**: 2026-04-13T02:54:00Z
**Priority**: medium
**Status**: resolved
**Category**: best_practice
**Area**: backend

### Summary
FastAPI 基础设施健康检查应使用 Depends 机制 + DB Session 复用

### Details
在代码审计中发现 callback API 的 `_check_system_ready()` 存在两个问题：
1. 只检查 Redis 和 Celery，**未检查 DB**（DB 不可用时 Inbox 无法写入）
2. 在多个 endpoint 中重复相同的内联检查逻辑

通过使用 FastAPI 的 `Depends` 机制，将健康检查抽离为独立函数：
- 接受 `db: AsyncSessionDep` 参数，复用 API 传入的 DB 连接
- 在需要 Fast Fail 的 endpoint 上通过 `dependencies=[Depends(fast_fail_check)]` 启用

### Suggested Action
在后续开发中，对于需要基础设施健康检查的 API，优先使用此模式：
```python
async def fast_fail_check(db: AsyncSessionDep) -> None:
    # 使用传入的 db session 检查
    ...

@router.post("/endpoint", dependencies=[Depends(fast_fail_check)])
async def handler(..., db: AsyncSessionDep):
    # db 已在 fast_fail_check 中复用
```

### Metadata
- Source: code_audit
- Related Files:
  - src/utils/fast_fail.py
  - src/utils/health.py
  - src/app/callback/v1/callback.py
- Tags: fastapi, health-check, best-practice

## [LRN-20260418-001] correction

**Logged**: 2026-04-18T11:33:50+08:00
**Priority**: medium
**Status**: resolved
**Category**: correction
**Area**: backend

### Summary
不要把 `RequireAPIPermission` 用到后台查询路由上

### Details
在 review 后续修复里，把 `src/app/callback/v1/callback_log.py` 的日志查询接口错误地加上了
`RequireAPIPermission("api:callback:log:*")`。这类依赖属于应用侧签名认证边界，要求请求带
`X-App-ID`、`X-Timestamp`、`X-Signature`，适用于 `/callback/result`、`/callback/event` 这类设备/
外部系统回调入口，不适用于后台或内部查询接口。

这次错误的根因是把“需要鉴权”直接等同为“需要 API 应用权限”，没有先核实该路由属于用户 JWT/RBAC
边界还是应用签名边界。

### Suggested Action
以后遇到权限类 review 项时，先区分：
1. 用户后台接口：`require_auth` / `RequirePermission`
2. 应用/设备调用接口：`RequireAPIAuth` / `RequireAPIPermission`
3. 公共接口：显式说明为何允许匿名

在没有证据前，不要把一种权限模型直接替换到另一种接口上。

### Metadata
- Source: user_feedback
- Related Files:
  - src/app/callback/v1/callback_log.py
  - src/core/api_security.py
  - src/core/rbac.py
- Tags: auth-boundary, review, correction

---

## [LRN-20260424-001] correction

**Logged**: 2026-04-24T19:40:00+08:00
**Priority**: high
**Status**: resolved
**Category**: correction
**Area**: backend

### Summary
删除代码前必须验证实际使用情况，不能仅凭文档或注释判断。

### Details
在 plugin refactoring 计划中，`transition_validator.py` 被标注为"未使用代码，可以删除"。但 Codex review 发现它实际上正在被 `orchestrator.py` 使用：
- `orchestrator.py:31` — import
- `orchestrator.py:151` — instantiate
- `orchestrator.py:479-484` — validate() call

如果按计划删除，会导致 runtime break + tests fail。

**根本原因**：计划依赖文档/注释判断代码使用情况，而非实际代码验证。

### Suggested Action
删除代码前的验证步骤：
1. 使用 `grep -r "from X import" src/` 检查导入
2. 使用 `grep -r "function_name" src/` 检查调用
3. 运行相关测试确认删除不会破坏功能
4. 检查是否有 TYPE_CHECKING 块内的引用

### Metadata
- Source: user_feedback
- Related Files:
  - src/workline_runtime/transition_validator.py
  - src/workline_runtime/orchestrator.py
- Tags: code-deletion, verification, review, runtime-dependency
- Pattern-Key: backend.verify_actual_usage_before_deletion

### Resolution
- **Resolved**: 2026-04-24T19:40:00+08:00
- **Commit/PR**: feature/plugin-refactoring
- **Notes**: 保留 transition_validator.py，只删除 registry 预留字段

---

## [LRN-20260424-002] correction

**Logged**: 2026-04-24T19:40:00+08:00
**Priority**: high
**Status**: resolved
**Category**: correction
**Area**: backend

### Summary
NullPlugin 默认不应静默返回 no-op，配置错误应该显式抛出。

### Details
原实现中，missing plugin 会 resolves to NullPlugin 并静默返回 no-op。这会 mask 配置错误：
- 生产环境配置错误被隐藏
- 插件未注册时，session 不会报错但也不会有业务逻辑
- 问题只能在事后追溯中发现，而非即时暴露

**修正方案**：
```python
_ALLOW_NULL_PLUGIN = False  # 默认不允许

def _load_plugin(plugin_class):
    if plugin_class is None:
        if not _ALLOW_NULL_PLUGIN:
            raise PluginNotFoundError(...)
        return null_plugin  # 只有显式 opt-in 时才返回
```

**允许范围**：
- ✅ Tests — `set_allow_null_plugin(True)`
- ✅ Explicit disabled lines — registry 中标记 disabled
- ❌ Missing plugin (生产) — 抛错，不 silent

### Suggested Action
设计 fallback/singleton 模式时：
1. 默认行为应暴露配置错误，而非静默处理
2. opt-in 机制要显式配置，不能依赖隐式行为
3. singleton 的注释和实现必须一致

### Metadata
- Source: user_feedback
- Related Files:
  - src/workline_runtime/orchestrator.py
  - src/workline_runtime/null_plugin.py
  - src/workline_runtime/exceptions.py
- Tags: null-plugin, config-error, silent-noop, error-handling
- Pattern-Key: backend.expose_config_errors_not_silent_fallback

### Resolution
- **Resolved**: 2026-04-24T19:40:00+08:00
- **Commit/PR**: feature/plugin-refactoring
- **Notes**: 新增 PluginNotFoundError，默认不允许 NullPlugin

---

## [LRN-20260424-003] best_practice

**Logged**: 2026-04-24T19:40:00+08:00
**Priority**: medium
**Status**: promoted
**Category**: best_practice
**Area**: backend

### Summary
Handler 签名选择规则：inbox vs NormalizedCommandResult 的注入优先级

### Details
框架 `_resolve_handler_model_arg()` 根据 handler 的第三个参数类型注解自动选择注入内容：

| Handler 签名 | 注入参数 | 适用场景 |
|-------------|---------|---------|
| `async def handler(self, ctx, inbox)` | 原始 inbox 实体 | 自定义 payload 解析、DSL 插件 |
| `async def handler(self, ctx, result: NormalizedCommandResult)` | 标准化输入模型 | 装饰器插件、类型安全、error_code 别名 |

**注入优先级**：
1. `ctx.normalized_input` 存在且类型匹配 → 直接返回
2. 调用 `normalize_inbox_input(inbox)` → 返回标准化模型
3. 标准化失败 → 回退到 `param_type.model_validate(payload)`

**系统级 vs 业务级字段**：
- 系统级（框架标准化）：`error_code`, `result`, `command_code`, `correlation_id`
- 业务级（插件解析）：`payload_json["data"]`, 业务模型

### Suggested Action
装饰器插件推荐使用 `NormalizedCommandResult`，获取系统级字段便利；业务数据自行解析保持灵活性。

### Metadata
- Source: investigation
- Related Files:
  - src/workline_runtime/plugin_base.py
  - src/workline_runtime/plugin_sdk/normalizers/input_normalizer.py
  - tests/workline_runtime/test_handler_signature_selection.py
- Tags: handler-signature, injection, plugin-framework, normalized-input
- Pattern-Key: backend.handler_signature_injection_priority

### Resolution
- **Resolved**: 2026-04-24T19:40:00+08:00
- **Promoted**: docs/plugin_development_guide.md
- **Notes**: 文档已新增 Handler 签名选择规则章节

---

## [LRN-20260425-001] best_practice

**Logged**: 2026-04-25T10:49:23Z
**Priority**: high
**Status**: resolved
**Category**: best_practice
**Area**: backend

### Summary
同一设备硬件任务队列不能只依赖状态字段读值，真实派发前必须锁定设备行。

### Details
本轮实现 DEVICE 队列语义时，最初通过 `device_status == RUNNING` 或 `current_command_id is not None` 阻止下一条同设备命令。这个逻辑在单 worker 下成立，但多 Celery worker 并发时，两个事务可能同时读到 `IDLE`，从而同时向同一硬件设备下发命令。

正确做法是：真实设备 HTTP 派发前通过 Repository 查询并 `SELECT ... FOR UPDATE` 锁定设备行，再检查 `device_status/current_command_id/maintenance_mode/capabilities`，ACK 后在同一事务内更新占用投影。

### Suggested Action
后续所有“单资源串行化”的运行态治理遵循：
1. 先锁定资源行；
2. 在锁内做状态/能力检查；
3. 在同一事务内写入占用或释放投影；
4. 用回归测试证明派发路径使用锁定查询。

### Metadata
- Source: review
- Related Files: src/app/device/repositories/device_repository.py, src/celery_app/tasks/workline.py, tests/workline_runtime/test_outbox_dispatcher.py
- Tags: concurrency, celery, device-queue, row-lock, outbox
- Pattern-Key: backend.device_dispatch_lock_before_hardware_side_effect
- Recurrence-Count: 1
- First-Seen: 2026-04-25
- Last-Seen: 2026-04-25

### Resolution
- **Resolved**: 2026-04-25T10:46:30Z
- **Commit/PR**: 67f506c / #10
- **Notes**: 新增 `get_by_device_code_for_update()`，派发前锁定设备行，并补充锁定查询回归测试。

---

## [LRN-20260425-002] correction

**Logged**: 2026-04-25T10:49:23Z
**Priority**: medium
**Status**: resolved
**Category**: correction
**Area**: docs

### Summary
用户明确要求“使用中文信息”，后续状态更新和最终汇总应使用中文。

### Details
在 PR 合并/清理流程中，用户提醒“使用中文信息”。本项目文档、业务沟通和多数状态说明以中文为主；当用户明确指定语言时，工具进度说明、PR/merge 结果摘要和后续收尾都应保持中文，避免英文模板化输出。

### Suggested Action
在该工作区内：
1. 用户使用中文或明确要求中文时，默认用中文回复；
2. 命令名、路径、PR 标题等技术标识保留原文；
3. 汇总要简洁列出结果、提交号、PR 链接和验证状态。

### Metadata
- Source: user_feedback
- Related Files: AGENTS.md
- Tags: communication, language-preference, chinese
- Pattern-Key: communication.prefer_chinese_when_user_requests_chinese
- Recurrence-Count: 1
- First-Seen: 2026-04-25
- Last-Seen: 2026-04-25

### Resolution
- **Resolved**: 2026-04-25T10:49:23Z
- **Commit/PR**: n/a
- **Notes**: 后续本工作区状态更新和最终答复使用中文。

---

## [LRN-20260506-001] correction

**Logged**: 2026-05-06T14:27:29+08:00
**Priority**: high
**Status**: promoted
**Category**: correction
**Area**: docs

### Summary
计划/规划文档应保持可读性，不能塞入大段实现代码。

### Details
用户纠正：`docs/superpowers/plans/2026-05-06-workline-emergency-stop.md` 中填入了太多完整代码和测试代码，破坏了计划文档可读性。正确做法是让计划文档承载目标、架构决策、业务约定、任务边界、验收标准、风险和验证方式；实现细节应在编码阶段通过 TDD、diff、测试和提交体现。

### Suggested Action
后续编写计划文档时，只保留关键接口名、文件职责、状态流、错误码、数据字段、测试场景和验证命令。避免完整类实现、完整函数实现和大段测试代码。该规则已提升到 `AGENTS.md` 和 `CLAUDE.md`。

### Metadata
- Source: user_feedback
- Related Files: AGENTS.md, CLAUDE.md, docs/superpowers/plans/2026-05-06-workline-emergency-stop.md
- Tags: planning, documentation, readability
- Pattern-Key: docs.planning_readability
- Recurrence-Count: 1
- First-Seen: 2026-05-06
- Last-Seen: 2026-05-06

### Resolution
- **Resolved**: 2026-05-06T14:27:29+08:00
- **Promoted**: AGENTS.md, CLAUDE.md
- **Notes**: 已在两个根级协作文件中加入 Planning Document Readability 规则。

---

## [LRN-20260506-002] correction

**Logged**: 2026-05-06T14:31:00+08:00
**Priority**: high
**Status**: resolved
**Category**: correction
**Area**: docs

### Summary
`.learnings` 只能追加记录，初始化或记录学习时不得覆盖既有内容。

### Details
用户纠正：`.learnings` 里的内容应该是追加，而不是覆盖。此前初始化命令对已存在的 `LEARNINGS.md`、`ERRORS.md`、`FEATURE_REQUESTS.md` 写入了默认头部，导致既有学习和错误记录被替换。正确做法是先检测文件是否存在；已存在时只追加新的学习条目，不重写文件头或历史内容。

### Suggested Action
后续使用 self-improvement 时：
1. 先检查 `.learnings` 文件是否存在；
2. 已存在的文件不写默认模板；
3. 新学习只追加到对应文件末尾；
4. 若误覆盖，立即用 git diff 确认并恢复，再重新追加。

### Metadata
- Source: user_feedback
- Related Files: .learnings/LEARNINGS.md, .learnings/ERRORS.md, .learnings/FEATURE_REQUESTS.md
- Tags: self-improvement, learnings, append-only, data-preservation
- Pattern-Key: learnings.append_not_overwrite
- Recurrence-Count: 1
- First-Seen: 2026-05-06
- Last-Seen: 2026-05-06

### Resolution
- **Resolved**: 2026-05-06T14:31:00+08:00
- **Commit/PR**: n/a
- **Notes**: 已恢复被覆盖的 `.learnings` 文件内容，并改为仅追加本次学习记录。

---

## [LRN-20260519-001] correction

**Logged**: 2026-05-19T23:40:35+08:00
**Priority**: high
**Status**: pending
**Category**: correction
**Area**: docs

### Summary
工业自动化方案必须先锚定物理工位和执行主体，再抽象 Workline Session。

### Details
用户纠正了 SMT 分拣入库文档中的目标侧供箱建模。此前把 `TARGET_RACK_SUPPLY` 抽象成“检查五层货架区可用货架”，容易让 WES 越过现场边界去全局选择货架。正确口径是：先检查分拣机五层货架工作位是否已有可用货架；若没有，WES 请求 WMS 分配可用五层货架，由 WMS 调度 AGV 将该货架送至分拣机五层货架工作位；AGV 到位回调后，WES 再触发 `TARGET_BIN_FLOW` 请求 CTU 从当前操作面取料箱。AGV 负责五层货架搬运和换面，CTU 只负责目标料箱在五层货架与流水线之间搬运，流水线步进电机负责料箱点位移动。

### Suggested Action
后续审计或编写工业自动化方案时，按以下顺序建模：
1. 先确认真实物理工位，例如分拣机五层货架工作位、单层货架 STATION、流水线扫码位/工作位/出料位；
2. 再确认每个动作的执行主体和权责边界，尤其区分 WES、WMS、AGV、CTU、流水线、机械臂；
3. 最后再设计 Session、业务键、状态流和回调合同；
4. 对“检查、分配、调度、到位、换面、取箱、投料、扫码、准入”等词逐个明确主语，避免让 WES 替代 WMS/RCS 做资源分配或设备控制。

### Metadata
- Source: user_feedback
- Related Files: docs/business/smt_sorter_inbound_workflow_guide.md, docs/superpowers/specs/2026-05-19-smt-sorter-inbound-plugin-spec.md
- Tags: industrial-automation, workline, docs, session-design, wes-wms-boundary
- Pattern-Key: docs.industrial_workline_physical_anchor
- Recurrence-Count: 1
- First-Seen: 2026-05-19
- Last-Seen: 2026-05-19

---

## [LRN-20260609-001] correction

**Logged**: 2026-06-09T11:26:35+08:00
**Priority**: high
**Status**: pending
**Category**: correction
**Area**: tests

### Summary
Mock ECS 设备运行耗时应在每条命令接收时随机生成，而不是新增手动接口或 Docker 启动时固定。

### Details
用户连续纠正了 Mock ECS 延迟建模方式。先前实现新增了 `/command-delay` 调试接口，随后又改成 Docker 启动/重置时为每台设备固定随机 2~8 秒；这两种方式都不够贴近真实设备。正确模型是：设备空闲时 `command_delay_seconds=null`；每次接收 WES 命令后立即 ACK，并为本条命令随机生成 2~8 秒耗时；执行期间状态保持 `RUNNING/current_command_id` 并暴露本条命令的 `command_delay_seconds`；Result 回调后恢复 `IDLE` 并清空延迟。

### Suggested Action
后续实现设备 Mock 行为时，优先在真实物理事件边界建模，不轻易新增人工调试接口。对“设备执行耗时”类行为，应在命令接收时生成命令级状态，并用测试覆盖：空闲状态无耗时、每条命令独立随机、旧调试接口不存在。

### Metadata
- Source: user_feedback
- Related Files: tests/mock/ecs_mock_server.py, tests/mock/test_ecs_mock_server.py, docs/hardware/粗分机内部Mock与Sandbox调试手册.md
- Tags: mock, ecs, device-state, command-delay, tdd
- Pattern-Key: mock.device_delay_per_command
- Recurrence-Count: 1
- First-Seen: 2026-06-09
- Last-Seen: 2026-06-09

---

## [LRN-20260609-002] insight

**Logged**: 2026-06-09T11:26:35+08:00
**Priority**: high
**Status**: pending
**Category**: insight
**Area**: backend

### Summary
当前粗分机 WorkLine 联调表现为工作线级串行准入，不是设备级并行推进。

### Details
并行联调中，通过让第一笔物料卡在 `RS-OUTPUT-ARM-01` 的 `PUT_TO_BIN` 等待结果阶段，验证第二笔 `SCAN_COMPLETED` 的处理方式。即使 `RS-INPUT-ARM-01` 和 `RS-CONVEYOR-01` 为空闲，第二笔仍被诊断为 `Workline entry admission blocked by busy session`，直到第一笔完成后才进入正式 session 并下发命令。这说明当前实现是 busy session 级入口闸门，而非按设备资源/WIP 工位并行。

### Suggested Action
如果要实现工作线内设备并行，不能只改 Mock；需要调整 WorkLine 准入和编排模型，从“工作线是否有 busy session”改为“物理工位、WIP、目标设备是否可接收下一笔”。同时保留每台设备自己的互斥和 `current_command_id` 约束。

### Metadata
- Source: conversation
- Related Files: src/app/workline/services/inbox_batch_processor.py, src/app/workline/services/device_command_gateway.py, src/workline_plugins/rough_sorter/plugin.py
- Tags: workline, rough-sorter, concurrency, admission, device-dispatch
- Pattern-Key: workline.admission_session_serial
- Recurrence-Count: 1
- First-Seen: 2026-06-09
- Last-Seen: 2026-06-09
