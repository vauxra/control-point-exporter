from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from control_point_exporter.discovery import discover_folder, normalize_inputs


def touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("<ead/>", encoding="utf-8")
    return path


def test_folder_sorts_xml_and_recursion_is_explicit(tmp_path):
    z = touch(tmp_path / "z.XML")
    a = touch(tmp_path / "a.xml")
    nested = touch(tmp_path / "child" / "é.xml")
    touch(tmp_path / "book.xlsx")
    assert discover_folder(tmp_path) == [a, z]
    assert discover_folder(tmp_path, recursive=True) == [a, nested, z]


def test_selection_order_deduplicates_resolved_paths_not_names(tmp_path):
    first = touch(tmp_path / "one" / "same.xml")
    second = touch(tmp_path / "two" / "same.xml")
    assert normalize_inputs([second, first, second.parent / ".." / "two" / second.name]) == [second, first]


def test_nonxml_and_directories_are_ignored_but_missing_xml_can_be_reported(tmp_path):
    directory = tmp_path / "directory.xml"
    directory.mkdir()
    absent = tmp_path / "missing.xml"
    assert normalize_inputs([tmp_path / "readme.txt", directory, absent]) == [absent]


def test_missing_folder_reports_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        discover_folder(tmp_path / "absent")
    with pytest.raises(NotADirectoryError):
        discover_folder(touch(tmp_path / "file.xml"))


def test_same_input_output_folder_ignores_workbook(tmp_path):
    xml = touch(tmp_path / "source.xml")
    (tmp_path / "control point - 2026-11-02_10-00-00.xlsx").write_bytes(b"workbook")
    assert discover_folder(tmp_path, recursive=True) == [xml]


def test_directory_links_and_junctions_are_not_followed(tmp_path):
    source = tmp_path / "source"
    expected = touch(source / "one.xml")
    target = tmp_path / "target"
    touch(target / "hidden.xml")
    link = source / "loop"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("Directory symlinks unavailable")
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True, text=True)
        if result.returncode:
            pytest.skip("Directory links and junctions unavailable")
    assert discover_folder(source, recursive=True) == [expected]
    with pytest.raises(OSError, match="actual folder"):
        discover_folder(link, recursive=True)


@pytest.mark.skipif(os.name != "nt", reason="Windows paths are case-insensitive")
def test_windows_case_variants_are_one_input(tmp_path):
    path = touch(tmp_path / "Mixed.XML")
    assert normalize_inputs([path, str(path).swapcase()]) == [path]
