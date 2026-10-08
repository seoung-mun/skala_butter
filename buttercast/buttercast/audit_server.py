"""Dedicated local server for reproducible verification, with a separate store."""
import argparse
from pathlib import Path

import uvicorn

from .app import create_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8093)
    args = parser.parse_args()
    uvicorn.run(create_app(args.root), host="127.0.0.1", port=args.port, access_log=False)


if __name__ == "__main__":
    main()
