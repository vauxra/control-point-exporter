"""GUI entry point with a report-producing headless acceptance interface."""
import argparse
from dataclasses import asdict
import json
import logging
from pathlib import Path
import platform
import sys

from . import __version__
from .logging_setup import configure_logging


def _write_report(stream, report):
    if stream is not None:
        stream.seek(0)
        stream.truncate()
        stream.write(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        stream.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Extract EAD XML control points to Excel.")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--headless", action="store_true", help="Convert without opening a GUI.")
    parser.add_argument("--output-dir", type=Path, help="Existing folder for the workbook.")
    parser.add_argument("--report", type=Path, help="Write a JSON run report.")
    parser.add_argument("--recursive", action="store_true", help="Include subfolders of input folders.")
    parser.add_argument("inputs", type=Path, nargs="*", help="XML files or folders.")
    args = parser.parse_args(argv)
    report_stream = None
    try:
        if args.headless and args.report is not None:
            if args.report.suffix.lower() != ".json":
                raise ValueError("The report filename must end in .json; source files and workbooks cannot be report destinations.")
            # Reserve before exporting, refusing to overwrite any existing file.
            report_stream = args.report.open("x", encoding="utf-8")
        configure_logging()
        logger = logging.getLogger("control_point_exporter")
        logger.info("Control Point Exporter %s started; Python %s; %s; frozen=%s", __version__, platform.python_version(), platform.platform(), bool(getattr(sys, "frozen", False)))
        if not args.headless:
            if args.inputs or args.output_dir or args.report or args.recursive:
                parser.error("Conversion arguments require --headless.")
            from .gui import launch
            return launch()
        if args.output_dir is None or not args.inputs:
            raise ValueError("--headless requires --output-dir and one or more XML files or folders.")
        from .discovery import discover_folder, normalize_inputs
        from .runner import run_export
        paths = []
        for source in args.inputs:
            if source.is_dir():
                paths.extend(discover_folder(source, recursive=args.recursive))
            elif source.suffix.lower() == ".xml":
                paths.append(source)
            else:
                raise ValueError(f"Only .xml files and folders are supported: {source}")
        paths = normalize_inputs(paths)
        if not paths:
            raise ValueError("No XML files were selected or found.")
        result = run_export(paths, args.output_dir)
        report = asdict(result)
        report.update(version=__version__, python=platform.python_version(), frozen=bool(getattr(sys, "frozen", False)))
        _write_report(report_stream, report)
        if sys.stdout is not None:
            print(json.dumps(report, ensure_ascii=False, default=str))
        return 130 if result.cancelled else 1 if result.failures else 0
    except Exception as exc:
        logging.getLogger("control_point_exporter").exception("Application error")
        try:
            _write_report(report_stream, {"error": str(exc), "version": __version__})
        except OSError:
            pass
        if args.headless:
            if sys.stderr is not None:
                print(f"Error: {exc}", file=sys.stderr)
        else:
            try:
                from tkinter import messagebox
                messagebox.showerror("Control Point Exporter", f"The application could not start: {exc}")
            except Exception:
                import ctypes
                ctypes.windll.user32.MessageBoxW(None, f"The application could not start: {exc}", "Control Point Exporter", 0x10)
        return 2
    finally:
        if report_stream is not None:
            report_stream.close()
