#!/usr/bin/env python3

from __future__ import annotations

import argparse
import os
import zipfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Create one atomic ZIP from a recorder evidence directory")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    run = args.run_dir.resolve()
    if not run.is_dir() or not (run / "run.json").is_file():
        raise SystemExit(f"Not a recorder run directory: {run}")
    output = (args.output or run.with_name(run.name + ".zip")).resolve()
    try:
        output.relative_to(run)
    except ValueError:
        pass
    else:
        raise SystemExit("Archive output must be outside the evidence directory")

    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp.{os.getpid()}")
    try:
        with zipfile.ZipFile(
            temporary,
            mode="w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
            strict_timestamps=False,
        ) as archive:
            for path in sorted(run.rglob("*")):
                if path.is_file():
                    archive.write(path, Path(run.name) / path.relative_to(run))
        os.chmod(temporary, 0o600)
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
