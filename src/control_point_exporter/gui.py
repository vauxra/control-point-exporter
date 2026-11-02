"""Readable Windows desktop interface for the shared conversion pipeline."""
from __future__ import annotations

import ctypes
import logging
import os
import queue
import re
import threading
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime
from importlib import resources
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from . import __version__
from .discovery import discover_folder, normalize_inputs
from .logging_setup import ensure_log_dir, get_log_dir
from .models import RunEvent, RunResult
from .settings import load_last_input_directory, load_preferences, save_preferences

LOGGER = logging.getLogger("control_point_exporter")


def _enable_dpi_awareness() -> None:
    if os.name != "nt":
        return
    try:
        # The packaged manifest also declares PerMonitorV2 awareness.
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass


class ExporterApp:
    """Main window; only its GUI thread reads or writes Tk state."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.paths: list[Path] = []
        self._items: dict[str, str] = {}
        self._events: queue.Queue[RunEvent | tuple[str, object]] = queue.Queue()
        self._cancel = threading.Event()
        self._worker: threading.Thread | None = None
        self._running = False
        self._close_when_done = False
        self._poll_after_id: str | None = None
        self._preferences_after_id: str | None = None
        self._last_result: RunResult | None = None
        self._input_controls: list[ttk.Widget] = []
        self._preferences = load_preferences()
        self._last_input_dir = load_last_input_directory()
        self._normal_geometry: str | None = None
        self._window_maximized = self._preferences.get("window_maximized", False)
        self.recursive_var = tk.BooleanVar(master=root, value=self._preferences.get("include_subfolders", False))
        documents = Path.home() / "Documents"
        self.output_var = tk.StringVar(master=root, value=self._preferences.get("output_directory", str(documents if documents.is_dir() else Path.home())))
        self.summary_var = tk.StringVar(master=root, value="Add EAD XML files or a folder to begin.")
        self.selection_var = tk.StringVar(master=root, value="0 files selected")
        self.progress_var = tk.StringVar(master=root, value="Ready")
        self._configure_style()
        self._build_window()
        self._restore_layout_preferences()
        root.report_callback_exception = self._callback_error
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.bind("<Destroy>", self._on_destroy, add="+")
        root.bind("<Configure>", self._layout_changed, add="+")
        self.table.bind("<ButtonRelease-1>", lambda event: self._schedule_preferences_save(), add="+")
        self.output_var.trace_add("write", lambda *args: self._schedule_preferences_save())
        self.recursive_var.trace_add("write", lambda *args: self._schedule_preferences_save())
        self._poll_after_id = root.after(75, self._poll_events)
        self._log(f"Control Point Exporter {__version__} ready. Logs: {get_log_dir()}")

    def _configure_style(self) -> None:
        for name in ("TkDefaultFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont"):
            tkfont.nametofont(name).configure(family="Segoe UI", size=12)
        tkfont.nametofont("TkTextFont").configure(family="Segoe UI", size=11)
        tkfont.nametofont("TkFixedFont").configure(family="Consolas", size=10)
        self.style = ttk.Style(self.root)
        if "vista" in self.style.theme_names():
            self.style.theme_use("vista")
        lineheight = tkfont.nametofont("TkDefaultFont").metrics("linespace")
        self.style.configure("Treeview", font="TkDefaultFont", rowheight=lineheight + 12)
        self.style.configure("Treeview.Heading", font="TkHeadingFont")
        self.style.configure("TButton", padding=(10, 6))
        self.style.configure("TEntry", padding=5)
        self.style.configure("Title.TLabel", font=("Segoe UI", 17, "bold"))
        self.style.configure("Summary.TLabel", font=("Segoe UI", 12, "bold"))

    def _build_window(self) -> None:
        root = self.root
        root.title("Control Point Exporter")
        self.menu_bar = tk.Menu(root, tearoff=False)
        self.file_menu = tk.Menu(self.menu_bar, tearoff=False)
        self.file_menu.add_command(label="Close", command=self.close, underline=0)
        self.help_menu = tk.Menu(self.menu_bar, tearoff=False)
        self.help_menu.add_command(label="About", command=self.about, underline=0)
        self.menu_bar.add_cascade(label="File", menu=self.file_menu, underline=0)
        self.menu_bar.add_cascade(label="Help", menu=self.help_menu, underline=0)
        root.configure(menu=self.menu_bar)
        factor = max(1.0, float(root.tk.call("tk", "scaling")) / (96 / 72))
        available_width = max(640, root.winfo_screenwidth() - 70)
        available_height = max(480, root.winfo_screenheight() - 100)
        width = min(round(1120 * factor), available_width)
        height = min(round(820 * factor), available_height)
        root.geometry(f"{width}x{height}")
        self._normal_geometry = f"{width}x{height}+0+0"
        root.minsize(min(round(850 * factor), available_width), min(round(650 * factor), available_height))
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        outer = ttk.Frame(root, padding=16)
        outer.grid(sticky="nsew")
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(3, weight=3)
        outer.rowconfigure(7, weight=2)
        ttk.Label(outer, text="Control Point Exporter", style="Title.TLabel").grid(row=0, sticky="w")

        inputs = ttk.Frame(outer)
        inputs.grid(row=2, sticky="ew", pady=(0, 8))
        for col, (text, command) in enumerate((
            ("Add XML files", self.add_files),
            ("Add folder", self.add_folder),
            ("Remove selected", self.remove_selected),
            ("Clear", self.clear),
        )):
            button = ttk.Button(inputs, text=text, command=command)
            button.grid(row=0, column=col, padx=(0, 7), sticky="w")
            self._input_controls.append(button)
        include = ttk.Checkbutton(inputs, text="Include subfolders", variable=self.recursive_var)
        include.grid(row=1, column=0, columnspan=2, sticky="w", pady=(7, 0))
        self._input_controls.append(include)
        ttk.Label(inputs, textvariable=self.selection_var).grid(row=1, column=2, columnspan=2, sticky="e", pady=(7, 0))

        table_frame = ttk.Frame(outer)
        table_frame.grid(row=3, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        self.table = ttk.Treeview(table_frame, columns=("path", "status", "rows"), show="headings", selectmode="extended", height=8)
        self.table.heading("path", text="Source EAD file")
        self.table.heading("status", text="Status")
        self.table.heading("rows", text="Control points")
        heading_font = tkfont.nametofont("TkHeadingFont")
        body_font = tkfont.nametofont("TkDefaultFont")
        status_width = max(heading_font.measure("Status"), body_font.measure("Processing")) + 32
        rows_width = heading_font.measure("Control points") + 32
        self.table.column("path", width=680, minwidth=heading_font.measure("Source EAD file") + 40, stretch=True)
        self.table.column("status", width=status_width, minwidth=status_width, stretch=False)
        self.table.column("rows", width=rows_width, minwidth=rows_width, stretch=False, anchor="e")
        self.table.grid(row=0, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll = ttk.Scrollbar(table_frame, orient="horizontal", command=self.table.xview)
        xscroll.grid(row=1, column=0, sticky="ew")
        self.table.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.table.tag_configure("Failed", foreground="#9C1C1C")
        self.table.tag_configure("Warning", foreground="#805000")

        destination = ttk.Frame(outer)
        destination.grid(row=4, sticky="ew", pady=(12, 10))
        destination.columnconfigure(1, weight=1)
        ttk.Label(destination, text="Output folder").grid(row=0, column=0, padx=(0, 8), sticky="w")
        output_entry = ttk.Entry(destination, textvariable=self.output_var)
        output_entry.grid(row=0, column=1, sticky="ew")
        browse = ttk.Button(destination, text="Browse", command=self.browse_output)
        browse.grid(row=0, column=2, padx=(8, 0))
        self._input_controls.extend((output_entry, browse))

        run_controls = ttk.Frame(outer)
        run_controls.grid(row=5, sticky="ew")
        run_controls.columnconfigure(2, weight=1)
        self.export_button = ttk.Button(run_controls, text="Export to Excel", command=self.start_export)
        self.export_button.grid(row=0, column=0, sticky="w")
        self.cancel_button = ttk.Button(run_controls, text="Cancel", command=self.cancel, state="disabled")
        self.cancel_button.grid(row=0, column=1, padx=(8, 12))
        ttk.Label(run_controls, textvariable=self.progress_var).grid(row=0, column=2, sticky="e")
        self.progress = ttk.Progressbar(run_controls, mode="determinate", maximum=1)
        self.progress.grid(row=1, column=0, columnspan=3, sticky="ew", pady=(9, 0))
        self.summary_label = ttk.Label(outer, textvariable=self.summary_var, style="Summary.TLabel", wraplength=900)
        self.summary_label.grid(row=6, sticky="ew", pady=(9, 7))
        outer.bind("<Configure>", lambda event: self.summary_label.configure(wraplength=max(200, event.width - 32)))
        self.log_text = ScrolledText(outer, height=6, wrap="word", font="TkTextFont", state="disabled", background="#FFFFFF", foreground="#17212B")
        self.log_text.grid(row=7, sticky="nsew")

        actions = ttk.Frame(outer)
        actions.grid(row=8, sticky="ew", pady=(10, 0))
        actions.columnconfigure(3, weight=1)
        self.open_workbook_button = ttk.Button(actions, text="Open workbook", command=self.open_workbook, state="disabled")
        self.open_workbook_button.grid(row=0, column=0, padx=(0, 7))
        self.open_output_button = ttk.Button(actions, text="Open output folder", command=self.open_output, state="disabled")
        self.open_output_button.grid(row=0, column=1, padx=(0, 7))
        ttk.Button(actions, text="Open log folder", command=self.open_log_folder).grid(row=0, column=2, padx=(0, 7))

    def _key(self, path: str | Path) -> str:
        return os.path.normcase(str(Path(path).resolve()))

    def _log(self, message: str, *, persist: bool = True, error: bool = False) -> None:
        if persist:
            LOGGER.log(logging.ERROR if error else logging.INFO, message)
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"{datetime.now():%H:%M:%S}  {message}\n")
        # Keep a long batch responsive; the complete log remains on disk.
        if int(self.log_text.index("end-1c").split(".")[0]) > 10000:
            self.log_text.delete("1.0", "1001.0")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def add_paths(self, paths: list[str | Path]) -> int:
        if self._running:
            return 0
        additions = [p for p in normalize_inputs(paths) if self._key(p) not in self._items]
        for path in additions:
            self.paths.append(path)
            self._items[self._key(path)] = self.table.insert("", "end", values=(str(path), "Ready", ""))
        self.selection_var.set(f"{len(self.paths):,} files selected")
        if additions:
            self.summary_var.set(f"{len(self.paths):,} EAD XML files ready to export.")
            self._log(f"Added {len(additions):,} XML files; {len(self.paths):,} selected.")
        return len(additions)

    def add_files(self) -> None:
        selected = filedialog.askopenfilenames(parent=self.root, title="Add EAD XML files", initialdir=self._input_initial_directory(), filetypes=[("EAD XML files", "*.xml"), ("All files", "*.*")])
        if selected:
            try:
                self.add_paths(list(selected))
                self._remember_input_directory(Path(selected[0]).parent)
            except OSError as exc:
                self._log(f"Could not add selected files: {exc}", error=True)
                messagebox.showerror("Cannot add files", f"{exc}\n\nCheck that the selected paths are accessible and try again.", parent=self.root)

    def add_folder(self) -> None:
        selected = filedialog.askdirectory(parent=self.root, title="Add a folder of EAD XML files", initialdir=self._input_initial_directory(), mustexist=True)
        if not selected:
            return
        try:
            paths = discover_folder(selected, recursive=self.recursive_var.get())
            added = self.add_paths(paths)
            self._remember_input_directory(Path(selected))
            self._log(f"Folder scanned: {selected} ({len(paths):,} XML files found; {added:,} added).")
            if not paths:
                messagebox.showinfo("No XML files found", "This folder contains no matching XML files. Enable Include subfolders to search nested folders.", parent=self.root)
        except OSError as exc:
            self._log(f"Could not read folder {selected}: {exc}", error=True)
            messagebox.showerror("Cannot read folder", f"{exc}\n\nChoose an accessible folder and try again.", parent=self.root)

    def remove_selected(self) -> None:
        if self._running:
            return
        selected = set(self.table.selection())
        if not selected:
            return
        keys = {key for key, item in self._items.items() if item in selected}
        self.paths = [path for path in self.paths if self._key(path) not in keys]
        for key in keys:
            self.table.delete(self._items.pop(key))
        self.selection_var.set(f"{len(self.paths):,} files selected")
        self.summary_var.set(f"{len(self.paths):,} EAD XML files ready to export.")

    def clear(self) -> None:
        if self._running:
            return
        self.paths.clear()
        self._items.clear()
        self.table.delete(*self.table.get_children())
        self.selection_var.set("0 files selected")
        self.summary_var.set("Add EAD XML files or a folder to begin.")
        self.progress_var.set("Ready")
        self.progress["value"] = 0

    def _dialog_directory(self, candidate: str | Path | None) -> str:
        if candidate is not None and str(candidate).strip():
            try:
                path = Path(str(candidate).strip()).expanduser().resolve()
                for directory in (path, *path.parents):
                    if directory.is_dir():
                        return str(directory)
            except (OSError, ValueError, RuntimeError) as exc:
                LOGGER.warning("Could not use the file-picker start folder %r: %s", str(candidate), exc)
        return str(Path.home().resolve())

    def _input_initial_directory(self) -> str:
        return self._dialog_directory(self._last_input_dir)

    def _remember_input_directory(self, directory: Path) -> None:
        self._last_input_dir = directory.expanduser().resolve()
        self._persist_preferences()

    def _restore_layout_preferences(self) -> None:
        saved_geometry = self._preferences.get("window_geometry", "")
        match = re.fullmatch(r"(\d+)x(\d+)([+-]\d+|\+-\d+)([+-]\d+|\+-\d+)", saved_geometry)
        if match:
            width, height = int(match[1]), int(match[2])
            x, y = (int(token[1:]) if token.startswith("+") else int(token) for token in (match[3], match[4]))
            screen_width = max(640, self.root.winfo_screenwidth() - 40)
            screen_height = max(480, self.root.winfo_screenheight() - 90)
            minimum_width, minimum_height = self.root.minsize()
            width = min(screen_width, max(minimum_width, width))
            height = min(screen_height, max(minimum_height, height))
            x = min(max(0, x), max(0, screen_width - width))
            y = min(max(0, y), max(0, screen_height - height))
            self._normal_geometry = f"{width}x{height}+{x}+{y}"
            self.root.geometry(self._normal_geometry)
        for name, width in self._preferences.get("column_widths", {}).items():
            minimum = self.table.column(name, "minwidth")
            self.table.column(name, width=min(max(minimum, width), self.root.winfo_screenwidth()))
        # A previous minimized state is never saved or restored.
        if self._window_maximized and self.root.state() != "withdrawn":
            self.root.state("zoomed")

    def _layout_changed(self, event: tk.Event) -> None:
        if event.widget is self.root:
            self._schedule_preferences_save()

    def _schedule_preferences_save(self) -> None:
        if self._preferences_after_id is not None:
            self.root.after_cancel(self._preferences_after_id)
        self._preferences_after_id = self.root.after(250, self._persist_preferences)

    def _persist_preferences(self) -> None:
        if self._preferences_after_id is not None:
            self.root.after_cancel(self._preferences_after_id)
            self._preferences_after_id = None
        state = self.root.state()
        if state in ("normal", "withdrawn"):
            geometry = self.root.geometry()
            match = re.match(r"(\d+)x(\d+)", geometry)
            if match and int(match[1]) > 1 and int(match[2]) > 1:
                self._normal_geometry = geometry
            if state == "normal":
                self._window_maximized = False
        elif state == "zoomed":
            self._window_maximized = True
        preferences = {
            "output_directory": self.output_var.get(),
            "include_subfolders": self.recursive_var.get(),
            "window_maximized": self._window_maximized,
            "column_widths": {name: self.table.column(name, "width") for name in ("path", "status", "rows")},
        }
        if self._normal_geometry is not None:
            preferences["window_geometry"] = self._normal_geometry
        if self._last_input_dir is not None:
            preferences["last_input_directory"] = str(self._last_input_dir)
        save_preferences(preferences)

    def browse_output(self) -> None:
        selected = filedialog.askdirectory(parent=self.root, title="Choose an output folder", initialdir=self._dialog_directory(self.output_var.get()), mustexist=True)
        if selected:
            self.output_var.set(str(Path(selected).expanduser().resolve()))

    def _set_running(self, running: bool) -> None:
        self._running = running
        for control in self._input_controls:
            control.configure(state="disabled" if running else "normal")
        self.export_button.configure(state="disabled" if running else "normal")
        self.cancel_button.configure(state="normal" if running else "disabled")

    def start_export(self) -> None:
        if self._running:
            return
        if not self.paths:
            messagebox.showinfo("Add EAD files", "Add at least one EAD XML file before exporting.", parent=self.root)
            return
        if not self.output_var.get().strip():
            messagebox.showinfo("Choose an output folder", "Enter or browse to the folder for the Excel workbook.", parent=self.root)
            return
        output_dir = Path(self.output_var.get().strip()).expanduser().resolve()
        inputs = list(self.paths)
        self._cancel = threading.Event()
        self._last_result = None
        self.open_workbook_button.configure(state="disabled")
        self.open_output_button.configure(state="disabled")
        self._set_running(True)
        for path in self.paths:
            self.table.item(self._items[self._key(path)], values=(str(path), "Queued", ""), tags=())
        self.progress.configure(maximum=len(inputs), value=0)
        self.progress_var.set(f"0 / {len(inputs):,} files")
        self.summary_var.set("Processing EAD files…")
        self._worker = threading.Thread(target=self._run_worker, args=(inputs, output_dir), name="ead-export", daemon=True)
        self._worker.start()

    def _run_worker(self, inputs: list[Path], output_dir: Path) -> None:
        try:
            from .runner import run_export
            result = run_export(inputs, output_dir, cancel_event=self._cancel, on_event=self._events.put)
            self._events.put(("done", result))
        except Exception as exc:
            LOGGER.exception("Export failed")
            self._events.put(("error", str(exc)))

    def cancel(self) -> None:
        if self._running:
            self._cancel.set()
            self.cancel_button.configure(state="disabled")
            self.summary_var.set("Canceling… waiting for the current safe checkpoint.")
            self._log("Cancellation requested.")

    def _poll_events(self) -> None:
        self._poll_after_id = None
        try:
            for _ in range(500):
                try:
                    event = self._events.get_nowait()
                except queue.Empty:
                    break
                if isinstance(event, tuple):
                    kind, payload = event
                    if kind == "done":
                        self._finish(payload)
                    else:
                        self._set_running(False)
                        self.summary_var.set("Export failed. See the log below for details.")
                        self.progress_var.set("Failed")
                        self._log(f"Export failed: {payload}", persist=False, error=True)
                        if not self._close_when_done:
                            messagebox.showerror("Could not export workbook", f"{payload}\n\nCheck the output folder, permissions, and available disk space. You can open the log folder for details.", parent=self.root)
                        else:
                            self.root.destroy()
                            return
                    continue
                self._handle_event(event)
            self._poll_after_id = self.root.after(75, self._poll_events)
        except tk.TclError:
            # The window may have been closed after the final worker event.
            return

    def _on_destroy(self, event: tk.Event) -> None:
        if event.widget is self.root and self._preferences_after_id is not None:
            try:
                self.root.after_cancel(self._preferences_after_id)
            except tk.TclError:
                pass
            self._preferences_after_id = None
        if event.widget is self.root and self._poll_after_id is not None:
            try:
                self.root.after_cancel(self._poll_after_id)
            except tk.TclError:
                pass
            self._poll_after_id = None

    def _handle_event(self, event: RunEvent) -> None:
        if event.message:
            self._log(event.message, persist=False)
        if event.source_file:
            item = self._items.get(self._key(event.source_file))
            if item and event.kind in ("file_started", "file_finished"):
                status = "Processing" if event.kind == "file_started" else event.status
                rows = "" if event.kind == "file_started" else f"{event.rows:,}"
                self.table.item(item, values=(event.source_file, status, rows), tags=(status,))
                self.table.see(item)
        if event.kind in ("progress", "file_finished"):
            self.progress["value"] = event.current
            self.progress_var.set(f"{event.current:,} / {event.total:,} files")

    def _finish(self, result: RunResult) -> None:
        self._last_result = result
        self._set_running(False)
        counts = (f"{result.files_processed:,}/{result.files_total:,} files processed · "
                  f"{result.entries:,} control points · {result.warnings:,} warnings · {result.failures:,} failed files")
        if result.cancelled:
            self.summary_var.set(f"Canceled. {counts}. No workbook saved.")
            self.progress_var.set("Canceled")
            for path in self.paths:
                item = self._items[self._key(path)]
                values = self.table.item(item, "values")
                if values[1] in ("Queued", "Processing"):
                    self.table.item(item, values=(str(path), "Canceled", ""))
        else:
            state = "Completed with issues" if result.warnings or result.failures else "Completed"
            self.summary_var.set(f"{state}. {counts}.")
            self.progress_var.set(state)
            self.progress["value"] = result.files_processed
        if result.workbook:
            self.open_workbook_button.configure(state="normal")
            self.open_output_button.configure(state="normal")
            self._log(f"Workbook saved: {result.workbook}", persist=False)
        if self._close_when_done:
            self._persist_preferences()
            self.root.destroy()

    def _open_path(self, path: Path) -> None:
        try:
            os.startfile(str(path.expanduser().resolve()))
        except (OSError, AttributeError) as exc:
            self._log(f"Could not open {path}: {exc}", error=True)
            messagebox.showerror("Could not open item", f"{path}\n\n{exc}", parent=self.root)

    def open_log_folder(self) -> None:
        try:
            log_dir = ensure_log_dir()
        except OSError as exc:
            self._log(f"Could not create or open the log folder: {exc}", error=True)
            messagebox.showerror("Could not open log folder", f"{exc}\n\nCheck the application folder permissions.", parent=self.root)
            return
        self._open_path(log_dir)

    def open_workbook(self) -> None:
        if self._last_result and self._last_result.workbook:
            self._open_path(self._last_result.workbook)

    def open_output(self) -> None:
        if self._last_result and self._last_result.workbook:
            self._open_path(self._last_result.workbook.parent)

    def about(self) -> None:
        dialog = tk.Toplevel(self.root)
        dialog.title("About — Control Point Exporter")
        dialog.transient(self.root)
        dialog.geometry(f"{min(850, self.root.winfo_screenwidth() - 80)}x{min(650, self.root.winfo_screenheight() - 120)}")
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(1, weight=1)
        ttk.Label(dialog, text=f"Control Point Exporter {__version__}", style="Title.TLabel", padding=12).grid(row=0, sticky="w")
        text = ScrolledText(dialog, wrap="word", font="TkTextFont", padx=12, pady=10)
        text.grid(row=1, sticky="nsew")
        try:
            notices = resources.files("control_point_exporter").joinpath("resources/THIRD_PARTY_NOTICES.txt").read_text(encoding="utf-8")
        except (OSError, FileNotFoundError):
            LOGGER.exception("Could not read bundled third-party notices")
            notices = "Third-party notices could not be loaded. Please consult the notices included with this distribution."
        text.insert("1.0", "Extract EAD XML control points into Excel with source attribution.\n\n" + notices)
        text.configure(state="disabled")
        ttk.Button(dialog, text="Close", command=dialog.destroy).grid(row=2, sticky="e", padx=12, pady=10)
        dialog.bind("<Escape>", lambda event: dialog.destroy())

    def _callback_error(self, exc_type: type[BaseException], exc: BaseException, traceback: object) -> None:
        LOGGER.error("Unexpected GUI error", exc_info=(exc_type, exc, traceback))
        self._log(f"Unexpected interface error: {exc}", persist=False, error=True)
        messagebox.showerror("Unexpected error", f"{exc}\n\nDetails were saved to the application log.", parent=self.root)

    def close(self) -> None:
        if self._running:
            if not messagebox.askyesno("Cancel export and close?", "An export is running. Cancel it and close after the current safe checkpoint?", parent=self.root):
                return
            self._close_when_done = True
            self._persist_preferences()
            self.cancel()
        else:
            self._persist_preferences()
            self.root.destroy()


def launch() -> int:
    _enable_dpi_awareness()
    root = tk.Tk()
    ExporterApp(root)
    root.mainloop()
    return 0
