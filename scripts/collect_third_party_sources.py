"""Create the source/relink archive shipped beside the Windows EXE.

Downloads are immutable and hash-checked. Upstream trees are written into the
output ZIP without running upstream code or extracting it onto the computer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import tarfile
from urllib.request import urlopen
import zipfile

from control_point_exporter import __version__

ROOT = Path(__file__).resolve().parents[1]


def safe_relative(name: str) -> str:
    parts = PurePosixPath(name).parts
    if not parts or any(p in {"..", "/"} or "\\" in p or ":" in p for p in parts):
        raise ValueError(f"Unsafe source archive path: {name!r}")
    return "/".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist/windows" / __version__)
    parser.add_argument("--cache-dir", type=Path, default=ROOT / ".tools/upstream-sources")
    args = parser.parse_args()
    manifest_path = ROOT / "packaging/third-party-sources.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    cache = args.cache_dir.resolve()
    cache.mkdir(parents=True, exist_ok=True)
    verified = []
    for item in manifest["sources"]:
        path = cache / item["filename"]
        if not path.exists():
            data = urlopen(item["url"], timeout=60).read()
            if len(data) != item["size_bytes"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
                raise ValueError(f"Source download hash/size mismatch: {item['id']}")
            path.write_bytes(data)
        data = path.read_bytes()
        if len(data) != item["size_bytes"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise ValueError(f"Cached source hash/size mismatch: {path}; remove the damaged cache file and retry.")
        verified.append((item, path))
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    archive_path = output / f"ControlPointExporter-{__version__}-third-party-sources.zip"
    written: set[str] = set()
    hashes: dict[str, str] = {}
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as target:
        def write(name: str, data: bytes) -> None:
            name = safe_relative(name)
            if name in written:
                raise ValueError(f"Duplicate source archive member: {name}")
            written.add(name)
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            target.writestr(info, data)
            hashes[name] = hashlib.sha256(data).hexdigest()

        for item, path in verified:
            destination = item["destination"]
            if item["format"] == "zip":
                with zipfile.ZipFile(path) as source:
                    for member in source.infolist():
                        if member.is_dir():
                            continue
                        relative = safe_relative(member.filename).partition("/")[2]
                        if relative:
                            write(f"{destination}/{relative}", source.read(member))
            else:
                with tarfile.open(path, "r:gz") as source:
                    for member in source.getmembers():
                        if not member.isfile():
                            continue
                        relative = safe_relative(member.name).partition("/")[2]
                        stream = source.extractfile(member)
                        if relative and stream:
                            write(f"{destination}/{relative}", stream.read())

        root_files = [p for p in ROOT.iterdir() if p.is_file() and (
            p.name in {".gitignore", ".python-version", "LICENSE", "launcher.py", "pyproject.toml", "uv.lock", "requirements.txt", "requirements-dev.txt"}
            or p.suffix.lower() == ".md" and p.name not in {"PLAN.md", "VALIDATION.md"})]
        app_files = list(root_files)
        for directory in ("src", "tests", "packaging", "scripts"):
            app_files.extend(p for p in (ROOT / directory).rglob("*") if p.is_file()
                             and "__pycache__" not in p.parts and p.suffix not in {".pyc", ".pyo"})
        for path in sorted(set(app_files)):
            write("application-source/" + path.relative_to(ROOT).as_posix(), path.read_bytes())
        write("SOURCE-MANIFEST.json", manifest_path.read_bytes())
        write("README-REBUILD.md", (ROOT / "packaging/REBUILD_THIRD_PARTY.md").read_bytes())
        write("SOURCE-FILE-SHA256.json", (json.dumps(hashes, indent=2, sort_keys=True) + "\n").encode())
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    archive_path.with_name(archive_path.name + ".sha256").write_text(f"{digest}  {archive_path.name}\n", encoding="ascii")
    print(json.dumps({"archive": str(archive_path), "sha256": digest, "size_bytes": archive_path.stat().st_size,
                      "source_files": len(written), "verified_downloads": len(verified),
                      "includes_private_corpus": False}, indent=2))


if __name__ == "__main__":
    main()
