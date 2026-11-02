"""Local rotating operational logs; never log XML contents."""
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path


def get_log_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    return (base / "ControlPointExporter" / "logs").resolve()


def ensure_log_dir() -> Path:
    log_dir = get_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    # Packaged Windows hosts can redirect LocalAppData. Explorer runs outside
    # that context and needs the physical path, rather than its virtual alias.
    return log_dir.resolve()


def configure_logging() -> Path:
    log_dir = ensure_log_dir()
    logger = logging.getLogger("control_point_exporter")
    logger.setLevel(logging.INFO)
    if not any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
        handler = RotatingFileHandler(log_dir / "app.log", maxBytes=5 * 1024 * 1024,
                                      backupCount=4, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    return log_dir
