"""C1 re-export shim (workline.models → runtime.orchestration.models)."""

import src.app.workline.models.object_transition_event as _mod
from src.app.workline.models.object_transition_event import *  # noqa: F403


def __getattr__(name: str):  # PEP 562: 转发非 __all__ 符号
    return getattr(_mod, name)
