"""C1 re-export shim (workline.repositories → runtime.orchestration.repositories)."""

import src.app.workline.repositories.dispatch_attempt_repository as _mod
from src.app.workline.repositories.dispatch_attempt_repository import *  # noqa: F403


def __getattr__(name: str):  # PEP 562: 转发非 __all__ 符号
    return getattr(_mod, name)
