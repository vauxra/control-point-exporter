"""Native Tk callback tests with dialogs intercepted; no OS input is sent."""
from __future__ import annotations

import json
import logging
from pathlib import Path
import tkinter as tk
import time

import pytest

from control_point_exporter import gui, settings


@pytest.fixture
def app_factory(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local App Data"))
    monkeypatch.setattr(gui.messagebox, "showinfo", lambda *args, **kwargs: None)
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *args, **kwargs: None)
    windows = []

    def create():
        root = tk.Tk()
        root.withdraw()
        app = gui.ExporterApp(root)
        # Drain startup theme/layout idle events before a test can destroy a root.
        root.update_idletasks()
        windows.append(root)
        return root, app

    yield create
    for root in windows:
        try:
            root.destroy()
        except tk.TclError:
            pass


def xml_file(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("<ead/>", encoding="utf-8")
    return path.resolve()


def test_both_input_pickers_share_folder_and_remember_it_after_restart(app_factory, monkeypatch, tmp_path):
    root, app = app_factory()
    first = xml_file(tmp_path / "file input" / "first.xml")
    second = xml_file(tmp_path / "folder input" / "second.xml")
    calls = []

    def choose_files(**kwargs):
        calls.append(kwargs)
        return (str(first),)

    monkeypatch.setattr(gui.filedialog, "askopenfilenames", choose_files)
    app.add_files()
    assert calls[-1]["initialdir"] == str(Path.home().resolve())
    assert app._last_input_dir == first.parent

    def choose_folder(**kwargs):
        calls.append(kwargs)
        return str(second.parent)

    monkeypatch.setattr(gui.filedialog, "askdirectory", choose_folder)
    app.add_folder()
    assert calls[-1]["initialdir"] == str(first.parent)
    assert app.paths == [first, second]
    saved = settings.get_settings_path().read_bytes()
    assert json.loads(saved)["last_input_directory"] == str(second.parent)

    monkeypatch.setattr(gui.filedialog, "askopenfilenames", lambda **kwargs: calls.append(kwargs) or ())
    app.add_files()
    assert calls[-1]["initialdir"] == str(second.parent)
    assert settings.get_settings_path().read_bytes() == saved
    root.destroy()

    _, restarted = app_factory()
    assert restarted._last_input_dir == second.parent
    restarted.add_files()
    assert calls[-1]["initialdir"] == str(second.parent)


def test_canceled_or_failed_folder_selection_does_not_replace_remembered_folder(app_factory, monkeypatch, tmp_path):
    _, app = app_factory()
    remembered = tmp_path / "remembered"
    remembered.mkdir()
    app._remember_input_directory(remembered)
    original = settings.get_settings_path().read_bytes()
    monkeypatch.setattr(gui.filedialog, "askdirectory", lambda **kwargs: "")
    app.add_folder()
    assert settings.get_settings_path().read_bytes() == original
    monkeypatch.setattr(gui.filedialog, "askdirectory", lambda **kwargs: str(tmp_path / "missing"))
    app.add_folder()
    assert settings.get_settings_path().read_bytes() == original


def test_empty_folder_is_remembered_as_a_successful_selection(app_factory, monkeypatch, tmp_path):
    _, app = app_factory()
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(gui.filedialog, "askdirectory", lambda **kwargs: str(empty))
    app.add_folder()
    assert app._last_input_dir == empty.resolve()
    assert settings.load_last_input_directory() == empty.resolve()


@pytest.mark.parametrize("path_kind", ["existing", "missing_child", "file", "blank", "home_expansion", "relative"])
def test_output_browse_uses_current_output_or_its_existing_parent(app_factory, monkeypatch, tmp_path, path_kind):
    _, app = app_factory()
    output = tmp_path / "current output"
    output.mkdir()
    input_folder = tmp_path / "last input"
    input_folder.mkdir()
    app._remember_input_directory(input_folder)
    if path_kind == "existing":
        supplied, expected = f"  {output}  ", output
    elif path_kind == "missing_child":
        supplied, expected = str(output / "not created" / "nested"), output
    elif path_kind == "file":
        existing_file = output / "item.txt"
        existing_file.write_text("existing", encoding="utf-8")
        supplied, expected = str(existing_file), output
    elif path_kind == "blank":
        supplied, expected = "   ", Path.home()
    elif path_kind == "home_expansion":
        supplied, expected = " ~ ", Path.home()
    else:
        monkeypatch.chdir(tmp_path)
        supplied, expected = "current output", output
    app.output_var.set(supplied)
    captured = []
    monkeypatch.setattr(gui.filedialog, "askdirectory", lambda **kwargs: captured.append(kwargs) or "")
    app.browse_output()
    assert captured[-1]["initialdir"] == str(expected.resolve())
    assert Path(captured[-1]["initialdir"]).is_absolute()
    assert app.output_var.get() == supplied
    assert settings.load_last_input_directory() == input_folder.resolve()


def test_successful_output_browse_updates_only_output(app_factory, monkeypatch, tmp_path):
    _, app = app_factory()
    remembered = tmp_path / "input"
    remembered.mkdir()
    selected = tmp_path / "chosen output"
    selected.mkdir()
    app._remember_input_directory(remembered)
    saved = settings.get_settings_path().read_bytes()
    monkeypatch.setattr(gui.filedialog, "askdirectory", lambda **kwargs: selected.as_posix())
    app.browse_output()
    assert app.output_var.get() == str(selected.resolve())
    assert settings.get_settings_path().read_bytes() == saved


def test_menus_move_about_into_help_and_file_close_stays_cancel_safe(app_factory, monkeypatch):
    root, app = app_factory()
    assert [app.menu_bar.entrycget(i, "label") for i in range(app.menu_bar.index("end") + 1)] == ["File", "Help"]
    assert app.file_menu.entrycget(0, "label") == "Close"
    assert app.help_menu.entrycget(0, "label") == "About"
    assert app.file_menu.entrycget(0, "underline") == 0
    assert app.help_menu.entrycget(0, "underline") == 0
    assert [app.menu_bar.entrycget(i, "underline") for i in (0, 1)] == [0, 0]
    footer_texts = [str(child.cget("text")) for child in app.open_workbook_button.master.winfo_children()]
    assert footer_texts == ["Open workbook", "Open output folder", "Open log folder"]
    app.help_menu.invoke(0)
    dialogs = [child for child in root.winfo_children() if isinstance(child, tk.Toplevel)]
    assert len(dialogs) == 1 and dialogs[0].title() == "About — Control Point Exporter"
    dialogs[0].destroy()

    app._running = True
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *args, **kwargs: False)
    app.file_menu.invoke(0)
    assert not app._close_when_done and not app._cancel.is_set()
    assert root.winfo_exists()
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *args, **kwargs: True)
    app.file_menu.invoke(0)
    assert app._close_when_done and app._cancel.is_set()
    assert root.winfo_exists()
    app._running = False
    app.file_menu.invoke(0)
    with pytest.raises(tk.TclError):
        root.winfo_exists()


def test_log_folder_is_created_and_native_canonical_path_is_opened(app_factory, monkeypatch, tmp_path):
    _, app = app_factory()
    missing_log_dir = gui.get_log_dir()
    assert tmp_path.resolve() in missing_log_dir.parents
    assert not missing_log_dir.exists()
    opened = []
    monkeypatch.setattr(gui.os, "startfile", opened.append)
    app.open_log_folder()
    assert missing_log_dir.is_dir()
    assert opened == [str(missing_log_dir.resolve())]
    missing_log_dir.rmdir()
    app.open_log_folder()
    assert missing_log_dir.is_dir()
    assert opened == [str(missing_log_dir.resolve())] * 2


def test_open_action_resolves_existing_path_before_shell_call(app_factory, monkeypatch, tmp_path):
    _, app = app_factory()
    file = tmp_path / "workbook.xlsx"
    file.write_bytes(b"placeholder")
    monkeypatch.chdir(tmp_path)
    opened = []
    monkeypatch.setattr(gui.os, "startfile", opened.append)
    app._open_path(Path("workbook.xlsx"))
    assert opened == [str(file.resolve())]


def test_log_folder_failure_is_actionable_and_nonfatal(app_factory, monkeypatch):
    _, app = app_factory()
    def unavailable():
        raise PermissionError("The application folder is not writable.")
    monkeypatch.setattr(gui, "ensure_log_dir", unavailable)
    dialogs = []
    monkeypatch.setattr(gui.messagebox, "showerror", lambda *args, **kwargs: dialogs.append(args))
    app.open_log_folder()
    assert len(dialogs) == 1 and dialogs[0][0] == "Could not open log folder"
    assert "not writable" in dialogs[0][1]


def test_corrupt_preferences_are_ignored_nonfatally(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    target = settings.get_settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("broken JSON", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="control_point_exporter"):
        assert settings.load_last_input_directory() is None
    assert "Could not read file-picker preferences" in caplog.text


def test_failed_atomic_settings_replace_preserves_previous_preferences(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    first = tmp_path / "first"
    first.mkdir()
    second = tmp_path / "second"
    second.mkdir()
    assert settings.save_last_input_directory(first)
    target = settings.get_settings_path()
    original = target.read_bytes()
    def unavailable(*args, **kwargs):
        raise PermissionError("Settings are locked.")
    monkeypatch.setattr(settings.os, "replace", unavailable)
    with caplog.at_level(logging.WARNING, logger="control_point_exporter"):
        assert not settings.save_last_input_directory(second)
    assert target.read_bytes() == original
    assert not list(target.parent.glob("settings-*.tmp"))
    assert "Could not save file-picker preferences" in caplog.text


def test_file_close_reopen_restores_all_defaults_without_export_or_input_queue(app_factory, tmp_path):
    root, app = app_factory()
    source = xml_file(tmp_path / "input" / "source.xml")
    output = f"  {tmp_path / 'typed output not created'}  "
    app.add_paths([source])
    app.output_var.set(output)
    app.recursive_var.set(True)
    app._remember_input_directory(source.parent)
    root.geometry("1040x740+25+35")
    root.update_idletasks()
    app.table.column("status", width=215)
    app.table.column("rows", width=235)
    root.update_idletasks()
    expected_widths = {name: app.table.column(name, "width") for name in ("path", "status", "rows")}
    expected_geometry = root.geometry()
    app.file_menu.invoke(0)
    saved = settings.load_preferences()
    assert saved["output_directory"] == output
    assert saved["include_subfolders"] is True
    assert saved["last_input_directory"] == str(source.parent)
    assert saved["window_geometry"] == expected_geometry
    assert saved["column_widths"] == expected_widths

    restarted_root, restarted = app_factory()
    restarted_root.update_idletasks()
    assert restarted.output_var.get() == output
    assert restarted.recursive_var.get() is True
    assert restarted._last_input_dir == source.parent
    assert restarted_root.geometry() == expected_geometry
    assert {name: restarted.table.column(name, "width") for name in ("path", "status", "rows")} == expected_widths
    assert restarted.paths == []
    assert restarted.selection_var.get() == "0 files selected"


def test_live_control_changes_save_without_export_or_close(app_factory, tmp_path):
    root, app = app_factory()
    output = str(tmp_path / "new output")
    app.output_var.set(output)
    app.recursive_var.set(True)
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline and not settings.get_settings_path().exists():
        root.update()
        time.sleep(0.01)
    saved = settings.load_preferences()
    assert saved["output_directory"] == output
    assert saved["include_subfolders"] is True
    assert not app.paths


def test_last_input_update_preserves_output_and_layout_preferences(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    original = {"output_directory": "  D:\\chosen output  ", "include_subfolders": True,
                "window_geometry": "1040x740+25+35", "window_maximized": True,
                "column_widths": {"path": 500, "status": 210, "rows": 220}}
    assert settings.save_preferences(original)
    chosen = tmp_path / "input"
    chosen.mkdir()
    assert settings.save_last_input_directory(chosen)
    saved = settings.load_preferences()
    assert {key: saved[key] for key in original} == original
    assert saved["last_input_directory"] == str(chosen.resolve())


def test_invalid_typed_preferences_fall_back_safely_and_geometry_is_clamped(app_factory):
    target = settings.get_settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({"output_directory": 123, "include_subfolders": "yes",
                                 "last_input_directory": ["invalid"],
                                 "window_geometry": "99999x99999+99999+99999",
                                 "window_maximized": "not a boolean",
                                 "column_widths": {"path": -1, "status": "wide", "rows": True}}), encoding="utf-8")
    root, app = app_factory()
    root.update_idletasks()
    assert app.recursive_var.get() is False
    assert app._last_input_dir is None
    assert app.output_var.get() != "123"
    assert root.state() != "iconic"
    assert root.winfo_width() <= root.winfo_screenwidth()
    assert root.winfo_height() <= root.winfo_screenheight()
    assert 0 <= root.winfo_x() < root.winfo_screenwidth()
    assert 0 <= root.winfo_y() < root.winfo_screenheight()


def test_declined_close_does_not_flush_but_confirmed_close_does(app_factory, monkeypatch, tmp_path):
    _, app = app_factory()
    app.output_var.set(str(tmp_path / "typed output"))
    app.recursive_var.set(True)
    app._running = True
    saves = []
    monkeypatch.setattr(gui, "save_preferences", lambda values: saves.append(values.copy()) or True)
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *args, **kwargs: False)
    app.close()
    assert saves == [] and not app._cancel.is_set()
    monkeypatch.setattr(gui.messagebox, "askyesno", lambda *args, **kwargs: True)
    app.close()
    assert len(saves) == 1 and saves[0]["include_subfolders"] is True
    assert saves[0]["output_directory"] == str(tmp_path / "typed output")
    assert app._cancel.is_set()
    app._running = False


def test_minimized_close_keeps_last_normal_geometry_and_maximized_default(app_factory, monkeypatch):
    root, app = app_factory()
    app._normal_geometry = "1040x740+25+35"
    app._window_maximized = True
    monkeypatch.setattr(root, "state", lambda: "iconic")
    app._persist_preferences()
    saved = settings.load_preferences()
    assert saved["window_geometry"] == "1040x740+25+35"
    assert saved["window_maximized"] is True
    assert "window_minimized" not in saved
