"""Shared sequential conversion pipeline for the GUI and packaged CLI."""
import logging
from pathlib import Path
from threading import Event
from typing import Callable, Sequence

from .exporter import WorkbookExporter, ExportCancelled, check_cancel
from .extractor import extract_file
from .models import ExtractionResult, FileSummary, Issue, RunEvent, RunResult

logger = logging.getLogger("control_point_exporter")


def run_export(inputs: Sequence[Path], output_dir: Path, cancel_event: Event | None = None,
               on_event: Callable[[RunEvent], None] | None = None) -> RunResult:
    paths = [Path(p).expanduser().resolve() for p in inputs]
    result = RunResult(files_total=len(paths))
    exporter = None

    def emit(kind: str, message: str, **kwargs):
        logger.info(message)
        if on_event is not None:
            try:
                on_event(RunEvent(kind, message, **kwargs))
            except Exception:
                logger.exception("Progress callback failed")

    emit("log", f"Run started: {len(paths)} file(s); output folder: {output_dir}")
    try:
        check_cancel(cancel_event)
        exporter = WorkbookExporter(output_dir)
        for path in paths:
            check_cancel(cancel_event)
            emit("file_started", f"Reading {path}", source_file=str(path),
                 current=result.files_processed, total=len(paths))
            try:
                extracted = extract_file(path)
            except Exception as exc:
                logger.exception("Unexpected extraction error for %s", path)
                extracted = ExtractionResult(str(path), issues=[Issue(str(path), "Error", f"Unexpected extraction error: {exc}")])
            check_cancel(cancel_event)
            exporter.append_points(extracted.points, cancel_event)
            exporter.append_issues(extracted.issues, cancel_event)
            result.entries += len(extracted.points)
            result.warnings += sum(i.severity.lower() == "warning" for i in extracted.issues)
            result.failures += int(extracted.failed)
            for issue in extracted.issues:
                emit("log", f"{issue.severity}: {path}: {issue.problem}", source_file=str(path))
            result.files_processed += 1
            status = "Failed" if extracted.failed else "Warning" if extracted.issues else "Done"
            result.file_results.append(FileSummary(str(path), len(extracted.points), status))
            emit("file_finished", f"{status}: {path} — {len(extracted.points):,} control point(s)",
                 source_file=str(path), rows=len(extracted.points), status=status,
                 current=result.files_processed, total=len(paths))
        check_cancel(cancel_event)
        emit("log", "Writing Excel workbook…")
        result.workbook = exporter.finish(cancel_event)
        emit("completed", f"Completed{' with issues' if result.warnings or result.failures else ''}: "
             f"{result.files_processed:,}/{result.files_total:,} file(s), {result.entries:,} control point(s), "
             f"{result.warnings:,} warning(s), {result.failures:,} failed file(s). Saved: {result.workbook}", result=result)
        return result
    except ExportCancelled:
        result.cancelled = True
        emit("completed", f"Canceled after {result.files_processed:,}/{result.files_total:,} file(s). No workbook was published.", result=result)
        return result
    except Exception:
        logger.exception("Export failed; no workbook published")
        raise
    finally:
        if exporter is not None:
            exporter.abort()
