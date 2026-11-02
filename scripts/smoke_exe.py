"""Run a packaged EXE against fixtures with Python removed from its environment."""
from datetime import datetime, timezone
from pathlib import Path
import argparse
import hashlib
import json
import os
import subprocess
import time
import uuid

from openpyxl import load_workbook

from control_point_exporter import __version__

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, default=ROOT / "dist/windows/ControlPointExporter.exe")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts/packaged-smoke")
    args = parser.parse_args()
    exe = args.exe.resolve()
    output = args.output_dir.resolve() / f"run-{uuid.uuid4().hex[:8]}"
    inputs = output / "input with spaces — café"
    inputs.mkdir(parents=True)
    xml2 = inputs / "sample.XML"
    xml2.write_text('''<?xml version="1.0" encoding="UTF-8"?>
<ead xmlns="urn:isbn:1-931666-22-9"><eadheader><eadid>urn:taro:ttu.sw.00042</eadid><filedesc><titlestmt>
<titleproper>Finding aid header title should not be exported</titleproper></titlestmt></filedesc></eadheader><archdesc>
<did><unittitle>Café collection, <unitdate>1900-1950</unitdate></unittitle><unitdate>1900-1950</unitdate></did>
<controlaccess><subject>=1+1</subject><controlaccess><persname>Smith, <emph>Jane</emph>.</persname>
<subject>=1+1</subject></controlaccess></controlaccess></archdesc></ead>''', encoding="utf-8")
    xml3 = inputs / "ead3.xml"
    xml3.write_text('''<?xml version="1.0" encoding="UTF-16"?>
<e:ead xmlns:e="http://ead3.archivists.org/schema/"><e:control><e:recordid>record.series.003</e:recordid>
<e:filedesc><e:titlestmt><e:titleproper>EAD3 header title should not be exported</e:titleproper></e:titlestmt></e:filedesc></e:control>
<e:archdesc><e:did><e:unittitle>EAD3 collection,</e:unittitle><e:unitdatestructured>
<e:daterange><e:fromdate>1930</e:fromdate><e:todate>1940</e:todate></e:daterange></e:unitdatestructured></e:did>
<e:controlaccess><e:geogname><e:part>Texas</e:part><e:part>History</e:part>
<e:geographiccoordinates>31.0,-100.0</e:geographiccoordinates></e:geogname></e:controlaccess></e:archdesc></e:ead>''', encoding="utf-16")
    invalid = inputs / "malformed.xml"
    invalid.write_text("<ead><broken>", encoding="utf-8")
    env = os.environ.copy()
    for key in list(env):
        if key.upper().startswith("PYTHON") or key.upper() in {"VIRTUAL_ENV", "TCL_LIBRARY", "TK_LIBRARY"}:
            env.pop(key)
    env["PATH"] = str(Path(env.get("SystemRoot", r"C:\Windows")) / "System32")
    checks = []

    def convert(label: str, files: list[Path], expected_exit: int) -> tuple[dict, object]:
        destination = output / label
        destination.mkdir()
        report_path = destination / "result.json"
        start = time.perf_counter()
        process = subprocess.run([str(exe), "--headless", "--output-dir", str(destination),
                                  "--report", str(report_path), *map(str, files)],
                                 cwd=destination, env=env, timeout=120, capture_output=True, text=True)
        assert process.returncode == expected_exit, (label, process.returncode, process.stdout, process.stderr)
        report = json.loads(report_path.read_text(encoding="utf-8"))
        assert report.get("frozen") is True, "The report did not come from the packaged runtime."
        assert report.get("version") == __version__, "The EXE version does not match the source being verified."
        book = load_workbook(report["workbook"], data_only=False)
        checks.append({"scenario": label, "exit_code": process.returncode,
                       "entries": report["entries"], "workbook": report["workbook"],
                       "seconds": round(time.perf_counter() - start, 3)})
        return report, book

    report, book = convert("single", [xml2], 0)
    try:
        rows = list(book["Control points"].values)[1:]
        assert len(rows) == 3 and report["entries"] == 3
        assert [r[1] for r in rows] == ["=1+1", "Smith, Jane.", "=1+1"]
        assert all(r[2] == "00042" and r[3] == "Café collection, 1900-1950" and r[4] == str(xml2) for r in rows)
        assert all(book["Control points"].cell(r, 2).data_type == "s" for r in (2, 3, 4))
        assert book["Issues"].max_row == 1
        assert book["Control points"].freeze_panes == "A2"
        assert book["Control points"].auto_filter.ref == "A1:E4"
    finally:
        book.close()
    report, book = convert("mixed", [xml2, xml3, invalid], 1)
    try:
        rows = list(book["Control points"].values)[1:]
        assert len(rows) == 4 and report["files_processed"] == 3 and report["failures"] == 1
        assert rows[-1] == ("geogname", "Texas | History", "003", "EAD3 collection, 1930 - 1940", str(xml3))
        issues = list(book["Issues"].values)[1:]
        assert len(issues) == 1 and issues[0][0] == str(invalid) and str(issues[0][1]).lower() == "error"
    finally:
        book.close()
    report, book = convert("invalid-only", [invalid], 1)
    try:
        assert report["entries"] == 0 and book["Control points"].max_row == 1
        assert book["Issues"].max_row == 2
    finally:
        book.close()
    summary = {
        "passed": True, "checked_utc": datetime.now(timezone.utc).isoformat(), "exe": str(exe),
        "exe_sha256": hashlib.sha256(exe.read_bytes()).hexdigest(), "application_version": __version__,
        "scenarios": checks,
        "environment": "Python/Tcl/Tk environment variables removed; PATH contains only Windows System32",
        "verified": ["Single input", "Mixed EAD2002/EAD3/invalid batch", "UTF-16 input",
                     "Non-ASCII/spaced paths", "ID suffix after final dot preserves leading zeros",
                     "Collection title and dates instead of header title", "Nested date is not duplicated",
                     "EAD3 human-readable structured date range",
                     "Formula-like text remains literal",
                     "Duplicate preservation", "Source EAD associations", "Issue-only rows",
                     "Zero-entry workbook", "Frozen/filterable headers"],
        "remaining_platform_checks": "This is not a clean VM without Python installed; offline clean-machine Windows 10/11 checks must be recorded separately.",
    }
    (output / "validation.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
