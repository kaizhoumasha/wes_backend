import asyncio
import runpy
import sys
from collections.abc import Coroutine
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts.manual import redis_degradation_drill


class FakeCircuitBreaker:
    half_open_max_calls = 3


class FakeRecoveryCache:
    def __init__(self) -> None:
        self.circuit_breaker = FakeCircuitBreaker()
        self.set_calls: list[tuple[str, object]] = []
        self.state = "half_open"

    async def set(self, key: str, value: object) -> bool:
        self.set_calls.append((key, value))
        if len(self.set_calls) >= self.circuit_breaker.half_open_max_calls:
            self.state = "closed"
        return True

    def get_status(self) -> dict[str, str]:
        return {"circuit_breaker_state": self.state}


def test_cli_keyboard_interrupt_exits_with_standard_interrupt_status(monkeypatch: pytest.MonkeyPatch) -> None:
    script_path = Path(redis_degradation_drill.__file__)

    def interrupt(coroutine: Coroutine[Any, Any, Any]) -> None:
        coroutine.close()
        raise KeyboardInterrupt

    monkeypatch.setattr(sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(asyncio, "run", interrupt)

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(str(script_path), run_name="__main__")

    assert exc_info.value.code == 130


@pytest.mark.parametrize(
    ("set_result", "cached_value", "delete_result", "failure"),
    [
        (True, "expected", True, None),
        (False, "expected", True, "写入"),
        (True, "unexpected", True, "读取"),
        (True, "expected", False, "删除"),
    ],
)
def test_require_initial_cache_health_fails_closed_for_any_unhealthy_operation(
    set_result: bool,
    cached_value: str,
    delete_result: bool,
    failure: str | None,
) -> None:
    require_initial_cache_health = getattr(redis_degradation_drill, "require_initial_cache_health", None)

    assert callable(require_initial_cache_health)
    if failure is None:
        require_initial_cache_health(
            set_result=set_result,
            cached_value=cached_value,
            expected_value="expected",
            delete_result=delete_result,
        )
        return
    with pytest.raises(RuntimeError, match=failure):
        require_initial_cache_health(
            set_result=set_result,
            cached_value=cached_value,
            expected_value="expected",
            delete_result=delete_result,
        )


def test_require_circuit_state_accepts_match_and_rejects_mismatch() -> None:
    require_circuit_state = getattr(redis_degradation_drill, "require_circuit_state", None)

    assert callable(require_circuit_state)
    require_circuit_state(actual="open", expected="open", phase="故障观测")
    with pytest.raises(RuntimeError, match=r"故障观测.*预期.*open.*实际.*closed"):
        require_circuit_state(actual="closed", expected="open", phase="故障观测")


async def test_attempt_cache_recovery_uses_required_successful_writes_until_closed() -> None:
    attempt_cache_recovery = getattr(redis_degradation_drill, "attempt_cache_recovery", None)
    cache = FakeRecoveryCache()
    test_value = {"data": "test"}

    assert callable(attempt_cache_recovery)
    final_state = await attempt_cache_recovery(cache, test_key="drill:key", test_value=test_value)

    assert cache.set_calls == [("drill:key", test_value)] * cache.circuit_breaker.half_open_max_calls
    assert final_state == "closed"
