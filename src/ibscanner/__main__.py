"""Entry point: `python -m ibscanner` or `ibscanner` (after install)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import load_config
from .tui import ScannerApp


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ibscanner",
        description="Stock market scanner TUI for Interactive Brokers.",
    )
    parser.add_argument(
        "-c",
        "--config",
        default="scanners.yaml",
        help="path to scanners YAML config (default: ./scanners.yaml)",
    )
    args = parser.parse_args()

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"config not found: {config_path}", file=sys.stderr)
        print(
            "Copy scanners.example.yaml to scanners.yaml and edit it.",
            file=sys.stderr,
        )
        sys.exit(1)

    config = load_config(config_path)
    if not config.scanners:
        print("no scanners defined in config", file=sys.stderr)
        sys.exit(1)

    ScannerApp(config).run()


if __name__ == "__main__":
    main()
