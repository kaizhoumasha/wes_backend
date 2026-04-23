from types import SimpleNamespace

import pytest

from src.workline_runtime.device_target_resolver import resolve_command_target
from src.workline_runtime.types import CommandIntent, CommandTargetScope


def _device(
    device_id: int,
    *,
    code: str,
    role: str,
    upstream_device_id: int | None = None,
    role_index: int = 0,
    sort_order: int = 0,
):
    return SimpleNamespace(
        id=device_id,
        device_code=code,
        device_role=role,
        upstream_device_id=upstream_device_id,
        role_index=role_index,
        sort_order=sort_order,
    )


class TestResolveCommandTarget:
    def test_resolve_current_scope_to_source_device(self):
        source = _device(10, code="ARM-01", role="INPUT_ARM")

        target = resolve_command_target(
            command_intent=CommandIntent(
                action="PICK_AND_PUT",
                target_scope=CommandTargetScope.CURRENT,
                device_role="INPUT_ARM",
            ),
            source_device=source,
            devices=[source],
        )

        assert target is source

    def test_resolve_current_scope_without_role(self):
        source = _device(10, code="ARM-01", role="INPUT_ARM")

        target = resolve_command_target(
            command_intent=CommandIntent(
                action="PICK_AND_PUT",
                target_scope=CommandTargetScope.CURRENT,
            ),
            source_device=source,
            devices=[source],
        )

        assert target is source

    def test_resolve_unique_downstream_without_role(self):
        source = _device(10, code="ARM-01", role="INPUT_ARM")
        downstream = _device(20, code="CONVEYOR-01", role="CONVEYOR", upstream_device_id=10)

        target = resolve_command_target(
            command_intent=CommandIntent(
                action="MOVE_FORWARD",
                target_scope=CommandTargetScope.DOWNSTREAM,
            ),
            source_device=source,
            devices=[source, downstream],
        )

        assert target is downstream

    def test_resolve_unique_downstream_device(self):
        source = _device(10, code="ARM-01", role="INPUT_ARM")
        downstream = _device(20, code="CONVEYOR-01", role="CONVEYOR", upstream_device_id=10)

        target = resolve_command_target(
            command_intent=CommandIntent(
                action="MOVE_FORWARD",
                target_scope=CommandTargetScope.DOWNSTREAM,
                device_role="CONVEYOR",
            ),
            source_device=source,
            devices=[source, downstream],
        )

        assert target is downstream

    def test_resolve_downstream_prefers_sorted_role_match(self):
        source = _device(10, code="SCANNER-01", role="SCANNER")
        downstream_b = _device(
            21,
            code="ARM-02",
            role="INPUT_ARM",
            upstream_device_id=10,
            role_index=2,
            sort_order=2,
        )
        downstream_a = _device(
            20,
            code="ARM-01",
            role="INPUT_ARM",
            upstream_device_id=10,
            role_index=1,
            sort_order=1,
        )

        with pytest.raises(ValueError, match="Ambiguous command target"):
            resolve_command_target(
                command_intent=CommandIntent(
                    action="MEASUREMENT_REEL",
                    target_scope=CommandTargetScope.DOWNSTREAM,
                    device_role="INPUT_ARM",
                ),
                source_device=source,
                devices=[source, downstream_b, downstream_a],
            )

    def test_raise_when_downstream_is_ambiguous_without_role(self):
        source = _device(10, code="SCANNER-01", role="SCANNER")
        downstream_a = _device(20, code="ARM-01", role="INPUT_ARM", upstream_device_id=10)
        downstream_b = _device(21, code="CONVEYOR-01", role="CONVEYOR", upstream_device_id=10)

        with pytest.raises(ValueError, match="Ambiguous command target"):
            resolve_command_target(
                command_intent=CommandIntent(
                    action="MOVE_FORWARD",
                    target_scope=CommandTargetScope.DOWNSTREAM,
                ),
                source_device=source,
                devices=[source, downstream_a, downstream_b],
            )

    def test_raise_when_no_matching_downstream_role(self):
        source = _device(10, code="ARM-01", role="INPUT_ARM")
        downstream = _device(20, code="CONVEYOR-01", role="CONVEYOR", upstream_device_id=10)

        with pytest.raises(ValueError, match="No command target matched"):
            resolve_command_target(
                command_intent=CommandIntent(
                    action="PICK_AND_PUT",
                    target_scope=CommandTargetScope.DOWNSTREAM,
                    device_role="OUTPUT_ARM",
                ),
                source_device=source,
                devices=[source, downstream],
            )

    def test_resolve_explicit_target_device_id(self):
        source = _device(10, code="ARM-01", role="INPUT_ARM")
        downstream = _device(20, code="CONVEYOR-01", role="CONVEYOR", upstream_device_id=10)

        target = resolve_command_target(
            command_intent=CommandIntent(action="MOVE_FORWARD", target_device_id=20),
            source_device=source,
            devices=[source, downstream],
        )

        assert target is downstream
