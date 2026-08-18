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


CACHE_STATUSES = ("HIT", "MISS", "BYPASS", "EXPIRED", "STALE", "UPDATING", "REVALIDATED")
CACHE_SERVED = {"HIT", "STALE", "UPDATING", "REVALIDATED"}
CACHE_STATUS_RE = re.compile(
    r'(?:^|[\s"=:,\[])(' + "|".join(CACHE_STATUSES) + r')(?=$|[\s";,\]}])'
)
COMBINED_TIME_RE = re.compile(r"\[([^\]]+)\]")
ISO_TIME_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2}))\b")
COMBINED_REQUEST_RE = re.compile(r'"([A-Z]+)\s+\S+(?:\s+HTTP/[^"\s]+)?"\s+(\d{3})(?:\s|$)')
TIME_KEYS = ("time_iso8601", "@timestamp", "timestamp", "time", "ts", "epoch")
STATUS_KEYS = ("status", "status_code", "http_status", "response_status")
METHOD_KEYS = ("method", "request_method", "http_method")


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


def parse_line(line: str) -> tuple[int | None, set[str], dict[str, Any] | None, int | None, str | None]:
    payload = None
    if line.lstrip().startswith("{"):
        try:
            parsed = json.loads(line)
            if isinstance(parsed, dict):
                payload = parsed
        except json.JSONDecodeError:
            pass

    epoch = None
    http_status = None
    method = None
    cache_statuses: set[str] = set()
    if payload is not None:
        for key, value in nested_items(payload):
            lowered = key.lower()
            if epoch is None and lowered in TIME_KEYS:
                epoch = parse_datetime(value)
            if http_status is None and lowered in STATUS_KEYS and str(value).isdigit():
                candidate = int(value)
                if 100 <= candidate <= 599:
                    http_status = candidate
            if method is None and lowered in METHOD_KEYS and isinstance(value, str):
                method = value.upper()
            if "cache" in lowered and isinstance(value, str):
                cache_statuses.update(CACHE_STATUS_RE.findall(value.upper()))

    if epoch is None:
        combined = COMBINED_TIME_RE.search(line)
        if combined:
            epoch = parse_datetime(combined.group(1))
        else:
            iso = ISO_TIME_RE.search(line)
            if iso:
                epoch = parse_datetime(iso.group(1))
    combined_request = COMBINED_REQUEST_RE.search(line)
    if combined_request:
        method = method or combined_request.group(1)
        http_status = http_status or int(combined_request.group(2))
    if not cache_statuses:
        cache_statuses.update(CACHE_STATUS_RE.findall(line))
    return epoch, cache_statuses, payload, http_status, method


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
        candidates.extend(base.parent.glob(base.name + "-*"))
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


def display(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}%"


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyse web-server access logs for one recorder window")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--log", type=Path, action="append", required=True)
    parser.add_argument("--host", default="")
    parser.add_argument("--server", default="unknown")
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
    if not files:
        raise SystemExit("No readable access logs or rotations were found")

    cache_statuses: Counter[str] = Counter()
    response_statuses: Counter[int] = Counter()
    methods: Counter[str] = Counter()
    scanned = in_window = unparsed_time = cache_conflicts = host_filtered = 0
    bytes_considered = 0
    analysed_files: list[dict[str, Any]] = []
    warnings: list[str] = []
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
                    epoch, found_cache, payload, http_status, method = parse_line(line)
                    if epoch is None:
                        unparsed_time += 1
                        continue
                    if epoch < start or epoch > end:
                        continue
                    if args.host and not host_matches(line, payload, args.host):
                        host_filtered += 1
                        continue
                    in_window += 1
                    if http_status is not None:
                        response_statuses[http_status] += 1
                    if method:
                        methods[method] += 1
                    if len(found_cache) == 1:
                        cache_statuses[next(iter(found_cache))] += 1
                    elif len(found_cache) > 1:
                        cache_conflicts += 1
        except (OSError, EOFError) as exc:
            complete = False
            warnings.append(f"Could not finish {path}: {exc}")
        analysed_files.append({"path": str(path), "bytes": size, "lines_scanned": file_lines})

    classified_cache = sum(cache_statuses.values())
    if classified_cache == 0:
        warnings.append("No recognised cache-status values were found. Request and HTTP response counts are still valid; cache ratios are unavailable.")
    if unparsed_time:
        warnings.append(f"Ignored {unparsed_time} lines whose timestamp could not be parsed")
    if cache_conflicts:
        warnings.append(f"Ignored cache status on {cache_conflicts} lines containing conflicting values")

    status_classes: Counter[str] = Counter()
    for status, count in response_statuses.items():
        status_classes[f"{status // 100}xx"] += count
    cache_summary = {
        status: {"count": cache_statuses[status], "percent_of_classified": percent(cache_statuses[status], classified_cache)}
        for status in CACHE_STATUSES
    }
    served = sum(cache_statuses[status] for status in CACHE_SERVED)
    result = {
        "schema_version": 1,
        "web_server": args.server,
        "window": {"start_epoch": start, "end_epoch": end, "inclusive": True},
        "host_filter": args.host or None,
        "logs": analysed_files,
        "input_bytes_considered": bytes_considered,
        "complete": complete,
        "lines": {
            "scanned": scanned,
            "requests_in_window": in_window,
            "unparsed_timestamp": unparsed_time,
            "host_filtered": host_filtered,
            "with_http_status": sum(response_statuses.values()),
            "with_cache_status": classified_cache,
            "conflicting_cache_status": cache_conflicts,
        },
        "http_statuses": {str(key): response_statuses[key] for key in sorted(response_statuses)},
        "http_status_classes": {key: status_classes[key] for key in sorted(status_classes)},
        "methods": {key: methods[key] for key in sorted(methods)},
        "cache": {
            "statuses": cache_summary,
            "ratios": {
                "hit_percent": percent(cache_statuses["HIT"], classified_cache),
                "miss_percent": percent(cache_statuses["MISS"], classified_cache),
                "bypass_percent": percent(cache_statuses["BYPASS"], classified_cache),
                "cache_served_percent": percent(served, classified_cache),
            },
            "served_statuses": sorted(CACHE_SERVED),
        },
        "warnings": warnings,
    }

    analysis = run / "analysis"
    analysis.mkdir(parents=True, exist_ok=True)
    (analysis / "access-log-summary.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    response_rows = "\n".join(f"| {key} | {response_statuses[key]:,} |" for key in sorted(response_statuses)) or "| n/a | 0 |"
    cache_rows = "\n".join(
        f"| {status} | {cache_statuses[status]:,} | {display(cache_summary[status]['percent_of_classified'])} |"
        for status in CACHE_STATUSES
    )
    warning_text = "\n".join(f"- {warning}" for warning in warnings) or "- None"
    report = f"""# Access-log analysis

- Web server: {args.server}
- Exact recorder window: {start} to {end}, inclusive
- Log files read: {len(analysed_files)}
- Lines scanned: {scanned:,}
- Requests in the window: {in_window:,}
- Requests with an HTTP response status: {sum(response_statuses.values()):,}
- Requests with one recognised cache status: {classified_cache:,}

## HTTP responses

| Status | Requests |
|---|---:|
{response_rows}

## Cache outcomes

| Cache status | Requests | Share of classified requests |
|---|---:|---:|
{cache_rows}

Cache ratios are calculated only when the selected log format contains recognised cache outcomes. Raw access logs are not copied into the evidence bundle.

## Warnings

{warning_text}
"""
    (analysis / "access-log-report.md").write_text(report, encoding="utf-8")
    print(report, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
