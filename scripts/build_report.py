"""Record the exact packaged binary, runtime and locked build environment."""
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path
import argparse
import hashlib
import json
import platform
import struct
import sys
import tkinter

from control_point_exporter import __version__

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exe", type=Path, default=ROOT / "dist/windows/ControlPointExporter.exe")
    args = parser.parse_args()
    binary = args.exe.resolve()
    from PyInstaller.utils.win32.versioninfo import read_version_info_from_executable
    version_info = read_version_info_from_executable(str(binary))
    if version_info is None:
        raise ValueError("The executable has no Windows version information.")
    file_version = (version_info.ffi.fileVersionMS >> 16, version_info.ffi.fileVersionMS & 0xFFFF,
                    version_info.ffi.fileVersionLS >> 16, version_info.ffi.fileVersionLS & 0xFFFF)
    expected_version = tuple(map(int, __version__.split("."))) + (0,)
    if file_version != expected_version:
        raise ValueError(f"Executable version {file_version} does not match source version {__version__}; build the intended release first.")
    with binary.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    (binary.parent / f"{binary.name}.sha256").write_text(f"{digest}  {binary.name}\n", encoding="ascii")
    from lxml import etree
    from PyInstaller.archive.readers import CArchiveReader
    archive_names = [name.replace("\\", "/") for name in CArchiveReader(str(binary)).toc]
    required_runtime = ["python312.dll", "_tkinter.pyd", "tcl86t.dll", "tk86t.dll",
                        "control_point_exporter/resources/THIRD_PARTY_NOTICES.txt"]
    assert all(name in archive_names for name in required_runtime), "Packaged runtime component missing."
    assert any(name.startswith("lxml/etree.") and name.endswith(".pyd") for name in archive_names)
    assert not any(name.startswith("data/ead/") for name in archive_names), "Corpus must not be bundled."
    source_paths = [ROOT / name for name in ("launcher.py", "pyproject.toml", "uv.lock", ".python-version", "LICENSE")]
    for directory in (ROOT / "src", ROOT / "packaging", ROOT / "scripts"):
        source_paths.extend(p for p in directory.rglob("*")
                            if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
    source_hashes = {str(p.relative_to(ROOT)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in sorted(source_paths)}
    report = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "application_version": __version__,
        "windows_file_version": ".".join(map(str, file_version)),
        "artifact": binary.name,
        "size_bytes": binary.stat().st_size,
        "sha256": digest,
        "python": sys.version,
        "architecture_bits": struct.calcsize("P") * 8,
        "build_os": platform.platform(),
        "tcl": tkinter.Tcl().call("info", "patchlevel"),
        "libxml2": ".".join(map(str, etree.LIBXML_VERSION)),
        "libxslt": ".".join(map(str, etree.LIBXSLT_VERSION)),
        "uv_lock_sha256": hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
        "source_sha256": source_hashes,
        "archive_checks": {"required_components_present": required_runtime + ["lxml/etree extension"],
                           "corpus_absent": True},
        "locked_environment_packages": dict(sorted((d.metadata["Name"], d.version) for d in metadata.distributions())),
        "packaging": "PyInstaller single-file/windowed; Windows x64; no administrator privileges required",
        "scope": "The EAD corpus is external input and is not embedded in the EXE.",
    }
    path = binary.parent / "build-report.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"SHA256 {digest}\nWrote {path}")


if __name__ == "__main__":
    main()
