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

for server in nginx apache openlitespeed litespeed; do
    log="$workspace/${server}_access.log"
    printf '%s\n' \
        '127.0.0.1 - - [17/Aug/2023:12:10:00 +0000] "GET / HTTP/1.1" 200 123 "-" "fixture" HIT' \
        '127.0.0.1 - - [17/Aug/2023:12:20:00 +0000] "POST /wp-admin/admin-ajax.php HTTP/1.1" 503 12 "-" "fixture" MISS' \
        > "$log"
    printf '%s\n' \
        '127.0.0.1 - - [17/Aug/2023:12:30:00 +0000] "GET /old HTTP/1.1" 404 1 "-" "fixture"' \
        | gzip > "${log}-20230817.gz"

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
printf 'PASS: evidence ZIP is valid and contains the run directory\n'
