"""Exercise the real module entry point, its exit codes, reports and artifacts."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from openpyxl import load_workbook


FIXTURES = Path(__file__).parent / "fixtures"


def invoke(tmp_path, *arguments):
    environment = os.environ.copy()
    environment["LOCALAPPDATA"] = str(tmp_path / "local app data")
    environment["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "control_point_exporter", "--headless", *map(str, arguments)],
        cwd=tmp_path, env=environment, capture_output=True, encoding="utf-8", timeout=30,
    )


def test_cli_unicode_batch_reopens_workbook_and_report(tmp_path):
    source_folder = tmp_path / "Finding aids García"
    source_folder.mkdir()
    first = source_folder / "é-first.xml"
    second = source_folder / "é-second.xml"
    shutil.copyfile(FIXTURES / "ead2002.xml", first)
    shutil.copyfile(FIXTURES / "ead3.xml", second)
    report_path = tmp_path / "résultats.json"
    completed = invoke(tmp_path, "--output-dir", tmp_path, "--report", report_path, first, second)
    assert completed.returncode == 0, completed.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["files_total"] == report["files_processed"] == 2
    assert report["entries"] == 13
    assert report["failures"] == report["warnings"] == 0
    book = load_workbook(report["workbook"])
    rows = list(book["Control points"].values)[1:]
    assert len(rows) == 13
    assert rows[0][2:5] == ("00042", "García & Sons 1900-1910", str(first.resolve()))
    assert rows[-1][2:5] == ("E3-0007", "EAD3 collection 1945-1950", str(second.resolve()))
    assert len(list(book["Issues"].values)) == 1
    book.close()


def test_cli_partial_failure_returns_one_but_publishes_good_rows(tmp_path):
    report_path = tmp_path / "report.json"
    completed = invoke(tmp_path, "--output-dir", tmp_path, "--report", report_path,
                       FIXTURES / "malformed.xml", FIXTURES / "ead2002.xml")
    assert completed.returncode == 1, completed.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["failures"] == 1 and report["files_processed"] == 2
    assert report["entries"] == 11
    assert [item["status"] for item in report["file_results"]] == ["Failed", "Done"]
    book = load_workbook(report["workbook"])
    assert all(row[0] == str((FIXTURES / "malformed.xml").resolve()) for row in list(book["Issues"].values)[1:])
    assert len(list(book["Control points"].values)) == 12
    book.close()


def test_cli_missing_xml_is_file_error_with_issue_workbook(tmp_path):
    missing = tmp_path / "missing.xml"
    report_path = tmp_path / "report.json"
    completed = invoke(tmp_path, "--output-dir", tmp_path, "--report", report_path, missing)
    assert completed.returncode == 1, completed.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["files_processed"] == report["failures"] == 1
    assert report["entries"] == 0
    book = load_workbook(report["workbook"])
    assert len(list(book["Control points"].values)) == 1
    issue = list(book["Issues"].values)[1]
    assert issue[0] == str(missing.resolve()) and issue[1] == "Error"
    assert "Cannot read XML" in issue[2]
    book.close()


def test_cli_no_inputs_produces_actionable_fatal_report(tmp_path):
    report_path = tmp_path / "report.json"
    completed = invoke(tmp_path, "--output-dir", tmp_path, "--report", report_path)
    assert completed.returncode == 2
    assert not list(tmp_path.glob("*.xlsx"))
    assert "requires" in completed.stderr
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert "error" in report and "requires" in report["error"]


def test_cli_no_output_directory_produces_fatal_report(tmp_path):
    report_path = tmp_path / "report.json"
    completed = invoke(tmp_path, "--report", report_path, FIXTURES / "ead2002.xml")
    assert completed.returncode == 2
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert "--output-dir" in report["error"]


def test_cli_empty_folder_produces_fatal_report(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    report_path = tmp_path / "report.json"
    completed = invoke(tmp_path, "--output-dir", tmp_path, "--report", report_path, empty)
    assert completed.returncode == 2
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert "No XML files" in report["error"]
    assert not list(tmp_path.glob("*.xlsx"))


def test_cli_report_cannot_overwrite_an_input_xml(tmp_path):
    source = tmp_path / "source.xml"
    shutil.copyfile(FIXTURES / "ead2002.xml", source)
    original = source.read_bytes()
    completed = invoke(tmp_path, "--output-dir", tmp_path, "--report", source, source)
    assert completed.returncode == 2
    assert source.read_bytes() == original
    assert not list(tmp_path.glob("*.xlsx"))


def test_cli_report_cannot_overwrite_a_workbook(tmp_path):
    existing = tmp_path / "existing.xlsx"
    existing.write_bytes(b"preserve existing workbook")
    completed = invoke(tmp_path, "--output-dir", tmp_path, "--report", existing, FIXTURES / "ead2002.xml")
    assert completed.returncode == 2
    assert existing.read_bytes() == b"preserve existing workbook"
    assert list(tmp_path.glob("*.xlsx")) == [existing]


def test_cli_existing_json_report_is_preserved(tmp_path):
    report_path = tmp_path / "existing.json"
    report_path.write_text('{"previous":"preserve"}', encoding="utf-8")
    completed = invoke(tmp_path, "--output-dir", tmp_path, "--report", report_path,
                       FIXTURES / "ead2002.xml")
    assert completed.returncode == 2
    assert report_path.read_text(encoding="utf-8") == '{"previous":"preserve"}'
    assert not list(tmp_path.glob("*.xlsx"))


def test_cli_unwritable_report_location_stops_before_export(tmp_path):
    report_path = tmp_path / "missing parent" / "report.json"
    completed = invoke(tmp_path, "--output-dir", tmp_path, "--report", report_path,
                       FIXTURES / "ead2002.xml")
    assert completed.returncode == 2
    assert not list(tmp_path.glob("*.xlsx"))
    assert "Error:" in completed.stderr
