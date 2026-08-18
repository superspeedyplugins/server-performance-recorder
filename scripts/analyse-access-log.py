#!/usr/bin/env python3

from __future__ import annotations

import argparse
import gzip
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import parse_qs, urlsplit


CACHE_STATUSES = ("HIT", "MISS", "BYPASS", "EXPIRED", "STALE", "UPDATING", "REVALIDATED")
CACHE_SERVED = {"HIT", "STALE", "UPDATING", "REVALIDATED"}
CACHE_STATUS_RE = re.compile(
    r'(?:^|[\s"=:,\[])(' + "|".join(CACHE_STATUSES) + r')(?=$|[\s";,\]}])'
)
COMBINED_TIME_RE = re.compile(r"\[([^\]]+)\]")
ISO_TIME_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2}))\b")
COMBINED_REQUEST_RE = re.compile(r'"([A-Z]+)\s+(\S+)(?:\s+HTTP/[^"\s]+)?"\s+(\d{3})(?:\s|$)')
COMBINED_USER_AGENT_RE = re.compile(r'"[A-Z]+\s+\S+(?:\s+HTTP/[^"\s]+)?"\s+\d{3}\s+\S+\s+"[^"]*"\s+"([^"]*)"')
REQUEST_TIME_RE = re.compile(r'(?:^|[\s"])(?:request_time|rt)[=:]"?(\d+(?:\.\d+)?)')
UPSTREAM_TIME_RE = re.compile(r'(?:^|[\s"])(?:upstream_response_time|urt)[=:]"?(\d+(?:\.\d+)?)')
TIME_KEYS = ("time_iso8601", "@timestamp", "timestamp", "time", "ts", "epoch")
STATUS_KEYS = ("status", "status_code", "http_status", "response_status")
METHOD_KEYS = ("method", "request_method", "http_method")
TARGET_KEYS = ("request_uri", "uri", "path", "request_path", "url")
USER_AGENT_KEYS = ("http_user_agent", "user_agent", "useragent", "agent")
REQUEST_TIME_KEYS = ("request_time", "request_time_seconds")
UPSTREAM_TIME_KEYS = ("upstream_response_time", "upstream_response_time_seconds")
AUTOMATION_CLASSES = (
    "claimed_search_crawler",
    "claimed_shopping_crawler",
    "claimed_generic_crawler",
    "not_identified_as_automation",
)
SEARCH_CRAWLER_RE = re.compile(
    r"googlebot|bingbot|yandex(?:bot)?|baiduspider|duckduckbot|slurp|applebot|petalbot|seznambot|sogou",
    re.I,
)
SHOPPING_CRAWLER_RE = re.compile(r"googlebot-product|adsbot-google|shoppingbot|pricebot|merchantbot", re.I)
GENERIC_CRAWLER_RE = re.compile(
    r"bot|crawler|spider|crawl|facebookexternalhit|facebot|twitterbot|linkedinbot|"
    r"semrush|ahrefs|mj12|dotbot|bytespider|gptbot|chatgpt-user|claudebot|anthropic-ai|"
    r"perplexitybot|ccbot|amazonbot|ia_archiver",
    re.I,
)
STATIC_EXTENSION_RE = re.compile(
    r"\.(?:avif|bmp|css|eot|gif|ico|jpe?g|js|json|map|mp4|pdf|png|svg|ttf|webm|webp|woff2?|xml)(?:$|/)",
    re.I,
)


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


def numeric_seconds(raw: Any) -> float | None:
    if isinstance(raw, (int, float)):
        return max(0.0, float(raw))
    if not isinstance(raw, str):
        return None
    candidates = re.findall(r"\d+(?:\.\d+)?", raw)
    return max(0.0, float(candidates[-1])) if candidates else None


def parse_line(line: str) -> tuple[
    int | None,
    set[str],
    dict[str, Any] | None,
    int | None,
    str | None,
    str | None,
    str | None,
    float | None,
    float | None,
]:
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
    target = None
    user_agent = None
    request_time = None
    upstream_time = None
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
            if target is None and lowered in TARGET_KEYS and isinstance(value, str):
                target = value
            if user_agent is None and lowered in USER_AGENT_KEYS and isinstance(value, str):
                user_agent = value
            if request_time is None and lowered in REQUEST_TIME_KEYS:
                request_time = numeric_seconds(value)
            if upstream_time is None and lowered in UPSTREAM_TIME_KEYS:
                upstream_time = numeric_seconds(value)
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
        target = target or combined_request.group(2)
        http_status = http_status or int(combined_request.group(3))
    if user_agent is None:
        combined_user_agent = COMBINED_USER_AGENT_RE.search(line)
        if combined_user_agent:
            user_agent = combined_user_agent.group(1)
    if request_time is None:
        matched = REQUEST_TIME_RE.search(line)
        request_time = numeric_seconds(matched.group(1)) if matched else None
    if upstream_time is None:
        matched = UPSTREAM_TIME_RE.search(line)
        upstream_time = numeric_seconds(matched.group(1)) if matched else None
    if not cache_statuses:
        cache_statuses.update(CACHE_STATUS_RE.findall(line))
    return epoch, cache_statuses, payload, http_status, method, target, user_agent, request_time, upstream_time


def automation_class(user_agent: str | None) -> str:
    value = user_agent or ""
    if SHOPPING_CRAWLER_RE.search(value):
        return "claimed_shopping_crawler"
    if SEARCH_CRAWLER_RE.search(value):
        return "claimed_search_crawler"
    if GENERIC_CRAWLER_RE.search(value):
        return "claimed_generic_crawler"
    return "not_identified_as_automation"


def request_class(method: str | None, target: str | None) -> str:
    if not target:
        return "unknown"
    try:
        split = urlsplit(target)
        path = split.path.lower()
        query = parse_qs(split.query, keep_blank_values=True)
    except ValueError:
        return "unknown"
    if STATIC_EXTENSION_RE.search(path):
        return "static"
    if path.endswith("/admin-ajax.php"):
        return "admin_ajax"
    if path.startswith("/wp-json/") or "rest_route" in query:
        return "rest"
    if re.search(r"/(?:cart|basket)(?:/|$)", path):
        return "cart"
    if re.search(r"/checkout(?:/|$)", path):
        return "checkout"
    if re.search(r"/(?:my-account|account)(?:/|$)", path):
        return "account"
    if path.startswith(("/product-category/", "/product-tag/")):
        tail = path.strip("/").split("/")[2:]
        filter_query = any(key.startswith("filter_") for key in query)
        if filter_query or len(tail) > 1:
            return "deep_filter"
        return "product_archive"
    if path == "/shop" or path.startswith("/shop/"):
        filter_query = any(key.startswith("filter_") for key in query)
        return "deep_filter" if filter_query else "shop"
    if path.startswith("/product/"):
        return "product_single"
    if (method or "").upper() not in {"GET", "HEAD"}:
        return "other_private_method"
    return "other"


class TimingAggregate:
    def __init__(self) -> None:
        self.count = 0
        self.total_ms = 0.0
        self.histogram: Counter[int] = Counter()

    def add_seconds(self, seconds: float | None) -> None:
        if seconds is None:
            return
        milliseconds = max(0, int(round(seconds * 1000)))
        self.count += 1
        self.total_ms += seconds * 1000
        self.histogram[milliseconds] += 1

    def percentile(self, percentile: float) -> float | None:
        if not self.count:
            return None
        rank = max(1, int(self.count * percentile + 0.999999))
        seen = 0
        for milliseconds in sorted(self.histogram):
            seen += self.histogram[milliseconds]
            if seen >= rank:
                return float(milliseconds)
        return None

    def summary(self) -> dict[str, Any]:
        if not self.count:
            return {"quality": "unavailable", "count": 0, "sum": None, "average": None, "p95": None}
        return {
            "quality": "parsed_from_log_field",
            "count": self.count,
            "sum": round(self.total_ms, 3),
            "average": round(self.total_ms / self.count, 3),
            "p95": self.percentile(0.95),
        }


def host_matches(line: str, payload: dict[str, Any] | None, wanted: str) -> bool:
    wanted = wanted.lower()
    if payload is not None:
        for key, value in nested_items(payload):
            if key.lower() in {"host", "http_host", "server_name", "vhost"} and str(value).lower() == wanted:
                return True
    return re.search(r"(?<![a-z0-9.-])" + re.escape(wanted) + r"(?![a-z0-9.-])", line.lower()) is not None


def rotation_sort_key(base: Path, path: Path) -> tuple[int, int | float, str]:
    if path == base:
        return (0, 0, path.name)
    suffix = path.name[len(base.name):]
    numbered = re.fullmatch(r"\.(\d+)(?:\.gz)?", suffix)
    if numbered:
        return (1, int(numbered.group(1)), path.name)
    dated = re.match(r"-(\d{8,14})", suffix)
    if dated:
        return (2, -int(dated.group(1)), path.name)
    try:
        modified = path.stat().st_mtime
    except OSError:
        modified = 0
    return (3, -modified, path.name)


def rotated_files(base: Path) -> list[Path]:
    base = base.resolve()
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
    return sorted(unique.values(), key=lambda item: rotation_sort_key(base, item))


def dated_rotation_boundary(base: Path, path: Path) -> datetime | None:
    if path == base:
        return None
    suffix = path.name[len(base.name):]
    matched = re.fullmatch(r"-(\d{8}|\d{10}|\d{12}|\d{14})(?:\.gz)?", suffix)
    if not matched:
        return None
    raw = matched.group(1)
    formats = {8: "%Y%m%d", 10: "%Y%m%d%H", 12: "%Y%m%d%H%M", 14: "%Y%m%d%H%M%S"}
    try:
        return datetime.strptime(raw, formats[len(raw)])
    except ValueError:
        return None


def files_for_window(base: Path, files: Iterable[Path], start: int, end: int) -> tuple[list[Path], list[Path]]:
    """Use dateext rotation names as interval boundaries without reading log contents."""
    base = base.resolve()
    ordered = list(files)
    dated = [(path, dated_rotation_boundary(base, path)) for path in ordered]
    boundaries = sorted({boundary for _, boundary in dated if boundary is not None})
    if not boundaries:
        return ordered, []

    start_time = datetime.fromtimestamp(start)
    end_time = datetime.fromtimestamp(end)
    closing_boundary = next((boundary for boundary in boundaries if boundary > end_time), None)
    selected: list[Path] = []
    skipped: list[Path] = []
    for path, rotation_boundary in dated:
        if rotation_boundary is None:
            if path != base or closing_boundary is None:
                selected.append(path)
            else:
                skipped.append(path)
            continue
        if rotation_boundary > start_time and (
            closing_boundary is None or rotation_boundary <= closing_boundary
        ):
            selected.append(path)
        else:
            skipped.append(path)
    return selected, skipped


def open_log(path: Path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return path.open("rt", encoding="utf-8", errors="replace")


def percent(part: int, whole: int) -> float | None:
    return round(part / whole * 100, 4) if whole else None


def display(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}%"


def display_bytes(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.2f} {unit}"
        amount /= 1024
    return f"{value} B"


def input_plan(files: Iterable[Path], ceiling: int) -> tuple[list[tuple[Path, int]], list[tuple[Path, int]]]:
    selected: list[tuple[Path, int]] = []
    skipped: list[tuple[Path, int]] = []
    used = 0
    over_limit = False
    for path in files:
        size = path.stat().st_size
        if over_limit or used + size > ceiling:
            over_limit = True
            skipped.append((path, size))
        else:
            selected.append((path, size))
            used += size
    return selected, skipped


def print_dry_run(
    start: int,
    end: int,
    selected: list[tuple[Path, int]],
    skipped_outside_window: list[tuple[Path, int]],
    skipped_over_ceiling: list[tuple[Path, int]],
    ceiling: int,
) -> None:
    total = sum(size for _, size in selected)
    print("# Access-log analysis dry run")
    print(f"Recorder window: {start} to {end}, inclusive")
    print(f"On-disk input ceiling: {display_bytes(ceiling)} ({ceiling:,} bytes)")
    print(f"Selected input: {display_bytes(total)} ({total:,} bytes) across {len(selected)} file(s)")
    print("Raw log data copied or extracted: 0 B")
    print(f"Compressed files to stream: {sum(path.suffix == '.gz' for path, _ in selected)}")
    print("Compressed size does not predict decompressed line count or scan time.")
    print()
    print("Selection\tOn-disk size\tPath")
    for path, size in selected:
        print(f"READ\t{display_bytes(size)}\t{path}")
    for path, size in skipped_outside_window:
        print(f"SKIP (outside window)\t{display_bytes(size)}\t{path}")
    for path, size in skipped_over_ceiling:
        print(f"SKIP (over ceiling)\t{display_bytes(size)}\t{path}")
    print()
    print("Dry run: no log contents were read and no analysis or archive files were created.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyse web-server access logs for one recorder window")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--log", type=Path, action="append", required=True)
    parser.add_argument("--host", default="")
    parser.add_argument("--server", default="unknown")
    parser.add_argument("--max-input-bytes", type=int, default=20 * 1024 * 1024 * 1024)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--progress-interval-seconds", type=int, default=10)
    args = parser.parse_args()
    if args.max_input_bytes <= 0:
        parser.error("--max-input-bytes must be positive")
    if args.progress_interval_seconds <= 0:
        parser.error("--progress-interval-seconds must be positive")

    run = args.run_dir.resolve()
    if args.output_dir:
        derived = args.output_dir.resolve()
        if derived == run or run in derived.parents:
            parser.error("--output-dir must be outside the original run directory")
    manifest = json.loads((run / "run.json").read_text(encoding="utf-8"))
    timestamps = manifest.get("timestamps", {})
    start = int(timestamps["start_epoch"])
    end = int(timestamps.get("end_epoch") or timestamps["planned_end_epoch"])

    files: dict[str, Path] = {}
    outside_window: dict[str, Path] = {}
    for base in args.log:
        rotated = rotated_files(base)
        window_files, window_skipped = files_for_window(base.resolve(), rotated, start, end)
        for path in window_files:
            files[str(path)] = path
            outside_window.pop(str(path), None)
        for path in window_skipped:
            if str(path) not in files:
                outside_window[str(path)] = path
    if not files:
        raise SystemExit("No readable access logs or rotations were found")
    selected_files, skipped_files = input_plan(files.values(), args.max_input_bytes)
    outside_window_files = [(path, path.stat().st_size) for path in outside_window.values()]
    if args.dry_run:
        print_dry_run(start, end, selected_files, outside_window_files, skipped_files, args.max_input_bytes)
        return 0
    if not selected_files:
        raise SystemExit("The first access log exceeds --max-input-bytes; no log contents were read")

    cache_statuses: Counter[str] = Counter()
    response_statuses: Counter[int] = Counter()
    methods: Counter[str] = Counter()
    automation_classes: Counter[str] = Counter()
    automation_cache_statuses: dict[str, Counter[str]] = {
        item: Counter() for item in AUTOMATION_CLASSES
    }
    request_classes: Counter[str] = Counter()
    request_times = TimingAggregate()
    upstream_times = TimingAggregate()
    request_groups: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    archive_proxy_requests = 0
    archive_proxy_request_times = TimingAggregate()
    archive_proxy_upstream_times = TimingAggregate()
    scanned = in_window = unparsed_time = cache_conflicts = host_filtered = 0
    bytes_considered = 0
    analysed_files: list[dict[str, Any]] = []
    warnings: list[str] = []
    complete = not skipped_files
    if skipped_files:
        first_skipped, _ = skipped_files[0]
        warnings.append(f"Input limit reached before {first_skipped}; increase --max-input-bytes to include it")

    scan_started = time.monotonic()
    next_progress = scan_started + args.progress_interval_seconds
    for file_number, (path, size) in enumerate(selected_files, 1):
        bytes_considered += size
        file_lines = 0
        file_started = time.monotonic()
        print(
            f"Scanning {file_number}/{len(selected_files)}: {path} ({display_bytes(size)} on disk)",
            file=sys.stderr,
            flush=True,
        )
        try:
            with open_log(path) as source:
                for line in source:
                    scanned += 1
                    file_lines += 1
                    if scanned % 100_000 == 0 and time.monotonic() >= next_progress:
                        now = time.monotonic()
                        elapsed = max(now - scan_started, 0.001)
                        print(
                            f"Progress: {scanned:,} lines scanned; {in_window:,} requests in window; "
                            f"{scanned / elapsed:,.0f} lines/s",
                            file=sys.stderr,
                            flush=True,
                        )
                        next_progress = now + args.progress_interval_seconds
                    (
                        epoch,
                        found_cache,
                        payload,
                        http_status,
                        method,
                        target,
                        user_agent,
                        request_time,
                        upstream_time,
                    ) = parse_line(line)
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
                    automation = automation_class(user_agent)
                    automation_classes[automation] += 1
                    classified_request = request_class(method, target)
                    request_classes[classified_request] += 1
                    request_times.add_seconds(request_time)
                    upstream_times.add_seconds(upstream_time)
                    if (
                        automation != "not_identified_as_automation"
                        and classified_request == "deep_filter"
                        and (method or "").upper() in {"GET", "HEAD"}
                    ):
                        archive_proxy_requests += 1
                        archive_proxy_request_times.add_seconds(request_time)
                        archive_proxy_upstream_times.add_seconds(upstream_time)
                    cache_group = "unavailable"
                    if len(found_cache) == 1:
                        cache_status = next(iter(found_cache))
                        cache_group = cache_status
                        cache_statuses[cache_status] += 1
                        automation_cache_statuses[automation][cache_status] += 1
                    elif len(found_cache) > 1:
                        cache_group = "conflicting"
                        cache_conflicts += 1
                    group_key = (
                        automation,
                        method or "unavailable",
                        str(http_status) if http_status is not None else "unavailable",
                        classified_request,
                        cache_group,
                    )
                    if group_key not in request_groups:
                        request_groups[group_key] = {
                            "requests": 0,
                            "request_time": TimingAggregate(),
                            "upstream_response_time": TimingAggregate(),
                        }
                    request_groups[group_key]["requests"] += 1
                    request_groups[group_key]["request_time"].add_seconds(request_time)
                    request_groups[group_key]["upstream_response_time"].add_seconds(upstream_time)
        except (OSError, EOFError) as exc:
            complete = False
            warnings.append(f"Could not finish {path}: {exc}")
        analysed_files.append({"path": str(path), "bytes": size, "lines_scanned": file_lines})
        print(
            f"Finished {path}: {file_lines:,} lines in {time.monotonic() - file_started:.1f}s",
            file=sys.stderr,
            flush=True,
        )

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
        status: {
            "count": cache_statuses[status] if classified_cache else None,
            "percent_of_classified": percent(cache_statuses[status], classified_cache),
        }
        for status in CACHE_STATUSES
    }
    served = sum(cache_statuses[status] for status in CACHE_SERVED)
    claimed_automation_requests = sum(
        automation_classes[key]
        for key in AUTOMATION_CLASSES
        if key != "not_identified_as_automation"
    )
    duration_seconds = max(1, end - start)
    request_group_export = []
    for key in sorted(request_groups):
        automation, method, http_status, classified_request, cache_status = key
        group = request_groups[key]
        request_group_export.append({
            "automation": automation,
            "method": method,
            "http_status": int(http_status) if http_status.isdigit() else http_status,
            "request_class": classified_request,
            "cache_status": cache_status,
            "requests": group["requests"],
            "request_time_ms": group["request_time"].summary(),
            "upstream_response_time_ms": group["upstream_response_time"].summary(),
        })
    result = {
        "schema_version": 2,
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
        "automation": {
            "quality": "inferred_from_user_agent",
            "denominator": "requests_in_window",
            "classes": {
                key: {
                    "count": automation_classes[key],
                    "percent_of_requests": percent(automation_classes[key], in_window),
                }
                for key in AUTOMATION_CLASSES
            },
            "cache_statuses_by_class": {
                key: {
                    status: automation_cache_statuses[key][status]
                    for status in CACHE_STATUSES
                    if automation_cache_statuses[key][status]
                }
                for key in AUTOMATION_CLASSES
            },
        },
        "request_classes": {
            "quality": "heuristic_from_request_target",
            "denominator": "requests_in_window",
            "counts": {key: request_classes[key] for key in sorted(request_classes)},
        },
        "request_groups": request_group_export,
        "archive_protection_proxy": {
            "quality": "heuristic_request_class_not_ssf_policy",
            "denominator": "claimed_automation_requests_in_window",
            "claimed_automation_requests": claimed_automation_requests,
            "claimed_automation_deep_filter_requests": archive_proxy_requests,
            "percent_of_claimed_automation_requests": percent(archive_proxy_requests, claimed_automation_requests),
            "projected_daily_requests": round(archive_proxy_requests * 86400 / duration_seconds),
            "request_time_ms": archive_proxy_request_times.summary(),
            "upstream_response_time_ms": archive_proxy_upstream_times.summary(),
        },
        "cache": {
            "quality": "parsed_from_log_field" if classified_cache else "unavailable",
            "statuses": cache_summary,
            "ratios": {
                "hit_percent": percent(cache_statuses["HIT"], classified_cache),
                "miss_percent": percent(cache_statuses["MISS"], classified_cache),
                "bypass_percent": percent(cache_statuses["BYPASS"], classified_cache),
                "cache_served_percent": percent(served, classified_cache),
            },
            "served_statuses": sorted(CACHE_SERVED),
        },
        "timings": {
            "request_time_ms": request_times.summary(),
            "upstream_response_time_ms": upstream_times.summary(),
        },
        "cloudflare_edge": {
            "quality": "unavailable",
            "reason": "origin_logs_do_not_contain_requests_served_entirely_at_the_cloudflare_edge",
        },
        "warnings": warnings,
    }

    analysis = args.output_dir.resolve() if args.output_dir else run / "analysis"
    analysis.mkdir(parents=True, exist_ok=True)
    (analysis / "access-log-summary.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    response_rows = "\n".join(f"| {key} | {response_statuses[key]:,} |" for key in sorted(response_statuses)) or "| n/a | 0 |"
    cache_rows = "\n".join(
        f"| {status} | {cache_statuses[status]:,} | {display(cache_summary[status]['percent_of_classified'])} |"
        for status in CACHE_STATUSES
    ) if classified_cache else "| unavailable | unavailable | unavailable |"
    automation_rows = "\n".join(
        f"| {key} | {automation_classes[key]:,} | {display(percent(automation_classes[key], in_window))} |"
        for key in AUTOMATION_CLASSES
    )
    request_class_rows = "\n".join(
        f"| {key} | {request_classes[key]:,} |" for key in sorted(request_classes)
    ) or "| unknown | 0 |"
    request_timing = request_times.summary()
    upstream_timing = upstream_times.summary()
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

## Automation claimed by user agent

| Classification | Requests | Share of all requests |
|---|---:|---:|
{automation_rows}

"Not identified as automation" does not mean human. The classification uses bounded user-agent token checks and stores aggregates only.

## Request classes

| Heuristic class | Requests |
|---|---:|
{request_class_rows}

## Available timings

| Field | Quality | Samples | Average ms | p95 ms |
|---|---|---:|---:|---:|
| request time | {request_timing['quality']} | {request_timing['count']:,} | {request_timing['average'] if request_timing['average'] is not None else 'unavailable'} | {request_timing['p95'] if request_timing['p95'] is not None else 'unavailable'} |
| upstream response time | {upstream_timing['quality']} | {upstream_timing['count']:,} | {upstream_timing['average'] if upstream_timing['average'] is not None else 'unavailable'} | {upstream_timing['p95'] if upstream_timing['p95'] is not None else 'unavailable'} |

Cloudflare edge hits are unavailable from origin logs and require a matching Cloudflare analytics export.

## Warnings

{warning_text}
"""
    (analysis / "access-log-report.md").write_text(report, encoding="utf-8")
    print(report, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
