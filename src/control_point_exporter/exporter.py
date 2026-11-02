"""Streaming XLSX writing and non-overwriting publication."""
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import tempfile
from threading import Event
from zipfile import ZIP_DEFLATED, ZipFile

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.writer.excel import ExcelWriter

from .models import ControlPoint, Issue

POINT_HEADERS = ("Control Point Type", "Control Point Value", "Finding Aid ID",
                 "Finding Aid Title", "Source File")
ISSUE_HEADERS = ("Source File", "Severity", "Problem")
MAX_ROWS = 1_048_576
MAX_CELL_LENGTH = 32_767
_ILLEGAL = re.compile(r"[\x00-\x08\x0b-\x0c\x0e-\x1f]")


class ExportError(Exception):
    """The workbook cannot be safely written."""


class ExportCancelled(Exception):
    """Cancellation before publication."""


def check_cancel(cancel_event: Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ExportCancelled()


class WorkbookExporter:
    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir).expanduser().resolve()
        if not self.output_dir.is_dir():
            raise ExportError(f"Output folder does not exist: {self.output_dir}. Choose an existing folder.")
        self.book = Workbook(write_only=True)
        self.points = self.book.create_sheet("Control points")
        self.issues = self.book.create_sheet("Issues")
        self.point_count = 0
        self.issue_count = 0
        self.temp_path: Path | None = None
        self.closed = False
        self._prepare(self.points, POINT_HEADERS, (23, 60, 25, 60, 85))
        self._prepare(self.issues, ISSUE_HEADERS, (85, 14, 100))

    def _prepare(self, sheet, headers, widths):
        sheet.freeze_panes = "A2"
        sheet.sheet_view.showGridLines = False
        from openpyxl.utils import get_column_letter
        for i, width in enumerate(widths, 1):
            sheet.column_dimensions[get_column_letter(i)].width = width
        cells = self._cells(sheet, headers)
        for cell in cells:
            cell.font = Font(name="Calibri", size=12, bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="17365D")
        sheet.row_dimensions[1].height = 24
        sheet.append(cells)

    def _cells(self, sheet, values):
        cells = []
        for value in values:
            text = str(value)
            if len(text) > MAX_CELL_LENGTH or len(text.encode("utf-16-le")) // 2 > MAX_CELL_LENGTH:
                raise ExportError(f"An Excel cell exceeds {MAX_CELL_LENGTH:,} characters on '{sheet.title}'. Nothing was published.")
            if _ILLEGAL.search(text):
                raise ExportError(f"A value contains a control character Excel cannot store on '{sheet.title}'. Nothing was published.")
            cell = WriteOnlyCell(sheet, value=text)
            # Override automatic formula detection, including strings beginning '='.
            cell.data_type = "s"
            cell.number_format = "@"
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            cell.font = Font(name="Calibri", size=11)
            cells.append(cell)
        return cells

    def append_points(self, points: list[ControlPoint], cancel_event: Event | None = None):
        for point in points:
            check_cancel(cancel_event)
            if self.point_count + 2 > MAX_ROWS:
                raise ExportError(f"Control points exceed Excel's {MAX_ROWS - 1:,} data-row limit. Export a smaller batch.")
            self.points.append(self._cells(self.points, (point.type, point.value, point.finding_aid_id,
                                                        point.finding_aid_title, point.source_file)))
            self.point_count += 1

    def append_issues(self, issues: list[Issue], cancel_event: Event | None = None):
        for issue in issues:
            check_cancel(cancel_event)
            if self.issue_count + 2 > MAX_ROWS:
                raise ExportError("Issues exceed Excel's worksheet row limit. Export a smaller batch.")
            self.issues.append(self._cells(self.issues, (issue.source_file, issue.severity, issue.problem)))
            self.issue_count += 1

    def _save_workbook(self, path: Path) -> None:
        # Workbook.save() does not close its ZIP archive if writing fails. Own
        # the archive so Windows releases its handle before cleanup, even when
        # disk space or permissions change partway through a save.
        self.book.properties.modified = datetime.now(timezone.utc).replace(tzinfo=None)
        with ZipFile(path, "w", ZIP_DEFLATED, allowZip64=True) as archive:
            ExcelWriter(self.book, archive).save()

    def finish(self, cancel_event: Event | None = None) -> Path:
        check_cancel(cancel_event)
        self.points.auto_filter.ref = f"A1:E{self.point_count + 1}"
        self.issues.auto_filter.ref = f"A1:C{self.issue_count + 1}"
        try:
            fd, name = tempfile.mkstemp(prefix=".control-point-", suffix=".xlsx", dir=self.output_dir)
            os.close(fd)
            self.temp_path = Path(name)
            self._save_workbook(self.temp_path)
            self.closed = True
            check_cancel(cancel_event)
            base = f"control point - {datetime.now():%Y-%m-%d_%H-%M-%S}"
            attempt = 1
            while True:
                check_cancel(cancel_event)
                suffix = "" if attempt == 1 else f" ({attempt})"
                destination = self.output_dir / f"{base}{suffix}.xlsx"
                try:
                    if os.name == "nt":
                        # Windows rename is atomic and fails rather than replacing.
                        self.temp_path.rename(destination)
                    else:
                        os.link(self.temp_path, destination)
                        self.temp_path.unlink()
                    self.temp_path = None
                    return destination
                except FileExistsError:
                    attempt += 1
        except OSError as exc:
            raise ExportError(f"Cannot write workbook in {self.output_dir}: {exc}. Check permissions, free space, and whether the destination is available.") from exc
        finally:
            if self.temp_path is not None:
                self.temp_path.unlink(missing_ok=True)

    def abort(self):
        # Closing write-only streams and removing their temporary XML avoids leaks
        # when a batch is canceled before Workbook.save().
        if not self.closed:
            for sheet in self.book.worksheets:
                if not sheet.closed:
                    sheet.close()
                writer = getattr(sheet, "_writer", None)
                if writer is not None and Path(writer.out).exists():
                    writer.cleanup()
            self.book.close()
            self.closed = True
        if self.temp_path is not None:
            self.temp_path.unlink(missing_ok=True)
            self.temp_path = None
