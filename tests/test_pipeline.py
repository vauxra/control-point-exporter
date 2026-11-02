"""Acceptance checks for workbook fidelity and transactional runs."""
from datetime import datetime
from pathlib import Path
from threading import Event

from openpyxl import load_workbook
import pytest

from control_point_exporter import exporter
from control_point_exporter.exporter import ExportError, WorkbookExporter
from control_point_exporter.models import ControlPoint, Issue


def point(value="00001"):
    return ControlPoint("subject", value, "00042", "=SUM(A1:A2)", "C:\\EADs\\á finding aid.xml")


def test_workbook_text_styles_headers_and_issues(tmp_path):
    writer = WorkbookExporter(tmp_path)
    writer.append_points([point(), point("=1+1"), point("+123"), point("@name")])
    writer.append_issues([Issue("example.xml", "Warning", "Empty subject (line 12)")])
    path = writer.finish()
    book = load_workbook(path)
    assert book.sheetnames == ["Control points", "Issues"]
    sheet = book["Control points"]
    assert tuple(c.value for c in sheet[1]) == exporter.POINT_HEADERS
    assert [sheet.cell(i, 2).value for i in range(2, 6)] == ["00001", "=1+1", "+123", "@name"]
    assert all(sheet.cell(i, j).data_type == "s" for i in range(2, 6) for j in range(1, 6))
    assert sheet["C2"].value == "00042"
    assert sheet["D2"].value == "=SUM(A1:A2)"
    assert sheet.freeze_panes == "A2" and sheet.auto_filter.ref == "A1:E5"
    assert sheet["B2"].alignment.wrap_text
    assert sheet["A1"].font.bold and sheet.row_dimensions[1].height == 24
    assert list(book["Issues"].values)[1] == ("example.xml", "Warning", "Empty subject (line 12)")
    book.close()


def test_empty_workbook_and_clean_issue_sheet(tmp_path):
    writer = WorkbookExporter(tmp_path)
    path = writer.finish()
    book = load_workbook(path)
    assert list(book["Control points"].values) == [exporter.POINT_HEADERS]
    assert list(book["Issues"].values) == [exporter.ISSUE_HEADERS]
    book.close()


def test_filename_collision_keeps_existing(tmp_path, monkeypatch):
    class Clock:
        @staticmethod
        def now(tz=None):
            return datetime(2026, 11, 2, 14, 35, 7, tzinfo=tz)
    monkeypatch.setattr(exporter, "datetime", Clock)
    first = WorkbookExporter(tmp_path).finish()
    original = first.read_bytes()
    second = WorkbookExporter(tmp_path).finish()
    third = WorkbookExporter(tmp_path).finish()
    assert first.name == "control point - 2026-11-02_14-35-07.xlsx"
    assert second.name.endswith(" (2).xlsx") and third.name.endswith(" (3).xlsx")
    assert first.read_bytes() == original


@pytest.mark.parametrize("value", ["x" * 32768, "😀" * 16384, "bad\x01value"])
def test_cell_limits_never_silently_truncate(tmp_path, value):
    writer = WorkbookExporter(tmp_path)
    with pytest.raises(ExportError):
        writer.append_points([point(value)])
    writer.abort()
    assert not list(tmp_path.iterdir())


def test_excel_row_limit_is_checked(tmp_path, monkeypatch):
    monkeypatch.setattr(exporter, "MAX_ROWS", 2)
    writer = WorkbookExporter(tmp_path)
    with pytest.raises(ExportError, match="row limit"):
        writer.append_points([point(), point()])
    writer.abort()


def test_cancellation_after_save_before_publish_cleans_up(tmp_path, monkeypatch):
    cancel = Event()
    writer = WorkbookExporter(tmp_path)
    writer.append_points([point()])
    original = writer._save_workbook
    def save_then_cancel(path):
        original(path)
        cancel.set()
    monkeypatch.setattr(writer, "_save_workbook", save_then_cancel)
    with pytest.raises(exporter.ExportCancelled):
        writer.finish(cancel)
    writer.abort()
    assert not list(tmp_path.iterdir())


def test_destination_write_failure_cleans_up(tmp_path, monkeypatch):
    writer = WorkbookExporter(tmp_path)
    def denied(*args, **kwargs):
        raise PermissionError("Access denied")
    monkeypatch.setattr(exporter.tempfile, "mkstemp", denied)
    with pytest.raises(ExportError, match="Check permissions"):
        writer.finish()
    writer.abort()
    assert not list(tmp_path.iterdir())


def test_partial_zip_failure_closes_handles_and_cleans_all_temp_files(tmp_path, monkeypatch):
    from openpyxl.worksheet import _writer as worksheet_writer
    from openpyxl.writer.excel import ExcelWriter
    from control_point_exporter.runner import run_export

    sheet_temps = tmp_path / "sheet temps"
    sheet_temps.mkdir()
    destination = tmp_path / "output"
    destination.mkdir()
    original_tempfile = worksheet_writer.NamedTemporaryFile

    def create_sheet_temp(*args, **kwargs):
        kwargs["dir"] = sheet_temps
        return original_tempfile(*args, **kwargs)

    def interrupted_write(writer):
        writer._archive.writestr("partial.xml", "<partial/>")
        raise PermissionError("Simulated write failure after opening the ZIP")

    monkeypatch.setattr(worksheet_writer, "NamedTemporaryFile", create_sheet_temp)
    monkeypatch.setattr(ExcelWriter, "write_data", interrupted_write)
    source = write_ead(tmp_path / "source.xml")
    with pytest.raises(ExportError, match="Check permissions"):
        run_export([source], destination)
    assert not list(destination.iterdir())
    assert not list(sheet_temps.iterdir())


def write_ead(path, term="World"):
    path.write_text(f'<ead><eadheader><eadid>urn:example.collection.0007</eadid><filedesc><titlestmt><titleproper>Header title</titleproper></titlestmt></filedesc></eadheader><archdesc><did><unittitle>Collection title</unittitle><unitdate>1900-1910</unitdate></did><controlaccess><subject>{term}</subject><subject>{term}</subject></controlaccess></archdesc></ead>', encoding="utf-8")
    return path


def test_mixed_batch_preserves_sources_duplicates_and_continues(tmp_path):
    from control_point_exporter.runner import run_export
    folder = tmp_path / "EADs with spaces á"
    folder.mkdir()
    first = write_ead(folder / "first.xml", "=1+1")
    malformed = folder / "bad.xml"
    malformed.write_text("<ead>", encoding="utf-8")
    last = write_ead(folder / "last.xml", "Other")
    result = run_export([first, malformed, last], folder)
    assert result.files_processed == 3 and result.failures == 1 and result.entries == 4
    book = load_workbook(result.workbook)
    rows = list(book["Control points"].values)[1:]
    assert [r[4] for r in rows] == [str(first.resolve())] * 2 + [str(last.resolve())] * 2
    assert rows[0] == rows[1] and rows[2] == rows[3]
    assert rows[0][1:3] == ("=1+1", "0007")
    assert rows[0][3] == "Collection title 1900-1910"
    issues = list(book["Issues"].values)[1:]
    assert issues and all(r[0] == str(malformed.resolve()) for r in issues)
    book.close()


def test_cancel_between_files_retains_no_workbook(tmp_path):
    from control_point_exporter.runner import run_export
    first = write_ead(tmp_path / "first.xml")
    last = write_ead(tmp_path / "last.xml")
    cancel = Event()
    def progress(event):
        if event.kind == "file_finished":
            cancel.set()
    result = run_export([first, last], tmp_path, cancel, progress)
    assert result.cancelled and result.files_processed == 1 and result.workbook is None
    assert not list(tmp_path.glob("*.xlsx")) and not list(tmp_path.glob(".control-point-*"))


def test_cancel_before_start(tmp_path):
    from control_point_exporter.runner import run_export
    cancel = Event()
    cancel.set()
    result = run_export([], tmp_path, cancel)
    assert result.cancelled and not list(tmp_path.iterdir())


def test_all_failed_inputs_still_publish_issue_workbook(tmp_path):
    from control_point_exporter.runner import run_export
    bad = tmp_path / "bad.xml"
    bad.write_text("<not-ead/>", encoding="utf-8")
    result = run_export([bad], tmp_path)
    assert result.entries == 0 and result.failures == 1
    book = load_workbook(result.workbook)
    assert list(book["Control points"].values) == [exporter.POINT_HEADERS]
    assert len(list(book["Issues"].values)) > 1
    book.close()


def test_output_folder_must_exist(tmp_path):
    with pytest.raises(ExportError, match="does not exist"):
        WorkbookExporter(tmp_path / "missing")
