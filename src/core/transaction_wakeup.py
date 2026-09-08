"""事务提交后发布无业务快照的唤醒；失败由既有 Beat 扫描兜底。"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, SessionTransaction

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)
_KEY = "_transaction_wakeups"
_pending: set[asyncio.Future[None]] = set()


def defer_wakeup(db: object, wake: Callable[[], None]) -> None:
    """同一事务内合并唤醒，savepoint 回滚不污染外层事务。"""
    session = db.sync_session if isinstance(db, AsyncSession) else db
    # 领域端口的内存替身不拥有 SQLAlchemy 事务，也不发布生产队列消息。
    if not isinstance(session, Session):
        return
    transaction = session.get_nested_transaction() or session.get_transaction()
    if transaction is None:
        raise RuntimeError("defer_wakeup requires an active transaction")
    pending = session.info.setdefault(_KEY, {})
    pending.setdefault(transaction, {})[wake] = None


def publish_wakeup(wake: Callable[[], None]) -> None:
    """已结束事务的同步 worker 续批；消息丢失仍可由扫描恢复。"""
    _publish((wake,))


def _publish(wakes: tuple[Callable[[], None], ...]) -> None:
    for wake in wakes:
        try:
            wake()
        except Exception:
            logger.exception("wakeup.publish_failed; periodic scan will recover")


@event.listens_for(Session, "after_commit")
def _after_commit(session: Session) -> None:
    transaction = session.get_nested_transaction() or session.get_transaction()
    pending = session.info.get(_KEY, {})
    wakes = pending.pop(transaction, {})
    if transaction is not None and transaction.nested:
        pending.setdefault(transaction.parent, {}).update(wakes)
        return
    if not wakes:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _publish(tuple(wakes))
    else:
        # 不让同步 broker I/O 占用 API/worker 的事件循环或数据库连接。
        task = loop.run_in_executor(None, _publish, tuple(wakes))
        _pending.add(task)
        task.add_done_callback(_pending.discard)


@event.listens_for(Session, "after_transaction_end")
def _after_transaction_end(session: Session, transaction: SessionTransaction) -> None:
    pending = session.info.get(_KEY, {})
    pending.pop(transaction, None)
    if transaction.parent is None:
        session.info.pop(_KEY, None)


__all__ = ["defer_wakeup", "publish_wakeup"]
