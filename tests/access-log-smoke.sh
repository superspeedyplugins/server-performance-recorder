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
        '127.0.0.1 - - [17/Aug/2023:12:10:00 +0000] "GET / HTTP/1.1" 200 123 "-" "fixture" HIT' \
        '127.0.0.1 - - [17/Aug/2023:12:20:00 +0000] "POST /wp-admin/admin-ajax.php HTTP/1.1" 503 12 "-" "fixture" MISS' \
        '127.0.0.1 - - [17/Aug/2023:12:30:00 +0000] "GET /old HTTP/1.1" 404 1 "-" "fixture"' \
        | gzip > "${log}-20230818.gz"

    python3 "$ROOT/scripts/analyse-access-log.py" --run-dir "$run" --log "$log" --server "$server" >/dev/null
    python3 - "$run/analysis/access-log-summary.json" "$server" <<'PY'
import json, sys
value = json.load(open(sys.argv[1], encoding="utf-8"))
assert value["web_server"] == sys.argv[2]
assert value["lines"]["requests_in_window"] == 3, value
assert value["http_statuses"] == {"200": 1, "404": 1, "503": 1}, value
assert value["methods"] == {"GET": 2, "POST": 1}, value
assert value["cache"]["statuses"]["HIT"]["count"] == 1, value
assert value["cache"]["statuses"]["MISS"]["count"] == 1, value
PY
done

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
printf 'PASS: evidence ZIP is valid and contains the run directory\n'
