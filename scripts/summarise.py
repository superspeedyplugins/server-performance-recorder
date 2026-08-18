#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import statistics
from pathlib import Path
from typing import Iterable


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1))
    return ordered[index]


def stats(values: Iterable[float]) -> dict[str, float | int | None]:
    clean = [float(value) for value in values if math.isfinite(float(value))]
    return {
        "samples": len(clean),
        "min": min(clean) if clean else None,
        "average": statistics.fmean(clean) if clean else None,
        "p95": percentile(clean, 0.95),
        "max": max(clean) if clean else None,
    }


def number(row: dict[str, str], key: str) -> float | None:
    try:
        return float(row[key]) if row.get(key, "") != "" else None
    except (KeyError, TypeError, ValueError):
        return None


def load_samples(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8") as source:
        return list(csv.DictReader(source))


def fmt(value: float | int | None, suffix: str = "", decimals: int = 2) -> str:
    if value is None:
        return "not available"
    return f"{float(value):.{decimals}f}{suffix}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    manifest = json.loads((run / "run.json").read_text(encoding="utf-8"))
    effective_path = run / "config" / "effective.json"
    effective = json.loads(effective_path.read_text(encoding="utf-8")) if effective_path.is_file() else {}
    samples = load_samples(run / "telemetry" / "system.csv")
    ticks = float(effective.get("clock_ticks") or os.sysconf("SC_CLK_TCK"))
    disk_device_count = max(1, len(effective.get("disk_devices", [])))

    interval_metrics: dict[str, list[tuple[float, float]]] = {
        "cpu_busy_percent": [], "cpu_iowait_percent": [], "disk_read_mib_s": [],
        "disk_write_mib_s": [], "disk_util_percent": [], "network_rx_mib_s": [],
        "network_tx_mib_s": [], "recorder_cpu_percent": [],
    }
    sample_metrics: dict[str, list[tuple[float, float]]] = {
        "memory_used_percent": [], "memory_available_gib": [], "swap_used_gib": [],
        "load_1m": [], "load_5m": [], "load_15m": [], "recorder_rss_mib": [],
    }

    for row in samples:
        epoch = number(row, "epoch")
        total = number(row, "mem_total_kb")
        available = number(row, "mem_available_kb")
        if epoch is None:
            continue
        if total and available is not None:
            sample_metrics["memory_used_percent"].append((epoch, (total - available) / total * 100))
            sample_metrics["memory_available_gib"].append((epoch, available / 1024 / 1024))
        swap_total = number(row, "swap_total_kb")
        swap_free = number(row, "swap_free_kb")
        if swap_total is not None and swap_free is not None:
            sample_metrics["swap_used_gib"].append((epoch, (swap_total - swap_free) / 1024 / 1024))
        for source, target in (("load1", "load_1m"), ("load5", "load_5m"), ("load15", "load_15m")):
            value = number(row, source)
            if value is not None:
                sample_metrics[target].append((epoch, value))
        rss = number(row, "recorder_rss_kb")
        if rss is not None:
            sample_metrics["recorder_rss_mib"].append((epoch, rss / 1024))

    cpu_fields = ("cpu_user", "cpu_nice", "cpu_system", "cpu_idle", "cpu_iowait", "cpu_irq", "cpu_softirq", "cpu_steal")
    for previous, current in zip(samples, samples[1:]):
        start = number(previous, "epoch")
        end = number(current, "epoch")
        if start is None or end is None or end <= start:
            continue
        elapsed = end - start
        deltas = []
        for field in cpu_fields:
            old, new = number(previous, field), number(current, field)
            deltas.append((new - old) if old is not None and new is not None else None)
        if all(value is not None for value in deltas) and sum(deltas) > 0:
            total_delta = sum(deltas)
            idle_delta = deltas[3]
            iowait_delta = deltas[4]
            interval_metrics["cpu_busy_percent"].append((end, (total_delta - idle_delta - iowait_delta) / total_delta * 100))
            interval_metrics["cpu_iowait_percent"].append((end, iowait_delta / total_delta * 100))

        for field, target, factor in (
            ("disk_read_sectors", "disk_read_mib_s", 512 / 1024 / 1024),
            ("disk_write_sectors", "disk_write_mib_s", 512 / 1024 / 1024),
            ("network_rx_bytes", "network_rx_mib_s", 1 / 1024 / 1024),
            ("network_tx_bytes", "network_tx_mib_s", 1 / 1024 / 1024),
        ):
            old, new = number(previous, field), number(current, field)
            if old is not None and new is not None and new >= old:
                interval_metrics[target].append((end, (new - old) * factor / elapsed))
        old_io, new_io = number(previous, "disk_io_ms"), number(current, "disk_io_ms")
        if old_io is not None and new_io is not None and new_io >= old_io:
            interval_metrics["disk_util_percent"].append((end, (new_io - old_io) / (elapsed * 1000 * disk_device_count) * 100))
        old_ticks = sum(number(previous, field) or 0 for field in ("recorder_utime_ticks", "recorder_stime_ticks", "recorder_cutime_ticks", "recorder_cstime_ticks"))
        new_ticks = sum(number(current, field) or 0 for field in ("recorder_utime_ticks", "recorder_stime_ticks", "recorder_cutime_ticks", "recorder_cstime_ticks"))
        if new_ticks >= old_ticks:
            interval_metrics["recorder_cpu_percent"].append((end, (new_ticks - old_ticks) / ticks / elapsed * 100))

    metric_points = {**sample_metrics, **interval_metrics}
    metrics = {name: stats(value for _, value in points) for name, points in metric_points.items()}
    duration = (number(samples[-1], "epoch") - number(samples[0], "epoch")) if len(samples) >= 2 else 0
    expected = int(effective.get("duration_seconds", 0))
    sample_interval = int(effective.get("sample_interval_seconds", 10))
    expected_samples = math.ceil(expected / sample_interval) + 1 if expected else 0
    coverage = len(samples) / expected_samples * 100 if expected_samples else None
    bundle_bytes = sum(path.stat().st_size for path in run.rglob("*") if path.is_file())

    summary = {
        "schema_version": 1,
        "run_id": manifest.get("run_id"),
        "label": manifest.get("label"),
        "scope": {
            "server_wide_metrics": True,
            "observed_site": effective.get("observed_site", ""),
            "server_site_count": effective.get("server_site_count", "unknown"),
            "environment_note": effective.get("environment_note", ""),
            "disk_devices": effective.get("disk_devices", []),
        },
        "coverage": {
            "samples": len(samples), "expected_samples": expected_samples,
            "percent": coverage, "observed_seconds": duration,
        },
        "metrics": metrics,
        "recorder_impact": {
            "cpu_percent": metrics.get("recorder_cpu_percent"),
            "rss_mib": metrics.get("recorder_rss_mib"),
            "evidence_bytes": bundle_bytes,
        },
    }
    access_log_path = run / "analysis" / "access-log-summary.json"
    if access_log_path.is_file():
        summary["access_log"] = json.loads(access_log_path.read_text(encoding="utf-8"))

    (run / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    site_count = summary["scope"]["server_site_count"]
    qualification = "The figures below are for the whole server."
    if site_count != "unknown":
        qualification += f" The observed site was one of {site_count} sites hosted on it during this window."
    report = f"""# Server performance recording: {manifest.get('label', '')}

{qualification}

- Window: {fmt(duration, ' seconds', 0)}
- Samples: {len(samples)} of {expected_samples} expected ({fmt(coverage, '%')})
- CPU busy: average {fmt(metrics['cpu_busy_percent']['average'], '%')}, trough {fmt(metrics['cpu_busy_percent']['min'], '%')}, p95 {fmt(metrics['cpu_busy_percent']['p95'], '%')}, peak {fmt(metrics['cpu_busy_percent']['max'], '%')}
- CPU I/O wait: average {fmt(metrics['cpu_iowait_percent']['average'], '%')}, trough {fmt(metrics['cpu_iowait_percent']['min'], '%')}, peak {fmt(metrics['cpu_iowait_percent']['max'], '%')}
- RAM used: average {fmt(metrics['memory_used_percent']['average'], '%')}, trough {fmt(metrics['memory_used_percent']['min'], '%')}, peak {fmt(metrics['memory_used_percent']['max'], '%')}
- Available RAM: average {fmt(metrics['memory_available_gib']['average'], ' GiB')}, trough {fmt(metrics['memory_available_gib']['min'], ' GiB')}
- Disk reads: average {fmt(metrics['disk_read_mib_s']['average'], ' MiB/s')}, trough {fmt(metrics['disk_read_mib_s']['min'], ' MiB/s')}, p95 {fmt(metrics['disk_read_mib_s']['p95'], ' MiB/s')}, peak {fmt(metrics['disk_read_mib_s']['max'], ' MiB/s')}
- Disk writes: average {fmt(metrics['disk_write_mib_s']['average'], ' MiB/s')}, trough {fmt(metrics['disk_write_mib_s']['min'], ' MiB/s')}, p95 {fmt(metrics['disk_write_mib_s']['p95'], ' MiB/s')}, peak {fmt(metrics['disk_write_mib_s']['max'], ' MiB/s')}
- Disk busy: average {fmt(metrics['disk_util_percent']['average'], '%')}, trough {fmt(metrics['disk_util_percent']['min'], '%')}, peak {fmt(metrics['disk_util_percent']['max'], '%')}
- Recorder CPU: average {fmt(metrics['recorder_cpu_percent']['average'], '%')}, peak {fmt(metrics['recorder_cpu_percent']['max'], '%')}
- Recorder RSS: average {fmt(metrics['recorder_rss_mib']['average'], ' MiB')}, peak {fmt(metrics['recorder_rss_mib']['max'], ' MiB')}
- Evidence size at summary time: {bundle_bytes / 1024 / 1024:.2f} MiB

The CPU, RAM, load, disk and network figures are server-wide. Access-log request/cache analysis, when attached, is site-specific only if the supplied access log or host filter is site-specific.
"""
    (run / "report.md").write_text(report, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
