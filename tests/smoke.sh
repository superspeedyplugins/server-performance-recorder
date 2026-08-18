#!/usr/bin/env bash

set -Eeuo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd -P)
workspace=$(mktemp -d)
trap 'rm -rf -- "$workspace"' EXIT

config="$workspace/smoke.conf"
disk_device=$(find /sys/class/block -mindepth 1 -maxdepth 1 -type l -printf '%f\n' | sed -n '1p')
[[ -n "$disk_device" ]]
cat > "$config" <<EOF
RUN_LABEL=smoke-before
OBSERVED_SITE=smoke.invalid
SERVER_SITE_COUNT=10
ENVIRONMENT_NOTE='Linux smoke test'
DURATION_SECONDS=20
SAMPLE_INTERVAL_SECONDS=10
OUTPUT_ROOT=$workspace/runs
DISK_DEVICES=$disk_device
LOW_PRIORITY=1
WEB_SERVER_TYPE=nginx
ACCESS_LOG_PATH=$workspace/access.log
EOF

output=$("$ROOT/record" launch "$config")
run=$(printf '%s\n' "$output" | awk '/^Launched:/ {print $2}')
[[ -n "$run" && -d "$run" ]]
"$ROOT/record" wait "$run"
"$ROOT/record" validate "$run"

python3 - "$run" "$workspace/access.log" <<'PY'
import gzip, json, sys
from datetime import datetime, timezone
from pathlib import Path

run = Path(sys.argv[1])
log = Path(sys.argv[2])
manifest = json.loads((run / "run.json").read_text(encoding="utf-8"))
start = int(manifest["timestamps"]["start_epoch"])

def line(offset, status="-"):
    stamp = datetime.fromtimestamp(start + offset, timezone.utc).strftime("%d/%b/%Y:%H:%M:%S %z")
    return f'127.0.0.1 - - [{stamp}] "GET /test-{offset}/ HTTP/1.1" 200 123 "-" "smoke" {status}\n'

log.write_text(line(0, "HIT") + line(10, "MISS") + line(15, "BYPASS") + line(16), encoding="utf-8")
with gzip.open(str(log) + ".1.gz", "wt", encoding="utf-8") as target:
    target.write(line(5, "HIT"))
    target.write(line(20, "STALE"))
    target.write(line(-60, "HIT"))
PY

archive_before=$(sha256sum "$run.zip" | awk '{print $1}')
dry_output=$("$ROOT/collect" --analyse-access-log --dry-run --config "$config")
grep -Fq 'Dry run: no log contents were read' <<<"$dry_output"
[[ ! -e "$run/analysis" ]]
archive_after=$(sha256sum "$run.zip" | awk '{print $1}')
[[ "$archive_before" == "$archive_after" ]]

"$ROOT/collect" --analyse-access-log --config "$config"

archive_with_analysis=$(sha256sum "$run.zip" | awk '{print $1}')
derived="$workspace/derived-access-analysis"
"$ROOT/collect" --analyse-access-log --derived-output "$derived" --config "$config" >/dev/null
[[ -f "$derived/access-log-summary.json" && -f "$derived/access-log-report.md" ]]
[[ "$archive_with_analysis" == $(sha256sum "$run.zip" | awk '{print $1}') ]]

python3 - "$run" <<'PY'
import csv, json, sys
from pathlib import Path

run = Path(sys.argv[1])
rows = list(csv.DictReader((run / "telemetry/system.csv").open(encoding="utf-8")))
assert len(rows) == 3, len(rows)
epochs = [int(row["epoch"]) for row in rows]
assert all(9 <= right - left <= 11 for left, right in zip(epochs, epochs[1:])), epochs
assert all(row["mem_total_kb"] for row in rows)
assert all(row["cpu_idle"] for row in rows)
summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
assert summary["coverage"]["percent"] >= 95
assert summary["scope"]["server_site_count"] == "10"
assert summary["scope"]["server_wide_metrics"] is True
manifest = json.loads((run / "run.json").read_text(encoding="utf-8"))
assert manifest["status"] == "validated", manifest
effective = json.loads((run / "config/effective.json").read_text(encoding="utf-8"))
assert effective["schema_version"] == 2, effective
assert effective["web_server_type"] == "nginx", effective
assert effective["access_log_path"].endswith("/access.log"), effective
assert (run / "SHA256SUMS").stat().st_size > 0
archive = Path(f"{run}.zip")
assert archive.stat().st_size > 0
assert (run / "runtime/bin/recorder-runner").is_file()
access = json.loads((run / "analysis/access-log-summary.json").read_text(encoding="utf-8"))
assert access["lines"]["requests_in_window"] == 5, access
assert access["lines"]["with_cache_status"] == 5, access
assert access["cache"]["statuses"]["HIT"]["count"] == 2, access
assert access["cache"]["statuses"]["MISS"]["count"] == 1, access
assert access["cache"]["statuses"]["BYPASS"]["count"] == 1, access
assert access["cache"]["statuses"]["STALE"]["count"] == 1, access
assert access["cache"]["ratios"]["hit_percent"] == 40.0, access
assert access["cache"]["ratios"]["cache_served_percent"] == 60.0, access
assert "analysis/access-log-summary.json" in (run / "SHA256SUMS").read_text(encoding="utf-8")
print(f"PASS: {len(rows)} samples at {epochs}")
print(f"PASS: recorder CPU average {summary['recorder_impact']['cpu_percent']['average']}%")
print(f"PASS: recorder RSS average {summary['recorder_impact']['rss_mib']['average']} MiB")
print(f"PASS: evidence bundle {run}")
print(f"PASS: automatic download archive {archive}")
print("PASS: collect dry run did not modify analysis or archive output")
print("PASS: access-log rotations analysed for the exact run window")
print("PASS: derived access-log analysis left the original run and ZIP unchanged")
PY
