#!/usr/bin/env python3

from __future__ import annotations

import argparse
import gzip
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


STATUSES = ("HIT", "MISS", "BYPASS", "EXPIRED", "STALE", "UPDATING", "REVALIDATED")
CACHE_SERVED = {"HIT", "STALE", "UPDATING", "REVALIDATED"}
STATUS_RE = re.compile(
    r'(?:^|[\s"=:,\[])(' + "|".join(STATUSES) + r')(?=$|[\s";,\]}])'
)
COMBINED_TIME_RE = re.compile(r"\[([^\]]+)\]")
ISO_TIME_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2}))\b")
TIME_KEYS = ("time_iso8601", "@timestamp", "timestamp", "time", "ts", "epoch")


def nested_items(value: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key), item
            yield from nested_items(item)
    elif isinstance(value, list):
        for item in value:
            yield from nested_items(item)


def parse_datetime(raw: Any) -> int | None:
    if isinstance(raw, (int, float)):
        return int(raw)
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    if re.fullmatch(r"\d{10}(?:\.\d+)?", raw):
        return int(float(raw))
    for fmt in ("%d/%b/%Y:%H:%M:%S %z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return int(datetime.strptime(raw.replace("Z", "+0000"), fmt).timestamp())
        except ValueError:
            pass
    try:
        return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def parse_line(line: str) -> tuple[int | None, set[str], dict[str, Any] | None]:
    payload = None
    if line.lstrip().startswith("{"):
        try:
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                payload = parsed
        except json.JSONDecodeError:
            pass

    epoch = None
    statuses: set[str] = set()
    if payload is not None:
        for key, value in nested_items(payload):
            lowered = key.lower()
            if epoch is None and lowered in TIME_KEYS:
                epoch = parse_datetime(value)
            if "cache" in lowered and isinstance(value, str):
                statuses.update(STATUS_RE.findall(value.upper()))

    if epoch is None:
        combined = COMBINED_TIME_RE.search(line)
        if combined:
            epoch = parse_datetime(combined.group(1))
        else:
            iso = ISO_TIME_RE.search(line)
            if iso:
                epoch = parse_datetime(iso.group(1))
    if not statuses:
        statuses.update(STATUS_RE.findall(line))
    return epoch, statuses, payload


def host_matches(line: str, payload: dict[str, Any] | None, wanted: str) -> bool:
    wanted = wanted.lower()
    if payload is not None:
        for key, value in nested_items(payload):
            if key.lower() in {"host", "http_host", "server_name", "vhost"} and str(value).lower() == wanted:
                return True
    return re.search(r"(?<![a-z0-9.-])" + re.escape(wanted) + r"(?![a-z0-9.-])", line.lower()) is not None


def rotated_files(base: Path) -> list[Path]:
    candidates = [base]
    if base.parent.is_dir():
        candidates.extend(base.parent.glob(base.name + ".*"))
    unique: dict[str, Path] = {}
    for path in candidates:
        try:
            if path.is_file():
                unique[str(path.resolve())] = path.resolve()
        except OSError:
            continue
    return sorted(unique.values(), key=lambda item: item.name)


def open_log(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("rt", encoding="utf-8", errors="replace")


def percent(part: int, whole: int) -> float | None:
    return round(part / whole * 100, 4) if whole else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyse Nginx cache outcomes for one recorder window")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--log", type=Path, action="append", required=True)
    parser.add_argument("--host", default="")
    parser.add_argument("--max-input-bytes", type=int, default=20 * 1024 * 1024 * 1024)
    args = parser.parse_args()

    run = args.run_dir.resolve()
    manifest = json.loads((run / "run.json").read_text(encoding="utf-8"))
    timestamps = manifest.get("timestamps", {})
    start = int(timestamps["start_epoch"])
    end = int(timestamps.get("end_epoch") or timestamps["planned_end_epoch"])

    files: dict[str, Path] = {}
    for base in args.log:
        for path in rotated_files(base):
            files[str(path)] = path

    warnings: list[str] = []
    if not files:
        raise SystemExit("No readable Nginx access logs or rotations were found")

    statuses: Counter[str] = Counter()
    scanned = in_window = unparsed_time = unclassified = conflicts = host_filtered = 0
    bytes_considered = 0
    analysed_files: list[dict[str, Any]] = []
    complete = True

    for path in files.values():
        size = path.stat().st_size
        if bytes_considered + size > args.max_input_bytes:
            complete = False
            warnings.append(f"Input limit reached before {path}; increase --max-input-bytes to include it")
            break
        bytes_considered += size
        file_lines = 0
        try:
            with open_log(path) as source:
                for line in source:
                    scanned += 1
                    file_lines += 1
                    epoch, found, payload = parse_line(line)
                    if epoch is None:
                        unparsed_time += 1
                        continue
                    if epoch < start or epoch > end:
                        continue
                    if args.host and not host_matches(line, payload, args.host):
                        host_filtered += 1
                        continue
                    in_window += 1
                    if len(found) == 1:
                        statuses[next(iter(found))] += 1
                    elif not found:
                        unclassified += 1
                    else:
                        conflicts += 1
        except (OSError, EOFError) as exc:
            complete = False
            warnings.append(f"Could not finish {path}: {exc}")
        analysed_files.append({"path": str(path), "bytes": size, "lines_scanned": file_lines})

    classified = sum(statuses.values())
    if classified == 0:
        warnings.append("No Nginx cache-status values were found in the selected run window. The access-log format must include $upstream_cache_status.")
    if unparsed_time:
        warnings.append(f"Ignored {unparsed_time} lines whose timestamp could not be parsed")
    if conflicts:
        warnings.append(f"Ignored {conflicts} lines containing conflicting cache-status values")

    status_summary = {
        status: {"count": statuses[status], "percent_of_classified": percent(statuses[status], classified)}
        for status in STATUSES
    }
    served = sum(statuses[status] for status in CACHE_SERVED)
    result = {
        "schema_version": 1,
        "window": {"start_epoch": start, "end_epoch": end, "inclusive": True},
        "host_filter": args.host or None,
        "logs": analysed_files,
        "input_bytes_considered": bytes_considered,
        "complete": complete,
        "lines": {
            "scanned": scanned,
            "in_window_after_host_filter": in_window,
            "unparsed_timestamp": unparsed_time,
            "host_filtered": host_filtered,
            "unclassified_in_window": unclassified,
            "conflicting_status_in_window": conflicts,
            "classified_in_window": classified,
        },
        "statuses": status_summary,
        "ratios": {
            "hit_percent": percent(statuses["HIT"], classified),
            "miss_percent": percent(statuses["MISS"], classified),
            "bypass_percent": percent(statuses["BYPASS"], classified),
            "cache_served_percent": percent(served, classified),
        },
        "cache_served_statuses": sorted(CACHE_SERVED),
        "warnings": warnings,
    }

    analysis = run / "analysis"
    analysis.mkdir(parents=True, exist_ok=True)
    json_path = analysis / "nginx-summary.json"
    json_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def display(value: float | None) -> str:
        return "n/a" if value is None else f"{value:.2f}%"

    rows = "\n".join(
        f"| {status} | {statuses[status]:,} | {display(status_summary[status]['percent_of_classified'])} |"
        for status in STATUSES
    )
    warning_text = "\n".join(f"- {warning}" for warning in warnings) or "- None"
    report = f"""# Nginx cache analysis

- Exact recorder window: {start} to {end}, inclusive
- Log files read: {len(analysed_files)}
- Lines scanned: {scanned:,}
- Requests in the window: {in_window:,}
- Requests with one recognised cache status: {classified:,}
- HIT ratio: {display(result['ratios']['hit_percent'])}
- MISS ratio: {display(result['ratios']['miss_percent'])}
- BYPASS ratio: {display(result['ratios']['bypass_percent'])}
- Served from cache, including HIT, STALE, UPDATING and REVALIDATED: {display(result['ratios']['cache_served_percent'])}

| Cache status | Requests | Share of classified requests |
|---|---:|---:|
{rows}

Ratios use only requests in the recorder window that contain exactly one recognised Nginx cache status. Raw Nginx logs are not copied into the evidence bundle.

## Warnings

{warning_text}
"""
    report_path = analysis / "nginx-report.md"
    report_path.write_text(report, encoding="utf-8")
    print(report, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
