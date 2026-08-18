#!/usr/bin/env bash

set -Eeuo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd -P)
workspace=$(mktemp -d)
trap 'rm -rf -- "$workspace"' EXIT
export HOME="$workspace/home"
mkdir -p "$HOME/logs/nginx" "$HOME/logs/apache2"
touch "$HOME/logs/nginx/app-shop-example-com_access.log"
touch "$HOME/logs/nginx/app-shop-example-com_error.log"
touch "$HOME/logs/nginx/app-opsholders_access.log"
touch "$HOME/logs/apache2/app-shop-example-com_access.log"
touch "$HOME/logs/apache2/app-shop-example-com_error.log"
touch "$HOME/logs/apache2/app-opsholders_access.log"
touch "$HOME/logs/app-shop-example-com_access.log"
touch "$HOME/logs/app-shop-example-com_error.log"

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
grep -Fxq "$HOME/logs/nginx/app-shop-example-com_access.log" <<<"$access"
grep -Fxq "$HOME/logs/nginx/app-shop-example-com_error.log" <<<"$error"

apache_access=$(spr_discover_apache_access_logs)
apache_error=$(spr_discover_apache_error_logs)
grep -Fxq "$HOME/logs/apache2/app-shop-example-com_access.log" <<<"$apache_access"
grep -Fxq "$HOME/logs/apache2/app-shop-example-com_error.log" <<<"$apache_error"

litespeed_access=$(spr_discover_litespeed_access_logs)
litespeed_error=$(spr_discover_litespeed_error_logs)
grep -Fxq "$HOME/logs/app-shop-example-com_access.log" <<<"$litespeed_access"
grep -Fxq "$HOME/logs/app-shop-example-com_error.log" <<<"$litespeed_error"

suggested=$(spr_suggest_access_log shop.example.com nginx)
[[ "$suggested" == "$HOME/logs/nginx/app-shop-example-com_access.log" ]]
runcloud_suggested=$(spr_suggest_access_log opsholders.com multiple)
[[ "$runcloud_suggested" == "$HOME/logs/nginx/app-opsholders_access.log" ]]

cat > "$workspace/legacy.conf" <<EOF
NGINX_LOG_PATH=$HOME/logs/nginx/app-shop-example-com_access.log
EOF
spr_load_config "$workspace/legacy.conf"
[[ "$ACCESS_LOG_PATH" == "$HOME/logs/nginx/app-shop-example-com_access.log" ]]

printf 'PASS: Nginx, Apache and LiteSpeed access/error logs were discovered\n'
printf 'PASS: matching site log was selected as the setup default\n'
printf 'PASS: legacy NGINX_LOG_PATH configs remain compatible\n'
