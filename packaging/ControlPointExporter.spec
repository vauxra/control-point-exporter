# Build on Windows x64 with the project's locked environment.
from pathlib import Path

project = Path(SPECPATH).parent
resources = project / "src" / "control_point_exporter" / "resources"

a = Analysis(
    [str(project / "launcher.py")],
    pathex=[str(project / "src")],
    binaries=[],
    datas=[(str(resources / "THIRD_PARTY_NOTICES.txt"), "control_point_exporter/resources")],
    hiddenimports=["tkinter", "tkinter.ttk", "tkinter.filedialog", "tkinter.messagebox", "lxml.etree", "openpyxl.cell._writer"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "unittest", "pydoc", "doctest", "numpy", "pandas", "matplotlib", "PIL", "lxml.isoschematron"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ControlPointExporter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    version=str(project / "packaging" / "version_info.txt"),
    manifest=str(project / "packaging" / "app.manifest"),
    uac_admin=False,
)
