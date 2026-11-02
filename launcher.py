"""PyInstaller entry point. Application code is shared with the source CLI."""
from control_point_exporter.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
