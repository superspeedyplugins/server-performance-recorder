#!/usr/bin/env python3

from __future__ import annotations

import argparse
import fcntl
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TERMINAL = {"validated", "failed", "stopped"}


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            json.dump(value, target, indent=2, sort_keys=True)
            target.write("\n")
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def mutate(path: Path, callback) -> dict[str, Any]:
    lock = path.with_suffix(".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open("a+", encoding="utf-8") as descriptor:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        callback(value)
        value["updated_at"] = now()
        atomic_json(path, value)
        return value


def parse_value(raw: str) -> Any:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def set_dotted(value: dict[str, Any], dotted: str, item: Any) -> None:
    current = value
    parts = dotted.split(".")
    for part in parts[:-1]:
        child = current.setdefault(part, {})
        if not isinstance(child, dict):
            raise ValueError(f"Cannot set {dotted}: {part} is not an object")
        current = child
    current[parts[-1]] = item


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--run-id", required=True)
    init.add_argument("--label", required=True)
    update = sub.add_parser("set")
    update.add_argument("pairs", nargs="+")
    heartbeat = sub.add_parser("heartbeat")
    heartbeat.add_argument("--progress", default="")
    failure = sub.add_parser("fail")
    failure.add_argument("message")
    sub.add_parser("show")
    args = parser.parse_args()

    if args.command == "init":
        def initialise(value: dict[str, Any]) -> None:
            if value:
                raise ValueError("Refusing to overwrite an existing manifest")
            value.update({
                "schema_version": 1,
                "run_id": args.run_id,
                "label": args.label,
                "status": "planned",
                "created_at": now(),
                "errors": [],
            })
        mutate(args.manifest, initialise)
    elif args.command == "set":
        def apply(value: dict[str, Any]) -> None:
            for pair in args.pairs:
                if "=" not in pair:
                    raise ValueError(f"Expected key=value, got {pair!r}")
                key, raw = pair.split("=", 1)
                set_dotted(value, key, parse_value(raw))
        mutate(args.manifest, apply)
    elif args.command == "heartbeat":
        def beat(value: dict[str, Any]) -> None:
            value["heartbeat_at"] = now()
            value["heartbeat_epoch"] = int(datetime.now().timestamp())
            if args.progress:
                value["progress"] = args.progress
        mutate(args.manifest, beat)
    elif args.command == "fail":
        def fail(value: dict[str, Any]) -> None:
            if value.get("status") in TERMINAL:
                return
            value["status"] = "failed"
            value["failed_at"] = now()
            if args.message not in value.setdefault("errors", []):
                value["errors"].append(args.message)
        mutate(args.manifest, fail)
    else:
        print(args.manifest.read_text(encoding="utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
