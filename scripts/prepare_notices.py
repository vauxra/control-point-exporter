"""Collect actual locked dependency/runtime license texts for the EXE."""
from importlib import metadata
from pathlib import Path
import sys
import tkinter

from control_point_exporter import __version__

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "src/control_point_exporter/resources/THIRD_PARTY_NOTICES.txt"


def main() -> None:
    sections = [
        f"Control Point Exporter {__version__} — third-party notices\n"
        "The application bundles these components. Their original licenses follow.\n"
        "PyInstaller's license includes its bootloader distribution exception.\n"
        "Source and license references: https://www.python.org/; https://www.tcl.tk/; "
        "https://lxml.de/; https://openpyxl.readthedocs.io/; https://pyinstaller.org/.\n"
        "libiconv source releases: https://ftp.gnu.org/pub/gnu/libiconv/; "
        "lxml source releases: https://pypi.org/project/lxml/#files.\n"
        "This application includes GNU libiconv under LGPL 2.1. Corresponding "
        "source and Windows rebuild/relink materials are distributed with this release at:\n"
        f"https://github.com/vauxra/control-point-exporter/releases/download/v{__version__}/"
        f"ControlPointExporter-{__version__}-third-party-sources.zip\n"
        "The application's own code is MIT-licensed; the third-party components "
        "retain their respective licenses below.\n"
    ]

    def add(title: str, path: Path) -> None:
        if not path.is_file():
            raise FileNotFoundError(f"Required license missing: {path}")
        sections.append(f"\n{'=' * 72}\n{title}\n{'=' * 72}\n{path.read_text(encoding='utf-8', errors='replace')}")

    add("Control Point Exporter — MIT License", ROOT / "LICENSE")
    add(f"CPython {sys.version.split()[0]}", Path(sys.base_prefix) / "LICENSE.txt")
    tcl = tkinter.Tcl()
    tcl_dir = Path(tcl.eval("info library"))
    add(f"Tcl {tcl.call('info', 'patchlevel')}", ROOT / "packaging/licenses/TCL_LICENSE.txt")
    tk_license = tcl_dir.parent / "tk8.6/license.terms"
    add("Tk", tk_license)
    for name, title in (
        ("OPENSSL_LICENSE.txt", "OpenSSL (Apache License 2.0)"),
        ("LIBFFI_LICENSE.txt", "libffi"),
        ("ZLIB_LICENSE.txt", "zlib"),
        ("LGPL-2.1.txt", "GNU LGPL 2.1 — libiconv, referenced by the lxml wheel notices"),
        ("EXPAT_LICENSE.txt", "Expat"),
        ("LIBMPDEC_LICENSE.txt", "libmpdec"),
        ("ELEMENTTREE_LICENSE.txt", "ElementTree"),
    ):
        add(title, ROOT / "packaging/licenses" / name)
    for name in ("lxml", "openpyxl", "et-xmlfile", "pyinstaller", "pyinstaller-hooks-contrib"):
        dist = metadata.distribution(name)
        licenses = [f for f in (dist.files or []) if any(s in f.name.lower() for s in ("license", "licence", "copying"))]
        if not licenses:
            raise RuntimeError(f"No installed license texts found for {name}.")
        for item in licenses:
            add(f"{name} {dist.version} — {item.name}", Path(dist.locate_file(item)))
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    DESTINATION.write_text("\n".join(sections), encoding="utf-8")
    print(f"Wrote {DESTINATION.relative_to(ROOT)} ({DESTINATION.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
