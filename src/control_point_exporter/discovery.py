"""Deterministic XML discovery without following directory links."""
from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Iterable


def _is_link_directory(path: Path) -> bool:
    info = path.lstat()
    return path.is_symlink() or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    )


def normalize_inputs(paths: Iterable[str | Path]) -> list[Path]:
    """Keep XML paths in selection order, deduplicating their resolved paths.

    Missing XML paths remain inputs so processing can report a per-file error.
    Directories and non-XML paths are not accepted as file selections.
    """
    output: list[Path] = []
    seen: set[str] = set()
    for supplied in paths:
        path = Path(supplied).expanduser()
        if path.suffix.lower() != ".xml" or path.is_dir():
            continue
        path = path.resolve()
        key = os.path.normcase(str(path))
        if key not in seen:
            seen.add(key)
            output.append(path)
    return output


def discover_folder(folder: str | Path, recursive: bool = False) -> list[Path]:
    """Return path-sorted XML files; inaccessible folders raise an OSError."""
    root = Path(folder).expanduser()
    if not root.exists():
        raise FileNotFoundError(f"The selected folder does not exist: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"The selected path is not a folder: {root}")
    if _is_link_directory(root):
        raise OSError("Select the actual folder rather than a directory link or junction.")
    root = root.resolve()
    found: list[Path] = []
    pending = [root]
    while pending:
        current = pending.pop()
        with os.scandir(current) as entries:
            for entry in entries:
                path = Path(entry.path)
                if entry.is_dir(follow_symlinks=False):
                    if recursive and not _is_link_directory(path):
                        pending.append(path)
                elif path.suffix.lower() == ".xml" and entry.is_file():
                    found.append(path)
    return normalize_inputs(sorted(found, key=lambda p: (str(p).casefold(), str(p))))
