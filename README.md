# Control Point Exporter

[![AI-DECLARATION: copilot](https://img.shields.io/badge/%E4%B7%BC%20AI--DECLARATION-copilot-fee2e2?labelColor=fee2e2)](AI-DECLARATION.md)

A small Windows desktop app that extracts control points from one or many EAD XML finding aids into Excel. Each row includes the control point's source attribute, finding aid ID, collection title, and source file. Input XML files are left unchanged.

Supports EAD2002 and EAD3 XML. Built with Python, Tkinter, lxml, and openpyxl.

## Build on Windows

Use native Windows 10/11 x64 on Intel or AMD. Building needs Git and CPython 3.12 x64 with Tcl/Tk. The recommended [uv](https://docs.astral.sh/uv/) setup installs the pinned Python runtime and dependencies for you.

Run in PowerShell:

```powershell
git clone https://github.com/vauxra/control-point-exporter.git
cd control-point-exporter
uv sync --locked --group dev
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\Build.ps1 -OutputDirectory dist\windows\1.0.2
```

The app is `dist\windows\1.0.2\ControlPointExporter.exe`. Users of the EXE need no Python, Excel, or separate graphics card.

## Run or test the source

```powershell
uv run python -m control_point_exporter
uv run python -m pytest -q
```

## Without uv

With Python 3.12 x64 and Tcl/Tk already installed:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt -e .
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\Build.ps1 -SkipSync -OutputDirectory dist\windows\1.0.2
```

`requirements.txt` contains runtime dependencies; `requirements-dev.txt` also installs the test and build tools. The build script runs tests before creating the EXE.

Application source is [MIT licensed](LICENSE). Third-party components retain their own licenses, included in the app's About view.
