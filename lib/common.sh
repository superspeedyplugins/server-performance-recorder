#!/usr/bin/env bash

set -o pipefail

spr_root_dir() {
    cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd -P
}

spr_utc_now() {
    date -u +'%Y-%m-%dT%H:%M:%SZ'
}

spr_epoch_now() {
    date +'%s'
}

spr_die() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

spr_warn() {
    printf 'WARNING: %s\n' "$*" >&2
}

spr_info() {
    printf '%s %s\n' "$(spr_utc_now)" "$*"
}

spr_absolute_path() {
    local path=${1:?path required}
    local parent
    parent=$(cd -- "$(dirname -- "$path")" >/dev/null 2>&1 && pwd -P) || return 1
    printf '%s/%s\n' "$parent" "$(basename -- "$path")"
}

spr_load_config() {
    local config=${1:?config path required}
    [[ -r "$config" ]] || spr_die "Cannot read config: $config"

    RUN_LABEL=${RUN_LABEL:-recording}
    OBSERVED_SITE=${OBSERVED_SITE:-}
    SERVER_SITE_COUNT=${SERVER_SITE_COUNT:-unknown}
    ENVIRONMENT_NOTE=${ENVIRONMENT_NOTE:-}
    DURATION_SECONDS=${DURATION_SECONDS:-86400}
    SAMPLE_INTERVAL_SECONDS=${SAMPLE_INTERVAL_SECONDS:-10}
    OUTPUT_ROOT=${OUTPUT_ROOT:-/var/log/server-performance-recorder/runs}
    NGINX_LOG_PATH=${NGINX_LOG_PATH:-}
    DISK_DEVICES=${DISK_DEVICES:-auto}
    LOW_PRIORITY=${LOW_PRIORITY:-1}

    # The config is a trusted, local shell assignment file. It is not copied verbatim
    # into evidence because it may name credential and cookie files.
    # shellcheck disable=SC1090
    source "$config"

    export RUN_LABEL OBSERVED_SITE SERVER_SITE_COUNT ENVIRONMENT_NOTE
    export DURATION_SECONDS SAMPLE_INTERVAL_SECONDS OUTPUT_ROOT NGINX_LOG_PATH
    export DISK_DEVICES LOW_PRIORITY
}

spr_is_uint() {
    [[ ${1:-} =~ ^[0-9]+$ ]]
}

spr_is_bool() {
    [[ ${1:-} == 0 || ${1:-} == 1 ]]
}

spr_validate_config() {
    spr_is_uint "$DURATION_SECONDS" && (( DURATION_SECONDS >= 10 )) || spr_die 'DURATION_SECONDS must be an integer of at least 10'
    spr_is_uint "$SAMPLE_INTERVAL_SECONDS" && (( SAMPLE_INTERVAL_SECONDS >= 10 )) || spr_die 'SAMPLE_INTERVAL_SECONDS must be at least 10 to protect live traffic'
    spr_is_bool "$LOW_PRIORITY" || spr_die 'LOW_PRIORITY must be 0 or 1'
    [[ -n "$OUTPUT_ROOT" ]] || spr_die 'OUTPUT_ROOT must not be empty'
    [[ "$SERVER_SITE_COUNT" == unknown ]] || spr_is_uint "$SERVER_SITE_COUNT" || spr_die 'SERVER_SITE_COUNT must be an integer or unknown'
}

spr_resolve_disk_devices() {
    SPR_DISK_DEVICES=$DISK_DEVICES
    if [[ "$SPR_DISK_DEVICES" == auto ]]; then
        command -v findmnt >/dev/null 2>&1 || spr_die 'findmnt is required for DISK_DEVICES=auto'
        command -v lsblk >/dev/null 2>&1 || spr_die 'lsblk is required for DISK_DEVICES=auto'
        local root_source
        root_source=$(findmnt -n -o SOURCE / 2>/dev/null || true)
        [[ -n "$root_source" ]] || spr_die 'Could not identify the block device mounted at /'
        SPR_DISK_DEVICES=$(lsblk -ndo KNAME "$root_source" 2>/dev/null | sed -n '1p' || true)
        [[ -n "$SPR_DISK_DEVICES" ]] || SPR_DISK_DEVICES=$(basename -- "$root_source")
    fi
    local device count=0
    for device in $SPR_DISK_DEVICES; do
        [[ "$device" =~ ^[A-Za-z0-9._!+-]+$ ]] || spr_die "Unsafe block-device name: $device"
        [[ -r "/sys/class/block/$device/stat" ]] || spr_die "Cannot read block statistics for $device"
        count=$((count + 1))
    done
    (( count > 0 )) || spr_die 'No block devices were selected'
}

spr_background() {
    if command -v setsid >/dev/null 2>&1; then
        nohup setsid "$@" </dev/null >/dev/null 2>&1 &
    else
        nohup "$@" </dev/null >/dev/null 2>&1 &
    fi
    SPR_BACKGROUND_PID=$!
}

spr_nice_prefix() {
    SPR_NICE=()
    if [[ ${LOW_PRIORITY:-1} == 1 ]]; then
        command -v nice >/dev/null 2>&1 && SPR_NICE+=(nice -n 19)
        command -v ionice >/dev/null 2>&1 && SPR_NICE+=(ionice -c 3)
    fi
}
