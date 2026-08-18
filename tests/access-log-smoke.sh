#!/usr/bin/env bash

set -Eeuo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd -P)
workspace=$(mktemp -d)
trap 'rm -rf -- "$workspace"' EXIT
run="$workspace/run"
mkdir -p "$run"

cat > "$run/run.json" <<'EOF'
{
  "timestamps": {
    "start_epoch": 1692273600,
    "end_epoch": 1692277200
  }
}
EOF

dry_run="$workspace/dry-run"
dry_log="$workspace/dry_access.log"
mkdir -p "$dry_run"
cp "$run/run.json" "$dry_run/run.json"
printf '%s\n' 'dry run must not read this line' > "$dry_log"
chmod 000 "$dry_log"
dry_output=$(python3 "$ROOT/scripts/analyse-access-log.py" \
    --run-dir "$dry_run" --log "$dry_log" --server nginx --dry-run)
chmod 600 "$dry_log"
grep -Fq 'Dry run: no log contents were read' <<<"$dry_output"
grep -Fq "$dry_log" <<<"$dry_output"
[[ ! -e "$dry_run/analysis" ]]
limited_output=$(python3 "$ROOT/scripts/analyse-access-log.py" \
    --run-dir "$dry_run" --log "$dry_log" --server nginx --dry-run --max-input-bytes 1)
grep -Fq 'SKIP (over ceiling)' <<<"$limited_output"
grep -Fq 'Selected input: 0 B (0 bytes) across 0 file(s)' <<<"$limited_output"

rotation_run="$workspace/rotation-window"
rotation_log="$workspace/OPSHOLDERS_access.log"
mkdir -p "$rotation_run"
cat > "$rotation_run/run.json" <<'EOF'
{
  "timestamps": {
    "start_epoch": 1786976578,
    "end_epoch": 1786990978
  }
}
EOF
printf '%s\n' 'current log after the recorder window' > "$rotation_log"
printf '%s\n' 'rotation containing the recorder window' > "${rotation_log}-20260818"
printf '%s\n' 'rotation before the recorder window' | gzip > "${rotation_log}-20260817.gz"
printf '%s\n' 'much older rotation' | gzip > "${rotation_log}-20260807.gz"
rotation_output=$(TZ=UTC python3 "$ROOT/scripts/analyse-access-log.py" \
    --run-dir "$rotation_run" --log "$rotation_log" --server nginx --dry-run)
grep -F $'READ\t' <<<"$rotation_output" | grep -Fq "${rotation_log}-20260818"
grep -F $'SKIP (outside window)\t' <<<"$rotation_output" | grep -Fq "${rotation_log}-20260817.gz"
grep -F $'SKIP (outside window)\t' <<<"$rotation_output" | grep -Fq "${rotation_log}-20260807.gz"
grep -F $'SKIP (outside window)\t' <<<"$rotation_output" | grep -Fq "${rotation_log}"
[[ $(grep -c $'^READ\t' <<<"$rotation_output") -eq 1 ]]

for server in nginx apache openlitespeed litespeed; do
    log="$workspace/${server}_access.log"
    printf '%s\n' \
        '127.0.0.1 - - [18/Aug/2023:12:10:00 +0000] "GET /after HTTP/1.1" 200 123 "-" "fixture" HIT' \
        > "$log"
    printf '%s\n' \
        '127.0.0.1 - - [17/Aug/2023:12:10:00 +0000] "GET /product-category/wheels/filter_brand/acme/ HTTP/1.1" 200 123 "-" "Mozilla/5.0 (compatible; Googlebot/2.1)" HIT rt=0.200 urt=0.180' \
        '127.0.0.1 - - [17/Aug/2023:12:20:00 +0000] "POST /wp-admin/admin-ajax.php HTTP/1.1" 503 12 "-" "Mozilla/5.0" MISS rt=0.500 urt=0.450' \
        '127.0.0.1 - - [17/Aug/2023:12:30:00 +0000] "GET /old HTTP/1.1" 404 1 "-" "fixture"' \
        | gzip > "${log}-20230818.gz"

    python3 "$ROOT/scripts/analyse-access-log.py" --run-dir "$run" --log "$log" --server "$server" >/dev/null
    python3 - "$run/analysis/access-log-summary.json" "$server" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
encoded = json.dumps(value)
assert value["web_server"] == sys.argv[2]
assert value["lines"]["requests_in_window"] == 3, value
assert value["http_statuses"] == {"200": 1, "404": 1, "503": 1}, value
assert value["methods"] == {"GET": 2, "POST": 1}, value
assert value["cache"]["statuses"]["HIT"]["count"] == 1, value
assert value["cache"]["statuses"]["MISS"]["count"] == 1, value
assert value["automation"]["classes"]["claimed_search_crawler"]["count"] == 1, value
assert value["automation"]["classes"]["not_identified_as_automation"]["count"] == 2, value
assert value["request_classes"]["counts"]["deep_filter"] == 1, value
assert value["request_classes"]["counts"]["admin_ajax"] == 1, value
assert value["timings"]["request_time_ms"]["count"] == 2, value
assert value["timings"]["request_time_ms"]["average"] == 350.0, value
assert value["timings"]["upstream_response_time_ms"]["p95"] == 450.0, value
bot_filter = next(item for item in value["request_groups"] if item["automation"] == "claimed_search_crawler" and item["request_class"] == "deep_filter")
assert bot_filter["requests"] == 1, value
assert bot_filter["request_time_ms"]["sum"] == 200.0, value
assert bot_filter["upstream_response_time_ms"]["average"] == 180.0, value
assert value["archive_protection_proxy"]["claimed_automation_deep_filter_requests"] == 1, value
assert value["archive_protection_proxy"]["percent_of_claimed_automation_requests"] == 100.0, value
assert value["archive_protection_proxy"]["request_time_ms"]["sum"] == 200.0, value
assert "Googlebot" not in encoded and "/product-category/wheels/" not in encoded, value
PY
done

missing_log="$workspace/missing_fields_access.log"
printf '%s\n' \
    '127.0.0.1 - - [17/Aug/2023:12:10:00 +0000] "GET / HTTP/1.1" 200 123 "-" "Mozilla/5.0"' \
    > "$missing_log"
python3 "$ROOT/scripts/analyse-access-log.py" --run-dir "$run" --log "$missing_log" --server nginx >/dev/null
python3 - "$run/analysis/access-log-summary.json" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
assert value["cache"]["quality"] == "unavailable", value
assert all(item["count"] is None for item in value["cache"]["statuses"].values()), value
assert value["timings"]["request_time_ms"]["quality"] == "unavailable", value
assert value["timings"]["request_time_ms"]["average"] is None, value
assert value["timings"]["upstream_response_time_ms"]["p95"] is None, value
PY

derived_run="$workspace/derived-run"
derived_output="$workspace/derived-evidence"
mkdir -p "$derived_run"
cp "$run/run.json" "$derived_run/run.json"
python3 "$ROOT/scripts/analyse-access-log.py" \
    --run-dir "$derived_run" --log "$missing_log" --server nginx --output-dir "$derived_output" >/dev/null
[[ -f "$derived_output/access-log-summary.json" ]]
[[ -f "$derived_output/access-log-report.md" ]]
[[ ! -e "$derived_run/analysis" ]]

archive=$(python3 "$ROOT/scripts/archive.py" "$run")
python3 - "$archive" "$(basename -- "$run")" <<'PY'
import stat, sys, zipfile
from pathlib import Path
archive, run_name = sys.argv[1:]
with zipfile.ZipFile(archive) as value:
    assert value.testzip() is None
    names = set(value.namelist())
assert f"{run_name}/run.json" in names, names
assert f"{run_name}/analysis/access-log-summary.json" in names, names
assert stat.S_IMODE(Path(archive).stat().st_mode) == 0o600
assert not list(Path(archive).parent.glob(f".{Path(archive).name}.tmp.*"))
PY

printf 'PASS: Nginx, Apache, OpenLiteSpeed and LiteSpeed combined logs are analysed\n'
printf 'PASS: RunCloud date-suffixed gzip rotations are included\n'
printf 'PASS: dry run reads metadata only and honours the input ceiling\n'
printf 'PASS: RunCloud rotations outside the recorder window are skipped\n'
printf 'PASS: automation, request classes and available timings are aggregated without retaining raw values\n'
printf 'PASS: missing cache and timing fields remain unavailable rather than zero\n'
printf 'PASS: derived analysis can be written separately without modifying the original run\n'
printf 'PASS: evidence ZIP is valid and contains the run directory\n'
