"""独立插件 SDK、核心与具体插件依赖方向门禁。"""

from __future__ import annotations

import ast
import dataclasses
import importlib
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import get_args

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SDK_ROOT = REPO_ROOT / "packages/wes_plugin_sdk"
SDK_SOURCE_ROOT = SDK_ROOT / "src"
PLUGIN_ROOT = REPO_ROOT / "workline_plugins"
GUARDRAIL = REPO_ROOT / "scripts/architecture-guardrails.sh"


def _load_sdk():
    if not SDK_SOURCE_ROOT.is_dir():
        pytest.fail("packages/wes_plugin_sdk 尚未建立")
    source_root = str(SDK_SOURCE_ROOT)
    if source_root not in sys.path:
        sys.path.insert(0, source_root)
    return importlib.import_module("wes_plugin_sdk")


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imports.add(node.module)
    return imports


def test_plugin_sdk_is_an_independent_stdlib_only_package() -> None:
    if not SDK_ROOT.is_dir():
        pytest.fail("packages/wes_plugin_sdk 尚未建立")

    pyproject = tomllib.loads((SDK_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["dependencies"] == []
    assert pyproject["project"]["requires-python"] == ">=3.13"

    allowed_roots = sys.stdlib_module_names | {"wes_plugin_sdk"}
    offenders = {
        path.relative_to(REPO_ROOT).as_posix(): sorted(
            imported for imported in _imports(path) if imported.split(".", maxsplit=1)[0] not in allowed_roots
        )
        for path in SDK_SOURCE_ROOT.rglob("*.py")
        if any(imported.split(".", maxsplit=1)[0] not in allowed_roots for imported in _imports(path))
    }
    assert offenders == {}


def test_plugin_sdk_exposes_only_the_approved_fact_and_decision_categories() -> None:
    sdk = _load_sdk()

    assert hasattr(sdk, "FactReference")
    assert sdk.Fact is sdk.FactReference
    assert all(
        issubclass(fact_type, sdk.FactReference)
        for fact_type in (
            sdk.DeviceResultReadyFact,
            sdk.EvidenceReadyFact,
            sdk.RecoveryDecidedFact,
            sdk.TransportResultReadyFact,
            sdk.WmsResultReadyFact,
        )
    )
    assert {decision.__name__ for decision in get_args(sdk.Decision)} == {
        "CompleteExecution",
        "CreateDeviceCommand",
        "CreateTransportTask",
        "CreateWmsConfirmation",
        "DeferExecution",
        "PauseForReconciliation",
        "Wait",
    }

    decision = sdk.DeferExecution(
        material_execution_id="execution-1",
        fact_id="fact-1",
        reason_code="DEVICE_BUSY",
    )
    assert decision.material_execution_id == "execution-1"
    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.reason_code = "CHANGED"

    recovery = sdk.RecoveryDecidedFact(
        fact_id="fact-1",
        evidence_id="evidence-1",
        fact_version="1.0",
        material_execution_id="execution-1",
        recovery_id="recovery-1",
        decision=sdk.RecoveryDecision.ABORT,
        authoritative_position=None,
        reason_code="MATERIAL_MISSING",
    )
    assert recovery.recovery_id == "recovery-1"


def test_plugin_frozen_fact_subclass_can_carry_typed_decision_data() -> None:
    sdk = _load_sdk()
    fact_reference = getattr(sdk, "FactReference", None)
    assert fact_reference is not None

    @dataclasses.dataclass(frozen=True, slots=True)
    class BusinessResultFact(sdk.WmsResultReadyFact):
        outcome: str

    @sdk.handler(fact_type=BusinessResultFact, name="business-result", supported_versions=("1.0",))
    def handle(fact: BusinessResultFact):
        reason_code = "RESULT_ACCEPTED" if fact.outcome == "ACCEPTED" else "RESULT_REJECTED"
        return sdk.CompleteExecution(
            material_execution_id=fact.material_execution_id,
            fact_id=fact.fact_id,
            reason_code=reason_code,
        )

    fact = BusinessResultFact(
        fact_id="fact-1",
        evidence_id="evidence-1",
        fact_version="1.0",
        material_execution_id="execution-1",
        operation_id="operation-1",
        outcome="ACCEPTED",
    )

    assert isinstance(fact, fact_reference)
    assert handle(fact).reason_code == "RESULT_ACCEPTED"
    with pytest.raises(dataclasses.FrozenInstanceError):
        fact.outcome = "CHANGED"


def test_handler_rejects_fact_types_without_own_frozen_slots_dataclass_contract() -> None:
    sdk = _load_sdk()
    fact_reference = getattr(sdk, "FactReference", None)
    assert fact_reference is not None

    class OrdinaryFact:
        pass

    @dataclasses.dataclass
    class MutableFact:
        result: str

    @dataclasses.dataclass(frozen=True, slots=True)
    class FrozenFactWithoutMarker:
        result: str

    class UndeclaredFactSubclass(fact_reference):
        pass

    for invalid_fact_type in (OrdinaryFact, MutableFact, FrozenFactWithoutMarker, UndeclaredFactSubclass):
        with pytest.raises(TypeError):
            sdk.handler(fact_type=invalid_fact_type, name="invalid", supported_versions=("1.0",))


def test_handler_rejects_mutable_subclass_spoofing_dataclass_params() -> None:
    sdk = _load_sdk()

    class SpoofedFact(sdk.FactReference):
        __dataclass_params__ = sdk.EvidenceReadyFact.__dataclass_params__

    fact = SpoofedFact("fact-1", "evidence-1", "1.0", "execution-1")
    fact.mutable_values = []
    assert fact.__dict__ == {"mutable_values": []}

    with pytest.raises(TypeError):
        sdk.handler(fact_type=SpoofedFact, name="spoofed", supported_versions=("1.0",))


def test_handler_rejects_fact_with_slot_outside_copied_dataclass_fields() -> None:
    sdk = _load_sdk()

    class SlotSpoofFact(sdk.FactReference):
        __slots__ = ("mutable_values",)
        __dataclass_params__ = sdk.EvidenceReadyFact.__dataclass_params__
        __dataclass_fields__ = sdk.EvidenceReadyFact.__dataclass_fields__

    fact = SlotSpoofFact("fact-1", "evidence-1", "1.0", "execution-1")
    fact.mutable_values = []
    assert fact.mutable_values == []
    assert not hasattr(fact, "__dict__")

    with pytest.raises(TypeError):
        sdk.handler(fact_type=SlotSpoofFact, name="slot-spoofed", supported_versions=("1.0",))


def test_plugin_sdk_values_are_frozen_and_transport_keeps_business_identity_separate() -> None:
    sdk = _load_sdk()
    fact = sdk.EvidenceReadyFact(
        fact_id="fact-1",
        evidence_id="evidence-1",
        fact_version="1.0",
        material_execution_id="execution-1",
    )
    source = sdk.TransportRackPosition(location_code="RACK-SOURCE")
    target = sdk.TransportRackPosition(location_code="RACK-TARGET")
    decision = sdk.CreateTransportTask(
        material_execution_id="execution-1",
        fact_id=fact.fact_id,
        task_type=sdk.TransportTaskType.RACK_MOVE,
        rack_replacement_id="replacement-1",
        leg=sdk.TransportLeg.OLD_OUT,
        current_rack_id="rack-1",
        rack_id="rack-1",
        source=source,
        target=target,
        target_face=sdk.RackFace.A,
    )

    assert dataclasses.is_dataclass(fact)
    assert dataclasses.is_dataclass(decision)
    with pytest.raises(dataclasses.FrozenInstanceError):
        fact.fact_id = "changed"
    assert decision.business_identity == ("replacement-1", sdk.TransportLeg.OLD_OUT)
    assert not hasattr(decision, "client_request_id")


def test_device_decision_uses_stable_strings_not_plugin_business_enums() -> None:
    sdk = _load_sdk()

    assert not hasattr(sdk, "DeviceRole")
    assert not hasattr(sdk, "DeviceTaskType")
    assert not hasattr(sdk, "DeviceLocationType")

    source = sdk.DevicePosition(
        location_id="SOURCE",
        location_type="PLUGIN_SOURCE",
        material_trace_id="trace-1",
    )
    target = sdk.DevicePosition(
        location_id="TARGET",
        location_type="PLUGIN_TARGET",
        material_trace_id="trace-1",
    )
    decision = sdk.CreateDeviceCommand(
        material_execution_id="execution-1",
        fact_id="fact-1",
        device_role="PLUGIN_DEVICE",
        task_type="PLUGIN_ACTION",
        material_trace_id="trace-1",
        source=source,
        target=target,
    )

    assert decision.device_role == "PLUGIN_DEVICE"
    assert decision.task_type == "PLUGIN_ACTION"


def test_tuple_fields_and_nested_values_reject_mutable_or_duck_typed_inputs() -> None:
    sdk = _load_sdk()
    source = sdk.DevicePosition(
        location_id="SOURCE",
        location_type="PLUGIN_SOURCE",
        material_trace_id="trace-1",
    )
    target = sdk.DevicePosition(
        location_id="TARGET",
        location_type="PLUGIN_TARGET",
        material_trace_id="trace-1",
    )

    class MutablePosition:
        material_trace_id = "trace-1"

    class MutableRackPosition:
        location_code = "RACK-SOURCE"

    class MutableBinding:
        device_role = "PLUGIN_DEVICE"

    with pytest.raises(TypeError):
        sdk.CreateDeviceCommand(
            material_execution_id="execution-1",
            fact_id="fact-1",
            device_role="PLUGIN_DEVICE",
            task_type="PLUGIN_ACTION",
            material_trace_id="trace-1",
            source=MutablePosition(),
            target=target,
        )
    with pytest.raises(TypeError):
        sdk.CreateWmsConfirmation(
            material_execution_id="execution-1",
            fact_id="fact-1",
            operation="operation@v1",
            operation_id="operation-1",
            evidence_refs=["evidence-1"],
            snapshot_refs=("snapshot-1",),
        )
    with pytest.raises(TypeError):
        sdk.PauseForReconciliation(
            material_execution_id="execution-1",
            fact_id="fact-1",
            reason_code="RECONCILE",
            affected_resource_ids=["resource-1"],
        )
    with pytest.raises(TypeError):
        sdk.CreateTransportTask(
            material_execution_id="execution-1",
            fact_id="fact-1",
            task_type=sdk.TransportTaskType.RACK_MOVE,
            rack_replacement_id="replacement-1",
            leg=sdk.TransportLeg.OLD_OUT,
            current_rack_id="rack-1",
            rack_id="rack-1",
            source=MutableRackPosition(),
            target=sdk.TransportRackPosition(location_code="RACK-TARGET"),
            target_face=sdk.RackFace.A,
        )
    with pytest.raises(TypeError):
        sdk.HandlerMetadata(
            fact_type=sdk.EvidenceReadyFact,
            name="invalid-versions",
            supported_versions=["1.0"],
        )

    binding = sdk.DeviceBindingSnapshot(
        device_role="PLUGIN_DEVICE",
        device_code="device-1",
        contract_key="contract-1",
        contract_version="1.0",
    )
    position_binding = sdk.PositionBindingSnapshot(
        position_role="PIPELINE_INLET",
        location_id="LOCATION-IN",
        location_type="RACK_CELL",
    )
    with pytest.raises(TypeError):
        sdk.EpochConfigurationSnapshot(
            line_run_epoch_id="epoch-1",
            workline_code="line-1",
            plugin_key="plugin-1",
            plugin_version="1.0",
            config_digest="config-digest",
            topology_digest="topology-digest",
            device_bindings=[binding],
            position_bindings=(position_binding,),
        )
    with pytest.raises(TypeError):
        sdk.EpochConfigurationSnapshot(
            line_run_epoch_id="epoch-1",
            workline_code="line-1",
            plugin_key="plugin-1",
            plugin_version="1.0",
            config_digest="config-digest",
            topology_digest="topology-digest",
            device_bindings=(MutableBinding(),),
            position_bindings=(position_binding,),
        )

    external_bindings = [binding]
    snapshot = sdk.EpochConfigurationSnapshot(
        line_run_epoch_id="epoch-1",
        workline_code="line-1",
        plugin_key="plugin-1",
        plugin_version="1.0",
        config_digest="config-digest",
        topology_digest="topology-digest",
        device_bindings=tuple(external_bindings),
        position_bindings=(position_binding,),
    )
    external_bindings.clear()
    assert snapshot.device_bindings == (binding,)
    with pytest.raises(dataclasses.FrozenInstanceError):
        source.location_id = "CHANGED"


def test_plugin_sdk_exposes_typed_frozen_runtime_snapshots() -> None:
    sdk = _load_sdk()
    execution = sdk.ExecutionSnapshot(
        material_execution_id="execution-1",
        material_trace_id="trace-1",
        line_run_epoch_id="epoch-1",
        lifecycle=sdk.ExecutionLifecycle.RUNNING,
        version=2,
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        execution.version = 3


def test_handler_decorator_only_attaches_frozen_static_metadata() -> None:
    sdk = _load_sdk()

    @sdk.handler(
        fact_type=sdk.EvidenceReadyFact,
        name="material-evidence-ready",
        supported_versions=("1.0",),
    )
    def handle(_fact):
        return sdk.Wait(
            material_execution_id="execution-1",
            fact_id="fact-1",
            reason_code="WAITING_FOR_SNAPSHOT",
        )

    assert handle.__wes_handler__ == sdk.HandlerMetadata(
        fact_type=sdk.EvidenceReadyFact,
        name="material-evidence-ready",
        supported_versions=("1.0",),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        handle.__wes_handler__.name = "changed"

    handler_module = importlib.import_module("wes_plugin_sdk.handler")
    mutable_registries = [
        name
        for name, value in vars(handler_module).items()
        if not name.startswith("__") and isinstance(value, (dict, list, set))
    ]
    assert mutable_registries == []


def test_core_and_plugin_dependency_scanners_reject_forbidden_fixture_imports(tmp_path: Path) -> None:
    fixture = tmp_path / "repo"
    sdk_file = fixture / "packages/wes_plugin_sdk/src/wes_plugin_sdk/bad.py"
    sdk_dynamic_file = fixture / "packages/wes_plugin_sdk/src/wes_plugin_sdk/dynamic.py"
    sdk_alias_file = fixture / "packages/wes_plugin_sdk/src/wes_plugin_sdk/alias_evasions.py"
    sdk_registry_file = fixture / "packages/wes_plugin_sdk/src/wes_plugin_sdk/registry_call.py"
    sdk_instance_file = fixture / "packages/wes_plugin_sdk/src/wes_plugin_sdk/dependency_instance.py"
    sdk_assignment_alias_file = fixture / "packages/wes_plugin_sdk/src/wes_plugin_sdk/assignment_alias.py"
    sdk_path_receiver_file = fixture / "packages/wes_plugin_sdk/src/wes_plugin_sdk/path_receiver.py"
    sdk_import_time_file = fixture / "packages/wes_plugin_sdk/src/wes_plugin_sdk/import_time_contexts.py"
    sdk_function_flow_file = fixture / "packages/wes_plugin_sdk/src/wes_plugin_sdk/function_flow.py"
    sdk_function_overwrite_file = fixture / "packages/wes_plugin_sdk/src/wes_plugin_sdk/function_overwrite.py"
    sdk_safe_then_alias_file = fixture / "packages/wes_plugin_sdk/src/wes_plugin_sdk/safe_then_alias.py"
    sdk_local_safe_override_file = fixture / "packages/wes_plugin_sdk/src/wes_plugin_sdk/local_safe_override.py"
    sdk_import_time_dynamic_file = fixture / "packages/wes_plugin_sdk/src/wes_plugin_sdk/import_time_dynamic.py"
    core_file = fixture / "src/app/core.py"
    core_dynamic_file = fixture / "src/app/core_dynamic.py"
    core_constant_file = fixture / "src/app/core_constant.py"
    core_unknown_module_file = fixture / "src/app/core_unknown_module.py"
    core_function_flow_file = fixture / "src/app/core_function_flow.py"
    core_function_overwrite_file = fixture / "src/app/core_function_overwrite.py"
    core_local_safe_override_file = fixture / "src/app/core_local_safe_override.py"
    plugin_file = fixture / "workline_plugins/demo/src/demo/bad.py"
    plugin_dynamic_file = fixture / "workline_plugins/demo/src/demo/dynamic.py"
    plugin_constant_file = fixture / "workline_plugins/demo/src/demo/constant.py"
    plugin_unknown_module_file = fixture / "workline_plugins/demo/src/demo/unknown_module.py"
    plugin_function_flow_file = fixture / "workline_plugins/demo/src/demo/function_flow.py"
    plugin_local_safe_override_file = fixture / "workline_plugins/demo/src/demo/local_safe_override.py"
    for path in (
        sdk_file,
        sdk_dynamic_file,
        sdk_alias_file,
        sdk_registry_file,
        sdk_instance_file,
        sdk_assignment_alias_file,
        sdk_path_receiver_file,
        sdk_import_time_file,
        sdk_function_flow_file,
        sdk_function_overwrite_file,
        sdk_safe_then_alias_file,
        sdk_local_safe_override_file,
        sdk_import_time_dynamic_file,
        core_file,
        core_dynamic_file,
        core_constant_file,
        core_unknown_module_file,
        core_function_flow_file,
        core_function_overwrite_file,
        core_local_safe_override_file,
        plugin_file,
        plugin_dynamic_file,
        plugin_constant_file,
        plugin_unknown_module_file,
        plugin_function_flow_file,
        plugin_local_safe_override_file,
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
    sdk_file.write_text("import fastapi\n", encoding="utf-8")
    sdk_dynamic_file.write_text(
        "from importlib.metadata import entry_points\nHANDLERS = []\nentry_points()\n",
        encoding="utf-8",
    )
    sdk_alias_file.write_text(
        "import builtins as b\n"
        "from importlib import import_module as load\n"
        "from importlib.metadata import entry_points as eps\n"
        "from os import walk as traverse\n"
        "from pathlib import Path as P\n"
        "from pkgutil import iter_modules as scan\n"
        "b.__import__('hidden')\n"
        "load('hidden')\n"
        "eps()\n"
        "traverse('.')\n"
        "P('.').glob('*')\n"
        "P('.').rglob('*')\n"
        "scan()\n",
        encoding="utf-8",
    )
    sdk_registry_file.write_text(
        "from collections import defaultdict\n"
        "DICT_REGISTRY = dict()\n"
        "LIST_REGISTRY = list()\n"
        "SET_REGISTRY = set()\n"
        "DEFAULT_REGISTRY = defaultdict(list)\n",
        encoding="utf-8",
    )
    sdk_instance_file.write_text(
        "from concurrent.futures import ThreadPoolExecutor\nEXECUTOR = ThreadPoolExecutor()\n",
        encoding="utf-8",
    )
    sdk_assignment_alias_file.write_text(
        "from importlib import import_module\nload = import_module\nload('hidden')\n",
        encoding="utf-8",
    )
    sdk_path_receiver_file.write_text(
        "import glob as glob_module\n"
        "import os\n"
        "from pathlib import Path\n"
        "path = Path('.')\n"
        "path.rglob('*')\n"
        "Path('.').iterdir()\n"
        "os.scandir('.')\n"
        "glob_module.glob('*')\n"
        "glob_module.iglob('*')\n",
        encoding="utf-8",
    )
    sdk_import_time_file.write_text(
        "from collections import defaultdict\n"
        "from concurrent.futures import ThreadPoolExecutor\n"
        "def marker(value):\n    return value\n"
        "class Registries:\n    ITEMS = defaultdict(list)\n"
        "@marker(ThreadPoolExecutor())\n"
        "def decorated(client=ThreadPoolExecutor()):\n    return client\n"
        "CLIENTS = [ThreadPoolExecutor() for _ in range(1)]\n"
        "MUTABLE = [dict() for _ in range(1)]\n",
        encoding="utf-8",
    )
    sdk_function_flow_file.write_text(
        "from pathlib import Path\ndef scan():\n    path = Path('.')\n    return path.rglob('*')\n",
        encoding="utf-8",
    )
    sdk_function_overwrite_file.write_text(
        "from pathlib import Path\n"
        "def scan(safe_path):\n"
        "    path = Path('.')\n"
        "    matches = path.rglob('*')\n"
        "    path = safe_path\n"
        "    return matches\n",
        encoding="utf-8",
    )
    sdk_safe_then_alias_file.write_text(
        "from importlib import import_module\n"
        "def safe_load(module_name):\n"
        "    return module_name\n"
        "def load_safe_module():\n"
        "    load = safe_load\n"
        "    result = load('safe.module')\n"
        "    load = import_module\n"
        "    return result\n",
        encoding="utf-8",
    )
    sdk_local_safe_override_file.write_text(
        "from importlib import import_module\n"
        "load = import_module\n"
        "def safe_load(module_name):\n"
        "    return module_name\n"
        "def load_safe_module():\n"
        "    load = safe_load\n"
        "    return load('safe.module')\n",
        encoding="utf-8",
    )
    sdk_import_time_dynamic_file.write_text(
        "from importlib import import_module\n"
        "def marker(value):\n"
        "    return value\n"
        "class ImportAtDefinition:\n"
        "    MODULE = import_module('hidden.class')\n"
        "@marker(import_module('hidden.decorator'))\n"
        "def decorated(client=import_module('hidden.default')):\n"
        "    return client\n"
        "MODULES = [import_module('hidden.comprehension') for _ in range(1)]\n",
        encoding="utf-8",
    )
    core_file.write_text("import workline_plugins.demo\n", encoding="utf-8")
    core_dynamic_file.write_text(
        "from importlib import import_module as load\nload('workline_plugins.demo')\n",
        encoding="utf-8",
    )
    core_constant_file.write_text(
        "from importlib import import_module as load\n"
        "PLUGIN_MODULE = 'workline_plugins.demo'\n"
        "def resolve():\n    return load(PLUGIN_MODULE)\n",
        encoding="utf-8",
    )
    core_unknown_module_file.write_text(
        "from importlib import import_module\nimport_module(MODULE_NAME)\n",
        encoding="utf-8",
    )
    core_function_flow_file.write_text(
        "from importlib import import_module\n"
        "def load_plugin():\n"
        "    load = import_module\n"
        "    target = 'workline_plugins.demo'\n"
        "    return load(target)\n",
        encoding="utf-8",
    )
    core_function_overwrite_file.write_text(
        "from importlib import import_module\n"
        "def safe_load(module_name):\n"
        "    return module_name\n"
        "def load_plugin():\n"
        "    load = import_module\n"
        "    target = 'workline_plugins.demo'\n"
        "    result = load(target)\n"
        "    load = safe_load\n"
        "    return result\n",
        encoding="utf-8",
    )
    core_local_safe_override_file.write_text(
        "from importlib import import_module\n"
        "load = import_module\n"
        "def safe_load(module_name):\n"
        "    return module_name\n"
        "def load_safe_module():\n"
        "    load = safe_load\n"
        "    return load('workline_plugins.demo')\n",
        encoding="utf-8",
    )
    plugin_file.write_text("from src.app.device.service import DeviceService\n", encoding="utf-8")
    plugin_dynamic_file.write_text(
        "import builtins as b\nfrom importlib import import_module as load\n"
        "b.__import__('src.app.device')\nload('fastapi')\n",
        encoding="utf-8",
    )
    plugin_constant_file.write_text(
        "import builtins as b\nTARGET = 'src.app.device'\ndef resolve():\n    return b.__import__(TARGET)\n",
        encoding="utf-8",
    )
    plugin_unknown_module_file.write_text(
        "from importlib import import_module\nimport_module(MODULE_NAME)\n",
        encoding="utf-8",
    )
    plugin_function_flow_file.write_text(
        "from importlib import import_module\n"
        "def load_core():\n"
        "    load = import_module\n"
        "    target = 'src.app.device'\n"
        "    return load(target)\n",
        encoding="utf-8",
    )
    plugin_local_safe_override_file.write_text(
        "from importlib import import_module\n"
        "load = import_module\n"
        "def safe_load(module_name):\n"
        "    return module_name\n"
        "def load_safe_module():\n"
        "    load = safe_load\n"
        "    return load('src.app.device')\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.update(
        {
            "PLUGIN_BOUNDARY_GUARDRAIL_FIXTURE_ONLY": "1",
            "PLUGIN_BOUNDARY_GUARDRAIL_FIXTURE_ROOT": str(fixture),
        }
    )
    result = subprocess.run(
        ["/bin/bash", str(GUARDRAIL), "--mode", "enforced"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    def violations_for(relative_path: str) -> str:
        marker = f"  file: {relative_path}:"
        return "\n\n".join(block for block in result.stderr.split("\n\n") if marker in block)

    assert {
        "core_forbidden_call_before_overwrite_is_blocked": "workline_plugins.demo"
        in violations_for("src/app/core_function_overwrite.py"),
        "sdk_forbidden_call_before_overwrite_is_blocked": "pathlib.Path.rglob"
        in violations_for("packages/wes_plugin_sdk/src/wes_plugin_sdk/function_overwrite.py"),
        "sdk_safe_call_before_forbidden_alias_is_clean": violations_for(
            "packages/wes_plugin_sdk/src/wes_plugin_sdk/safe_then_alias.py"
        )
        == "",
    } == {
        "core_forbidden_call_before_overwrite_is_blocked": True,
        "sdk_forbidden_call_before_overwrite_is_blocked": True,
        "sdk_safe_call_before_forbidden_alias_is_clean": True,
    }
    assert {
        "core_function_local_safe_alias_is_clean": violations_for("src/app/core_local_safe_override.py") == "",
        "plugin_function_local_safe_alias_is_clean": violations_for(
            "workline_plugins/demo/src/demo/local_safe_override.py"
        )
        == "",
        "sdk_function_local_safe_alias_is_clean": violations_for(
            "packages/wes_plugin_sdk/src/wes_plugin_sdk/local_safe_override.py"
        )
        == "",
    } == {
        "core_function_local_safe_alias_is_clean": True,
        "plugin_function_local_safe_alias_is_clean": True,
        "sdk_function_local_safe_alias_is_clean": True,
    }
    assert result.returncode == 1
    assert "PLUGIN_SDK_DEPENDENCY_BOUNDARY" in result.stderr
    assert "动态扫描或全局可变集合" in result.stderr
    for forbidden_call in ("__import__", "import_module", "entry_points", "walk", "glob", "rglob", "iter_modules"):
        assert forbidden_call in result.stderr
    assert "registry_call.py" in result.stderr
    for mutable_constructor in ("builtins.dict", "builtins.list", "builtins.set", "collections.defaultdict"):
        assert mutable_constructor in result.stderr
    assert "计划外依赖实例化" in result.stderr
    assert "SDK 模块执行动态扫描/导入: importlib.import_module" in violations_for(
        "packages/wes_plugin_sdk/src/wes_plugin_sdk/assignment_alias.py"
    )
    for filesystem_scan in (
        "pathlib.Path.rglob",
        "pathlib.Path.iterdir",
        "os.scandir",
        "glob.glob",
        "glob.iglob",
    ):
        assert filesystem_scan in violations_for("packages/wes_plugin_sdk/src/wes_plugin_sdk/path_receiver.py")
    assert "concurrent.futures.ThreadPoolExecutor" in violations_for(
        "packages/wes_plugin_sdk/src/wes_plugin_sdk/import_time_contexts.py"
    )
    assert "importlib.import_module" in violations_for(
        "packages/wes_plugin_sdk/src/wes_plugin_sdk/import_time_dynamic.py"
    )
    assert "pathlib.Path.rglob" in violations_for("packages/wes_plugin_sdk/src/wes_plugin_sdk/function_flow.py")
    assert "pathlib.Path.rglob" in violations_for("packages/wes_plugin_sdk/src/wes_plugin_sdk/function_overwrite.py")
    assert violations_for("packages/wes_plugin_sdk/src/wes_plugin_sdk/safe_then_alias.py") == ""
    assert "CORE_PLUGIN_DEPENDENCY_BOUNDARY" in result.stderr
    assert "core_dynamic.py" in result.stderr
    assert "workline_plugins.demo" in violations_for("src/app/core_constant.py")
    assert "不可判定" in violations_for("src/app/core_unknown_module.py")
    assert "workline_plugins.demo" in violations_for("src/app/core_function_flow.py")
    assert "workline_plugins.demo" in violations_for("src/app/core_function_overwrite.py")
    assert "WORKLINE_PLUGIN_DEPENDENCY_BOUNDARY" in result.stderr
    assert "workline_plugins/demo/src/demo/dynamic.py" in result.stderr
    assert "src.app.device" in violations_for("workline_plugins/demo/src/demo/constant.py")
    assert "不可判定" in violations_for("workline_plugins/demo/src/demo/unknown_module.py")
    assert "src.app.device" in violations_for("workline_plugins/demo/src/demo/function_flow.py")
