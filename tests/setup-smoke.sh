#!/usr/bin/env bash

set -Eeuo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd -P)
workspace=$(mktemp -d)
trap 'rm -rf -- "$workspace"' EXIT
command -v script >/dev/null

disk_device=$(find /sys/class/block -mindepth 1 -maxdepth 1 -type l -printf '%f\n' | sed -n '1p')
[[ -n "$disk_device" ]]
config="$workspace/private/config.conf"
output="$workspace/evidence"

printf 'before-interactive\nshop.example.invalid\n10\n\n1\n%s\n\n\n%s\nn\n' "$disk_device" "$output" |
    script -qefc "$ROOT/setup $config" /dev/null

[[ -f "$config" ]]
[[ $(stat -c '%a' "$config") == 600 ]]
# shellcheck disable=SC1090
source "$config"
[[ "$RUN_LABEL" == before-interactive ]]
[[ "$OBSERVED_SITE" == shop.example.invalid ]]
[[ "$SERVER_SITE_COUNT" == 10 ]]
[[ "$DURATION_SECONDS" == 3600 ]]
[[ "$SAMPLE_INTERVAL_SECONDS" == 10 ]]
[[ "$DISK_DEVICES" == "$disk_device" ]]
[[ "$OUTPUT_ROOT" == "$output" ]]
[[ -n "$WEB_SERVER_TYPE" ]]
[[ ${ACCESS_LOG_PATH+x} ]]
printf 'PASS: interactive setup created a private mode-600 config\n'
