from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .result import ActionResult


class ActionModule(ABC):
    def resume_after_pause(self, duration_s: float) -> None:
        """Exclude a mission pause from this action's elapsed-time limits."""

    @abstractmethod
    def start(self, params: dict[str, Any] | None = None) -> None:
        ...

    @abstractmethod
    def update(self, context: dict[str, Any] | None = None) -> ActionResult:
        ...

    @abstractmethod
    def stop(self) -> None:
        ...

    @abstractmethod
    def reset(self) -> None:
        ...
