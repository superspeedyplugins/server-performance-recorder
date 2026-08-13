#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import hashlib
import json
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
    parser.add_argument("--verify-checksums", action="store_true")
    args = parser.parse_args()
    run = args.run_dir.resolve()
    failures: list[str] = []
    warnings: list[str] = []
    required = ("run.json", "config/effective.json", "telemetry/system.csv", "summary.json", "report.md")
    for relative in required:
        path = run / relative
        if not path.is_file() or path.stat().st_size == 0:
            failures.append(f"missing or empty: {relative}")
    if failures:
        print("\n".join(f"FAIL: {item}" for item in failures))
        return 1

    manifest = json.loads((run / "run.json").read_text(encoding="utf-8"))
    effective = json.loads((run / "config/effective.json").read_text(encoding="utf-8"))
    summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    if manifest.get("status") not in {"summarised", "validated"}:
        failures.append(f"run status is {manifest.get('status')!r}")
    if manifest.get("errors"):
        failures.append(f"run contains errors: {manifest['errors']!r}")
    coverage = summary.get("coverage", {}).get("percent")
    if coverage is None or coverage < 95:
        failures.append(f"sample coverage is {coverage!r}, expected at least 95%")
    if effective.get("sample_interval_seconds", 0) < 10:
        failures.append("sample interval was below the 10-second live-traffic safety floor")
    samples = list(csv.DictReader((run / "telemetry/system.csv").open(encoding="utf-8")))
    if len(samples) < 2:
        failures.append("fewer than two system samples")
    required_columns = (
        "epoch", "cpu_user", "cpu_system", "cpu_idle", "cpu_iowait",
        "mem_total_kb", "mem_available_kb", "load1",
        "disk_read_sectors", "disk_write_sectors", "disk_io_ms",
    )
    for index, row in enumerate(samples, 1):
        missing = [column for column in required_columns if row.get(column, "") == ""]
        if missing:
            failures.append(f"sample {index} has empty required fields: {missing!r}")
            break
    try:
        epochs = [int(row["epoch"]) for row in samples]
        if any(right <= left for left, right in zip(epochs, epochs[1:])):
            failures.append("sample epochs are not strictly increasing")
    except (KeyError, TypeError, ValueError):
        failures.append("sample epochs are invalid")
    intended_duration = int(effective.get("duration_seconds", 0))
    observed_duration = int(summary.get("coverage", {}).get("observed_seconds", 0))
    if intended_duration and observed_duration < intended_duration * 0.95:
        failures.append(f"observed duration was {observed_duration}s, below 95% of {intended_duration}s")
    recorder_average = summary.get("recorder_impact", {}).get("cpu_percent", {}).get("average")
    if recorder_average is not None and recorder_average > 1:
        warnings.append(f"recorder average CPU was {recorder_average:.3f}%, above the 1% review threshold")
    if summary.get("scope", {}).get("server_site_count") == "unknown":
        warnings.append("server site count is unknown; qualify the server-wide result manually")

    if args.verify_checksums:
        sums = run / "SHA256SUMS"
        if not sums.is_file():
            failures.append("SHA256SUMS is missing")
        else:
            for line_number, line in enumerate(sums.read_text(encoding="utf-8").splitlines(), 1):
                if not line:
                    continue
                expected, relative = line.split(maxsplit=1)
                target = (run / relative).resolve()
                try:
                    target.relative_to(run)
                except ValueError:
                    failures.append(f"checksum path escapes bundle on line {line_number}")
                    continue
                if not target.is_file() or digest(target) != expected:
                    failures.append(f"checksum mismatch: {relative}")

    for warning in warnings:
        print(f"WARNING: {warning}")
    for failure in failures:
        print(f"FAIL: {failure}")
    if failures:
        return 1
    print("PASS: recording bundle is internally consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
