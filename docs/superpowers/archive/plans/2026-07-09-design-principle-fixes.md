# 设计原则异常修复计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 消除 `docs/architecture/workline-and-plugin-restructuring.md` 验收中发现的 DRY/KISS/SOLID/YAGNI 违规项

**Architecture:** 分4个独立任务：Task 1 提取共享 helper 模块消除 DRY 违规；Task 2 拆分架构文档解决 KISS 问题；Task 3 评估并精简 WMS port 解决 SOLID 接口隔离问题；Task 4 标记/降级 YAGNI 实体

**Tech Stack:** Python 3.12+, Pydantic, SQLModel, pytest

## Global Constraints

- 使用中文进行沟通、文档和 Commit Comment
- 遵守分层架构：API → Service → Repository → Database
- 修改函数前运行 GitNexus impact analysis
- 项目命令使用 `uv run ...`
- 保留有价值注释，代码行为变化时同步更新注释
- 每个任务独立可测试、可回滚

---

## 调查数据摘要

### DRY 违规：6 类 helper 函数在 18 个文件中重复定义

| 函数 | 定义次数 | 文件分布 | 签名不一致 |
|------|---------|---------|-----------|
| `_required_text` | 11 | 6个模块 | ✅ 参数名/类型不一致 |
| `_non_empty_text` | 7 | 7个模块 | ✅ 返回类型不一致(bool vs str\|None) |
| `_text` | 5 | 4个模块 | ✅ 返回类型不一致(str vs str\|None) |
| `_string_list` | 3 | 3个模块 | ✅ 签名不一致 |
| `_dict_copy` | 2 | 2个模块 | 一致 |
| `_json_safe` | 1 | 1个模块 | N/A |

### SOLID 违规：7 个 WMS Port 零生产引用

| Port | 行数 | 方法数 | 生产引用 |
|------|------|--------|---------|
| `master_data.py` | 44 | 4 | 0 |
| `document.py` | 121 | 13 | 0 |
| `inventory_query.py` | 53 | 4 | 0 |
| `inventory_transaction.py` | 86 | 8 | 0 |
| `fulfillment.py` | 74 | 10 | 0 |
| `event.py` | 112 | 12 | 0 |
| `reconciliation_query.py` | 50 | 5 | 0 |

### YAGNI 实体：4 个已实现但 Phase 3/4 未使用

| 实体 | 行数 | 文件 | 状态 |
|------|------|------|------|
| `ExternalContractProfile` | 294 | `src/app/contracts/` | 被 capability dispatcher 引用但未用于业务流 |
| `IntegrationLab` | 144 | `src/app/runtime/orchestration/` | 完整实现但无调用方 |
| `ScenarioReplay` | 282 | `src/app/runtime/orchestration/` | 完整实现但无调用方 |
| `ActiveObjectRegistry` | 112 | `src/app/active_objects/` | 被 workline_active_objects 引用但未用于业务流 |

### KISS 问题

- 架构文档 2631 行 / 262KB，超出单文件可维护阈值
- 30+ 核心实体定义，部分仅占位

---

### Task 1: 扩展 value_normalization 模块消除 DRY 违规

**Files:**
- Modify: `src/utils/value_normalization.py` (追加 7 个新函数)
- Modify: `src/app/runtime/capabilities/material_flow/sorter_inbound_runtime_service.py:316-365`
- Modify: `src/app/runtime/capabilities/material_flow/sorter_inbound_preview_service.py:222-240`
- Modify: `src/app/runtime/capabilities/material_flow/smt_ng_wms_reconciliation_runtime_service.py:170-185`
- Modify: `src/app/runtime/capabilities/material_flow/smt_ng_wms_reconciliation_preview_service.py:125-130`
- Modify: `src/app/runtime/capabilities/material_flow/single_layer_rack_orchestration_service.py:606-612`
- Modify: `src/app/runtime/capabilities/material_flow/contracts/sorting_inbound_context.py:21-36`
- Modify: `src/app/runtime/orchestration/orchestrator_bridge.py:122-130`
- Modify: `src/app/runtime/orchestration/integration_lab.py:107-130`
- Modify: `src/app/runtime/orchestration/scenario_replay.py:205-210`
- Modify: `src/app/runtime/orchestration/benchmark_gate.py:87-92`
- Modify: `src/app/runtime/orchestration/p0_e2e_gate.py:230-235`
- Modify: `src/app/runtime/orchestration/runtime_intent_effects.py:223-228`
- Modify: `src/app/runtime/orchestration/services/conveyor_queue_membership_writer_service.py:845-855`
- Modify: `src/app/runtime/orchestration/services/device_runtime_projection_writer_service.py:38-45`
- Modify: `src/app/runtime/orchestration/services/query/runtime_query_service.py:2995-3000`
- Modify: `src/app/handling/services/operation_service.py:205-215`
- Modify: `src/app/wms_integration/services/transport_contract.py:261-270`
- Modify: `src/app/rack/services/operation_service.py:1176-1185`
- Modify: `src/app/rack/services/task_lifecycle_service.py:514-520`
- Modify: `src/app/device/services/device_command_service.py:44-50`
- Modify: `src/app/workline/inbox_claim_bucket.py:13-18`
- Modify: `src/app/workline/services/workline_service.py:125-130`
- Test: `tests/utils/test_value_normalization.py` (追加新测试)

**Interfaces:**
- Consumes: `src/utils/value_normalization.py` 已有函数 `coerce_string_value`, `coerce_optional_str`
- Produces (新增 7 个):
  - `require_text(payload: Mapping[str, Any], field_name: str) -> str`
  - `require_text_any(payload: Mapping[str, Any], *field_names: str) -> str`
  - `string_list(payload: Mapping[str, Any], field_name: str) -> list[str]`
  - `mapping_copy(value: Any) -> dict[str, Any]` — 区别于已有 `as_dict`（仅接受 `dict`）
  - `json_safe(value: Any) -> Any`
  - `positive_quantity(value: Any) -> float`
  - `positive_timeout_seconds(value: Any) -> int`
- 复用已有（2 个）:
  - `coerce_string_value(value, default="")` 替代 `_text`
  - `coerce_optional_str(value)` 替代 `_non_empty_text`

**映射表（旧 → 新）:**

| 旧函数 | 新函数 | 位置 |
|--------|--------|------|
| `_text(value)` | `coerce_string_value(value, default="")` | 已有 |
| `_non_empty_text(value)` | `coerce_optional_str(value)` | 已有 |
| `_required_text(payload, field)` | `require_text(payload, field)` | 新增 |
| `_required_text_any(payload, *fields)` | `require_text_any(payload, *fields)` | 新增 |
| `_string_list(payload, field)` | `string_list(payload, field)` | 新增 |
| `_dict_copy(value)` | `mapping_copy(value)` | 新增 |
| `_json_safe(value)` | `json_safe(value)` | 新增 |
| `_positive_quantity(value)` | `positive_quantity(value)` | 新增 |
| `_positive_timeout_seconds(value)` | `positive_timeout_seconds(value)` | 新增 |

- [ ] **Step 1: 运行 GitNexus impact analysis**

```bash
for symbol in "sorter_inbound_runtime_service" "single_layer_rack_orchestration_service" "sorting_inbound_context" "conveyor_queue_membership_writer_service"; do
  echo "=== $symbol ===" && gitnexus impact --target "$symbol" --direction upstream 2>/dev/null || true
done
```

- [ ] **Step 2: 扩展 `src/utils/value_normalization.py`**

在现有文件 `__all__` 之前追加 7 个新函数:

```python
def require_text(payload: Mapping[str, Any], field_name: str) -> str:
    """从 Mapping 中取必填非空字符串，缺失或为空抛出 ValueError。"""
    value = coerce_string_value(payload.get(field_name))
    if not value:
        raise ValueError(f"{field_name} is required")
    return value


def require_text_any(payload: Mapping[str, Any], *field_names: str) -> str:
    """从多个候选字段中取第一个非空字符串值，全部缺失抛出 ValueError。"""
    for field_name in field_names:
        value = coerce_string_value(payload.get(field_name))
        if value:
            return value
    raise ValueError(f"{'/'.join(field_names)} is required")


def string_list(payload: Mapping[str, Any], field_name: str) -> list[str]:
    """从 Mapping 中安全取字符串列表。"""
    raw = payload.get(field_name)
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw]
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if str(item)]


def mapping_copy(value: Any) -> dict[str, Any]:
    """安全浅拷贝 Mapping 为 dict，非 Mapping 返回 {}。
    
    区别于 as_dict()：as_dict 仅接受 dict 类型，本函数接受任何 Mapping。
    """
    if not isinstance(value, Mapping):
        return {}
    return dict(value)


def json_safe(value: Any) -> Any:
    """递归转换 Decimal/tuple 为 JSON 可序列化类型。"""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe(item) for item in value]
    return value


def positive_quantity(value: Any) -> float:
    """正数校验，非正数抛出 ValueError。"""
    try:
        qty = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("quantity must be positive") from exc
    if qty <= 0:
        raise ValueError("quantity must be positive")
    return qty


def positive_timeout_seconds(value: Any) -> int:
    """正整数超时校验，None 默认 300。"""
    if value is None:
        return 300
    try:
        secs = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout_seconds must be positive") from exc
    if secs <= 0:
        raise ValueError("timeout_seconds must be positive")
    return secs
```

更新 `__all__` 追加: `"json_safe", "mapping_copy", "positive_quantity", "positive_timeout_seconds", "require_text", "require_text_any", "string_list"`

- [ ] **Step 3: 编写新函数的单元测试**

在 `tests/utils/test_value_normalization.py` 中追加新测试类: `TestRequireText`, `TestRequireTextAny`, `TestStringList`, `TestMappingCopy`, `TestJsonSafe`, `TestPositiveQuantity`, `TestPositiveTimeoutSeconds`（覆盖正常值、None、空字符串、零值、负数等边界条件）。

- [ ] **Step 4: 运行新测试确认通过**

```bash
uv run pytest tests/utils/test_value_normalization.py -v
```

- [ ] **Step 5: 逐文件替换 — material-flow 域（4 文件）**

**5a. `sorter_inbound_runtime_service.py`** — 删除 L316-365 私有 helper，替换 import:
```python
from src.utils.value_normalization import (
    coerce_string_value, positive_quantity, positive_timeout_seconds,
    require_text, require_text_any, string_list,
)
```
映射: `_required_text`→`require_text`, `_required_text_any`→`require_text_any`, `_text`→`coerce_string_value`, `_string_list`→`string_list`, `_positive_quantity`→`positive_quantity`, `_positive_timeout_seconds`→`positive_timeout_seconds`

**5b. `sorter_inbound_preview_service.py`** — 删除 L222-240，替换 import: `from src.utils.value_normalization import coerce_string_value, string_list`

**5c. `smt_ng_wms_reconciliation_runtime_service.py`** — 删除 L170-185，替换 import: `from src.utils.value_normalization import coerce_string_value, require_text`

**5d. `smt_ng_wms_reconciliation_preview_service.py`** — 删除 L125-130，替换 import: `from src.utils.value_normalization import coerce_string_value`

- [ ] **Step 6: 运行 material-flow 域测试确认无回归**

```bash
uv run pytest tests/workline_runtime/ tests/contracts/workline/ -v --tb=short
```

- [ ] **Step 7: 替换 — single_layer_rack_orchestration_service.py**

删除 L606-612 的 `_non_empty_text` 和 `_enum_text`，替换 import:
```python
from src.utils.value_normalization import coerce_optional_str
```
`_non_empty_text`→`coerce_optional_str`, `_enum_text` 内联为 `coerce_optional_str(getattr(value, "value", value))`

- [ ] **Step 8: 替换 — sorting_inbound_context.py**

删除 L21-36 的 `_dict_copy` 和 `_json_safe`，替换 import:
```python
from src.utils.value_normalization import json_safe, mapping_copy
```

- [ ] **Step 9: 逐文件替换 — runtime/orchestration 域（7 文件）**

**9a. `orchestrator_bridge.py`** — `_dict_copy` → `mapping_copy`
**9b. `integration_lab.py`** — `_required_text` → `require_text`, `_required_text_tuple` 保留
**9c. `scenario_replay.py`** — `_text` → `coerce_string_value`
**9d. `benchmark_gate.py`** — 删除 L87-88，替换 import: `from src.utils.value_normalization import coerce_optional_str`。调用处适配: `if not _non_empty_text(x)` → `if coerce_optional_str(x) is None`
**9e. `p0_e2e_gate.py`** — 同上。`if _non_empty_text(x) and ...` → `if coerce_optional_str(x) is not None and ...`
**9f. `runtime_intent_effects.py`** — `_non_empty_text` → `coerce_optional_str`
**9g. `conveyor_queue_membership_writer_service.py`** — `_required_text` → `require_text`
**9h. `device_runtime_projection_writer_service.py`** — `_required_text` → `require_text`
**9i. `runtime_query_service.py`** — `_non_empty_text` → `coerce_optional_str`

- [ ] **Step 10: 逐文件替换 — 其他域（6 文件）**

**10a. `handling/services/operation_service.py`** — `_required_text` → `require_text`
**10b. `wms_integration/services/transport_contract.py`** — `_required_text` → `require_text`
**10c. `rack/services/operation_service.py`** — `_required_text` → `require_text`
**10d. `rack/services/task_lifecycle_service.py`** — `_required_text` → `require_text`
**10e. `device/services/device_command_service.py`** — `_non_empty_text` → `coerce_optional_str`
**10f. `workline/inbox_claim_bucket.py`** — `_non_empty_text` → `coerce_optional_str`
**10g. `workline/services/workline_service.py`** — `_string_list` → `string_list`

- [ ] **Step 11: 运行全量测试确认无回归**

```bash
uv run pytest tests/ -x --tb=short -q
```

- [ ] **Step 12: 运行架构门禁确认无新增违规**

```bash
uv run pytest tests/architecture/ -v --tb=short
```

- [ ] **Step 13: Commit**

```bash
git add src/utils/value_normalization.py tests/utils/test_value_normalization.py
git add src/app/runtime/capabilities/material_flow/sorter_inbound_runtime_service.py
git add src/app/runtime/capabilities/material_flow/sorter_inbound_preview_service.py
git add src/app/runtime/capabilities/material_flow/smt_ng_wms_reconciliation_runtime_service.py
git add src/app/runtime/capabilities/material_flow/smt_ng_wms_reconciliation_preview_service.py
git add src/app/runtime/capabilities/material_flow/single_layer_rack_orchestration_service.py
git add src/app/runtime/capabilities/material_flow/contracts/sorting_inbound_context.py
git add src/app/runtime/orchestration/orchestrator_bridge.py
git add src/app/runtime/orchestration/integration_lab.py
git add src/app/runtime/orchestration/scenario_replay.py
git add src/app/runtime/orchestration/benchmark_gate.py
git add src/app/runtime/orchestration/p0_e2e_gate.py
git add src/app/runtime/orchestration/runtime_intent_effects.py
git add src/app/runtime/orchestration/services/conveyor_queue_membership_writer_service.py
git add src/app/runtime/orchestration/services/device_runtime_projection_writer_service.py
git add src/app/runtime/orchestration/services/query/runtime_query_service.py
git add src/app/handling/services/operation_service.py
git add src/app/wms_integration/services/transport_contract.py
git add src/app/rack/services/operation_service.py
git add src/app/rack/services/task_lifecycle_service.py
git add src/app/device/services/device_command_service.py
git add src/app/workline/inbox_claim_bucket.py
git add src/app/workline/services/workline_service.py
git commit -m "refactor: 扩展 value_normalization 模块消除 DRY 违规

将 6 类重复定义的 helper 函数从 18 个文件统一收敛到
src/utils/value_normalization.py。复用已有 coerce_string_value /
coerce_optional_str，新增 require_text / string_list / mapping_copy /
json_safe / positive_quantity / positive_timeout_seconds。

影响范围: material-flow / runtime / handling / wms_integration /
rack / device / workline 共 7 个域"
```

---

### Task 2: 拆分架构文档解决 KISS 问题

**Files:**
- Create: `docs/architecture/workline-restructuring-overview.md`
- Create: `docs/architecture/workline-restructuring-data-design.md`
- Create: `docs/architecture/workline-restructuring-interface-design.md`
- Create: `docs/architecture/workline-restructuring-state-recovery.md`
- Create: `docs/architecture/workline-restructuring-security.md`
- Create: `docs/architecture/workline-restructuring-nonfunctional.md`
- Create: `docs/architecture/workline-restructuring-module-design.md`
- Create: `docs/architecture/workline-restructuring-implementation-plan.md`
- Modify: `docs/architecture/workline-and-plugin-restructuring.md` (改为索引文件)

**Interfaces:**
- Consumes: 无
- Produces: 1 个索引文件 + 8 个子文档，每个 ≤ 500 行

- [ ] **Step 1: 分析当前文档结构**

```bash
grep "^## " docs/architecture/workline-and-plugin-restructuring.md
```

Expected: 输出所有二级标题，确认拆分边界

- [ ] **Step 2: 创建索引文件**

将 `workline-and-plugin-restructuring.md` 精简为索引文件（保留 frontmatter + §1-2 概述 + 子文档链接表）:

```markdown
---
status: Draft v7 — 已拆分为多文件索引
created_at: 2026-06-23
updated_at: 2026-07-09
parent_goal: 对当前 WORKLINE + PLUGIN 体系进行全面重构/重做
document_type: 概要设计说明书索引
---

# WORKLINE + PLUGIN 体系全面重构顶层设计（索引）

> 概要设计说明书（GB/T 8567 风格）
> 版本：Draft v7（2026-07-08 WorkLine restructuring cleanup completed）

本文档是 WORKLINE + PLUGIN 体系重构的**顶层索引**。详细设计已拆分为独立子文档。

## 子文档索引

| 文档 | 内容 | 行数 |
|------|------|------|
| [概述与系统目标](workline-restructuring-overview.md) | §1-2: 引言、系统概述、P0目标、明确不做 | ~200 |
| [数据设计](workline-restructuring-data-design.md) | §4: 核心实体、ExecutionCorrelation、EvidenceEnvelope、队列模型 | ~400 |
| [接口设计](workline-restructuring-interface-design.md) | §5: WMS Port、plane接口、callback鉴权、idempotency | ~500 |
| [状态与恢复设计](workline-restructuring-state-recovery.md) | §6: 11态机、timeout、RECONCILING、3路UNION | ~300 |
| [安全设计](workline-restructuring-security.md) | §7: 威胁模型、不变量(17条) | ~200 |
| [非功能性设计](workline-restructuring-nonfunctional.md) | §8: 性能、容量、可靠性、可观测性 | ~200 |
| [模块设计](workline-restructuring-module-design.md) | §9: 8个域详细设计 | ~500 |
| [实施计划](workline-restructuring-implementation-plan.md) | §10-13: Phase路线图、执行规范、风险、附录 | ~300 |

## 快速导航

- **P0 系统目标**: [概述](workline-restructuring-overview.md#22-p0-系统目标)
- **域边界**: [模块设计](workline-restructuring-module-design.md)
- **关键不变量**: [安全设计](workline-restructuring-security.md#75-关键不变量)
- **实施路线图**: [实施计划](workline-restructuring-implementation-plan.md)
```

- [ ] **Step 3: 按 § 边界拆分为 8 个子文档**

使用 `sed` 按 `## ` 标题边界提取各章节到对应子文档:

```bash
cd docs/architecture

# §1-2 → overview (行 1-258)
sed -n '1,258p' workline-and-plugin-restructuring.md > workline-restructuring-overview.md

# §3 → 保留在 overview 中（体系结构设计是概述核心）
# §4 → data-design (行 540-680)
sed -n '540,680p' workline-and-plugin-restructuring.md > workline-restructuring-data-design.md

# ... 依此类推
```

- [ ] **Step 4: 验证所有子文档可独立阅读**

```bash
for f in docs/architecture/workline-restructuring-*.md; do
  echo "=== $(basename $f) === $(wc -l < $f) lines"
done
```

Expected: 每个子文档 ≤ 600 行

- [ ] **Step 5: 更新相关文档中的交叉引用**

```bash
grep -rn "workline-and-plugin-restructuring.md" docs/ --include="*.md" | grep -v ".git"
```

将引用更新为对应的子文档路径。

- [ ] **Step 6: Commit**

```bash
git add docs/architecture/workline-and-plugin-restructuring.md
git add docs/architecture/workline-restructuring-*.md
git commit -m "docs: 拆分架构设计文档为 1+8 索引结构

原 2631 行单文件拆分为 1 个索引 + 8 个子文档，每个 ≤600 行。
解决 KISS 原则中单文件过大难以维护的问题。"
```

---

### Task 3: 评估并精简 WMS Port 解决 SOLID 接口隔离

**Files:**
- Modify: `src/app/wms_integration/ports/__init__.py`
- 可能删除/降级: `document.py`, `event.py`, `reconciliation_query.py`（如确认 Phase 3/4 不需要）

**Interfaces:**
- Consumes: 无（Port 定义层，无消费者）
- Produces: 精简后的 Port 导出集合

- [ ] **Step 1: 确认各 Port 的 Phase 需求**

对照架构文档 §10 实施计划，确认每个 Port 在 Phase 3/4 是否被需要:

| Port | Phase 3 需要? | Phase 4 需要? | 判断 |
|------|-------------|-------------|------|
| `master_data` | ✅ 物料/货架/料箱校验 | ✅ | 保留 |
| `inventory_query` | ✅ 库存查询 | ✅ | 保留 |
| `inventory_transaction` | ✅ 预留/释放/确认 | ✅ | 保留 |
| `fulfillment` | ✅ 搬运/补给/交换 | ✅ | 保留 |
| `document` | ❓ GRN/工单查询 | ❓ | 待确认 |
| `event` | ❓ WMS事件接收 | ❓ | 待确认 |
| `reconciliation_query` | ❓ drift检测 | ❓ | 待确认 |

- [ ] **Step 2: 将 Phase 3/4 不需要的 Port 标记为 `@deferred`**

在不需要立即实现的 Port 文件顶部添加:

```python
"""WMS Document Port — @deferred to Phase 5.

本 Port 定义 WMS 单据查询能力合同。Phase 3/4 的粗分机/分拣机流程
通过 WmsMasterDataPort + WmsInventoryQueryPort 满足物料校验需求，
不需要独立的单据查询 Port。

激活条件: Phase 5 WMS 全量集成或业务需求明确需要 GRN/工单查询。
"""
```

- [ ] **Step 3: 更新 `__init__.py` 导出**

```python
# Phase 3/4 活跃 Port
from src.app.wms_integration.ports.fulfillment import WmsFulfillmentPort
from src.app.wms_integration.ports.inventory_query import WmsInventoryQueryPort
from src.app.wms_integration.ports.inventory_transaction import WmsInventoryTransactionPort
from src.app.wms_integration.ports.master_data import WmsMasterDataPort

# @deferred — Phase 5
# from src.app.wms_integration.ports.document import WmsDocumentPort
# from src.app.wms_integration.ports.event import WmsEventPort
# from src.app.wms_integration.ports.reconciliation_query import WmsReconciliationQueryPort

__all__ = [
    "WmsFulfillmentPort",
    "WmsInventoryQueryPort",
    "WmsInventoryTransactionPort",
    "WmsMasterDataPort",
]
```

- [ ] **Step 4: 运行测试确认无 import 错误**

```bash
uv run python -c "from src.app.wms_integration.ports import WmsMasterDataPort, WmsInventoryQueryPort, WmsInventoryTransactionPort, WmsFulfillmentPort; print('OK')"
uv run pytest tests/ -x --tb=short -q
```

- [ ] **Step 5: Commit**

```bash
git add src/app/wms_integration/ports/
git commit -m "refactor: 精简 WMS Port 导出为 Phase 3/4 需要的 4 个

将 document/event/reconciliation_query 标记为 @deferred (Phase 5)。
解决 SOLID 接口隔离原则中首版暴露过多 Port 的问题。

活跃 Port: master_data, inventory_query, inventory_transaction, fulfillment"
```

---

### Task 4: 标记/降级 YAGNI 实体

**Files:**
- Modify: `src/app/contracts/external_contract_profile.py` (添加模块级 docstring)
- Modify: `src/app/runtime/orchestration/integration_lab.py` (添加模块级 docstring)
- Modify: `src/app/runtime/orchestration/scenario_replay.py` (添加模块级 docstring)
- Modify: `src/app/active_objects/registry.py` (添加模块级 docstring)

**Interfaces:**
- Consumes: 无
- Produces: 带 `@yagni` 标记的模块文档

- [ ] **Step 1: 确认各实体的实际使用情况**

```bash
# ExternalContractProfile
grep -rn "ExternalContractProfile" src/app/ --include="*.py" | grep -v "__pycache__" | grep -v "test_"
# IntegrationLab
grep -rn "IntegrationLab" src/app/ --include="*.py" | grep -v "__pycache__" | grep -v "test_"
# ScenarioReplay
grep -rn "ScenarioReplay\|ScenarioRecorder" src/app/ --include="*.py" | grep -v "__pycache__" | grep -v "test_"
# ActiveObjectRegistry
grep -rn "ActiveObjectRegistry" src/app/ --include="*.py" | grep -v "__pycache__" | grep -v "test_"
```

- [ ] **Step 2: 为 YAGNI 实体添加模块级标记**

在每个文件的 module docstring 第一行添加标记:

`external_contract_profile.py`:
```python
"""ExternalContractProfile 生产路径 — @yagni: Phase 5 联调前为占位合同。

当前状态: 被 capability_dispatcher 和 runtime_capability_catalog 引用，
但所有 WMS/ECS provider 使用默认 profile。Phase 3/4 粗分机/分拣机流程
不需要动态合同切换能力。

激活条件: 多 provider 并行联调或 WMS/ECS 合同版本差异需要运行时切换。
"""
```

`integration_lab.py`:
```python
"""IntegrationLab fixture runner — @yagni: Phase 5 联调前为占位能力。

当前状态: 完整实现但无生产调用方。Phase 3/4 使用 tests/mock/ 中的
ecs_mock_server + wms_mock_server 进行合同测试。

激活条件: 硬件未到位时需要 simulator 验证完整业务链路。
"""
```

`scenario_replay.py`:
```python
"""ScenarioReplay 录制/回放 — @yagni: Phase 5 联调前为占位能力。

当前状态: ScenarioRecorder + ScenarioReplayRunner 完整实现但无生产调用方。

激活条件: 现场联调需要录制/回放乱序、重复、超时场景。
"""
```

`registry.py` (active_objects):
```python
"""ActiveObjectRegistry 读模型 — @yagni: 3路UNION冲突仲裁暂不需要。

当前状态: 被 workline_active_objects_service 引用，但 Phase 3/4
粗分机/分拣机流程中料箱/料盘并发度低，跨投影冲突概率极小。

激活条件: 生产环境出现同对象多投影归属冲突需要自动仲裁。
"""
```

- [ ] **Step 3: 运行测试确认标记不影响功能**

```bash
uv run pytest tests/ -x --tb=short -q
```

- [ ] **Step 4: Commit**

```bash
git add src/app/contracts/external_contract_profile.py
git add src/app/runtime/orchestration/integration_lab.py
git add src/app/runtime/orchestration/scenario_replay.py
git add src/app/active_objects/registry.py
git commit -m "docs: 为 4 个 YAGNI 实体添加 @yagni 标记和激活条件

标记 ExternalContractProfile / IntegrationLab / ScenarioReplay /
ActiveObjectRegistry 为 Phase 5 前占位能力，明确激活条件。
解决 YAGNI 原则中过早实现未使用能力的问题。"
```

---

## 自检清单

**1. 覆盖率检查:**
- [x] DRY: Task 1 覆盖全部 6 类重复 helper（`_required_text`, `_text`, `_non_empty_text`, `_string_list`, `_dict_copy`, `_json_safe`），涉及 18 个文件
- [x] KISS: Task 2 将 2631 行文档拆分为 1+8 结构
- [x] SOLID: Task 3 将 7 Port 精简为 4 活跃 + 3 deferred
- [x] YAGNI: Task 4 标记 4 个过早实现的实体

**2. Placeholder 扫描:** 无 TBD/TODO/占位符

**3. 类型一致性:**
- `require_text(payload, field_name) -> str` 统一替代 3 种不同签名的 `_required_text`
- `non_empty_text(value) -> str | None` 统一替代 `bool` 和 `str | None` 两种返回类型
- `text(value) -> str` 统一替代 `str` 和 `str | None` 两种返回类型

**4. 可独立执行:** 4 个 Task 互不依赖，可并行或按任意顺序执行

---

## NOT in scope

| 项目 | 原因 |
|------|------|
| 删除 YAGNI 实体/Port 代码 | 用户选择标记方案，保留代码 + docstring 说明激活条件 |
| 统一 `_text_or_none` / `_require_text` 等变体 | 这些函数语义不同（`_text_or_none` 返回 `str\|None`，`_require_text` 参数顺序不同），不纳入本次统一 |
| 重构 `value_normalization.py` 已有函数 | 已有函数有稳定调用方，本次只追加不修改 |
| 自动化 DRY 检查门禁 | 后续 Phase 可考虑，本次不引入新架构门禁 |

## What already exists

| 已有能力 | 位置 | 本次处理 |
|---------|------|---------|
| `coerce_string_value` / `coerce_optional_str` | `src/utils/value_normalization.py` | Task 1 复用，替代 `_text` / `_non_empty_text` |
| `as_dict` | `src/utils/value_normalization.py` | 保留，新增 `mapping_copy` 处理 Mapping 类型 |
| 架构文档 2631 行 | `docs/architecture/workline-and-plugin-restructuring.md` | Task 2 拆分为 1+8 索引结构 |
| 7 个 WMS Port (557 行) | `src/app/wms_integration/ports/` | Task 3 标记 3 个为 @deferred |
| 4 个 YAGNI 实体 (832 行) | `src/app/contracts/`, `runtime/orchestration/`, `active_objects/` | Task 4 添加 @yagni 标记 |

## Failure modes

| 风险 | 影响 | 缓解 |
|------|------|------|
| `coerce_optional_str` 行为与旧 `_non_empty_text(bool)` 不等价 | benchmark_gate / p0_e2e_gate 逻辑反转 | Step 9d/9e 显式适配 `is None` / `is not None` |
| `mapping_copy` 接受 Mapping 而旧 `_dict_copy` 仅接受 dict | 行为变宽但不变窄，无回归风险 | 单元测试覆盖 dict + list 输入 |
| 架构文档拆分后交叉引用断裂 | 其他文档中的链接 404 | Step 5 搜索并更新所有交叉引用 |
| WMS Port import 路径被注释后编译失败 | 如有直接 import 会报错 | Step 4 import 验证 + 全量测试 |

## Worktree parallelization strategy

4 个 Task 互不依赖，可并行执行：

| Lane | Tasks | Modules touched | Depends on |
|------|-------|----------------|------------|
| A | Task 1 (DRY) | `src/utils/`, `src/app/runtime/`, `src/app/handling/`, `src/app/wms_integration/`, `src/app/rack/`, `src/app/device/`, `src/app/workline/` | — |
| B | Task 2 (KISS) | `docs/architecture/` | — |
| C | Task 3 (SOLID) | `src/app/wms_integration/ports/` | — |
| D | Task 4 (YAGNI) | `src/app/contracts/`, `src/app/runtime/orchestration/`, `src/app/active_objects/` | — |

**执行顺序:** 4 个 Lane 可同时启动。Lane A 和 Lane C 都触及 `wms_integration/` 但不同子目录（ports vs services），冲突概率低。建议先完成 B/C/D（快速），再集中做 A（文件最多）。

## Implementation Tasks

- [ ] **T1 (P1, human: ~1h / CC: ~20min)** — value_normalization — 扩展模块 + 18 文件替换
  - Surfaced by: Architecture Review — DRY 违规
  - Files: `src/utils/value_normalization.py` + 18 个调用文件 + `tests/utils/test_value_normalization.py`
  - Verify: `uv run pytest tests/ -x --tb=short -q` + `uv run pytest tests/architecture/ -v`

- [ ] **T2 (P2, human: ~20min / CC: ~5min)** — docs — 拆分架构文档
  - Surfaced by: Architecture Review — KISS 问题
  - Files: `docs/architecture/workline-and-plugin-restructuring.md` + 8 个新子文档
  - Verify: 每个子文档 ≤ 600 行，交叉引用无死链

- [ ] **T3 (P2, human: ~15min / CC: ~5min)** — wms_integration — 精简 Port 导出
  - Surfaced by: Architecture Review — SOLID 接口隔离
  - Files: `src/app/wms_integration/ports/__init__.py` + 3 个 @deferred port 文件
  - Verify: `uv run python -c "from src.app.wms_integration.ports import ..."` + 全量测试

- [ ] **T4 (P2, human: ~15min / CC: ~5min)** — docs — 标记 YAGNI 实体
  - Surfaced by: Architecture Review — YAGNI 违规
  - Files: 4 个实体文件添加 @yagni docstring
  - Verify: `uv run pytest tests/ -x --tb=short -q`

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 1 issue (resolved: 显式适配代码 + 合并到 value_normalization.py) |
| Outside Voice | Codex | Independent 2nd opinion | 1 | DONE | 3 tensions surfaced, all resolved per user choice |

**CODEX:** 指出 helper 签名不一致风险、`src/common/` vs `src/utils/` 位置问题、标记方案 vs 删除方案。用户选择合并到 `value_normalization.py` + 保留标记方案。

**CROSS-MODEL:** 审查和 Codex 在 helper 统一风险上一致（需要显式适配代码）。在 YAGNI/Port 处理上分歧（审查建议标记，Codex 建议删除），用户选择标记方案。

**VERDICT:** ENG + OUTSIDE VOICE CLEARED — ready to implement.

NO UNRESOLVED DECISIONS
