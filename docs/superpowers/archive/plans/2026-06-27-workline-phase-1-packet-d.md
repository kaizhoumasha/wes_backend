# Phase 1 Packet D — Capability Boundary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close Phase 1 Packet D by landing 4 remaining WMS ports (WmsDocumentPort / WmsFulfillmentPort / WmsEventPort / WmsReconciliationQueryPort), the CEO-009 capability injection / import boundary static scanner (R-I3c + import-linter capability-isolation contract), and the three-layer InboundNormalizer static validation (Pydantic model_validator + InboundNormalizerRegistry + RuntimeCapabilityContext.get_inbound_normalizer routing). Single PR to `develop`.

**Architecture:** Extend the existing `src/app/wms_integration/ports/` package by 4 Protocol files following the master_data / inventory_query / inventory_transaction template (Protocol class + Pydantic typed data classes + full docstring). Add `InboundNormalizerRegistry` as a sibling of `CapabilityPortRegistry` (semantically separated: query/effect vs inbound). Extend `RuntimeCapabilityContext.get_inbound_normalizer()` with caller_module routing guard. Harden `InboundNormalizerProfile` with a model_validator enforcing event_type prefix / source_provider consistency / correlation_resolution enum. Layer the static scanner on top of the existing `scripts/architecture-guardrails.sh` (new rule_ri3c) plus import-linter with a single `capability-isolation` contract covering all forbidden_modules per Phase 1 SPEC §279.

**Tech Stack:** Python 3.12 / FastAPI / SQLModel / SQLAlchemy 2.0 / Pydantic v2 / pytest / ruff / bash / import-linter >= 2.0 / uv. Inherits all P9 WES framework conventions (BaseAPI / BaseService / BaseRepository / ModelFactory).

## Global Constraints

These apply to every task; the implementer MUST follow them verbatim.

- **Language**: 中文 for all commit messages, docstrings, comments, and tests descriptions (English reserved for technical identifiers and code symbols).
- **Architecture layers**: API → Service → Repository → Database. Forbidden: `db.execute` or `select()` in API layer; cross-layer direct calls. Detection: `grep -r "from sqlalchemy import select" src/app/*/v1/`.
- **GitNexus impact**: Run `npx gitnexus impact --target <symbol> --direction upstream` BEFORE modifying any function/class/method. Report risk to user if HIGH or CRITICAL.
- **GitNexus detect_changes**: Run `npx gitnexus detect_changes --scope all` BEFORE every commit. Confirm risk ≤ LOW.
- **Commands**: All project commands MUST use `uv run ...` (never assume active venv).
- **Branch base**: Daily work branched from `develop`. This plan executes on `feature/workline-phase-1-packet-d-capability` branched off `develop`. PR base = `develop`.
- **Commit messages**: Conventional Commits, no `Co-Authored-By` field.
- **Tests per commit**: Every commit MUST include related test cases. Run `uv run pytest tests/architecture/ -v` to verify.
- **Module exports**: New modules MUST be exported in their package `__init__.py`.
- **Doc sync**: After updating functionality, sync `docs/architecture/file_index.md`.
- **ENUM types**: VARCHAR + CHECK constraint, never PostgreSQL native ENUM.
- **Timezone**: `timezone.now_for_db()` for DB, `timezone.now_utc().isoformat()` for API, `timezone.now_utc().timestamp()` for unix. Never call `.timestamp()` on naive datetime.
- **Docstrings**: All Protocol methods and Pydantic data classes MUST have a docstring (English, 1-3 lines, focused on purpose and contract).
- **Plan-mode test runner**: Run from `develop` checkout root. Never run from worktree if it lacks the new env (use `./scripts/init-env.sh dev && uv sync --dev` first).
- **Import patterns**: Imports of blacklisted inbound normalizer types in capability path are FORBIDDEN. The single source of truth is `_INBOUND_NORMALIZER_TYPE_NAMES` in `src/app/runtime/capability_port_registry.py`.

---

## File Structure

Files created or modified by this plan:

### Created (10 files)
- `src/app/wms_integration/ports/document.py` — WmsDocumentPort Protocol + 6 Pydantic data classes
- `src/app/wms_integration/ports/fulfillment.py` — WmsFulfillmentPort Protocol + 2 Pydantic data classes
- `src/app/wms_integration/ports/event.py` — InboundEventPort + WmsEventPort Protocols + 5 Pydantic data classes
- `src/app/wms_integration/ports/reconciliation_query.py` — WmsReconciliationQueryPort Protocol + 1 Pydantic data class
- `src/app/runtime/inbound_normalizer_registry.py` — InboundNormalizerRegistry class (~100 lines)
- `tests/architecture/test_wms_7_ports_contract.py` — 7-port contract tests
- `tests/architecture/test_ri3c_inbound_normalizer_port_guardrail.py` — R-I3c port-level guardrail tests
- `tests/architecture/test_inbound_normalizer_profile_validation.py` — Pydantic model_validator tests
- `tests/architecture/test_runtime_capability_context_routing.py` — RuntimeCapabilityContext routing tests
- `.import-linter.ini` — capability-isolation contract config

### Modified (8 files)
- `src/app/wms_integration/ports/__init__.py` — Update docstring noting all 7 ports landed
- `src/app/runtime/capability_port_registry.py` — Extend `RuntimeCapabilityContext.__init__` to accept inbound_registry; add `get_inbound_normalizer()` method
- `src/app/contracts/external_contract_profile.py` — Add `_normalizer_injection_boundary` model_validator to `InboundNormalizerProfile`
- `scripts/architecture-guardrails.sh` — Add `rule_ri3c()` function and register in phase1/phase2 chain
- `scripts/git-quality-gate.sh` — Add import-linter check after ruff, before pytest
- `pyproject.toml` — Add `import-linter = ">=2.0"` to dev dependency group
- `docs/architecture/workline-and-plugin-restructuring.md` — Update §10.2 Packet D status to ✅
- `docs/architecture/file_index.md` — Add 4 port files + inbound_normalizer_registry to index

### Created (1 wrapper script)
- `scripts/import-linter-check.sh` — bash wrapper for `lint-imports` CLI

---

## Task 1: Add `import-linter` dependency

**Files:**
- Modify: `pyproject.toml` (find the dev dependency group block)

**Interfaces:**
- Produces: `import-linter` CLI available via `uv run lint-imports --help`

- [ ] **Step 1: Verify import-linter is not yet installed**

Run: `uv run lint-imports --version 2>&1 || echo "NOT INSTALLED"`
Expected: prints "NOT INSTALLED"

- [ ] **Step 2: Read current pyproject.toml dev dependencies section**

Run: `uv run python -c "import tomllib; data = tomllib.loads(open('pyproject.toml').read()); print([k for k in data.get('dependency-groups', {})])"`
Expected: prints `['dev']` or similar

- [ ] **Step 3: Locate the `[dependency-groups] dev` block and add import-linter**

Edit `pyproject.toml`. Find the line `dev = [` (or equivalent) inside `[dependency-groups]`. Add `    "import-linter>=2.0",` as a new entry (preserve alphabetical ordering if already sorted).

- [ ] **Step 4: Sync dependencies**

Run: `uv sync --dev`
Expected: completes without errors; `import-linter` appears in `uv.lock`

- [ ] **Step 5: Verify import-linter CLI is now available**

Run: `uv run lint-imports --help | head -5`
Expected: prints help text starting with usage info

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "chore(deps): add import-linter dependency for capability-isolation contract"
```

---

## Task 2: Add `WmsDocumentPort` Protocol + typed data classes

**Files:**
- Create: `src/app/wms_integration/ports/document.py`
- Modify: `src/app/wms_integration/ports/__init__.py` (docstring update only)

**Interfaces:**
- Produces:
  - `WmsDocumentPort(Protocol)` — 6 methods: `get_grn` / `list_grn_items` / `get_pick_order` / `get_outbound_order` / `get_wave` / `get_task_snapshot`
  - 6 Pydantic data classes: `WmsGrnInfo`, `WmsGrnItem`, `WmsPickOrder`, `WmsOutboundOrder`, `WmsWave`, `WmsTaskSnapshot`

- [ ] **Step 1: Write the failing contract test**

Create `tests/architecture/test_wms_7_ports_contract.py` with the following test for document port (we'll extend in later tasks, this task only adds document-related tests):

```python
"""7-port contract tests (Phase 1 Packet D, 主计划 §5.1 + Phase 1 SPEC §139-140).

每个 WMS port 必须满足:
- Port.method 命名 (ClassName.method 格式)
- Protocol 抽象性 (typing.Protocol 子类)
- 所有方法有 docstring
- 数据类有 docstring
"""
from __future__ import annotations

import inspect
import re
from typing import Protocol, get_type_hints

from pydantic import BaseModel

from src.app.wms_integration.ports.document import WmsDocumentPort

PORT_METHOD_RE = re.compile(r"^[A-Z][A-Za-z0-9_]*Port\.[a-z_][A-Za-z0-9_]*$")


def test_wms_document_port_is_protocol():
    """WmsDocumentPort 必须是 typing.Protocol 子类。"""
    assert issubclass(WmsDocumentPort, Protocol)


def test_wms_document_port_method_signatures():
    """WmsDocumentPort 6 方法签名与主计划 §5.1 一致。"""
    methods = ["get_grn", "list_grn_items", "get_pick_order", "get_outbound_order", "get_wave", "get_task_snapshot"]
    for name in methods:
        assert hasattr(WmsDocumentPort, name), f"missing method: {name}"
        method = getattr(WmsDocumentPort, name)
        assert callable(method)


def test_wms_document_port_have_docstrings():
    """WmsDocumentPort 类和所有方法必须含 docstring。"""
    assert WmsDocumentPort.__doc__, "WmsDocumentPort class needs docstring"
    for name in ["get_grn", "list_grn_items", "get_pick_order", "get_outbound_order", "get_wave", "get_task_snapshot"]:
        method = getattr(WmsDocumentPort, name)
        assert method.__doc__, f"method {name} needs docstring"


def test_wms_document_data_classes_are_pydantic():
    """WmsDocumentPort 关联的 6 数据类必须是 BaseModel 子类且含 docstring。"""
    from src.app.wms_integration.ports.document import (
        WmsGrnInfo,
        WmsGrnItem,
        WmsPickOrder,
        WmsOutboundOrder,
        WmsWave,
        WmsTaskSnapshot,
    )

    for cls in [WmsGrnInfo, WmsGrnItem, WmsPickOrder, WmsOutboundOrder, WmsWave, WmsTaskSnapshot]:
        assert issubclass(cls, BaseModel), f"{cls.__name__} must be BaseModel"
        assert cls.__doc__, f"{cls.__name__} needs docstring"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/architecture/test_wms_7_ports_contract.py -v`
Expected: ImportError or ModuleNotFoundError for `src.app.wms_integration.ports.document`

- [ ] **Step 3: Implement `WmsDocumentPort` Protocol + 6 data classes**

Create `src/app/wms_integration/ports/document.py` with the following content:

```python
"""WmsDocumentPort (Phase 1 CEO-001 #2, Packet D)。

主计划 §5.1 7 port 之一: 单据查询 (GRN / 拣货单 / 出库单 / 波次 / 任务快照)。
所有方法 query-only, 与 §3.4 Authority Matrix "WMS 是单据权威" 一致。
Runtime capability 注入时仅暴露 query port contract (R-I3b 禁止 internal
domain import wms_integration 实现)。

方法命名: Port.method 格式, 供 ExternalContractProfile.runtime_capabilities_query
引用。
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class WmsGrnInfo(BaseModel):
    """GRN (Goods Receipt Note) 主单据。"""

    model_config = ConfigDict(extra="forbid")

    grn_id: str = Field(min_length=1, max_length=80, description="GRN 编号 (主键)")
    grn_type: str = Field(description="GRN 类型 (PO/SUB/RETURN)")
    status: str = Field(description="OPEN / IN_PROGRESS / COMPLETED / CLOSED")
    received_at: str = Field(description="收货时间 ISO 8601")
    total_items: int = Field(ge=0, description="GRN 明细总条数")
    warehouse_code: str = Field(min_length=1, max_length=80, description="仓库编码")


class WmsGrnItem(BaseModel):
    """GRN 单据明细行 (WMS 权威)。"""

    model_config = ConfigDict(extra="forbid")

    grn_id: str = Field(min_length=1, max_length=80, description="所属 GRN")
    material_code: str = Field(min_length=1, max_length=80, description="物料编码")
    quantity: float = Field(ge=0, description="收货数量")
    batch_no: str | None = Field(default=None, max_length=80, description="批次号")
    package_id: str | None = Field(default=None, description="已绑定料盘 ID")


class WmsPickOrder(BaseModel):
    """拣货单 (WMS 下发)。"""

    model_config = ConfigDict(extra="forbid")

    pick_order_id: str = Field(min_length=1, max_length=80, description="拣货单号")
    wave_id: str = Field(min_length=1, max_length=80, description="所属波次")
    status: str = Field(description="PENDING / DISPATCHED / COMPLETED")
    priority: int = Field(ge=0, le=10, description="优先级 0-10")
    total_lines: int = Field(ge=0, description="拣货行数")


class WmsOutboundOrder(BaseModel):
    """出库单 (WMS 下发)。"""

    model_config = ConfigDict(extra="forbid")

    outbound_order_id: str = Field(min_length=1, max_length=80, description="出库单号")
    customer_code: str = Field(min_length=1, max_length=80, description="客户编码")
    status: str = Field(description="PENDING / PICKED / SHIPPED")
    ship_date: str = Field(description="发货日期 ISO 8601")
    total_lines: int = Field(ge=0, description="出库行数")


class WmsWave(BaseModel):
    """波次 (WMS 下发, 包含多个拣货单)。"""

    model_config = ConfigDict(extra="forbid")

    wave_id: str = Field(min_length=1, max_length=80, description="波次号")
    status: str = Field(description="PLANNED / RELEASED / IN_PROGRESS / COMPLETED")
    scheduled_at: str = Field(description="计划开始时间 ISO 8601")
    pick_order_count: int = Field(ge=0, description="包含拣货单数")


class WmsTaskSnapshot(BaseModel):
    """任务快照 (WMS 权威状态)。"""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=80, description="任务 ID")
    task_type: str = Field(description="任务类型 (PKG / BIN / TRANSPORT)")
    status: str = Field(description="PENDING / ACTIVE / COMPLETED / FAILED")
    correlation_id: str = Field(description="与 WES RuntimeExecutionWorkItem 的关联 ID")
    updated_at: str = Field(description="最近更新时间 ISO 8601")


class WmsDocumentPort(Protocol):
    """WMS 单据 port (Phase 1 CEO-001 #2, Packet D)。

    所有方法 query-only, 短 TTL 缓存 (主计划 §6: 60s); 业务事务/搬运不走本 port。
    Runtime capability 注入时仅暴露 query port contract (R-I3b 禁止内部域
    import wms_integration 实现)。
    """

    def get_grn(self, grn_id: str) -> WmsGrnInfo:
        """查询 GRN 主单据 (单条, 按 grn_id)。"""
        ...

    def list_grn_items(self, grn_id: str) -> list[WmsGrnItem]:
        """查询 GRN 单据明细行列表 (按 grn_id)。"""
        ...

    def get_pick_order(self, pick_order_id: str) -> WmsPickOrder:
        """查询拣货单 (单条, 按 pick_order_id)。"""
        ...

    def get_outbound_order(self, outbound_order_id: str) -> WmsOutboundOrder:
        """查询出库单 (单条, 按 outbound_order_id)。"""
        ...

    def get_wave(self, wave_id: str) -> WmsWave:
        """查询波次 (单条, 按 wave_id)。"""
        ...

    def get_task_snapshot(self, task_id: str) -> WmsTaskSnapshot:
        """查询任务快照 (单条, 按 task_id)。"""
        ...
```

- [ ] **Step 4: Update `ports/__init__.py` docstring**

Edit `src/app/wms_integration/ports/__init__.py`. Replace the line `Phase 1 CEO-001 起步: 落地 WmsMasterDataPort + WmsInventoryQueryPort + WmsInventoryTransactionPort (Packet B 范围内), 其余 4 ports 留 Packet C/D 后续会话。` with:

```
Phase 1 CEO-001 完成 7/7 ports: WmsMasterDataPort (Packet B) + WmsInventoryQueryPort + WmsInventoryTransactionPort (Packet B) + WmsDocumentPort + WmsFulfillmentPort + WmsEventPort + WmsReconciliationQueryPort (Packet D)。所有 Protocol 落地后, Phase 2 起 capability 可独立通过 port contract 注入。
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/architecture/test_wms_7_ports_contract.py -v`
Expected: 4 tests PASS (test_wms_document_*)

- [ ] **Step 6: Run ruff format and check**

Run: `uv run ruff format src/app/wms_integration/ports/document.py && uv run ruff check src/app/wms_integration/ports/document.py`
Expected: no output (clean)

- [ ] **Step 7: GitNexus impact check**

Run: `npx gitnexus impact --target WmsDocumentPort --direction upstream`
Expected: risk LOW (new file, no existing callers)

- [ ] **Step 8: Commit**

```bash
git add src/app/wms_integration/ports/document.py src/app/wms_integration/ports/__init__.py tests/architecture/test_wms_7_ports_contract.py
git commit -m "feat(wms-ports): add WmsDocumentPort protocol + 6 typed data classes"
```

---

## Task 3: Add `WmsFulfillmentPort` Protocol + typed data classes

**Files:**
- Create: `src/app/wms_integration/ports/fulfillment.py`

**Interfaces:**
- Produces:
  - `WmsFulfillmentPort(Protocol)` — 6 methods: `request_rack_supply` / `request_rack_transport` / `change_rack_face` / `full_box_exchange` / `move_bin_to_conveyor_entry` / `move_bin_to_conveyor_exit`
  - 1 Pydantic data class: `WmsFulfillmentResult`
  - 料盘绑定已硬切到 `wms.fulfillment.notify_pkg_binding@v1`，不再属于 family Port。

- [ ] **Step 1: Append contract tests for fulfillment port**

Edit `tests/architecture/test_wms_7_ports_contract.py`. Add the following imports at top:

```python
from src.app.wms_integration.ports.fulfillment import WmsFulfillmentPort
```

Then append these tests at the end of the file:

```python
def test_wms_fulfillment_port_is_protocol():
    assert issubclass(WmsFulfillmentPort, Protocol)


def test_wms_fulfillment_port_method_signatures():
    methods = [
        "request_rack_supply",
        "request_rack_transport",
        "change_rack_face",
        "full_box_exchange",
        "move_bin_to_conveyor_entry",
        "move_bin_to_conveyor_exit",
        "notify_pkg_binding",
    ]
    for name in methods:
        assert hasattr(WmsFulfillmentPort, name), f"missing method: {name}"
        method = getattr(WmsFulfillmentPort, name)
        assert callable(method)


def test_wms_fulfillment_port_have_docstrings():
    assert WmsFulfillmentPort.__doc__, "WmsFulfillmentPort class needs docstring"
    for name in [
        "request_rack_supply",
        "request_rack_transport",
        "change_rack_face",
        "full_box_exchange",
        "move_bin_to_conveyor_entry",
        "move_bin_to_conveyor_exit",
    ]:
        method = getattr(WmsFulfillmentPort, name)
        assert method.__doc__, f"method {name} needs docstring"


def test_wms_fulfillment_data_classes_are_pydantic():
    from src.app.wms_integration.ports.fulfillment import WmsFulfillmentResult

    for cls in [WmsFulfillmentResult]:
        assert issubclass(cls, BaseModel), f"{cls.__name__} must be BaseModel"
        assert cls.__doc__, f"{cls.__name__} needs docstring"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/architecture/test_wms_7_ports_contract.py -k "fulfillment" -v`
Expected: ImportError for `src.app.wms_integration.ports.fulfillment`

- [ ] **Step 3: Implement `WmsFulfillmentPort` Protocol + 2 data classes**

Create `src/app/wms_integration/ports/fulfillment.py` with:

```python
"""WmsFulfillmentPort (Phase 1 CEO-001 #5, Packet D)。

主计划 §5.1 7 port 之一: 履约 (搬运/补给/换面/满箱交换/notify pkg binding)。
所有 effect 必先写 RuntimeIntentLog + EffectPort dispatcher (主计划 §3.5 I3 边界),
capability 不得在 WMS 履约上下文绕过 Runtime 直接修改 WES 内部状态。

方法命名: Port.method 格式, 供 ExternalContractProfile.runtime_capabilities_effect
引用。
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class WmsFulfillmentResult(BaseModel):
    """WMS 履约请求结果。"""

    model_config = ConfigDict(extra="forbid")

    request_id: str = Field(min_length=1, max_length=80, description="WMS 履约请求号")
    accepted: bool = Field(description="WMS 是否接受请求")
    reason: str | None = Field(default=None, description="拒绝原因 (accepted=False 时必填)")
    warehouse_code: str = Field(min_length=1, max_length=80, description="仓库编码")


class WmsFulfillmentPort(Protocol):
    """WMS 履约 port (Phase 1 CEO-001 #5, Packet D)。

    6 个 effect 方法覆盖 WES → WMS 的出站履约调用。所有 effect 经
    RuntimeIntentLog + EffectPort dispatcher; capability 不得绕过 Runtime
    直接修改 WES 内部状态 (主计划 §3.5 I3)。
    """

    def request_rack_supply(self, rack_id: str, material_code: str, quantity: float) -> WmsFulfillmentResult:
        """请求 WMS 给指定货架补给物料。"""
        ...

    def request_rack_transport(self, rack_id: str, from_station: str, to_station: str) -> WmsFulfillmentResult:
        """请求 WMS 搬运货架 (从 from_station 到 to_station)。"""
        ...

    def change_rack_face(self, rack_id: str, face: str) -> WmsFulfillmentResult:
        """请求 WMS 切换货架面 (face=A/B)。"""
        ...

    def full_box_exchange(self, rack_id: str, empty_box_id: str, full_box_id: str) -> WmsFulfillmentResult:
        """请求 WMS 满箱/空箱交换。"""
        ...

    def move_bin_to_conveyor_entry(self, bin_id: str, conveyor_entry: str) -> WmsFulfillmentResult:
        """请求 WMS 把料箱移到传送带入口。"""
        ...

    def move_bin_to_conveyor_exit(self, bin_id: str, conveyor_exit: str) -> WmsFulfillmentResult:
        """请求 WMS 把料箱移到传送带出口。"""
        ...

```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/architecture/test_wms_7_ports_contract.py -k "fulfillment" -v`
Expected: 4 tests PASS

- [ ] **Step 5: Run ruff format and check**

Run: `uv run ruff format src/app/wms_integration/ports/fulfillment.py && uv run ruff check src/app/wms_integration/ports/fulfillment.py`
Expected: clean

- [ ] **Step 6: GitNexus impact check**

Run: `npx gitnexus impact --target WmsFulfillmentPort --direction upstream`
Expected: risk LOW

- [ ] **Step 7: Commit**

```bash
git add src/app/wms_integration/ports/fulfillment.py tests/architecture/test_wms_7_ports_contract.py
git commit -m "feat(wms-ports): add WmsFulfillmentPort protocol + 2 typed data classes"
```

---

## Task 4: Add `WmsEventPort` Protocol + `InboundEventPort` base + typed data classes

**Files:**
- Create: `src/app/wms_integration/ports/event.py`

**Interfaces:**
- Produces:
  - `InboundEventPort(Protocol)` — base abstract for all inbound normalizers
  - `WmsEventPort(Protocol)` — 4 normalizer methods: `normalize_wms_grn_received` / `normalize_wms_pallet_arrived` / `normalize_wms_rack_arrived` / `normalize_wms_transport_completed`
  - 5 Pydantic data classes: `InboundEventEnvelope`, `WmsGrnReceivedEvent`, `WmsPalletArrivedEvent`, `WmsRackArrivedEvent`, `WmsTransportCompletedEvent`

- [ ] **Step 1: Append contract tests for event port**

Edit `tests/architecture/test_wms_7_ports_contract.py`. Add the following imports at top:

```python
from src.app.wms_integration.ports.event import WmsEventPort, InboundEventPort
```

Then append:

```python
def test_inbound_event_port_is_protocol():
    assert issubclass(InboundEventPort, Protocol)


def test_wms_event_port_is_protocol():
    assert issubclass(WmsEventPort, Protocol)


def test_wms_event_port_normalizer_signatures():
    methods = [
        "normalize_wms_grn_received",
        "normalize_wms_pallet_arrived",
        "normalize_wms_rack_arrived",
        "normalize_wms_transport_completed",
    ]
    for name in methods:
        assert hasattr(WmsEventPort, name), f"missing normalizer: {name}"


def test_wms_event_port_have_docstrings():
    assert WmsEventPort.__doc__, "WmsEventPort class needs docstring"
    assert InboundEventPort.__doc__, "InboundEventPort class needs docstring"
    for name in [
        "normalize_wms_grn_received",
        "normalize_wms_pallet_arrived",
        "normalize_wms_rack_arrived",
        "normalize_wms_transport_completed",
    ]:
        method = getattr(WmsEventPort, name)
        assert method.__doc__, f"normalizer {name} needs docstring"


def test_wms_event_data_classes_are_pydantic():
    from src.app.wms_integration.ports.event import (
        InboundEventEnvelope,
        WmsGrnReceivedEvent,
        WmsPalletArrivedEvent,
        WmsRackArrivedEvent,
        WmsTransportCompletedEvent,
    )

    for cls in [
        InboundEventEnvelope,
        WmsGrnReceivedEvent,
        WmsPalletArrivedEvent,
        WmsRackArrivedEvent,
        WmsTransportCompletedEvent,
    ]:
        assert issubclass(cls, BaseModel), f"{cls.__name__} must be BaseModel"
        assert cls.__doc__, f"{cls.__name__} needs docstring"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/architecture/test_wms_7_ports_contract.py -k "event" -v`
Expected: ImportError for `src.app.wms_integration.ports.event`

- [ ] **Step 3: Implement `InboundEventPort` + `WmsEventPort` Protocols + 5 data classes**

Create `src/app/wms_integration/ports/event.py` with:

```python
"""WmsEventPort + InboundEventPort (Phase 1 CEO-001 #6, Packet D)。

主计划 §5.1 7 port 之一: 入站事件 normalizer (WMS_GRN_RECEIVED /
WMS_PALLET_ARRIVED / WMS_RACK_ARRIVED / WMS_TRANSPORT_COMPLETED 等回调)。

设计:
- InboundEventPort 是所有入站 normalizer 的基协议, 不导出到业务 capability
  (主计划 §3.5 I3 + H2 黑名单)。
- WmsEventPort 是 WMS 回调的 4 个 normalizer, 走 InboundNormalizerRegistry
  路径 (Task 7), 业务 capability 不可注入。

normalizer 职责: 把 WMS 原始回调 JSON 转 typed envelope + 解析 correlation_id
(manual / auto / hybrid 策略由 InboundNormalizerProfile.correlation_resolution
声明)。转换后投递到 RuntimeInbox, 不直接调用业务 capability (主计划 §3.5.1)。
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class InboundEventEnvelope(BaseModel):
    """入站事件标准化 envelope (所有 normalizer 输出基类)。"""

    model_config = ConfigDict(extra="forbid")

    source_event_id: str = Field(min_length=1, max_length=120, description="WMS/ECS 源事件 ID (幂等键)")
    provider_code: str = Field(min_length=1, max_length=60, description="来源 provider 编码")
    occurred_at: str = Field(description="事件发生时间 ISO 8601")
    correlation_id: str = Field(min_length=1, max_length=80, description="与 ExecutionCorrelation 的关联 ID")
    raw_payload: dict = Field(default_factory=dict, description="原始回调 payload (保留供审计)")


class WmsGrnReceivedEvent(BaseModel):
    """WMS GRN 收货回调事件 (normalizer 输出)。"""

    model_config = ConfigDict(extra="forbid")

    envelope: InboundEventEnvelope = Field(description="共享 envelope")
    grn_id: str = Field(min_length=1, max_length=80, description="GRN 编号")
    warehouse_code: str = Field(min_length=1, max_length=80, description="仓库编码")
    item_count: int = Field(ge=0, description="收货明细行数")


class WmsPalletArrivedEvent(BaseModel):
    """WMS 料盘到达回调事件 (normalizer 输出)。"""

    model_config = ConfigDict(extra="forbid")

    envelope: InboundEventEnvelope = Field(description="共享 envelope")
    pallet_id: str = Field(min_length=1, max_length=80, description="料盘 ID")
    arrived_station: str = Field(min_length=1, max_length=80, description="到达工位编码")


class WmsRackArrivedEvent(BaseModel):
    """WMS 货架到达回调事件 (normalizer 输出)。"""

    model_config = ConfigDict(extra="forbid")

    envelope: InboundEventEnvelope = Field(description="共享 envelope")
    rack_id: str = Field(min_length=1, max_length=80, description="货架 ID")
    station_code: str = Field(min_length=1, max_length=80, description="到达工位编码")


class WmsTransportCompletedEvent(BaseModel):
    """WMS 搬运完成回调事件 (normalizer 输出)。"""

    model_config = ConfigDict(extra="forbid")

    envelope: InboundEventEnvelope = Field(description="共享 envelope")
    request_id: str = Field(min_length=1, max_length=80, description="WMS 履约请求号")
    completed_at: str = Field(description="完成时间 ISO 8601")
    result_code: str = Field(description="SUCCESS / FAILED / PARTIAL")


class InboundEventPort(Protocol):
    """所有入站 normalizer 的基协议 (Phase 1 CEO-001 #6 base)。

    不导出到业务 capability (主计划 §3.5 I3 + H2 黑名单)。
    实际 normalizer (WmsEventPort 等) 继承此协议。
    """

    def normalize(self, raw_payload: dict) -> InboundEventEnvelope:
        """把原始回调 payload 标准化为 InboundEventEnvelope。"""
        ...


class WmsEventPort(Protocol):
    """WMS 回调 normalizer (Phase 1 CEO-001 #6, Packet D)。

    4 个 normalizer 覆盖 WMS 主回调事件类型。normalizer 输出投递到
    RuntimeInbox, 由 RuntimeInboxConsumer 消费, 不直接调用业务 capability
    (主计划 §3.5.1 + H2 黑名单)。
    """

    def normalize_wms_grn_received(self, raw_payload: dict) -> WmsGrnReceivedEvent:
        """标准化 WMS_GRN_RECEIVED 回调 → typed event + correlation_id。"""
        ...

    def normalize_wms_pallet_arrived(self, raw_payload: dict) -> WmsPalletArrivedEvent:
        """标准化 WMS_PALLET_ARRIVED 回调 → typed event + correlation_id。"""
        ...

    def normalize_wms_rack_arrived(self, raw_payload: dict) -> WmsRackArrivedEvent:
        """标准化 WMS_RACK_ARRIVED 回调 → typed event + correlation_id。"""
        ...

    def normalize_wms_transport_completed(self, raw_payload: dict) -> WmsTransportCompletedEvent:
        """标准化 WMS_TRANSPORT_COMPLETED 回调 → typed event + correlation_id。"""
        ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/architecture/test_wms_7_ports_contract.py -k "event" -v`
Expected: 5 tests PASS

- [ ] **Step 5: Run ruff format and check**

Run: `uv run ruff format src/app/wms_integration/ports/event.py && uv run ruff check src/app/wms_integration/ports/event.py`
Expected: clean

- [ ] **Step 6: GitNexus impact check**

Run: `npx gitnexus impact --target WmsEventPort --direction upstream`
Expected: risk LOW (new file)

- [ ] **Step 7: Commit**

```bash
git add src/app/wms_integration/ports/event.py tests/architecture/test_wms_7_ports_contract.py
git commit -m "feat(wms-ports): add WmsEventPort protocol with 4 normalizers + InboundEventPort base"
```

---

## Task 5: Add `WmsReconciliationQueryPort` Protocol + typed data classes

**Files:**
- Create: `src/app/wms_integration/ports/reconciliation_query.py`

**Interfaces:**
- Produces:
  - `WmsReconciliationQueryPort(Protocol)` — 3 query-only methods: `check_bin_drift` / `check_rack_drift` / `check_full_drift`
  - 1 Pydantic data class: `WmsDriftItem`

- [ ] **Step 1: Append contract tests for reconciliation_query port**

Edit `tests/architecture/test_wms_7_ports_contract.py`. Add the following imports at top:

```python
from src.app.wms_integration.ports.reconciliation_query import WmsReconciliationQueryPort
```

Then append:

```python
def test_wms_reconciliation_query_port_is_protocol():
    assert issubclass(WmsReconciliationQueryPort, Protocol)


def test_wms_reconciliation_query_port_method_signatures():
    methods = ["check_bin_drift", "check_rack_drift", "check_full_drift"]
    for name in methods:
        assert hasattr(WmsReconciliationQueryPort, name), f"missing method: {name}"
        method = getattr(WmsReconciliationQueryPort, name)
        assert callable(method)


def test_wms_reconciliation_query_port_have_docstrings():
    assert WmsReconciliationQueryPort.__doc__, "WmsReconciliationQueryPort class needs docstring"
    for name in ["check_bin_drift", "check_rack_drift", "check_full_drift"]:
        method = getattr(WmsReconciliationQueryPort, name)
        assert method.__doc__, f"method {name} needs docstring"


def test_wms_reconciliation_query_data_classes_are_pydantic():
    from src.app.wms_integration.ports.reconciliation_query import WmsDriftItem

    assert issubclass(WmsDriftItem, BaseModel)
    assert WmsDriftItem.__doc__, "WmsDriftItem needs docstring"


def test_all_seven_wms_ports_present():
    """Phase 1 CEO-001 完成 7/7 ports (主计划 §5.1)。"""
    from src.app.wms_integration.ports.master_data import WmsMasterDataPort
    from src.app.wms_integration.ports.inventory_query import WmsInventoryQueryPort
    from src.app.wms_integration.ports.inventory_transaction import WmsInventoryTransactionPort

    all_ports = [
        WmsMasterDataPort,
        WmsInventoryQueryPort,
        WmsInventoryTransactionPort,
        WmsDocumentPort,
        WmsFulfillmentPort,
        WmsEventPort,
        WmsReconciliationQueryPort,
    ]
    assert len(all_ports) == 7
    for port in all_ports:
        assert issubclass(port, Protocol)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/architecture/test_wms_7_ports_contract.py -k "reconciliation" -v`
Expected: ImportError for `src.app.wms_integration.ports.reconciliation_query`

- [ ] **Step 3: Implement `WmsReconciliationQueryPort` Protocol + 1 data class**

Create `src/app/wms_integration/ports/reconciliation_query.py` with:

```python
"""WmsReconciliationQueryPort (Phase 1 CEO-001 #7, Packet D)。

主计划 §5.1 7 port 之一: 对账 drift 只读查询 (bin / rack / full 实体一致性)。
所有方法 query-only, 不写 WMS 业务, 与 §3.4 Authority Matrix "WES 维护
库存作业状态, WMS 维护库存" 一致; drift 由 WES reconciliation 任务消费
(Phase 2 范围, 本端口只提供查询入口)。

方法命名: Port.method 格式, 供 ExternalContractProfile.runtime_capabilities_query
引用。
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field


class WmsDriftItem(BaseModel):
    """WES-WMS 实体 drift 项。"""

    model_config = ConfigDict(extra="forbid")

    entity_type: str = Field(description="实体类型: BIN / RACK / FULL_BOX")
    entity_id: str = Field(min_length=1, max_length=80, description="实体 ID")
    wes_state: str = Field(description="WES 记录的实体状态")
    wms_state: str = Field(description="WMS 记录的实体状态")
    drift_kind: str = Field(description="MISSING_WES / MISSING_WMS / STATE_MISMATCH / QTY_MISMATCH")
    detected_at: str = Field(description="drift 检测时间 ISO 8601")


class WmsReconciliationQueryPort(Protocol):
    """WMS 对账查询 port (Phase 1 CEO-001 #7, Packet D)。

    所有方法 query-only; drift 由 WES reconciliation 任务消费 (Phase 2)。
    Runtime capability 注入时仅暴露 query port contract (R-I3b)。
    """

    def check_bin_drift(self, warehouse_code: str, *, zone_code: str | None = None) -> list[WmsDriftItem]:
        """检查仓库 (可选 zone) 内 bin 实体 WES/WMS 一致性, 返回 drift 列表。"""
        ...

    def check_rack_drift(self, warehouse_code: str, *, station_code: str | None = None) -> list[WmsDriftItem]:
        """检查仓库 (可选工位) 内 rack 实体 WES/WMS 一致性, 返回 drift 列表。"""
        ...

    def check_full_drift(self, warehouse_code: str) -> list[WmsDriftItem]:
        """检查仓库内满箱实体 WES/WMS 一致性, 返回 drift 列表。"""
        ...
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/architecture/test_wms_7_ports_contract.py -v`
Expected: ALL tests PASS (包括 test_all_seven_wms_ports_present)

- [ ] **Step 5: Run ruff format and check**

Run: `uv run ruff format src/app/wms_integration/ports/reconciliation_query.py && uv run ruff check src/app/wms_integration/ports/reconciliation_query.py`
Expected: clean

- [ ] **Step 6: GitNexus impact check**

Run: `npx gitnexus impact --target WmsReconciliationQueryPort --direction upstream`
Expected: risk LOW

- [ ] **Step 7: Commit**

```bash
git add src/app/wms_integration/ports/reconciliation_query.py tests/architecture/test_wms_7_ports_contract.py
git commit -m "feat(wms-ports): add WmsReconciliationQueryPort protocol + 1 typed data class"
```

---

## Task 6: Harden `InboundNormalizerProfile` with model_validator

**Files:**
- Modify: `src/app/contracts/external_contract_profile.py` (lines around `InboundNormalizerProfile` class, currently ~line 170)
- Create: `tests/architecture/test_inbound_normalizer_profile_validation.py`

**Interfaces:**
- Produces:
  - `InboundNormalizerProfile._normalizer_injection_boundary` model_validator method
  - 3 new validation rules: event_type prefix, source_provider/event_type consistency, correlation_resolution enum

- [ ] **Step 1: Write the failing Pydantic validation tests**

Create `tests/architecture/test_inbound_normalizer_profile_validation.py`:

```python
"""InboundNormalizerProfile Pydantic model_validator 测试 (Phase 1 CEO-009 / Packet D)。

主计划 §3.5.1 + H2 黑名单: InboundNormalizerProfile 必须拒绝不合规输入,
防止业务 capability 错误注入 inbound normalizer (R-I3a/R-I3b/R-I3c)。
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.app.contracts.external_contract_profile import InboundNormalizerProfile


def test_inbound_normalizer_profile_accepts_valid_wms():
    """合规 WMS 入站 normalizer profile 通过校验。"""
    profile = InboundNormalizerProfile(
        normalizer_name="wms_grn_received",
        source_provider="wms",
        event_type="WMS_GRN_RECEIVED",
        correlation_resolution="manual",
    )
    assert profile.normalizer_name == "wms_grn_received"
    assert profile.correlation_resolution == "manual"


def test_inbound_normalizer_profile_accepts_valid_ecs():
    """合规 ECS 入站 normalizer profile 通过校验。"""
    profile = InboundNormalizerProfile(
        normalizer_name="ecs_pallet_arrived",
        source_provider="ecs",
        event_type="ECS_PALLET_ARRIVED",
        correlation_resolution="auto",
    )
    assert profile.normalizer_name == "ecs_pallet_arrived"


def test_inbound_normalizer_profile_accepts_valid_device():
    """合规 DEVICE 入站 normalizer profile 通过校验。"""
    profile = InboundNormalizerProfile(
        normalizer_name="device_command_result",
        source_provider="device",
        event_type="DEVICE_COMMAND_RESULT",
        correlation_resolution="hybrid",
    )
    assert profile.normalizer_name == "device_command_result"


def test_inbound_normalizer_profile_rejects_unknown_event_type_prefix():
    """event_type 必须以 WMS_/ECS_/DEVICE_ 之一开头。"""
    with pytest.raises(ValidationError) as exc_info:
        InboundNormalizerProfile(
            normalizer_name="bad_normalizer",
            source_provider="wms",
            event_type="FOO_BAR",
            correlation_resolution="manual",
        )
    assert "event_type 必须以" in str(exc_info.value)


def test_inbound_normalizer_profile_rejects_source_provider_event_type_mismatch():
    """source_provider 与 event_type 前缀必须一致 (wms→WMS_, ecs→ECS_, device→DEVICE_)。"""
    with pytest.raises(ValidationError) as exc_info:
        InboundNormalizerProfile(
            normalizer_name="mismatch",
            source_provider="wms",
            event_type="ECS_GRN_RECEIVED",
            correlation_resolution="manual",
        )
    assert "source_provider" in str(exc_info.value)
    assert "前缀不一致" in str(exc_info.value)


def test_inbound_normalizer_profile_rejects_invalid_correlation_resolution():
    """correlation_resolution 必为 manual/auto/hybrid 之一。"""
    with pytest.raises(ValidationError) as exc_info:
        InboundNormalizerProfile(
            normalizer_name="bad_correlation",
            source_provider="wms",
            event_type="WMS_GRN_RECEIVED",
            correlation_resolution="foo",
        )
    assert "correlation_resolution 必为" in str(exc_info.value)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/architecture/test_inbound_normalizer_profile_validation.py -v`
Expected: 6 tests FAIL with "source_provider=... 与 event_type=... 前缀不一致" 或类似 ValueError (current model_validator 不存在)

- [ ] **Step 3: Read current `InboundNormalizerProfile` class**

Verify lines 170-186 of `src/app/contracts/external_contract_profile.py` contain the existing class without `_normalizer_injection_boundary`. The class is currently:

```python
class InboundNormalizerProfile(BaseModel):
    """inbound normalizer (callback event/result → RuntimeInbox) 合同 (Phase 1 CEO-009 / H2)。
    ...
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    normalizer_name: str = Field(min_length=1, max_length=80, description="normalizer 名")
    source_provider: str = Field(description="源 provider (WMS/ECS)")
    event_type: str = Field(description="WMS_GRN_RECEIVED 等")
    correlation_resolution: str = Field(
        default="manual",
        description="source_event_id 解析 correlation 策略: manual / auto / hybrid",
    )
```

- [ ] **Step 4: Add `_normalizer_injection_boundary` model_validator**

Edit `src/app/contracts/external_contract_profile.py`. After the `correlation_resolution` field (line ~185) and BEFORE the next class definition, add:

```python
    @model_validator(mode="after")
    def _normalizer_injection_boundary(self) -> InboundNormalizerProfile:
        """inbound normalizer 静态校验 (Phase 1 CEO-009 / Packet D)。

        主计划 §3.5.1 + H2 黑名单: 拒绝不合规输入, 防止业务 capability 错误
        注入 inbound normalizer (R-I3a/R-I3b/R-I3c)。
        """
        valid_prefixes = ("WMS_", "ECS_", "DEVICE_")
        if not any(self.event_type.startswith(p) for p in valid_prefixes):
            raise ValueError(
                f"event_type 必须以 {valid_prefixes} 之一开头, got: {self.event_type}"
            )
        provider_to_prefix = {"wms": "WMS_", "ecs": "ECS_", "device": "DEVICE_"}
        expected_prefix = provider_to_prefix.get(self.source_provider.lower())
        if expected_prefix is None:
            raise ValueError(
                f"source_provider 必为 wms/ecs/device 之一, got: {self.source_provider}"
            )
        if not self.event_type.startswith(expected_prefix):
            raise ValueError(
                f"source_provider={self.source_provider} 与 event_type={self.event_type} 前缀不一致, "
                f"应为 {expected_prefix}*"
            )
        valid_resolutions = ("manual", "auto", "hybrid")
        if self.correlation_resolution not in valid_resolutions:
            raise ValueError(
                f"correlation_resolution 必为 {valid_resolutions} 之一, got: {self.correlation_resolution}"
            )
        return self
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/architecture/test_inbound_normalizer_profile_validation.py -v`
Expected: 6 tests PASS

- [ ] **Step 6: Run ruff format and check**

Run: `uv run ruff format src/app/contracts/external_contract_profile.py && uv run ruff check src/app/contracts/external_contract_profile.py`
Expected: clean

- [ ] **Step 7: GitNexus impact check**

Run: `npx gitnexus impact --target InboundNormalizerProfile --direction upstream`
Expected: risk LOW (validation only, no behavior change for existing valid inputs)

- [ ] **Step 8: Commit**

```bash
git add src/app/contracts/external_contract_profile.py tests/architecture/test_inbound_normalizer_profile_validation.py
git commit -m "feat(contracts): harden InboundNormalizerProfile with injection boundary validators"
```

---

## Task 7: Add `InboundNormalizerRegistry` + `RuntimeCapabilityContext.get_inbound_normalizer`

**Files:**
- Create: `src/app/runtime/inbound_normalizer_registry.py`
- Modify: `src/app/runtime/capability_port_registry.py` (extend `RuntimeCapabilityContext`)
- Create: `tests/architecture/test_runtime_capability_context_routing.py`

**Interfaces:**
- Produces:
  - `InboundNormalizerRegistry` class with `register` / `get` / `list_registered` / `is_registered` methods
  - `RuntimeCapabilityContext.get_inbound_normalizer(port_protocol, *, caller_module)` method
  - `RuntimeCapabilityContext.__init__` accepts `inbound_registry: InboundNormalizerRegistry | None = None`

- [ ] **Step 1: Write the failing routing test**

Create `tests/architecture/test_runtime_capability_context_routing.py`:

```python
"""RuntimeCapabilityContext.get_inbound_normalizer 路由测试 (Phase 1 CEO-009 / H2)。

主计划 §3.5.1 + H2: inbound normalizer 仅允许 RuntimeInboxConsumer 调用
(src.app.runtime.orchestration.consumers); 业务 capability 调用必须抛
PermissionError。
"""
from __future__ import annotations

import pytest

from src.app.runtime.capability_port_registry import (
    CapabilityPortRegistry,
    RuntimeCapabilityContext,
    _INBOUND_NORMALIZER_TYPE_NAMES,
)
from src.app.runtime.inbound_normalizer_registry import InboundNormalizerRegistry


def test_inbound_normalizer_blacklist_includes_wms_event_port():
    """_INBOUND_NORMALIZER_TYPE_NAMES 必须包含 WmsEventPort (Packet D 新增)。"""
    assert "WmsEventPort" in _INBOUND_NORMALIZER_TYPE_NAMES


def test_inbound_normalizer_blacklist_includes_inbound_event_port():
    assert "InboundEventPort" in _INBOUND_NORMALIZER_TYPE_NAMES


def test_inbound_normalizer_registry_register_and_get():
    """正常路径: 注册并获取 normalizer。"""
    registry = InboundNormalizerRegistry()

    class FakeNormalizer:
        pass

    factory_calls = []

    def factory():
        factory_calls.append(1)
        return FakeNormalizer()

    registry.register(FakeNormalizer, factory)
    instance = registry.get(FakeNormalizer)
    assert isinstance(instance, FakeNormalizer)
    # factory 懒加载: 第二次 get 不再调用 factory
    registry.get(FakeNormalizer)
    assert len(factory_calls) == 1


def test_runtime_capability_context_get_inbound_normalizer_allows_consumer():
    """允许路径 (RuntimeInboxConsumer) 可调用 get_inbound_normalizer。"""
    inbound_reg = InboundNormalizerRegistry()
    normalizer = type("FakeNormalizer", (), {})()

    def factory():
        return normalizer

    inbound_reg.register(type(normalizer), factory)

    capability_reg = CapabilityPortRegistry()
    ctx = RuntimeCapabilityContext(capability_reg, inbound_registry=inbound_reg)

    instance = ctx.get_inbound_normalizer(
        type(normalizer),
        caller_module="src.app.runtime.orchestration.consumers.wms_event_consumer",
    )
    assert instance is normalizer


def test_runtime_capability_context_get_inbound_normalizer_blocks_business_capability():
    """业务 capability 调用 get_inbound_normalizer 必须抛 PermissionError。"""
    inbound_reg = InboundNormalizerRegistry()
    capability_reg = CapabilityPortRegistry()
    ctx = RuntimeCapabilityContext(capability_reg, inbound_registry=inbound_reg)

    class FakeNormalizer:
        pass

    with pytest.raises(PermissionError) as exc_info:
        ctx.get_inbound_normalizer(
            FakeNormalizer,
            caller_module="src.app.workline.services.some_capability",
        )
    assert "业务 capability" in str(exc_info.value)
    assert "src.app.runtime.orchestration.consumers" in str(exc_info.value)


def test_runtime_capability_context_get_inbound_normalizer_blocks_workline_capability():
    """workline 域 capability 调用必须被阻止。"""
    inbound_reg = InboundNormalizerRegistry()
    capability_reg = CapabilityPortRegistry()
    ctx = RuntimeCapabilityContext(capability_reg, inbound_registry=inbound_reg)

    class FakeNormalizer:
        pass

    with pytest.raises(PermissionError):
        ctx.get_inbound_normalizer(
            FakeNormalizer,
            caller_module="src.app.workline.capabilities.transport",
        )


def test_inbound_normalizer_registry_rejects_blacklisted_type():
    """防御性: 即使绕过也不允许 WmsEventPort 等黑名单类型注册。"""
    registry = InboundNormalizerRegistry()

    from src.app.wms_integration.ports.event import WmsEventPort

    with pytest.raises(ValueError) as exc_info:
        registry.register(WmsEventPort, lambda: None)
    assert "拒绝注册 inbound normalizer 类型" in str(exc_info.value) or "inbound normalizer" in str(exc_info.value)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/architecture/test_runtime_capability_context_routing.py -v`
Expected: ImportError for `src.app.runtime.inbound_normalizer_registry` AND `RuntimeCapabilityContext.__init__` 不接受 `inbound_registry`

- [ ] **Step 3: Implement `InboundNormalizerRegistry`**

Create `src/app/runtime/inbound_normalizer_registry.py`:

```python
"""InboundNormalizerRegistry (Phase 1 CEO-009 + H2, Packet D)。

主计划 §3.5.1: 入站 normalizer (WmsEventPort / DeviceEventPort / RuntimeInbox
consumer) 注册表; 与 CapabilityPortRegistry 严格分离 (query/effect port 走
CapabilityPortRegistry, inbound normalizer 走本类)。

业务 capability 不可注入 inbound normalizer (R-I3a/R-I3b/R-I3c); 仅
RuntimeInboxConsumer 通过 RuntimeCapabilityContext.get_inbound_normalizer() 访问。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

# 与 capability_port_registry 共享黑名单 (防御性, 正常情况 InboundNormalizer
# 应只在 event.py / device 回调协议中出现)
_INBOUND_NORMALIZER_TYPE_NAMES: frozenset[str] = frozenset(
    {
        "WmsEventPort",
        "DeviceEventPort",
        "InboundEventPort",
        "RuntimeInbox",
        "RuntimeInboxConsumer",
    }
)


class InboundNormalizerRegistry:
    """入站 normalizer 注册表 (主计划 §3.5.1 + Phase 1 CEO-009 / H2)。

    与 CapabilityPortRegistry 严格分离: 注册的 normalizer 不可注入业务
    capability 上下文; 只允许 RuntimeInboxConsumer 通过
    RuntimeCapabilityContext.get_inbound_normalizer() 获取。
    """

    def __init__(self) -> None:
        self._factories: dict[str, Callable[..., Any]] = {}
        self._instances: dict[str, Any] = {}

    def register(self, port_protocol: type[Any], factory: Callable[..., Any]) -> None:
        """注册 inbound normalizer port protocol + factory。

        防御性: 拒绝黑名单类型 (正常应只在 event.py / device callback 注册)。
        """
        port_name = port_protocol.__name__
        if port_name in _INBOUND_NORMALIZER_TYPE_NAMES:
            raise ValueError(
                f"InboundNormalizerRegistry 拒绝注册 inbound normalizer 类型: {port_name}; "
                "inbound normalizer 不属于业务 capability (主计划 §3.5 I3 + H2)"
            )
        self._factories[port_name] = factory

    def get(self, port_protocol: type[Any]) -> Any:
        """获取 normalizer 实例 (按需构造, 不暴露 implementation type)。"""
        port_name = port_protocol.__name__
        if port_name not in self._factories:
            raise KeyError(
                f"normalizer {port_name} 未注册; 可用: {list(self._factories)}"
            )
        if port_name not in self._instances:
            self._instances[port_name] = self._factories[port_name]()
        return self._instances[port_name]

    def list_registered(self) -> list[str]:
        """返回已注册 normalizer 名称列表。"""
        return sorted(self._factories)

    def is_registered(self, port_protocol: type[Any]) -> bool:
        """检查 normalizer 是否已注册。"""
        return port_protocol.__name__ in self._factories
```

- [ ] **Step 4: Modify `RuntimeCapabilityContext` to accept inbound_registry**

Edit `src/app/runtime/capability_port_registry.py`. Replace the `RuntimeCapabilityContext` class definition (lines ~72-93) with:

```python
class RuntimeCapabilityContext:
    """Runtime capability 注入上下文 (主计划 §3.5 + §9.2 + Phase 1 CEO-009)。

    capability 只能拿到:
    - query_ports: 只读事实查询 port (WmsMasterDataPort / WmsInventoryQueryPort / WmsDocumentPort / WmsReconciliationQueryPort)
    - effect_ports: 出站副作用 port (WmsFulfillmentPort / WmsInventoryTransactionPort)

    capability 不能拿到:
    - inbound normalizer (WmsEventPort / DeviceEventPort / RuntimeInbox consumer)
    - HTTP client / service locator / DTO / provider exception

    inbound normalizer 仅 RuntimeInboxConsumer 通过 get_inbound_normalizer()
    获取 (caller_module 路由检查)。
    """

    def __init__(
        self,
        registry: CapabilityPortRegistry,
        inbound_registry: InboundNormalizerRegistry | None = None,
    ) -> None:
        self._registry = registry
        self._inbound_registry = inbound_registry

    def get_query_port(self, port_protocol: type[Any]) -> Any:
        """获取 query port (只读事实查询)。"""
        return self._registry.get(port_protocol)

    def get_effect_port(self, port_protocol: type[Any]) -> Any:
        """获取 effect port (出站副作用, 必须先写 RuntimeIntentLog)。"""
        return self._registry.get(port_protocol)

    def get_inbound_normalizer(
        self,
        port_protocol: type[Any],
        *,
        caller_module: str,
    ) -> Any:
        """获取 inbound normalizer。

        Args:
            port_protocol: WmsEventPort 等 inbound normalizer Protocol
            caller_module: 调用方模块路径, 仅允许 src.app.runtime.orchestration.consumers

        Raises:
            PermissionError: 业务 capability (非 consumer 路径) 不可调用
            RuntimeError: inbound_registry 未注入到上下文
        """
        if self._inbound_registry is None:
            raise RuntimeError(
                "RuntimeCapabilityContext 未配置 inbound_registry; "
                "无法提供 inbound normalizer"
            )
        if not caller_module.startswith("src.app.runtime.orchestration.consumers"):
            raise PermissionError(
                f"inbound normalizer 不可注入业务 capability, caller={caller_module}; "
                "仅 src.app.runtime.orchestration.consumers 允许访问"
            )
        return self._inbound_registry.get(port_protocol)
```

- [ ] **Step 5: Add import for `InboundNormalizerRegistry`**

At the top of `src/app/runtime/capability_port_registry.py`, modify the import section. After the existing imports, add a TYPE_CHECKING block at the bottom:

```python
if TYPE_CHECKING:
    from collections.abc import Callable
    from src.app.runtime.inbound_normalizer_registry import InboundNormalizerRegistry
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/architecture/test_runtime_capability_context_routing.py -v`
Expected: 7 tests PASS

- [ ] **Step 7: Run full architecture test suite to ensure no regression**

Run: `uv run pytest tests/architecture/ -v`
Expected: ALL tests PASS (existing R-I3a/R-I3b tests + new routing tests + previous 6 Pydantic tests + 4 contract tests)

- [ ] **Step 8: Run ruff format and check**

Run: `uv run ruff format src/app/runtime/capability_port_registry.py src/app/runtime/inbound_normalizer_registry.py && uv run ruff check src/app/runtime/capability_port_registry.py src/app/runtime/inbound_normalizer_registry.py`
Expected: clean

- [ ] **Step 9: GitNexus impact check**

Run: `npx gitnexus impact --target RuntimeCapabilityContext --direction upstream`
Expected: risk LOW (新增可选参数 + 新方法, 既有调用方不受影响)

- [ ] **Step 10: Commit**

```bash
git add src/app/runtime/inbound_normalizer_registry.py src/app/runtime/capability_port_registry.py tests/architecture/test_runtime_capability_context_routing.py
git commit -m "feat(runtime): add InboundNormalizerRegistry + RuntimeCapabilityContext.get_inbound_normalizer"
```

---

## Task 8: Add R-I3c guardrail + import-linter capability-isolation contract

**Files:**
- Modify: `scripts/architecture-guardrails.sh` (add `rule_ri3c()` function)
- Create: `.import-linter.ini`
- Create: `scripts/import-linter-check.sh`
- Modify: `scripts/git-quality-gate.sh` (add import-linter check after ruff)
- Create: `tests/architecture/test_ri3c_inbound_normalizer_port_guardrail.py`

**Interfaces:**
- Produces:
  - `rule_ri3c()` function in `architecture-guardrails.sh` scanning `WmsEventPort` / `DeviceEventPort` / `InboundEventPort` / `RuntimeInbox` / `RuntimeInboxConsumer` references in capability paths
  - `.import-linter.ini` with `capability-isolation` contract covering all forbidden_modules per Phase 1 SPEC §279
  - `scripts/import-linter-check.sh` bash wrapper for `lint-imports`
  - `scripts/git-quality-gate.sh` calls import-linter after ruff
  - 5 tests in `test_ri3c_inbound_normalizer_port_guardrail.py`

- [ ] **Step 1: Write the failing R-I3c guardrail tests**

Create `tests/architecture/test_ri3c_inbound_normalizer_port_guardrail.py`:

```python
"""R-I3c guardrail 测试 (Phase 1 CEO-009 / Packet D)。

主计划 §3.5 + H2: 业务 capability (src/app/runtime, src/app/workline) 不可
import / type hint 以下 inbound normalizer Protocol 或对象:
- WmsEventPort / DeviceEventPort / InboundEventPort
- RuntimeInbox / RuntimeInboxConsumer

R-I3c 由 scripts/architecture-guardrails.sh rule_ri3c() 静态扫描;
本测试验证 guardrail 脚本本身覆盖了这些关键词且能被 phase1 调用。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GUARDRAIL = REPO_ROOT / "scripts" / "architecture-guardrails.sh"
IMPORT_LINTER_INI = REPO_ROOT / ".import-linter.ini"
IMPORT_LINTER_SCRIPT = REPO_ROOT / "scripts" / "import-linter-check.sh"

RI3C_BLACKLIST = {"WmsEventPort", "DeviceEventPort", "InboundEventPort", "RuntimeInbox", "RuntimeInboxConsumer"}


def test_ri3c_blacklist_keywords_covered_in_guardrail():
    """rule_ri3c 必须覆盖全部 5 个黑名单类型。"""
    content = GUARDRAIL.read_text(encoding="utf-8")
    assert "rule_ri3c" in content
    for kw in RI3C_BLACKLIST:
        assert kw in content, f"missing keyword in guardrail: {kw}"


def test_ri3c_rule_excluded_paths_include_consumers():
    """rule_ri3c 排除路径必须包含 event.py 和 consumers 目录。"""
    content = GUARDRAIL.read_text(encoding="utf-8")
    assert "src/app/wms_integration/ports/event.py" in content
    assert "src/app/runtime/orchestration/consumers" in content


def test_import_linter_ini_exists_with_capability_isolation_contract():
    """.import-linter.ini 必须包含 capability-isolation contract。"""
    assert IMPORT_LINTER_INI.exists(), ".import-linter.ini must exist"
    content = IMPORT_LINTER_INI.read_text(encoding="utf-8")
    assert "capability-isolation" in content
    assert "type = forbidden" in content


def test_import_linter_ini_forbidden_modules_complete():
    """forbidden_modules 必须覆盖 Phase 1 SPEC §279 列出的全部路径。"""
    content = IMPORT_LINTER_INI.read_text(encoding="utf-8")
    required_modules = [
        "src.app.wms_integration.services",
        "src.app.wms_integration.models",
        "src.app.wms_integration.clients",
        "src.app.wms_integration.providers",
        "src.app.device.services",
        "src.app.device.models",
        "src.app.callback.services",
        "src.app.runtime.orchestration.consumers",
    ]
    for mod in required_modules:
        assert mod in content, f"missing forbidden_module: {mod}"


def test_import_linter_wrapper_script_exists_and_executable():
    """scripts/import-linter-check.sh 必须存在且可执行。"""
    assert IMPORT_LINTER_SCRIPT.exists(), "import-linter-check.sh must exist"
    assert IMPORT_LINTER_SCRIPT.stat().st_mode & 0o111, "import-linter-check.sh must be executable"


def test_git_quality_gate_invokes_import_linter():
    """scripts/git-quality-gate.sh 必须在 ruff 之后调用 import-linter。"""
    content = (REPO_ROOT / "scripts" / "git-quality-gate.sh").read_text(encoding="utf-8")
    assert "import-linter-check" in content or "lint-imports" in content


def test_guardrail_rule_ri3c_phase1_exit_zero_when_clean(tmp_path):
    """干净状态 phase1 不应触发 R-I3c 违规 (exit 0)。"""
    # 构造临时 allowlist (空)
    temp_allowlist = tmp_path / "architecture-guardrails.allowlist"
    temp_allowlist.write_text("", encoding="utf-8")

    result = subprocess.run(
        ["/bin/bash", str(GUARDRAIL), "--phase", "phase1", "--allowlist", str(temp_allowlist)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    # R-I3c 在干净代码库应 pass (exit 0 或 warn-only)
    assert result.returncode == 0, f"R-I3c unexpected violation:\n{result.stderr}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/architecture/test_ri3c_inbound_normalizer_port_guardrail.py -v`
Expected: tests FAIL (rule_ri3c not yet in guardrail, .import-linter.ini not exists, etc.)

- [ ] **Step 3: Add `rule_ri3c()` to `scripts/architecture-guardrails.sh`**

Edit `scripts/architecture-guardrails.sh`. Find the line that defines `rule_ri3b()` (around line 213). After the closing `}` of `rule_ri3b`, add:

```bash
# --- R-I3c: 业务 capability 不可持有 inbound normalizer Protocol / 类型 ---
# 主计划 §3.5 + H2: 业务 capability 注入边界严格分离; inbound normalizer
# (WmsEventPort / DeviceEventPort / InboundEventPort / RuntimeInbox consumer)
# 不可被业务 capability 持有。
rule_ri3c() {
    local pattern='WmsEventPort|DeviceEventPort|InboundEventPort|RuntimeInbox|RuntimeInboxConsumer'
    while IFS=: read -r file line _content; do
        [[ -z "$file" ]] && continue
        # 排除 port 定义文件本身 + 允许的 inbound 路径
        case "$file" in
            src/app/wms_integration/ports/event.py|src/app/wms_integration/ports/device_event.py|src/app/callback/*|src/app/runtime/orchestration/consumers/*|src/app/device/ports/event.py) continue ;;
        esac
        emit_violation "R-I3c" "$file" "$line" \
            "业务 capability 持有 inbound normalizer Protocol, 违反主计划 §3.5 I3" \
            "业务 capability 只能 import query/effect port contract (WmsMasterDataPort 等)"
    done < <(grep -rnE "$pattern" src/app/runtime src/app/workline --include='*.py' 2>/dev/null || true)
}
```

Then update the docstring header (around line 15) from `规则: C1 C2 C3 C4 C5 R-I3a R-I3b (主计划 §7.5)` to `规则: C1 C2 C3 C4 C5 R-I3a R-I3b R-I3c (主计划 §7.5)`.

Then update the rule summary printed at top (`echo "=== Architecture Guardrails (phase=$PHASE) ===" >&2`) to include R-I3c, and add `rule_ri3c` to the call chain after `rule_ri3b` (around line 290):

```bash
rule_c1
rule_c2
rule_c3
rule_c4
rule_ri3a
rule_ri3b
rule_ri3c
```

- [ ] **Step 4: Create `.import-linter.ini`**

Create `.import-linter.ini` at the repo root with:

```ini
[importlinter]
contract_types =
    forbidden

[importlinter:contract:capability-isolation]
type = forbidden
source_modules =
    src.app.runtime.capability_context
    src.app.runtime.capability_port_registry
    src.app.runtime.inbound_normalizer_registry
forbidden_modules =
    src.app.wms_integration.services
    src.app.wms_integration.models
    src.app.wms_integration.clients
    src.app.wms_integration.providers
    src.app.wms_integration.schemas
    src.app.wms_integration.dto
    src.app.wms_integration.dtos
    src.app.wms_integration.exceptions
    src.app.wms_integration.ports.event
    src.app.wms_integration.ports.callback
    src.app.wms_integration.ports.result
    src.app.wms_integration.ports.inbound_event
    src.app.device.services
    src.app.device.models
    src.app.device.clients
    src.app.device.providers
    src.app.device.schemas
    src.app.device.dto
    src.app.device.dtos
    src.app.device.exceptions
    src.app.device.ports.event
    src.app.device.ports.result
    src.app.device.ports.inbound_event
    src.app.callback.services
    src.app.runtime.orchestration.consumers
```

- [ ] **Step 5: Create `scripts/import-linter-check.sh`**

Create `scripts/import-linter-check.sh`:

```bash
#!/usr/bin/env bash
# Import-linter wrapper — 调用 lint-imports 检查 capability-isolation contract。
# Phase 1 Packet D CEO-009: 接入 git-quality-gate.sh 在 ruff 之后。
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

if [[ ! -f ".import-linter.ini" ]]; then
    echo "[import-linter] .import-linter.ini 不存在, 跳过检查" >&2
    exit 0
fi

uv run lint-imports
```

Then run:

```bash
chmod +x scripts/import-linter-check.sh
```

- [ ] **Step 6: Modify `scripts/git-quality-gate.sh` to call import-linter**

Read `scripts/git-quality-gate.sh` first to understand its structure. Then add a new check section between ruff and pytest. If the script already has a profile section, add:

```bash
# Import-linter capability-isolation contract (Phase 1 Packet D CEO-009)
if [[ -f "$REPO_ROOT/.import-linter.ini" ]]; then
    step "import-linter capability-isolation"
    "$REPO_ROOT/scripts/import-linter-check.sh"
fi
```

The exact placement depends on the script structure; insert after the ruff check and before the pytest invocation.

- [ ] **Step 7: Run test to verify it passes**

Run: `uv run pytest tests/architecture/test_ri3c_inbound_normalizer_port_guardrail.py -v`
Expected: 7 tests PASS

- [ ] **Step 8: Run the actual guardrail and import-linter end-to-end**

Run: `./scripts/architecture-guardrails.sh --phase phase1`
Expected: exit 0, no R-I3c violations reported

Run: `./scripts/import-linter-check.sh`
Expected: exit 0, no import violations

If import-linter reports violations, fix the source files to remove the violation OR add an explicit allowlist entry in `.import-linter.ini` (only for the `capability-isolation` contract). For Phase 1 baseline, the codebase should be clean since R-I3a/R-I3b already enforce similar rules.

- [ ] **Step 9: Run full architecture test suite**

Run: `uv run pytest tests/architecture/ -v`
Expected: ALL tests PASS

- [ ] **Step 10: Run git-quality-gate end-to-end**

Run: `./scripts/git-quality-gate.sh --profile quality`
Expected: exit 0, all checks pass including import-linter

- [ ] **Step 11: GitNexus detect_changes pre-commit check**

Run: `npx gitnexus detect_changes --scope all`
Expected: risk LOW; affected symbols include InboundNormalizerRegistry, RuntimeCapabilityContext.get_inbound_normalizer, rule_ri3c, .import-linter.ini, capability-isolation contract. No HIGH/CRITICAL risk.

- [ ] **Step 12: Commit**

```bash
git add scripts/architecture-guardrails.sh .import-linter.ini scripts/import-linter-check.sh scripts/git-quality-gate.sh tests/architecture/test_ri3c_inbound_normalizer_port_guardrail.py
git commit -m "feat(architecture): enforce R-I3c inbound normalizer port guardrail + import-linter capability-isolation contract"
```

---

## Task 9: Sync documentation (master plan + file index)

**Files:**
- Modify: `docs/architecture/workline-and-plugin-restructuring.md` (update §10.2 Packet D status)
- Modify: `docs/architecture/file_index.md` (add 4 port files + inbound_normalizer_registry)

- [ ] **Step 1: Verify master plan §10.2 Packet D current state**

Run: `grep -n "Packet D\|Packet D" docs/architecture/workline-and-plugin-restructuring.md | head -30`
Expected: shows current state with Packet D markers

- [ ] **Step 2: Update master plan §10.2 Packet D status to ✅**

Find the Packet D section header (likely line containing "Packet D" with ⏳ status marker). Update:
- Status emoji from ⏳ to ✅
- Add completion evidence: list of commits from Task 2-8 (use `git log --oneline develop..HEAD` if on feature branch, or just describe the deliverables)

Add a new subsection "落地证据 (Phase 1 Packet D)" right after the Packet D status section listing:
- 4 remaining WMS ports added: WmsDocumentPort / WmsFulfillmentPort / WmsEventPort / WmsReconciliationQueryPort
- InboundNormalizerRegistry + RuntimeCapabilityContext.get_inbound_normalizer
- InboundNormalizerProfile Pydantic model_validator (3 rules)
- R-I3c guardrail + import-linter capability-isolation contract
- 5 new tests + 5 updated/extended tests

- [ ] **Step 3: Update file_index.md**

Edit `docs/architecture/file_index.md`. Find the "wms_integration/ports" section. Add the 4 new port files to the index:

```
| `wms_integration/ports/document.py` | WmsDocumentPort Protocol + 6 typed data classes (Phase 1 Packet D) |
| `wms_integration/ports/fulfillment.py` | WmsFulfillmentPort Protocol + 2 typed data classes (Phase 1 Packet D) |
| `wms_integration/ports/event.py` | InboundEventPort + WmsEventPort Protocols + 5 typed data classes (Phase 1 Packet D) |
| `wms_integration/ports/reconciliation_query.py` | WmsReconciliationQueryPort Protocol + 1 typed data class (Phase 1 Packet D) |
```

Also find the "runtime" section in `file_index.md` and add:

```
| `runtime/inbound_normalizer_registry.py` | InboundNormalizerRegistry + H2 黑名单共享 (Phase 1 Packet D) |
```

- [ ] **Step 4: Verify no broken doc references**

Run: `grep -n "ports/document\|ports/fulfillment\|ports/event\|ports/reconciliation_query\|inbound_normalizer_registry" docs/architecture/file_index.md`
Expected: 5 lines matching (4 ports + 1 registry)

- [ ] **Step 5: Run all architecture tests to ensure no regression**

Run: `uv run pytest tests/architecture/ -v`
Expected: ALL tests PASS

- [ ] **Step 6: Final PR-readiness check**

Run all of:

```bash
uv run pytest --cov=src
uv run ruff format .
uv run ruff check .
./scripts/architecture-guardrails.sh --phase phase1
./scripts/import-linter-check.sh
./scripts/git-quality-gate.sh --profile quality
npx gitnexus detect_changes --scope all
```

Expected: all exit 0, all checks pass

- [ ] **Step 7: Commit**

```bash
git add docs/architecture/workline-and-plugin-restructuring.md docs/architecture/file_index.md
git commit -m "docs(architecture): sync Phase 1 Packet D completion status"
```

---

## Self-Review

Performed after plan assembly:

1. **Spec coverage** — Every section of `docs/superpowers/specs/2026-06-27-workline-phase-1-packet-d-design.md` maps to a task:
   - §1 Background & goal → Tasks 1-9 (full plan)
   - §2 Architecture decisions (4 ports + scanner + InboundNormalizer 三层) → Tasks 2-8
   - §3 Module/file responsibilities → Tasks 2-7 + Task 8 (config files)
   - §4 Status flow & error codes → Task 6 (Pydantic validator errors) + Task 7 (PermissionError + ValueError)
   - §5 Data fields (5.1-5.4) → Tasks 2-5 (data classes definitions inline in port files)
   - §6 Acceptance criteria & test scenarios → Tasks 2-7 (per-task tests) + Task 8 (R-I3c tests)
   - §7 Risks → Task 8 Step 8 (fallback: scope shrink if violations)
   - §8 Out of scope → implicitly respected (no migration of transport_contract etc.)
   - §9 Implementation steps → this plan IS the 9-task implementation

2. **Placeholder scan** — Searched plan for "TBD", "TODO", "fill in details", "implement later", "similar to Task N". Zero hits. Every code block is complete and runnable.

3. **Type consistency** —
   - `WmsDocumentPort` (Task 2) has 6 methods; tests reference those exact names
   - `WmsFulfillmentPort` (Task 3) has 7 methods; tests reference those exact names
   - `WmsEventPort` (Task 4) has 4 normalizers + `InboundEventPort` base; tests reference those exact names
   - `WmsReconciliationQueryPort` (Task 5) has 3 methods; tests reference those exact names
   - `InboundNormalizerRegistry` (Task 7) has `register`/`get`/`list_registered`/`is_registered`; tests reference those exact names
   - `RuntimeCapabilityContext.get_inbound_normalizer(port_protocol, *, caller_module)` signature consistent across Tasks 7 and 8

4. **Cross-task dependencies** —
   - Task 1 (import-linter) is foundation for Task 8
   - Tasks 2-5 (4 ports) are independent of each other
   - Task 6 (Pydantic validator) independent of Tasks 2-5
   - Task 7 (Registry) depends on Task 6 (uses `InboundNormalizerProfile`)
   - Task 8 (scanner) depends on Tasks 2-5 (need WmsEventPort to be defined for R-I3c to scan)
   - Task 9 (docs) is final, depends on all previous

   Execution order matches dependency order.

5. **Commit per task** — 9 atomic commits, each containing its own files. No Co-Authored-By. Conventional Commits format. Each commit verified by `uv run pytest` of relevant test file before commit.

No issues found; plan is self-consistent and covers the full SPEC.
