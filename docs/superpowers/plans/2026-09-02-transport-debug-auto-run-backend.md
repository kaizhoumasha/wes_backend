# Transport Debug Auto Run Backend Implementation Plan

> **2026-09-03 修订：** 创建与推进轮次不再读取或校验 `RackBinMount` 基础数据；货架、料箱和原槽位由操作员按现场实物直接录入并冻结。本计划中 active mount 查询、锁定和二次校验步骤已被该修订取代，其他状态机与 Evidence 安全边界保持不变。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立后端持久化 Transport 自动联调轮次，按 WMS 回调和 `SCAN12` Device Evidence 自动处理选定货架的多面料箱，并在全部料箱回架后执行 `CTU03`。

**Architecture:** 正式 Transport 合同直接接受 `RACK`/`ZONE` 字符串位置，现有 Transport task 继续负责可靠投递、回调收敛和 debug position projection。新增 diagnostics 专属的 run/step 聚合与幂等推进器，运行在既有 `wms-fulfillment` Celery 队列；它只读取中性 `InboundEvidence`，不修改 Device 基础能力。API/SSE 负责配置、查询和通知，数据库始终是权威状态。

**Tech Stack:** Python 3.13、FastAPI、Pydantic 2、SQLModel/SQLAlchemy、PostgreSQL/SQLite tests、Alembic、Celery、Redis SSE、pytest

**Spec:** `docs/superpowers/specs/2026-09-02-transport-debug-auto-run-design.md`

## Global Constraints

- `target_face`/`rack_face` 是不透明非空字符串；验证可以判断 `value.strip()` 是否为空，但保存、下发和比较必须使用原字符串。
- `CTU01` 固定 `RACK → RACK_POSITION(KT16)`；`CTU02` 固定在货架 `RACK` 引用上旋转；`CTU03` 固定 `RACK → ZONE(WH01)`，返库面固定 `"90"`。
- 每组只能包含 1～4 个料箱；一个 `BIN_MOVE` 必须一次携带整组，不拆分。
- 只有 WMS 权威 `SUCCEEDED` 回调和符合边界的 `SCAN12` Evidence 可以推进；ACK、HTTP 200、SSE、健康状态和固定延时都不是物理完成证据。
- `DELIVERY_UNKNOWN`、`RECONCILING`、位置未知、回调冲突和 Evidence 歧义必须停止在 `NEEDS_ATTENTION`，不得换新请求 ID 重发。
- `SCAN12` 暂定匹配 `device_code="SCAN12"`、`event_type="SCAN_COMPLETED"`、`data.barcode`；该假设只存在于 diagnostics 窄适配器。
- 任一时刻只允许一个 `RUNNING` 或 `NEEDS_ATTENTION` 轮次。
- 复用现有 `TransportDebugPositionProjection`；不得写业务 `PositionProjection`。
- 不自动 reset/delete Transport task、callback 或 Evidence；活动轮次引用的 task 必须拒绝现有 reset。
- `TransportDebugRunService` 保持 Transport 内部实现，只在模块内定义 `__all__` 并由 `composition.py` 精确导入；不得破坏 `src.app.transport` 顶层仅暴露稳定 port DTO/runtime 的公共边界。
- commit、push、PR、merge、deploy 和现场动作均需各自显式授权；下面的 commit 步骤只是审核边界，不代表已授权执行。

---

### Task 1: 扩展正式 Transport 位置合同和 WMS wire

**Files:**
- Modify: `src/app/transport/contracts.py`
- Modify: `src/app/transport/service.py`
- Modify: `src/app/transport/v1/tasks.py`
- Modify: `tests/mock/wms_mock_server.py`
- Modify: `tests/mock/wms_transport_mock_openapi.py`
- Modify: `docs/contracts/transport-fulfillment-contract.md`
- Modify: `docs/contracts/openapi/wes-wms-transport.openapi.json`
- Test: `tests/runtime/transport/test_transport_contracts.py`
- Test: `tests/runtime/transport/test_transport_acceptance_edges.py`
- Test: `tests/api/test_transport_tasks.py`
- Test: `tests/contracts/wms_adapter/test_transport_wire_acceptance.py`
- Test: `tests/mock/test_wms_transport_mock_server.py`

**Interfaces:**
- Consumes: 现有 `RackReference`、`RackPosition`、`ZonePosition`、`MoveRackRequest`、`RotateRackRequest`。
- Produces: `RackRotatePosition = RackReference | RackPosition`；`TransportService.create_debug_task_in_session(db, request)`；支持三条正式边的 canonical request/wire。

- [ ] **Step 1: 写正式合同失败测试**

在 `tests/runtime/transport/test_transport_contracts.py` 增加：

```python
def test_510056_edges_accept_rack_reference_without_face_mapping() -> None:
    caller = TransportCaller("TRANSPORT_DEBUG", "TRANSPORT_DEBUG_AUTO")
    outbound = MoveRackRequest(
        _REQUEST_ID,
        caller,
        "510056",
        RackReference("510056"),
        RackPosition("KT16"),
        "90",
        RcsTemplateId.CTU01,
    )
    rotate = RotateRackRequest(
        _REQUEST_ID,
        caller,
        "510056",
        RackReference("510056"),
        "270",
        RcsTemplateId.CTU02,
    )
    returned = MoveRackRequest(
        _REQUEST_ID,
        caller,
        "510056",
        RackReference("510056"),
        ZonePosition("WH01"),
        "90",
        RcsTemplateId.CTU03,
    )

    assert outbound.target_face == "90"
    assert rotate.target_face == "270"
    assert returned.target == ZonePosition("WH01")
```

同时增加 `RackReference("other-rack")` 与 `rack_id="510056"` 不一致、错误模板边仍被拒绝的参数化用例。

- [ ] **Step 2: 运行合同测试确认失败**

Run:

```bash
uv run pytest tests/runtime/transport/test_transport_contracts.py -q
```

Expected: `RotateRackRequest` 拒绝 `RackReference`，且 `CTU03 RACK → ZONE` 不在允许边中。

- [ ] **Step 3: 最小修改领域合同**

在 `src/app/transport/contracts.py` 定义并使用：

```python
type RackRotatePosition = RackReference | RackPosition

@dataclass(frozen=True, slots=True)
class RotateRackRequest:
    client_request_id: str
    caller: TransportCaller
    rack_id: str
    position: RackRotatePosition
    target_face: str
    rcs_template_id: RcsTemplateId = RcsTemplateId.CTU02
    kind: TransportTaskKind = field(default=TransportTaskKind.RACK_ROTATE, init=False)

    def __post_init__(self) -> None:
        _validate_request_identity(self.client_request_id, self.caller)
        require_transport_text(self.rack_id, "rack_id", max_length=100)
        if type(self.position) not in {RackReference, RackPosition}:
            raise TransportContractError("rack rotation position must be a rack reference or rack position")
        if type(self.position) is RackReference and self.position.location_code != self.rack_id:
            raise TransportContractError("RACK location_code must match rack_id")
        validate_opaque_face(self.target_face, "target_face", error_type=TransportContractError)
        if self.rcs_template_id is not RcsTemplateId.CTU02:
            raise TransportContractError("rack rotation requires rcs_template_id CTU02")
```

并只给 `RcsTemplateId.CTU03` 增加 `(RackReference, ZonePosition)`，不扩大其它模板。

- [ ] **Step 4: 让服务按宽位置引用校验当前事实**

修改 `TransportService.rotate_rack`、`rotate_rack_for_debug` 参数类型为 `RackRotatePosition`。在 `_create_task_in_session` 中保留“当前投影必须已知且有面”的前置条件，并按输入种类处理位置：

```python
if type(request.position) is RackPosition:
    if projection.position_json != _json_value(request.position):
        raise TransportContractError("rack current position is not confirmed")
else:
    if not isinstance(projection.position_json, dict) or projection.position_json.get("kind") != "RACK_POSITION":
        raise TransportContractError("rack current exact position is unknown")
```

新增公开事务内入口，供 run service 原子绑定 task：

```python
async def create_debug_task_in_session(
    self,
    db: AsyncSession,
    request: TransportRequest,
) -> TransportHandle:
    if request.caller.workline_id != TRANSPORT_DEBUG_CALLER_WORKLINE_ID:
        raise TransportContractError("debug task requires TRANSPORT_DEBUG caller")
    return await self._create_task_in_session(
        db,
        request,
        None,
        allow_debug_rack_face=isinstance(request, MoveBinsRequest),
        use_debug_rack_projection=isinstance(request, RotateRackRequest),
    )
```

- [ ] **Step 5: 对齐 Debug API 和删除 510056 特判**

在 `src/app/transport/v1/tasks.py` 增加 discriminator union：

```python
type _RackRotatePosition = Annotated[_RackReference | _RackPosition, Field(discriminator="kind")]

class _RackRotateData(_StrictApiModel):
    rack_id: _TEXT
    position: _RackRotatePosition
    target_face: _FACE
    rcs_template_id: RcsTemplateId | None = None
```

增加 `_rack_rotate_position()`，让 `_dispatch_debug_task()` 直接构造 `RackReference` 或 `RackPosition`。删除函数内两个只针对 `rack_id == "510056"` 的 source/target 特判；正式模板边是唯一约束来源。

- [ ] **Step 6: 写并运行 API/wire/mock 回归测试**

覆盖以下精确数据：

```python
assert build_submit_data(rotate, "transport-rotate") == {
    "transport_task_id": "transport-rotate",
    "kind": "RACK_ROTATE",
    "rcs_template_id": "CTU02",
    "rack_id": "510056",
    "source": {"kind": "RACK", "location_code": "510056"},
    "target": {"kind": "RACK", "location_code": "510056"},
    "target_face": "270",
}
```

Mock 的 `RACK_ROTATE` schema 允许相同 `RACK` source/target；`CTU03` edge 增加 `RACK → ZONE`。宽目标不会由 Mock 猜测最终点位，测试 callback 必须显式提供精确 `RACK_POSITION`。

Run:

```bash
uv run pytest \
  tests/runtime/transport/test_transport_contracts.py \
  tests/runtime/transport/test_transport_acceptance_edges.py \
  tests/api/test_transport_tasks.py \
  tests/contracts/wms_adapter/test_transport_wire_acceptance.py \
  tests/mock/test_wms_transport_mock_server.py -q
```

Expected: 全部 PASS，并且 payload 中 `"90"`/`"270"` 保持原字符串。

- [ ] **Step 7: 更新正式合同文档**

在 `docs/contracts/transport-fulfillment-contract.md` 和 WMS OpenAPI 中只新增本任务三条边、rotate union 及“回调仍需精确 `RACK_POSITION`”规则。运行 JSON 解析测试：

```bash
uv run pytest tests/contracts/wms_adapter/test_transport_openapi.py -q
```

Expected: PASS。

- [ ] **Step 8: Commit gate（需单独授权）**

```bash
git add src/app/transport/contracts.py src/app/transport/service.py src/app/transport/v1/tasks.py tests/mock/wms_mock_server.py tests/mock/wms_transport_mock_openapi.py docs/contracts/transport-fulfillment-contract.md docs/contracts/openapi/wes-wms-transport.openapi.json tests/runtime/transport/test_transport_contracts.py tests/runtime/transport/test_transport_acceptance_edges.py tests/api/test_transport_tasks.py tests/contracts/wms_adapter/test_transport_wire_acceptance.py tests/mock/test_wms_transport_mock_server.py
git commit -m "feat(transport): 支持货架引用联调边"
```

### Task 2: 建立持久化 run/step 聚合与数据库约束

**Files:**
- Create: `src/app/transport/debug_run_contracts.py`
- Modify: `src/app/transport/models.py`
- Create: `src/app/transport/debug_run_repository.py`
- Create: `migrations/versions/20260903_1143_8f3c61e57a90_增加_transport_自动联调轮次.py`
- Test: `tests/runtime/transport/test_transport_debug_run_contracts.py`
- Test: `tests/integration/transport/test_transport_debug_run_schema.py`
- Test: `tests/integration/transport/test_transport_debug_run_repository.py`

**Interfaces:**
- Consumes: `TransportTask`、`TransportResourceBinding`、`RackBinMount`、`InboundEvidence`。
- Produces: `TransportDebugRunStatus`、`TransportDebugRunPhase`、`TransportDebugRunStepStatus`、配置 dataclasses、`TransportDebugRun`、`TransportDebugRunStep`、`TransportDebugRunRepository`。

- [ ] **Step 1: 写合同和数据库失败测试**

合同测试明确以下 enum：

```python
assert tuple(TransportDebugRunStatus) == (
    TransportDebugRunStatus.RUNNING,
    TransportDebugRunStatus.NEEDS_ATTENTION,
    TransportDebugRunStatus.COMPLETED,
    TransportDebugRunStatus.FAILED,
    TransportDebugRunStatus.ABORTED,
)
assert TransportDebugRunPhase.WAIT_SCAN12.value == "WAIT_SCAN12"
```

PostgreSQL schema 测试必须证明：

```python
assert await insert_active_run("run-1", active_scope="GLOBAL") == "run-1"
with pytest.raises(IntegrityError):
    await insert_active_run("run-2", active_scope="GLOBAL")
```

并验证 `(run_id, ordinal)`、`client_request_id` 唯一，以及终态 `active_scope IS NULL`。

- [ ] **Step 2: 运行测试确认缺少模型**

```bash
uv run pytest tests/runtime/transport/test_transport_debug_run_contracts.py -q
```

Expected: collection/import FAIL，因为 contracts/models 尚不存在。

- [ ] **Step 3: 定义 diagnostics 合同**

`src/app/transport/debug_run_contracts.py` 至少定义：

```python
class TransportDebugRunStatus(StrEnum):
    RUNNING = "RUNNING"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    ABORTED = "ABORTED"

class TransportDebugRunPhase(StrEnum):
    RACK_TO_STATION = "RACK_TO_STATION"
    BINS_TO_INFEED = "BINS_TO_INFEED"
    WAIT_SCAN12 = "WAIT_SCAN12"
    BINS_TO_RACK = "BINS_TO_RACK"
    ROTATE_TO_NEXT_FACE = "ROTATE_TO_NEXT_FACE"
    RACK_TO_STORAGE = "RACK_TO_STORAGE"

class TransportDebugRunStepStatus(StrEnum):
    PENDING = "PENDING"
    WAITING = "WAITING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    NEEDS_ATTENTION = "NEEDS_ATTENTION"

@dataclass(frozen=True, slots=True)
class TransportDebugBinSelection:
    bin_id: str
    slot_id: str

@dataclass(frozen=True, slots=True)
class TransportDebugFaceGroup:
    face: str
    bins: tuple[TransportDebugBinSelection, ...]

@dataclass(frozen=True, slots=True)
class CreateTransportDebugRun:
    rack_id: str
    face_groups: tuple[TransportDebugFaceGroup, ...]
```

在 dataclass `__post_init__` 中拒绝空白-only 面、重复原始面字符串、重复料箱和组大小不在 1～4；不修改原始 face。

- [ ] **Step 4: 增加 run/step SQLModel**

在 `src/app/transport/models.py` 增加两张 `wes_runtime` 表：

```python
class TransportDebugRun(BaseMixin, table=True):
    __tablename__ = "transport_debug_runs"
    id: int | None = Field(default=None, primary_key=True)
    run_id: str = Field(max_length=80)
    status: str = Field(max_length=30)
    active_scope: str | None = Field(default=None, max_length=20)
    rack_id: str = Field(max_length=100)
    configuration_json: dict[str, Any] = Field(sa_type=JSON)
    current_group_index: int = Field(default=0)
    current_phase: str = Field(max_length=40)
    current_step_ordinal: int = Field(default=0)
    attention_code: str | None = Field(default=None, max_length=120)
    attention_detail: str | None = Field(default=None, sa_type=Text)
    version: int = Field(default=1)
    claim_token: str | None = Field(default=None, max_length=80)
    claim_until: datetime | None = None
    created_by_user_id: int
    aborted_by_user_id: int | None = None
    aborted_reason: str | None = Field(default=None, sa_type=Text)
    created_at: datetime
    updated_at: datetime

class TransportDebugRunStep(BaseMixin, table=True):
    __tablename__ = "transport_debug_run_steps"
    id: int | None = Field(default=None, primary_key=True)
    run_id: str = Field(max_length=80)
    ordinal: int
    group_index: int | None = None
    phase: str = Field(max_length=40)
    status: str = Field(max_length=30)
    client_request_id: str | None = Field(default=None, max_length=120)
    transport_task_id: str | None = Field(default=None, max_length=80)
    evidence_high_watermark: int | None = None
    evidence_not_before_ms: int | None = Field(default=None, sa_type=BigInteger)
    observed_bins_json: list[dict[str, Any]] = Field(default_factory=list, sa_type=JSON)
    reason_code: str | None = Field(default=None, max_length=120)
    created_at: datetime
    updated_at: datetime
```

用 table args 增加 check/unique/index，尤其是 `active_scope` 唯一约束、状态一致性和 claim 两字段 all-or-none。`transport_task_id` 保留普通字符串而不是 cascade FK，确保历史在人工 reset 后仍可审计。

- [ ] **Step 5: 写 Alembic migration**

通过 Alembic generator 生成 revision `8f3c61e57a90`，down revision 为 `ed5ed8eb0c46`，并创建与模型完全一致的约束和索引。新增模型与现有 Transport 模型同在 `src/app/transport/models.py`，现有 Alembic 模块导入即可完成 metadata 注册，不新增第二套 registry。

Run:

```bash
uv run pytest tests/integration/transport/test_transport_debug_run_schema.py -q
```

Expected: PostgreSQL fixture 可用时 PASS；不可用时按测试基础设施规则 SKIP，不把 SKIP 当 HEAVY 验收。

- [ ] **Step 6: 实现 repository 的锁和查询**

`TransportDebugRunRepository` 提供固定接口：

- `add_run(db, run, first_step) -> None`：写入聚合和首步骤。
- `get_run(db, run_id, for_update=False) -> TransportDebugRun | None`。
- `get_active_run(db, for_update=False) -> TransportDebugRun | None`。
- `claim_active_runs(db, token, now, claim_until, limit) -> list[tuple[str, str]]`：返回 `(run_id, token)`。
- `get_claimed_run(db, run_id, token, now) -> TransportDebugRun | None`。
- `get_current_step(db, run, for_update=False) -> TransportDebugRunStep | None`。
- `add_step(db, step) -> None`、`list_steps(db, run_id) -> list[TransportDebugRunStep]`。
- `list_recent_runs(db, limit, before_id) -> list[TransportDebugRun]`。
- `list_active_mounts(db, rack_id) -> list[RackBinMount]`。
- `max_device_evidence_id(db) -> int`。
- `list_device_evidences_after(db, evidence_id, limit) -> list[InboundEvidence]`。
- `has_active_transport_binding(db, run_id) -> bool`。

`claim_active_runs` 使用 `FOR UPDATE SKIP LOCKED` 并写入 30 秒 claim lease；Evidence 查询按 `id ASC`，只读 `DEVICE_EVENT`。

- [ ] **Step 7: 运行 repository 和 schema 测试**

```bash
uv run pytest \
  tests/runtime/transport/test_transport_debug_run_contracts.py \
  tests/integration/transport/test_transport_debug_run_repository.py \
  tests/integration/transport/test_transport_debug_run_schema.py -q
```

Expected: PASS。

- [ ] **Step 8: Commit gate（需单独授权）**

```bash
git add src/app/transport/debug_run_contracts.py src/app/transport/models.py src/app/transport/debug_run_repository.py migrations/versions/20260903_1143_8f3c61e57a90_增加_transport_自动联调轮次.py tests/runtime/transport/test_transport_debug_run_contracts.py tests/integration/transport/test_transport_debug_run_schema.py tests/integration/transport/test_transport_debug_run_repository.py
git commit -m "feat(transport): 持久化自动联调轮次"
```

### Task 3: 隔离 `SCAN12` Evidence 解析边界

**Files:**
- Create: `src/app/transport/debug_run_evidence.py`
- Test: `tests/runtime/transport/test_transport_debug_run_evidence.py`

**Interfaces:**
- Consumes: 持久化 `InboundEvidence`，不调用 Device service。
- Produces: `Scan12EvidenceDisposition`、`Scan12EvidenceEvaluation`、`evaluate_scan12_evidence(evidence, high_watermark, not_before_ms, selected_bins)`。

- [ ] **Step 1: 写纯函数失败测试**

测试构造 `DEVICE_EVENT` Evidence，覆盖：

```python
evaluation = evaluate_scan12_evidence(
    evidence,
    high_watermark=100,
    not_before_ms=1_725_000_000_000,
    selected_bins=frozenset({"A000001922", "A000002653"}),
)
assert evaluation.disposition is Scan12EvidenceDisposition.MATCH
assert evaluation.bin_id == "A000001922"
assert evaluation.evidence_id == 101
```

还要逐项断言：旧 ID、旧 event timestamp、其它 device/event、其它 barcode、重复 barcode 为 `IGNORE`；`RECONCILING`、缺少 timestamp、`data.barcode` 类型错误为 `ATTENTION`；`IGNORED` 的 debug Evidence 可被接受，`PENDING` 不可推进。

- [ ] **Step 2: 运行测试确认模块缺失**

```bash
uv run pytest tests/runtime/transport/test_transport_debug_run_evidence.py -q
```

Expected: import FAIL。

- [ ] **Step 3: 实现单一窄适配器**

核心接口：

```python
class Scan12EvidenceDisposition(StrEnum):
    MATCH = "MATCH"
    IGNORE = "IGNORE"
    ATTENTION = "ATTENTION"

@dataclass(frozen=True, slots=True)
class Scan12EvidenceEvaluation:
    disposition: Scan12EvidenceDisposition
    evidence_id: int | None = None
    source_event_id: str | None = None
    bin_id: str | None = None
    reason_code: str | None = None

def evaluate_scan12_evidence(
    evidence: InboundEvidence,
    *,
    high_watermark: int,
    not_before_ms: int,
    selected_bins: frozenset[str],
) -> Scan12EvidenceEvaluation:
```

先验证 evidence ID/kind/apply status，再用 `EcsDeviceEvent.model_validate(evidence.normalized_payload)` 校验中性合同。只在 `device_code == "SCAN12"`、`event_type == "SCAN_COMPLETED"`、时间边界满足且 `data["barcode"]` 是目标非空字符串时返回 MATCH。函数不得 import Transport run service、插件或业务 workflow。

- [ ] **Step 4: 运行全部适配器测试**

```bash
uv run pytest tests/runtime/transport/test_transport_debug_run_evidence.py -q
```

Expected: PASS。

- [ ] **Step 5: Commit gate（需单独授权）**

```bash
git add src/app/transport/debug_run_evidence.py tests/runtime/transport/test_transport_debug_run_evidence.py
git commit -m "feat(transport): 解析自动联调扫码证据"
```

### Task 4: 实现轮次创建、查询和安全终止

**Files:**
- Create: `src/app/transport/debug_run_service.py`
- Test: `tests/runtime/transport/test_transport_debug_run_service.py`
- Test: `tests/integration/transport/test_transport_debug_run_service.py`

**Interfaces:**
- Consumes: `TransportDebugRunRepository`、`CreateTransportDebugRun`、`TransportService`、`audit_log_service`。
- Produces: `TransportDebugRunService.create_run()`、`get_run()`、`list_runs()`、`abort_run()` 和稳定 snapshot dataclasses。

- [ ] **Step 1: 写创建校验失败测试**

至少覆盖：

```python
with pytest.raises(TransportDebugRunConflict, match="MOUNTED"):
    await service.create_run(
        CreateTransportDebugRun(
            rack_id="510056",
            face_groups=(
                TransportDebugFaceGroup(
                    face="90",
                    bins=(TransportDebugBinSelection("A000001922", "510056A3F2C101"),),
                ),
            ),
        ),
        actor_id=7,
    )
```

另有测试证明：存在一个活动 run 时第二个创建返回 conflict；合法 mount 被逐项冻结；原始 face `" 90 "` 保持原样；first step 是 `RACK_TO_STATION/PENDING` 且具有已持久化 UUIDv7 client ID。

- [ ] **Step 2: 运行测试确认 service 缺失**

```bash
uv run pytest tests/runtime/transport/test_transport_debug_run_service.py -q
```

Expected: import FAIL。

- [ ] **Step 3: 实现创建和 snapshot**

服务构造器固定为：

```python
class TransportDebugRunService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        repository: TransportDebugRunRepository,
        transport_service: TransportService,
        *,
        clock=timezone.now_for_db,
        event_publisher: TransportDebugRunEventPublisher = event_stream_service,
    ) -> None:
```

`create_run(request, actor_id)` 在一个事务中读取 active mounts、按 `(bin_code, rack_slot_code, mount_status=MOUNTED)` 精确比较、冻结如下结构并创建 first step：

```python
configuration = {
    "rack_id": request.rack_id,
    "face_groups": [
        {
            "face": group.face,
            "bins": [{"bin_id": item.bin_id, "slot_id": item.slot_id} for item in group.bins],
        }
        for group in request.face_groups
    ],
    "storage_zone": "WH01",
    "workstation": "KT16",
    "infeed_position": "CNV0301",
    "outfeed_position": "CNV0302",
    "rack_out_template": "CTU01",
    "rack_rotate_template": "CTU02",
    "rack_return_template": "CTU03",
    "rack_return_face": "90",
}
```

数据库唯一约束的 `IntegrityError` 映射为 `TransportDebugRunConflict("an active debug run already exists")`。

- [ ] **Step 4: 实现查询分页**

稳定接口为 `get_run(run_id) -> TransportDebugRunSnapshot` 和 `list_runs(limit, cursor) -> TransportDebugRunPage`。

snapshot 包含 frozen face groups、当前 phase/group、current step/task、每组已观察 barcode、attention 字段、服务端计算的 `can_abort`、version 和 timestamps。cursor 只编码 `(created_at, id)`，非法 cursor 抛 `TransportDebugRunContractError`。

- [ ] **Step 5: 写安全 abort 测试并实现**

允许路径：

```python
result = await service.abort_run(
    "debug-run-1",
    assertion="PHYSICAL_STATE_VERIFIED",
    reason="现场确认全部机构静止，关联任务已失败终结",
    actor_id=7,
)
assert result.status is TransportDebugRunStatus.ABORTED
```

拒绝 RUNNING、错误断言、空原因、任一关联 task 非确定终态、任一 active `TransportResourceBinding`。成功时记录 audit，设置 `active_scope=None`，不修改任何 Transport task/binding/evidence。

- [ ] **Step 6: 运行 service 测试**

```bash
uv run pytest \
  tests/runtime/transport/test_transport_debug_run_service.py \
  tests/integration/transport/test_transport_debug_run_service.py -q
```

Expected: PASS。

- [ ] **Step 7: Commit gate（需单独授权）**

```bash
git add src/app/transport/debug_run_service.py tests/runtime/transport/test_transport_debug_run_service.py tests/integration/transport/test_transport_debug_run_service.py
git commit -m "feat(transport): 创建和查询自动联调轮次"
```

### Task 5: 实现纯状态机与 Transport 请求构造

**Files:**
- Create: `src/app/transport/debug_run_state_machine.py`
- Test: `tests/runtime/transport/test_transport_debug_run_state_machine.py`

**Interfaces:**
- Consumes: frozen configuration、current group/phase、Transport task/member snapshot。
- Produces: `build_debug_transport_request()`、`evaluate_debug_transport_task()`、`next_debug_step()`。

- [ ] **Step 1: 写六阶段请求矩阵失败测试**

断言 request 构造结果：

```python
assert build_debug_transport_request(run, rack_to_station_step) == MoveRackRequest(
    rack_to_station_step.client_request_id,
    TransportCaller("TRANSPORT_DEBUG", "TRANSPORT_DEBUG_AUTO"),
    "510056",
    RackReference("510056"),
    RackPosition("KT16"),
    "90",
    RcsTemplateId.CTU01,
)
```

并分别断言：

- `BINS_TO_INFEED` 把整组 `RackBinSlot(rack_id, face, slot_id)` 搬到 `CNV0301`。
- `BINS_TO_RACK` 把整组从 `CNV0302` 搬回冻结 slot。
- `ROTATE_TO_NEXT_FACE` 使用 `RackReference(rack_id)` 和下一组原始 face。
- `RACK_TO_STORAGE` 使用 `RackReference(rack_id) → ZonePosition("WH01")`、`"90"`、`CTU03`。
- `WAIT_SCAN12` 不产生 Transport request。

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest tests/runtime/transport/test_transport_debug_run_state_machine.py -q
```

Expected: module/function 缺失。

- [ ] **Step 3: 实现 request builder 和步骤规划**

使用精确接口：

```python
def build_debug_transport_request(
    run: TransportDebugRun,
    step: TransportDebugRunStep,
) -> TransportRequest | None:
```

`next_debug_step(run, completed_step)` 返回 `(next_phase, next_group_index)`：

```python
RACK_TO_STATION -> BINS_TO_INFEED(same group)
BINS_TO_INFEED -> WAIT_SCAN12(same group)
WAIT_SCAN12 -> BINS_TO_RACK(same group)
BINS_TO_RACK -> ROTATE_TO_NEXT_FACE(next group) or RACK_TO_STORAGE
ROTATE_TO_NEXT_FACE -> BINS_TO_INFEED(same new group)
RACK_TO_STORAGE -> no next step
```

创建 `BINS_TO_INFEED` step 时必须先持久化 `evidence_high_watermark` 和 `evidence_not_before_ms`。

- [ ] **Step 4: 写严格结果校验并实现**

`evaluate_debug_transport_task(step, task, members)` 返回：

```python
@dataclass(frozen=True, slots=True)
class DebugTaskEvaluation:
    disposition: Literal["WAIT", "SUCCEEDED", "FAILED", "ATTENTION"]
    reason_code: str | None = None
```

映射规则：PENDING/ACCEPTED→WAIT，REJECTED/FAILED→FAILED，RECONCILING→ATTENTION。SUCCEEDED 时逐成员精确检查：CTU01/CTU02 为 `KT16` 和目标 face；CTU03 为已知 `RACK_POSITION` 和 `"90"`；BIN_MOVE 的每个 container、target、arrival face 与冻结请求相等。任何缺少、重复、未知或不一致均为 ATTENTION。

- [ ] **Step 5: 运行状态机测试**

```bash
uv run pytest tests/runtime/transport/test_transport_debug_run_state_machine.py -q
```

Expected: PASS，且多面顺序为 `CTU01 → 面1出/扫/回 → CTU02 → 面2出/扫/回 → CTU03`。

- [ ] **Step 6: Commit gate（需单独授权）**

```bash
git add src/app/transport/debug_run_state_machine.py tests/runtime/transport/test_transport_debug_run_state_machine.py
git commit -m "feat(transport): 定义自动联调状态机"
```

### Task 6: 实现幂等推进、Evidence 集合和重启恢复

**Files:**
- Modify: `src/app/transport/debug_run_service.py`
- Modify: `src/app/transport/debug_run_repository.py`
- Test: `tests/runtime/transport/test_transport_debug_run_advancement.py`
- Test: `tests/integration/transport/test_transport_debug_run_recovery.py`

**Interfaces:**
- Consumes: Task 2～5 的 repository/contracts/evidence/state-machine，以及 `TransportService.create_debug_task_in_session()`。
- Produces: `advance_run(run_id)`、`advance_active_runs(limit)`。

- [ ] **Step 1: 写 task intent 幂等失败测试**

模拟“已提交 PENDING step、尚未绑定 task”，连续执行两轮 scanner：

```python
await service.advance_active_runs(100)
await service.advance_active_runs(100)

steps = await repository.list_steps(db, run_id)
assert [step.client_request_id for step in steps if step.phase == "RACK_TO_STATION"] == [saved_request_id]
assert await count_transport_tasks(client_request_id=saved_request_id) == 1
```

再覆盖 task 创建后进程重启、重复 callback 唤醒、两个 worker 并发 claim，只能生成一个 task。

- [ ] **Step 2: 写扫码集合失败测试**

为当前组插入 Evidence：旧 Evidence、A、A 重复、无关 C、B。逐次推进后断言：

```python
assert snapshot.observed_bin_ids == ("A000001922",)
assert snapshot.current_phase == "WAIT_SCAN12"

await insert_scan("A000002653", evidence_id=105)
await service.advance_run(run_id)
assert (await service.get_run(run_id)).current_phase == "BINS_TO_RACK"
```

RECONCILING Evidence 或载荷冲突必须进入 `NEEDS_ATTENTION`。

- [ ] **Step 3: 实现一步一事务的推进循环**

接口：

```python
async def advance_run(self, run_id: str) -> bool:
    """领取指定轮次并最多完成一次持久状态跃迁；有变化返回 True。"""

async def advance_active_runs(self, limit: int) -> int:
    """批量写入 claim lease，再逐个推进活动轮次。"""

async def _advance_claimed_run(self, run_id: str, claim_token: str) -> bool:
    """只推进已由当前 token 领取的轮次，不重复领取。"""
```

规则：

1. `advance_run()` 用随机 token 领取单个 run；`advance_active_runs()` 批量领取后逐个调用 `_advance_claimed_run()`，不得再次领取。锁定时校验 token/claim_until，再重读 current step 和权威事实。
2. `PENDING` 外部步骤若无 task：使用已持久化 client ID 调用事务内 Transport 创建并绑定。
3. 有 task：按 Task 5 结果判断 WAIT/FAILED/ATTENTION/SUCCEEDED。
4. `WAIT_SCAN12`：读取 high-watermark 后 Evidence，调用 Task 3 适配器，以 `(bin_id, evidence_id, source_event_id)` 去重并持久化集合。
5. 阶段成功时同事务创建下一 step intent；外部 step 生成并持久化一个新的 UUIDv7。
6. `RACK_TO_STORAGE` 成功时设 `COMPLETED`、`active_scope=None`。
7. 每次变化增加 run.version；本轮结束清除 claim；进程崩溃后 claim 超时才能重新领取。

- [ ] **Step 4: 实现 `NEEDS_ATTENTION` 的有限自动恢复**

只有 `TRANSPORT_RECONCILING`/`TRANSPORT_DELIVERY_UNKNOWN` attention 可以在原 task 后续变为 `SUCCEEDED` 时重新走精确校验并继续。Evidence 载荷歧义、位置不一致和未知 step 不自动清除。

- [ ] **Step 5: 运行推进和恢复测试**

```bash
uv run pytest \
  tests/runtime/transport/test_transport_debug_run_advancement.py \
  tests/integration/transport/test_transport_debug_run_recovery.py -q
```

Expected: PASS；重启后使用相同 run、step、client request identity。

- [ ] **Step 6: Commit gate（需单独授权）**

```bash
git add src/app/transport/debug_run_service.py src/app/transport/debug_run_repository.py tests/runtime/transport/test_transport_debug_run_advancement.py tests/integration/transport/test_transport_debug_run_recovery.py
git commit -m "feat(transport): 自动推进联调轮次"
```

### Task 7: 接入生产 runtime、Celery 和 run SSE

**Files:**
- Modify: `src/app/transport/composition.py`
- Modify: `src/app/transport/debug_run_service.py`
- Modify: `src/app/sys/services/event_stream_service.py`
- Modify: `src/app/sys/services/__init__.py`
- Modify: `src/celery_app/tasks/transport.py`
- Modify: `src/celery_app/config.py`
- Test: `tests/runtime/transport/test_transport_composition.py`
- Modify: `tests/deployment/test_celery_task_runtime_contract.py`
- Modify: `tests/deployment/test_wms_confirmation_dispatcher.py`
- Test: `tests/integration/test_transport_fulfillment_queue.py`
- Test: `tests/e2e/transport/test_transport_production_wiring.py`

**Interfaces:**
- Consumes: `TransportDebugRunService.advance_active_runs(100)`。
- Produces: `TransportRuntime.debug_run_service`、Celery task `advance_transport_debug_runs_batch`、Redis channel `transport:debug-run:stream`。

- [ ] **Step 1: 写 runtime/queue 失败测试**

断言：

```python
assert runtime.debug_run_service is not None
assert celery_app.conf.task_routes[
    "src.celery_app.tasks.transport.advance_transport_debug_runs_batch"
] == {"queue": "wms-fulfillment"}
assert celery_app.conf.beat_schedule["advance-transport-debug-runs-batch"]["schedule"] == 10.0
```

生产接线测试还需证明 dedicated `wms-fulfillment` worker 能构造该 service，而不要求 DeviceCommand runtime。

- [ ] **Step 2: 运行测试确认失败**

```bash
uv run pytest \
  tests/runtime/transport/test_transport_composition.py \
  tests/integration/test_transport_fulfillment_queue.py -q
```

Expected: 新 runtime 属性和 task route 不存在。

- [ ] **Step 3: 装配服务和发布通知**

`build_transport_runtime()` 创建一个 `TransportDebugRunRepository` 和 `TransportDebugRunService`，注入同一个 `session_factory`、`TransportService` 和 `event_stream_service`。在 event stream service 定义：

```python
TRANSPORT_DEBUG_RUN_STREAM_CHANNEL = "transport:debug-run:stream"
```

run 每次 version 变化都在事务内冻结 payload、提交后发布；create、advance、attention 和 abort 统一走这一出口：

```python
{
    "run_id": run.run_id,
    "version": run.version,
    "status": run.status,
    "updated_at": timezone.to_utc(run.updated_at).isoformat(),
}
```

发布失败只记录日志，不回滚已提交的 run。

- [ ] **Step 4: 增加有界 Celery scanner**

```python
@celery_app.task(name="src.celery_app.tasks.transport.advance_transport_debug_runs_batch")
def advance_transport_debug_runs_batch(limit: int = 100) -> int:
    _require_fixed_batch(limit)

    async def _advance() -> int:
        runtime = celery_async_runtime.transport_runtime
        if runtime is None:
            raise RuntimeError("Transport runtime is unavailable in the current Celery child")
        return await runtime.debug_run_service.advance_active_runs(limit)

    return run_async(_advance)
```

在 `src/celery_app/config.py` 每 10 秒调度一次、消息 10 秒过期，并路由至 `wms-fulfillment`。不从 Device evidence service 直接 enqueue，保持基础边界中性；数据库扫描承担 callback/Evidence/restart 唤醒。

- [ ] **Step 5: 运行接线测试**

```bash
uv run pytest \
  tests/runtime/transport/test_transport_composition.py \
  tests/integration/test_transport_fulfillment_queue.py \
  tests/e2e/transport/test_transport_production_wiring.py -q
```

Expected: FAST owners PASS；E2E 在 HEAVY 环境实际执行且不得 SKIP。

- [ ] **Step 6: Commit gate（需单独授权）**

```bash
git add src/app/transport/composition.py src/app/transport/debug_run_service.py src/app/sys/services/event_stream_service.py src/app/sys/services/__init__.py src/celery_app/tasks/transport.py src/celery_app/config.py tests/runtime/transport/test_transport_composition.py tests/deployment/test_celery_task_runtime_contract.py tests/deployment/test_wms_confirmation_dispatcher.py tests/integration/test_transport_fulfillment_queue.py tests/e2e/transport/test_transport_production_wiring.py
git commit -m "feat(transport): 接入自动联调后台推进"
```

### Task 8: 暴露 Diagnostics API、RBAC 和 reset 保护

**Files:**
- Create: `src/app/transport/v1/debug_runs.py`
- Modify: `src/app/transport/v1/__init__.py`
- Modify: `src/app/transport/composition.py`
- Modify: `src/app/transport/service.py`
- Modify: `src/app/transport/debug_run_repository.py`
- Test: `tests/api/test_transport_debug_runs.py`
- Modify: `tests/api/test_qa_transport_debug_openapi_regression.py`
- Modify: `tests/integration/transport/test_transport_debug_reset.py`
- Modify: `tests/admin/test_permission_scanner.py`

**Interfaces:**
- Consumes: `TransportRuntime.debug_run_service` 和 run snapshots。
- Produces: POST/GET/list/stream/abort 五个 API；权限 `ops:transport-debug-run:list`、`ops:transport-debug-run:read`、`ops:transport-debug-run:start`、`ops:transport-debug-run:stream`、`ops:transport-debug-run:abort`。每个权限只绑定一个 method/path。

- [ ] **Step 1: 写 API/OpenAPI 失败测试**

测试路由：

```text
POST /api/v1/transport/debug-runs
GET  /api/v1/transport/debug-runs
GET  /api/v1/transport/debug-runs/stream
GET  /api/v1/transport/debug-runs/{run_id}
POST /api/v1/transport/debug-runs/{run_id}/abort
```

创建 body 使用：

```json
{
  "rack_id": "510056",
  "face_groups": [
    {"face": "90", "bins": [{"bin_id": "A000001922", "slot_id": "510056A3F2C101"}]},
    {"face": "270", "bins": [{"bin_id": "A000002653", "slot_id": "510056A2F2C101"}]}
  ]
}
```

断言额外字段 422、第二个 active run 409、runtime 缺失 503、非法合同 400、未知 run 404、创建 202。

- [ ] **Step 2: 运行 API 测试确认路由缺失**

```bash
uv run pytest tests/api/test_transport_debug_runs.py -q
```

Expected: 404 或 import FAIL。

- [ ] **Step 3: 实现 Pydantic schemas 和 route**

`debug_runs.py` 明确定义 `TransportDebugRunBinRequest`、`TransportDebugRunFaceGroupRequest`、`CreateTransportDebugRunRequest`、`AbortTransportDebugRunRequest`、`TransportDebugRunBinResponse`、`TransportDebugRunFaceGroupResponse`、`TransportDebugRunStepResponse`、`TransportDebugRunResponse`、`TransportDebugRunPageResponse` 和 `TransportDebugRunUpdated`。所有输入使用 `extra="forbid"`。`_FACE` 不启用 strip；model validator 仅拒绝 `not face.strip()`。route 权限：

```python
Depends(RequirePermission("ops:transport-debug-run:list"))
Depends(RequirePermission("ops:transport-debug-run:read"))
Depends(RequirePermission("ops:transport-debug-run:start"))
Depends(RequirePermission("ops:transport-debug-run:stream"))
Depends(RequirePermission("ops:transport-debug-run:abort"))
```

创建和 abort 从 `request.state.user_id` 传 actor。`/stream` 必须在 `/{run_id}` 之前声明，输出事件 `transport_debug_run.updated`，并保持 heartbeat/no-cache/no-buffering 与现有 SSE 一致。

- [ ] **Step 4: 保护现有 reset**

在 `TransportService.reset_debug_task()` 锁定 task 后，通过注入的 debug-run guard 查询是否被 `RUNNING`/`NEEDS_ATTENTION` run step 引用。production composition 必须让 `TransportService` guard 与 `TransportDebugRunService` 复用同一个 `TransportDebugRunRepository`；默认构造器也提供真实 guard，不得把 `None` 解释为跳过保护。若是，抛：

```python
TransportContractError("active transport debug run task cannot be reset")
```

补测试证明 COMPLETED/FAILED/ABORTED 历史允许既有人工 reset，活动轮次拒绝且未删除任何记录。

- [ ] **Step 5: 运行 API、权限和 reset 测试**

```bash
uv run pytest \
  tests/api/test_transport_debug_runs.py \
  tests/api/test_qa_transport_debug_openapi_regression.py \
  tests/integration/transport/test_transport_debug_reset.py \
  tests/admin/test_permission_scanner.py -q
```

Expected: PASS；OpenAPI 中 rotate position 是 `RACK | RACK_POSITION` discriminator union，run schema 字段稳定。

- [ ] **Step 6: Commit gate（需单独授权）**

```bash
git add src/app/transport/v1/debug_runs.py src/app/transport/v1/__init__.py src/app/transport/composition.py src/app/transport/service.py src/app/transport/debug_run_repository.py tests/api/test_transport_debug_runs.py tests/api/test_qa_transport_debug_openapi_regression.py tests/integration/transport/test_transport_debug_reset.py tests/admin/test_permission_scanner.py
git commit -m "feat(transport): 提供自动联调诊断接口"
```

### Task 9: 完成单面/多面闭环集成测试和 Mock 验收

**Files:**
- Create: `tests/integration/transport/test_transport_debug_auto_run.py`
- Modify: `tests/mock/test_wms_transport_mock_server.py`
- Modify: `tests/e2e/transport/test_transport_production_wiring.py`
- Create: `docs/integration/transport-joint-acceptance.md`

**Interfaces:**
- Consumes: 完整 API/service/worker、WMS callback、Device Evidence。
- Produces: 单面、多面、失败、恢复的可执行 acceptance evidence。

- [ ] **Step 1: 写单面 happy-path 集成测试**

测试按权威事件逐步断言 task 顺序：

```python
assert created_kinds == ["RACK_MOVE"]
await apply_wms_success(kind="RACK_MOVE", final_position="KT16", arrival_face="90")
assert created_kinds == ["RACK_MOVE", "BIN_MOVE"]
await apply_wms_success(kind="BIN_MOVE", final_position="CNV0301")
await persist_scan12("A000001922")
assert current_phase == "WAIT_SCAN12"
await persist_scan12("A000002653")
assert latest_task_targets() == ["510056A3F2C101", "510056A2F2C101"]
await apply_wms_success(kind="BIN_MOVE", final_positions=original_slots)
assert latest_template() == "CTU03"
await apply_wms_success(kind="RACK_MOVE", final_position="WH01-01", arrival_face="90")
assert run_status == "COMPLETED"
```

`WH01-01` 是测试中的 WMS 精确 `RACK_POSITION`，不是 WES 映射。

- [ ] **Step 2: 写多面和 fail-closed 集成测试**

两组 `"90"`/`"270"` 必须只生成一次 CTU02，且 CTU03 只能在第二组回架 task 成功后出现。分别注入 `DELIVERY_UNKNOWN`、回调 face 不一致、`position_unknown=true`、旧 scan、重复 scan、Evidence RECONCILING，断言无后继 task。

- [ ] **Step 3: 运行完整功能测试**

```bash
uv run pytest \
  tests/integration/transport/test_transport_debug_auto_run.py \
  tests/mock/test_wms_transport_mock_server.py \
  tests/e2e/transport/test_transport_production_wiring.py -q
```

Expected: FAST/Mock PASS；E2E 由 HEAVY 环境无 SKIP 执行。

- [ ] **Step 4: 更新联调验收文档**

文档明确分开：代码/Mock 通过、部署完成、现场真实 SCAN12 schema 确认、RCS/WMS/ECS 物理闭环和业务验收。不得把前三者写成现场完成。

- [ ] **Step 5: Commit gate（需单独授权）**

```bash
git add tests/integration/transport/test_transport_debug_auto_run.py tests/mock/test_wms_transport_mock_server.py tests/e2e/transport/test_transport_production_wiring.py docs/integration/transport-joint-acceptance.md
git commit -m "test(transport): 验证自动联调闭环"
```

### Task 10: 更新 HEAVY selector 并执行后端最终门禁

**Files:**
- Modify: `docs/architecture/heavy-test-impact.toml`
- Modify: `docs/superpowers/specs/2026-09-02-transport-debug-auto-run-design.md` only if implementation revealed a reviewed contract correction
- Test: `scripts/select_heavy_tests.py`

**Interfaces:**
- Consumes: 本计划所有后端变更。
- Produces: 可重复的 FAST/QUALITY/HEAVY 证据和干净 scope diff。

- [ ] **Step 1: 为所有新增 runtime 文件增加 HEAVY 映射**

增加精确 mapping：

```toml
[[mapping]]
source_glob = "src/app/transport/debug_run_{contracts,evidence,repository,service,state_machine}.py"
heavy_tests = [
  "tests/e2e/transport/test_transport_production_wiring.py",
  "tests/integration/transport/test_transport_debug_auto_run.py",
  "tests/integration/transport/test_transport_debug_run_recovery.py",
  "tests/integration/transport/test_transport_debug_run_schema.py",
]

[[mapping]]
source_glob = "migrations/versions/20260903_1143_8f3c61e57a90_增加_transport_自动联调轮次.py"
heavy_tests = [
  "tests/integration/test_initial_schema_baseline_postgresql.py",
  "tests/integration/transport/test_transport_debug_auto_run.py",
  "tests/integration/transport/test_transport_debug_run_schema.py",
]
```

把 `src/app/transport/v1/{__init__.py,tasks.py,evidence_stream.py,debug_runs.py}` 和 `src/app/transport/{models.py,repository.py}` 现有 mapping 纳入新增 route/test owner，不使用未经 hash 的 `heavy_tests=[]`。

- [ ] **Step 2: 运行 focused suite**

```bash
uv run pytest \
  tests/runtime/transport \
  tests/api/test_transport_tasks.py \
  tests/api/test_transport_debug_runs.py \
  tests/api/test_qa_transport_debug_openapi_regression.py \
  tests/contracts/wms_adapter \
  tests/mock/test_wms_transport_mock_server.py \
  tests/integration/transport/test_transport_debug_auto_run.py -q
```

Expected: PASS。

- [ ] **Step 3: 运行静态质量门禁**

```bash
uv run ruff format --check src/app/transport src/app/wms_adapter tests/runtime/transport tests/api/test_transport_debug_runs.py tests/contracts/wms_adapter
uv run ruff check src/app/transport src/app/wms_adapter tests/runtime/transport tests/api/test_transport_debug_runs.py tests/contracts/wms_adapter
./scripts/basedpyright-local.sh src/app/transport src/app/wms_adapter
./scripts/git-quality-gate.sh --profile quality
```

Expected: 全部 exit 0。

- [ ] **Step 4: 运行 selector 和 HEAVY**

```bash
uv run python scripts/select_heavy_tests.py --base origin/develop
./scripts/run_selected_heavy_local.sh --base origin/develop
```

Expected: selector 成功、选择的每个 HEAVY 测试执行且无 skip/failure。

- [ ] **Step 5: 最终 scope 检查**

```bash
git diff --check
git status --short
git diff --stat "$(git merge-base origin/develop HEAD)" HEAD
```

Expected: 只有本计划列出的 Transport、WMS mock/contract、migration、test 和文档文件；没有 frontend 文件、现场配置、部署变更或用户其它 dirty 文件。

- [ ] **Step 6: Final commit gate（需单独授权）**

```bash
git add docs/architecture/heavy-test-impact.toml
git commit -m "chore(test): 登记自动联调重型测试"
```

完成后停在后端 Review/commit/push/PR 授权门槛。前端不得从该 dirty feature checkout 冻结合同；必须等后端候选合入并在干净 `develop` 上可验证后，再执行配套前端计划。
