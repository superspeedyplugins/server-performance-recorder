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
NGINX_LOG_PATH=
EOF

output=$("$ROOT/record" launch "$config")
run=$(printf '%s\n' "$output" | awk '/^Launched:/ {print $2}')
[[ -n "$run" && -d "$run" ]]
"$ROOT/record" wait "$run"
"$ROOT/record" validate "$run"
"$ROOT/collect" "$config"

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
assert (run / "SHA256SUMS").stat().st_size > 0
assert Path(f"{run}.tar.gz").stat().st_size > 0
assert (run / "runtime/bin/recorder-runner").is_file()
print(f"PASS: {len(rows)} samples at {epochs}")
print(f"PASS: recorder CPU average {summary['recorder_impact']['cpu_percent']['average']}%")
print(f"PASS: recorder RSS average {summary['recorder_impact']['rss_mib']['average']} MiB")
print(f"PASS: evidence bundle {run}")
PY
