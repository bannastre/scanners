"""Entry point: `python -m ibscanner` or `ibscanner` (after install)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn

from .config import load_config
from .web import create_app


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="ibscanner",
        description="Stock market scanner web app for Interactive Brokers.",
    )
    parser.add_argument(
        "-c",
        "--config",
        default="scanners.yaml",
        help="path to scanners YAML config (default: ./scanners.yaml)",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="host to bind to (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="port to listen on (default: 8000)",
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

    uvicorn.run(create_app(config), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
