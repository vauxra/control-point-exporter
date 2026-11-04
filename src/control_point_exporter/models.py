"""Shared records used by extraction, export, CLI and GUI."""
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ControlPoint:
    type: str
    source: str
    value: str
    finding_aid_id: str
    finding_aid_title: str
    source_file: str


@dataclass(frozen=True, slots=True)
class Issue:
    source_file: str
    severity: str
    problem: str


@dataclass(slots=True)
class ExtractionResult:
    source_file: str
    points: list[ControlPoint] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    version: str = ""

    @property
    def failed(self) -> bool:
        return any(i.severity.lower() == "error" for i in self.issues)


@dataclass(frozen=True, slots=True)
class FileSummary:
    source_file: str
    rows: int
    status: str


@dataclass(slots=True)
class RunResult:
    workbook: Path | None = None
    files_total: int = 0
    files_processed: int = 0
    entries: int = 0
    warnings: int = 0
    failures: int = 0
    cancelled: bool = False
    file_results: list[FileSummary] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class RunEvent:
    kind: str
    message: str
    source_file: str = ""
    rows: int = 0
    status: str = ""
    current: int = 0
    total: int = 0
    result: RunResult | None = None
