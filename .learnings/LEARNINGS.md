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
