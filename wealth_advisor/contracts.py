from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


class InputError(ValueError):
    """Invalid or incomplete client financial input."""


class ToolError(RuntimeError):
    """Recoverable external tool failure."""


class CRMTool(Protocol):
    def fetch(self, client_id: str) -> dict[str, Any]: ...


class DataTool(Protocol):
    def prepare(self, raw: dict[str, Any]) -> dict[str, Any]: ...


class AnalysisTool(Protocol):
    def analyze(self, data: dict[str, Any]) -> dict[str, Any]: ...


class MemoryTool(Protocol):
    def previous(self, client_id: str) -> dict[str, Any] | None: ...
    def save(self, session: Session, result: dict[str, Any]) -> None: ...


@dataclass
class Session:
    run_id: str
    raw: dict[str, Any]
    cleaned: dict[str, Any] = field(default_factory=dict)
    curated: dict[str, Any] = field(default_factory=dict)
    history: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    trace: list[str] = field(default_factory=list)
