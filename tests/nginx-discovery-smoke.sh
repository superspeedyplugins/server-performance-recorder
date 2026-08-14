#!/usr/bin/env bash

set -Eeuo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd -P)
workspace=$(mktemp -d)
trap 'rm -rf -- "$workspace"' EXIT

cat > "$workspace/nginx" <<'EOF'
#!/usr/bin/env bash
cat >&2 <<'CONFIG'
nginx: configuration file /etc/nginx/nginx.conf test is successful
http {
    access_log /var/log/nginx/global-access.log cache;
    access_log off;
    error_log /var/log/nginx/error.log warn;
    server {
        access_log "/srv/logs/shop.access.log" cache;
        error_log '/srv/logs/shop.error.log';
    }
}
CONFIG
EOF
chmod +x "$workspace/nginx"

# shellcheck source=../lib/common.sh
source "$ROOT/lib/common.sh"
PATH="$workspace:$PATH"

access=$(spr_discover_nginx_access_logs)
error=$(spr_discover_nginx_error_logs)
grep -Fxq '/var/log/nginx/global-access.log' <<<"$access"
grep -Fxq '/srv/logs/shop.access.log' <<<"$access"
grep -Fxq '/var/log/nginx/error.log' <<<"$error"
grep -Fxq '/srv/logs/shop.error.log' <<<"$error"
! grep -Fxq 'off' <<<"$access"

printf 'PASS: Nginx access and error log directives were discovered\n'
