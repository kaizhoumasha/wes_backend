from unittest.mock import Mock

import pytest
from sqlalchemy.orm import Session

from src.core.transaction_wakeup import defer_wakeup


def test_commit_wakes_once_and_never_before_commit():
    wake = Mock()
    with Session() as db:
        with db.begin():
            defer_wakeup(db, wake)
            defer_wakeup(db, wake)
            wake.assert_not_called()
        wake.assert_called_once_with()


def test_rollback_discards_wakeup_and_session_can_be_reused():
    wake = Mock()
    with Session() as db:
        with pytest.raises(ValueError), db.begin():
            defer_wakeup(db, wake)
            raise ValueError("rollback")
        with db.begin():
            pass
    wake.assert_not_called()


def test_savepoint_commit_waits_for_outer_commit():
    wake = Mock()
    with Session() as db, db.begin():
        with db.begin_nested():
            defer_wakeup(db, wake)
        wake.assert_not_called()
    wake.assert_called_once_with()


def test_savepoint_rollback_preserves_outer_wakeup():
    outer, inner = Mock(), Mock()
    with Session() as db, db.begin():
        defer_wakeup(db, outer)
        with pytest.raises(ValueError), db.begin_nested():
            defer_wakeup(db, inner)
            raise ValueError("rollback savepoint")
    outer.assert_called_once_with()
    inner.assert_not_called()


def test_outer_rollback_discards_committed_savepoint():
    wake = Mock()
    with Session() as db:
        with pytest.raises(ValueError), db.begin():
            with db.begin_nested():
                defer_wakeup(db, wake)
            raise ValueError("rollback outer")
    wake.assert_not_called()


def test_broker_failure_does_not_fail_committed_transaction(caplog):
    broken = Mock(side_effect=ConnectionError("broker down"))
    good = Mock()
    with Session() as db, db.begin():
        defer_wakeup(db, broken)
        defer_wakeup(db, good)
    good.assert_called_once_with()
    assert "wakeup.publish_failed" in caplog.text


def test_session_close_discards_uncommitted_wakeup():
    wake = Mock()
    with Session() as db:
        db.begin()
        defer_wakeup(db, wake)
    wake.assert_not_called()


def test_worker_runner_does_not_need_another_message_to_publish():
    import asyncio
    import threading

    published = threading.Event()

    async def commit():
        with Session() as db, db.begin():
            defer_wakeup(db, published.set)

    with asyncio.Runner() as runner:
        runner.run(commit())
        assert published.wait(timeout=2), "wake must publish while the worker loop is idle"
