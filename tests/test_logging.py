"""Shell folder actions must use the real path on redirected Windows hosts."""
from pathlib import Path

from control_point_exporter.logging_setup import ensure_log_dir, get_log_dir


def test_log_folder_is_created_and_canonical(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local App Data"))
    actual = ensure_log_dir()
    assert actual.is_dir()
    assert actual == actual.resolve() == get_log_dir()
    assert actual == (tmp_path / "Local App Data" / "ControlPointExporter" / "logs").resolve()


def test_missing_log_folder_is_recreated(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    actual = ensure_log_dir()
    actual.rmdir()
    assert not actual.exists()
    assert ensure_log_dir() == actual and actual.is_dir()
