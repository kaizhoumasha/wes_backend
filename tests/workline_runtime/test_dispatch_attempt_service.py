from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


class _AttemptRepoStub:
    def __init__(self) -> None:
        self.created: object | None = None
        self.create = AsyncMock(side_effect=self._create)
        self.get_by_lease_token = AsyncMock(side_effect=self._get_by_lease_token)

    async def _create(self, _db: object, data: dict[str, object]) -> object:
        self.created = SimpleNamespace(id=12, **data)
        return self.created

    async def _get_by_lease_token(self, _db: object, lease_token: str) -> object | None:
        if self.created is not None and self.created.lease_token == lease_token:
            return self.created
        return None


@pytest.mark.asyncio
async def test_dispatch_attempt_service_creates_lease_and_finalizes_success() -> None:
    from src.app.workline.services.dispatch_attempt_service import WorklineDispatchAttemptService

    repo = _AttemptRepoStub()
    service = WorklineDispatchAttemptService(repository=repo)  # type: ignore[arg-type]
    outbox = SimpleNamespace(id=7, dispatch_key="device-command:CMD-1", attempt_count=2)

    attempt = await service.create_attempt(object(), outbox=outbox, auto_commit=False)

    assert attempt.outbox_id == 7
    assert attempt.dispatch_key == "device-command:CMD-1"
    assert attempt.attempt_no == 3
    assert attempt.status == "DISPATCHING"
    assert attempt.lease_token.startswith("dispatch-attempt:7:3:")

    finalized = await service.finalize_attempt(
        object(),
        lease_token=attempt.lease_token,
        success=True,
        response={"status_code": 200},
        auto_commit=False,
    )

    assert finalized is attempt
    assert finalized.status == "SENT"
    assert finalized.finalized_at is not None
    assert finalized.response_json == {"status_code": 200}


@pytest.mark.asyncio
async def test_dispatch_attempt_service_rejects_unknown_lease() -> None:
    from src.app.workline.services.dispatch_attempt_service import WorklineDispatchAttemptService

    service = WorklineDispatchAttemptService(repository=_AttemptRepoStub())  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="派发尝试不存在"):
        await service.finalize_attempt(
            object(),
            lease_token="missing",
            success=False,
            error_message="timeout",
            auto_commit=False,
        )
