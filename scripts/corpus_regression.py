"""Optionally verify the private reference corpus and its complete Excel export.

The reference reader uses stdlib ElementTree, not the application extractor.
Run with --exe to exercise the actual packaged application instead of source.
The private corpus/manifest are excluded from Git; normal builds and tests do
not require them.
"""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
TYPES = {"subject", "persname", "corpname", "geogname", "famname", "title", "genreform", "function", "occupation", "name"}
EXPECTED_TYPES = {"subject": 14259, "persname": 8549, "corpname": 7317, "geogname": 5391, "famname": 332, "title": 1}
HEADERS = ("Control Point Type", "Control Point Value", "Finding Aid ID", "Finding Aid Title", "Source File")
NS = "{urn:isbn:1-931666-22-9}"


def text(element: ET.Element | None) -> str:
    return " ".join("".join(element.itertext()).split()) if element is not None else ""


def reference(corpus: Path) -> tuple[Counter, set[str], dict]:
    rows = Counter()
    issue_files: set[str] = set()
    types = Counter()
    aids_with_points = without_controls = empty_terms = duplicates = 0
    files = sorted(corpus.glob("*.xml"))
    for file in files:
        source = str(file.resolve())
        root = ET.parse(file).getroot()
        assert root.tag == NS + "ead", (file, root.tag)
        aid = text(root.find(f"{NS}eadheader/{NS}eadid")).rsplit(".", 1)[-1].strip()
        did = root.find(f"{NS}archdesc/{NS}did")
        unit_title = did.find(f"{NS}unittitle") if did is not None else None
        title_parts = [text(unit_title)]
        nested_dates = {text(node) for node in unit_title.iter(f"{NS}unitdate")} if unit_title is not None else set()
        if did is not None:
            seen_dates = set(nested_dates)
            for date in did.findall(f"{NS}unitdate"):
                value = text(date)
                if value and value not in seen_dates:
                    title_parts.append(value)
                    seen_dates.add(value)
        title = " ".join(part for part in title_parts if part)
        assert aid and title, f"Corpus metadata unexpectedly missing: {file}"
        if not any(e.tag == NS + "controlaccess" for e in root.iter()):
            without_controls += 1
            issue_files.add(source)
        file_pairs = []

        def walk(node: ET.Element, in_control: bool = False) -> None:
            nonlocal empty_terms
            tag = node.tag.removeprefix(NS)
            in_control = in_control or node.tag == NS + "controlaccess"
            if in_control and tag in TYPES:
                value = text(node)
                if value:
                    rows[(tag, value, aid, title, source)] += 1
                    types[tag] += 1
                    file_pairs.append((tag, value))
                else:
                    empty_terms += 1
                    issue_files.add(source)
                return
            for child in node:
                walk(child, in_control)

        walk(root)
        aids_with_points += bool(file_pairs)
        duplicates += len(file_pairs) != len(set(file_pairs))
    baseline = {
        "files": len(files), "aids_with_points": aids_with_points,
        "aids_without_control_sections": without_controls, "nonblank_occurrences": rows.total(),
        "empty_terms": empty_terms, "aids_with_duplicate_pairs": duplicates, "types": dict(types),
    }
    assert baseline == {
        "files": 3419, "aids_with_points": 3392, "aids_without_control_sections": 27,
        "nonblank_occurrences": 35849, "empty_terms": 10, "aids_with_duplicate_pairs": 51,
        "types": EXPECTED_TYPES,
    }, baseline
    return rows, issue_files, baseline


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, help="Test this built EXE instead of Python source")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--skip-hashes", action="store_true", help="Skip transfer hash verification only")
    args = parser.parse_args()
    started = time.perf_counter()
    corpus = ROOT / "data/ead"
    manifest_path = ROOT / "corpus-manifest.json"
    if not corpus.is_dir() or not manifest_path.is_file():
        parser.error("This optional regression requires the private data/ead corpus and corpus-manifest.json, "
                     "which are not included in the repository. Normal tests, GUI smoke checks, and EXE builds "
                     "use synthetic fixtures and do not need that corpus.")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    actual = {p.resolve() for p in corpus.glob("*.xml")}
    assert actual == {(ROOT / f["path"]).resolve() for f in manifest["files"]}
    total_bytes = 0
    for entry in manifest["files"]:
        path = ROOT / entry["path"]
        total_bytes += path.stat().st_size
        assert path.stat().st_size == entry["size_bytes"], path
        if not args.skip_hashes:
            with path.open("rb") as stream:
                assert hashlib.file_digest(stream, "sha256").hexdigest() == entry["sha256"], path
    assert total_bytes == manifest["total_bytes"] == 282807396
    expected_rows, expected_issues, baseline = reference(corpus)
    output = (args.output_dir or ROOT / "artifacts" / ("corpus-exe" if args.exe else "corpus-source")).resolve()
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / f"run-{uuid.uuid4().hex}.json"
    command = [str(args.exe.resolve())] if args.exe else [sys.executable, "-m", "control_point_exporter"]
    command += ["--headless", "--output-dir", str(output), "--report", str(report_path), str(corpus)]
    conversion_start = time.perf_counter()
    process = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=600)
    conversion_seconds = time.perf_counter() - conversion_start
    assert process.returncode == 0, (process.returncode, process.stdout, process.stderr)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["entries"] == 35849 and report["files_processed"] == 3419 and report["failures"] == 0, report
    if args.exe:
        assert report.get("frozen") is True, "The report did not come from the packaged runtime."
    workbook = Path(report["workbook"])
    book = load_workbook(workbook, read_only=True, data_only=False)
    try:
        assert book.sheetnames == ["Control points", "Issues"], book.sheetnames
        sheet = book["Control points"]
        records = sheet.iter_rows()
        assert tuple(c.value for c in next(records)) == HEADERS
        actual_rows = Counter()
        for row in records:
            assert all(c.data_type == "s" for c in row), [c.data_type for c in row]
            actual_rows[tuple(c.value for c in row)] += 1
        assert actual_rows == expected_rows, {
            "missing": (expected_rows - actual_rows).most_common(3),
            "extra": (actual_rows - expected_rows).most_common(3),
        }
        issues = list(book["Issues"].values)
        assert issues[0] == ("Source File", "Severity", "Problem")
        assert {row[0] for row in issues[1:]} == expected_issues
        assert all(str(row[1]).lower() == "warning" for row in issues[1:])
    finally:
        book.close()
    summary = {
        "passed": True, "checked_utc": datetime.now(timezone.utc).isoformat(),
        "tested_application": str(args.exe.resolve()) if args.exe else "Python source",
        "exe_sha256": hashlib.sha256(args.exe.read_bytes()).hexdigest() if args.exe else None,
        "application_version": report.get("version"),
        "manifest_hashes_verified": not args.skip_hashes, "corpus_bytes": total_bytes,
        "baseline": baseline, "workbook": str(workbook),
        "verified": ["Exact headers", "All 35,849 complete row tuples against independent XML reader",
                     "Finding aid ID suffix/collection title plus dates/source associations", "Duplicate occurrence multiplicity",
                     "Literal text cell types", "Issue-only file associations", "No input failures"],
        "conversion_seconds": round(conversion_seconds, 3),
        "total_validation_seconds": round(time.perf_counter() - started, 3),
    }
    (output / "validation.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
