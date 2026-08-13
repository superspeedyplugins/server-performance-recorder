#!/usr/bin/env python3

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    output = run / "SHA256SUMS"
    ignored = {output.resolve(), (run / "run.json").resolve(), (run / "run.lock").resolve()}
    files = sorted(path for path in run.rglob("*") if path.is_file() and path.resolve() not in ignored and not path.name.startswith(".run.json."))
    temporary = output.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as target:
        for path in files:
            target.write(f"{digest(path)}  {path.relative_to(run)}\n")
        target.flush()
        os.fsync(target.fileno())
    temporary.replace(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
