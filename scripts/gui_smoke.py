"""Reproducible source-GUI acceptance checks on actual Tk windows.

Run: uv run --locked --group dev python scripts/gui_smoke.py
The layout checks change Tk's point-to-pixel scaling, not Windows OS scaling.
No mouse or keyboard input is sent; every test window closes automatically.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from importlib import resources
import json
import logging
import os
from pathlib import Path
import platform
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from unittest.mock import patch

from openpyxl import load_workbook

from control_point_exporter import __version__
from control_point_exporter.gui import ExporterApp, _enable_dpi_awareness
from control_point_exporter.logging_setup import configure_logging, get_log_dir
from control_point_exporter.runner import run_export


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures"


def window(*, mapped: bool = False, scaling: float | None = None) -> tuple[tk.Tk, ExporterApp]:
    root = tk.Tk()
    if not mapped:
        root.withdraw()
    if scaling is not None:
        root.tk.call("tk", "scaling", scaling)
    return root, ExporterApp(root)


def finish(root: tk.Tk, app: ExporterApp, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while app._running and time.monotonic() < deadline:
        root.update()
        time.sleep(0.01)
    if app._running:
        app.cancel()
        raise AssertionError("The GUI worker did not finish within the acceptance timeout.")
    root.update_idletasks()


def close(root: tk.Tk, app: ExporterApp) -> None:
    if app._running:
        app.cancel()
        finish(root, app)
    root.update_idletasks()
    root.destroy()


def export_check(output: Path) -> dict:
    output.mkdir(parents=True)
    sources = [FIXTURES / "ead2002.xml", FIXTURES / "ead3.xml"]
    root, app = window()
    try:
        assert app.add_paths(sources) == 2
        assert app.add_paths([sources[0]]) == 0
        app.output_var.set(str(output))
        app.start_export()
        assert app.export_button.instate(("disabled",))
        finish(root, app)
        result = app._last_result
        assert result is not None and not result.cancelled
        assert result.files_processed == 2 and result.entries == 13
        assert result.failures == 0 and result.warnings == 0
        assert result.workbook is not None and result.workbook.is_file()
        assert app.open_workbook_button.instate(("!disabled",))
        rows = [app.table.item(item, "values") for item in app.table.get_children()]
        assert [(r[1], r[2]) for r in rows] == [("Done", "11"), ("Done", "2")]
        workbook = load_workbook(result.workbook, read_only=True, data_only=False)
        try:
            exported = list(workbook["Control points"].iter_rows(min_row=2))
            assert len(exported) == 13
            assert all(cell.data_type == "s" for row in exported for cell in row)
            actual_sources = [row[4].value for row in exported]
            assert actual_sources.count(str(sources[0])) == 11
            assert actual_sources.count(str(sources[1])) == 2
            expected_metadata = {
                str(sources[0]): ("00042", "García & Sons 1900-1910"),
                str(sources[1]): ("E3-0007", "EAD3 collection 1945-1950"),
            }
            assert all((row[2].value, row[3].value) == expected_metadata[row[4].value] for row in exported)
        finally:
            workbook.close()
        return {"passed": True, "files": 2, "rows": 13, "workbook": str(result.workbook),
                "summary": app.summary_var.get(), "source_rows": rows,
                "duplicate_selection_ignored": True, "literal_text_cells": True}
    finally:
        close(root, app)


def cancellation_check(output: Path) -> dict:
    output.mkdir(parents=True)
    root, app = window()
    gate = threading.Event()

    def held_export(*args, **kwargs):
        assert gate.wait(5), "Test failed to release the worker."
        return run_export(*args, **kwargs)

    try:
        app.add_paths([FIXTURES / "ead2002.xml"])
        app.output_var.set(str(output))
        with patch("control_point_exporter.runner.run_export", side_effect=held_export):
            app.start_export()
            app.cancel()
            gate.set()
            finish(root, app)
        result = app._last_result
        assert result is not None and result.cancelled and result.workbook is None
        assert not list(output.glob("*.xlsx"))
        assert app.export_button.instate(("!disabled",))
        return {"passed": True, "checkpoint": "Initial processing checkpoint after GUI Cancel",
                "no_workbook": True, "input_controls_restored": True,
                "summary": app.summary_var.get()}
    finally:
        gate.set()
        close(root, app)


def destination_error_check(output: Path) -> dict:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("This path is a file, not a writable output directory.", encoding="utf-8")
    root, app = window()
    dialogs: list[tuple] = []
    try:
        app.add_paths([FIXTURES / "ead2002.xml"])
        app.output_var.set(str(output))
        with patch("control_point_exporter.gui.messagebox.showerror", side_effect=lambda *args, **kwargs: dialogs.append(args)):
            app.start_export()
            finish(root, app)
        assert app._last_result is None and len(dialogs) == 1
        assert app.export_button.instate(("!disabled",))
        assert app.summary_var.get().startswith("Export failed")
        return {"passed": True, "error_dialog_captured": list(dialogs[0]),
                "summary": app.summary_var.get(), "input_controls_restored": True,
                "note": "The error dialog was intercepted to keep acceptance unattended."}
    finally:
        close(root, app)


def layout_measure(root: tk.Tk, app: ExporterApp) -> dict:
    root.update()
    widgets = [*app._input_controls, app.export_button, app.cancel_button,
               app.table, app.log_text, *app.open_workbook_button.master.winfo_children()]
    bounds = []
    left, top = root.winfo_rootx(), root.winfo_rooty()
    right, bottom = left + root.winfo_width(), top + root.winfo_height()
    for widget in widgets:
        x, y = widget.winfo_rootx(), widget.winfo_rooty()
        width, height = widget.winfo_width(), widget.winfo_height()
        assert width > 1 and height > 1
        assert left <= x and top <= y and x + width <= right and y + height <= bottom, (
            "Widget outside the usable window", widget, (x, y, width, height), (left, top, right, bottom))
        bounds.append({"widget": widget.winfo_class(), "bounds": [x - left, y - top, width, height],
                       "requested": [widget.winfo_reqwidth(), widget.winfo_reqheight()]})
    heading = tkfont.nametofont("TkHeadingFont")
    assert app.table.column("rows", "width") >= heading.measure("Control points") + 20
    return {"window": [root.winfo_width(), root.winfo_height()],
            "requested": [root.winfo_reqwidth(), root.winfo_reqheight()],
            "table_columns": {name: app.table.column(name, "width") for name in ("path", "status", "rows")},
            "control_points_header_text_width": heading.measure("Control points"),
            "widgets": bounds, "all_controls_within_window": True}


def layout_checks(output: Path) -> list[dict]:
    layouts = []
    for percentage in (100, 150, 200):
        requested_scaling = 96 / 72 * percentage / 100
        with patch("control_point_exporter.settings.get_settings_path", return_value=output / f"settings-{percentage}.json"):
            root, app = window(mapped=True, scaling=requested_scaling)
            try:
                default = layout_measure(root, app)
                minimum_width, minimum_height = root.minsize()
                root.geometry(f"{minimum_width}x{minimum_height}")
                minimum = layout_measure(root, app)
                layouts.append({"equivalent_percent": percentage,
                                "tk_scaling": float(root.tk.call("tk", "scaling")),
                                "screen": [root.winfo_screenwidth(), root.winfo_screenheight()],
                                "default_size": default, "minimum_size": minimum})
            finally:
                close(root, app)
    return layouts


def licenses_check() -> dict:
    notices = resources.files("control_point_exporter").joinpath("resources/THIRD_PARTY_NOTICES.txt").read_text(encoding="utf-8")
    assert len(notices) > 1000
    root, app = window()
    try:
        assert app.help_menu.entrycget(0, "label") == "About"
        app.help_menu.invoke(0)
        root.update()
        dialogs = [child for child in root.winfo_children() if isinstance(child, tk.Toplevel)]
        assert len(dialogs) == 1
        assert dialogs[0].title() == "About — Control Point Exporter"
        texts = [child for frame in dialogs[0].winfo_children() for child in frame.winfo_children() if isinstance(child, tk.Text)]
        assert len(texts) == 1
        content = texts[0].get("1.0", "end-1c")
        assert content.endswith(notices)
        assert "Python" in content and "openpyxl" in content and "lxml" in content
        dialogs[0].destroy()
        return {"passed": True, "help_about_menu_opens_view": True,
                "full_notices_in_about_view": True, "notice_characters": len(notices)}
    finally:
        close(root, app)


def menu_dialog_checks(output: Path) -> dict:
    """Use isolated preferences and mocked pickers; preserve the user's settings."""
    first = output / "first input"
    second = output / "second input"
    destination = output / "typed output"
    for directory in (first, second, destination):
        directory.mkdir(parents=True)
    source = first / "first.xml"
    source.write_text("<ead/>", encoding="utf-8")
    (second / "second.xml").write_text("<ead/>", encoding="utf-8")
    preferences = output / "isolated-settings.json"
    with patch("control_point_exporter.settings.get_settings_path", return_value=preferences):
        root, app = window()
        try:
            assert [app.menu_bar.entrycget(i, "label") for i in (0, 1)] == ["File", "Help"]
            assert app.file_menu.entrycget(0, "label") == "Close"
            assert app.help_menu.entrycget(0, "label") == "About"
            assert app._last_input_dir is None
            with patch("control_point_exporter.gui.filedialog.askopenfilenames", return_value=(str(source),)) as picker:
                app.add_files()
                assert picker.call_args.kwargs["initialdir"] == str(Path.home().resolve())
            assert app._last_input_dir == first.resolve()
            with patch("control_point_exporter.gui.filedialog.askdirectory", return_value=str(second)) as picker:
                app.add_folder()
                assert picker.call_args.kwargs["initialdir"] == str(first.resolve())
            assert app._last_input_dir == second.resolve()
            saved_input = json.loads(preferences.read_text(encoding="utf-8"))["last_input_directory"]
            with patch("control_point_exporter.gui.filedialog.askopenfilenames", return_value=()) as picker:
                app.add_files()
                assert picker.call_args.kwargs["initialdir"] == str(second.resolve())
            assert json.loads(preferences.read_text(encoding="utf-8"))["last_input_directory"] == saved_input
            app.output_var.set(f"  {destination}  ")
            with patch("control_point_exporter.gui.filedialog.askdirectory", return_value="") as picker:
                app.browse_output()
                assert picker.call_args.kwargs["initialdir"] == str(destination.resolve())
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                root.update()
                saved_preferences = json.loads(preferences.read_text(encoding="utf-8"))
                if saved_preferences.get("output_directory") == app.output_var.get():
                    break
                time.sleep(0.01)
            assert saved_preferences["output_directory"] == app.output_var.get()
            assert app._last_input_dir == second.resolve()
            assert saved_preferences["last_input_directory"] == saved_input

            def children(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from children(child)

            assert not any("About" in str(widget.cget("text")) or "Licenses" in str(widget.cget("text"))
                           for widget in children(root) if widget.winfo_class() == "TButton")
        finally:
            close(root, app)
        root, app = window()
        try:
            root.update_idletasks()
            assert app._last_input_dir == second.resolve()
            assert app._input_initial_directory() == str(second.resolve())
            app.file_menu.invoke(0)
            try:
                alive = bool(root.winfo_exists())
            except tk.TclError:
                alive = False
            assert not alive, "File → Close did not close the window."
        finally:
            try:
                close(root, app)
            except tk.TclError:
                pass
    return {"passed": True, "menus": ["File → Close", "Help → About"],
            "close_menu_closes_window": True, "footer_about_removed": True,
            "shared_input_directory": True, "input_directory_persists_between_launches": True,
            "cancel_keeps_input_directory": True, "output_browse_uses_current_text": True,
            "preferences_isolated_from_user_settings": True}


def all_preferences_check(output: Path) -> dict:
    """Close and restart a mapped app with every editable default changed."""
    output.mkdir(parents=True)
    remembered = output / "remembered input"
    remembered.mkdir()
    source = remembered / "batch.xml"
    source.write_text("<ead/>", encoding="utf-8")
    destination = output / "saved destination"
    destination.mkdir()
    preferences = output / "preferences.json"
    with patch("control_point_exporter.settings.get_settings_path", return_value=preferences):
        root, app = window(mapped=True)
        try:
            root.update()
            app.output_var.set(str(destination))
            app.recursive_var.set(True)
            app._remember_input_directory(remembered)
            app.add_paths([source])
            minimum_width, minimum_height = root.minsize()
            root.geometry(f"{max(minimum_width, 1040)}x{max(minimum_height, 760)}+40+40")
            app.table.column("status", width=max(210, app.table.column("status", "minwidth") + 40))
            app.table.column("rows", width=max(190, app.table.column("rows", "minwidth") + 40))
            root.update()
            expected_columns = {name: app.table.column(name, "width") for name in ("path", "status", "rows")}
            expected_geometry = root.geometry()
            app.file_menu.invoke(0)
            stored = json.loads(preferences.read_text(encoding="utf-8"))
            assert stored["output_directory"] == str(destination)
            assert stored["include_subfolders"] is True
            assert stored["last_input_directory"] == str(remembered.resolve())
            assert stored["column_widths"] == expected_columns
            assert stored["window_geometry"] == expected_geometry
            assert stored["window_maximized"] is False
        finally:
            try:
                close(root, app)
            except tk.TclError:
                pass
        root, app = window(mapped=True)
        try:
            root.update()
            assert app.output_var.get() == str(destination)
            assert app.recursive_var.get() is True
            assert app._last_input_dir == remembered.resolve()
            assert not app.paths and not app.table.get_children(), "Batch files must not be restored as defaults."
            assert root.geometry() == expected_geometry
            restored_columns = {name: app.table.column(name, "width") for name in ("path", "status", "rows")}
            assert restored_columns == expected_columns, {"expected": expected_columns, "actual": restored_columns,
                                                          "geometry": root.geometry(), "saved": stored}

            def all_children(widget):
                for child in widget.winfo_children():
                    yield child
                    yield from all_children(child)

            assert not any("EAD XML finding aids → Excel" in str(widget.cget("text"))
                           for widget in all_children(root) if widget.winfo_class() == "TLabel")
            return {"passed": True, "output_directory_restored": True, "include_subfolders_restored": True,
                    "input_directory_restored": True, "window_geometry": expected_geometry,
                    "column_widths": restored_columns, "queue_starts_empty": True,
                    "subtitle_removed": True, "preferences_isolated_from_user_settings": True}
        finally:
            close(root, app)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts/gui-source")
    args = parser.parse_args()
    destination = args.output_dir.resolve()
    run_dir = destination / datetime.now().strftime("run-%Y%m%d_%H%M%S-%f")
    run_dir.mkdir(parents=True)
    configure_logging()
    _enable_dpi_awareness()
    report = {"generated_utc": datetime.now(timezone.utc).isoformat(), "platform": platform.platform(),
              "application_version": __version__,
              "python": platform.python_version(), "process_id": os.getpid(), "passed": False,
              "scope": "Source GUI with actual Tk event loop and mapped layout windows; no OS input automation.",
              "scaling_scope": "Tk point-to-pixel scaling equivalents only. Windows OS display scaling was not changed."}
    try:
        # A regression should fail this script rather than wait on an error dialog.
        # The destination-error scenario installs a nested patch to inspect its dialog.
        with patch("control_point_exporter.settings.get_settings_path", return_value=run_dir / "isolated-settings.json"), \
                patch("control_point_exporter.gui.messagebox.showerror", return_value=None):
            report["worker_export"] = export_check(run_dir / "export")
            report["cancellation"] = cancellation_check(run_dir / "canceled")
            report["destination_error"] = destination_error_check(run_dir / "invalid-destination")
            report["layouts"] = layout_checks(run_dir / "layouts")
            report["licenses"] = licenses_check()
            report["menus_and_directories"] = menu_dialog_checks(run_dir / "dialogs")
            report["persisted_defaults"] = all_preferences_check(run_dir / "persisted-defaults")
        for handler in logging.getLogger("control_point_exporter").handlers:
            handler.flush()
        log_file = get_log_dir() / "app.log"
        assert log_file.is_file()
        assert report["worker_export"]["workbook"] in log_file.read_text(encoding="utf-8")
        report["operational_log"] = {"path": str(log_file), "export_path_recorded": True, "exists": True}
        report["passed"] = True
    except BaseException as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "validation.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"passed": True, "report": str(destination / "validation.json"),
                      "workbook": report["worker_export"]["workbook"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
