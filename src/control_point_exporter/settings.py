"""Atomic, nonfatal per-user preferences for controls and window layout."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import re
import tempfile

from .logging_setup import get_log_dir

LOGGER = logging.getLogger("control_point_exporter")


def get_settings_path() -> Path:
    return get_log_dir().parent.resolve() / "settings.json"


def _validated_preferences(content: object) -> dict:
    if not isinstance(content, dict):
        raise ValueError("Preferences must be a JSON object.")
    cleaned = dict(content)
    validators = {
        "last_input_directory": lambda value: isinstance(value, str) and bool(value.strip()),
        "output_directory": lambda value: isinstance(value, str),
        "include_subfolders": lambda value: isinstance(value, bool),
        "window_geometry": lambda value: isinstance(value, str) and len(value) < 100 and bool(re.fullmatch(r"\d+x\d+(?:[+-]\d+|\+-\d+){2}", value)),
        "window_maximized": lambda value: isinstance(value, bool),
    }
    for key, valid in validators.items():
        if key in cleaned and not valid(cleaned[key]):
            LOGGER.warning("Ignoring invalid saved preference: %s", key)
            cleaned.pop(key)
    if "column_widths" in cleaned:
        widths = cleaned["column_widths"]
        if not isinstance(widths, dict):
            LOGGER.warning("Ignoring invalid saved preference: column_widths")
            cleaned.pop("column_widths")
        else:
            cleaned["column_widths"] = {
                name: value for name, value in widths.items()
                if name in ("path", "status", "rows") and isinstance(value, int)
                and not isinstance(value, bool) and 0 < value <= 10000
            }
    return cleaned


def load_preferences() -> dict:
    try:
        settings_path = get_settings_path()
        if not settings_path.exists():
            return {}
        content = json.loads(settings_path.read_text(encoding="utf-8"))
        return _validated_preferences(content)
    except (OSError, ValueError, RuntimeError) as exc:
        LOGGER.warning("Could not read file-picker preferences: %s", exc)
        return {}


def save_preferences(updates: dict) -> bool:
    temporary_path: Path | None = None
    try:
        settings_path = get_settings_path()
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        # Resolve after creation as packaged Windows app-data aliases can be redirected.
        settings_path = settings_path.parent.resolve() / settings_path.name
        content = load_preferences()
        content.update(updates)
        content = _validated_preferences(content)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix="settings-", suffix=".tmp",
                                         dir=settings_path.parent, delete=False) as stream:
            temporary_path = Path(stream.name)
            json.dump(content, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, settings_path)
        temporary_path = None
        return True
    except (OSError, ValueError, RuntimeError) as exc:
        LOGGER.warning("Could not save file-picker preferences: %s", exc)
        return False
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                LOGGER.warning("Could not remove temporary preferences file: %s", temporary_path)


def load_last_input_directory() -> Path | None:
    value = load_preferences().get("last_input_directory")
    if value is not None:
        try:
            return Path(value).expanduser().resolve()
        except (OSError, ValueError, RuntimeError) as exc:
            LOGGER.warning("Could not use saved input folder: %s", exc)
    return None


def save_last_input_directory(directory: Path) -> bool:
    try:
        value = str(Path(directory).expanduser().resolve())
        return save_preferences({"last_input_directory": value})
    except (OSError, ValueError, RuntimeError) as exc:
        LOGGER.warning("Could not save file-picker preferences: %s", exc)
        return False
