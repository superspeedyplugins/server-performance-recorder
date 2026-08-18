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
    WEB_SERVER_TYPE=${WEB_SERVER_TYPE:-auto}
    ACCESS_LOG_PATH=${ACCESS_LOG_PATH:-}
    NGINX_LOG_PATH=${NGINX_LOG_PATH:-}
    DISK_DEVICES=${DISK_DEVICES:-auto}
    LOW_PRIORITY=${LOW_PRIORITY:-1}

    # The config is a trusted, local shell assignment file. It is not copied verbatim
    # into evidence because it may name credential and cookie files.
    # shellcheck disable=SC1090
    source "$config"

    # NGINX_LOG_PATH was the original public setting. Keep old private configs
    # working while using the server-neutral name everywhere else.
    if [[ -z "$ACCESS_LOG_PATH" && -n "$NGINX_LOG_PATH" ]]; then
        ACCESS_LOG_PATH=$NGINX_LOG_PATH
    fi

    export RUN_LABEL OBSERVED_SITE SERVER_SITE_COUNT ENVIRONMENT_NOTE
    export DURATION_SECONDS SAMPLE_INTERVAL_SECONDS OUTPUT_ROOT WEB_SERVER_TYPE
    export ACCESS_LOG_PATH NGINX_LOG_PATH
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
    [[ "$WEB_SERVER_TYPE" =~ ^(auto|nginx|apache|openlitespeed|litespeed|unknown|multiple)$ ]] || \
        spr_die 'WEB_SERVER_TYPE must be auto, nginx, apache, openlitespeed, litespeed, unknown or multiple'
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

spr_normalise_site_name() {
    printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]' | sed -E 's#^[a-z]+://##; s#/.*$##; s/^www\.//; s/[^a-z0-9]+//g'
}

spr_process_running() {
    local wanted=$1 process
    [[ -d /proc ]] || return 1
    for process in /proc/[0-9]*/comm; do
        [[ -r "$process" ]] || continue
        IFS= read -r name < "$process" || continue
        [[ "$name" =~ $wanted ]] && return 0
    done
    return 1
}

spr_detect_web_servers() {
    local found=() active=()
    spr_process_running '^(nginx)$' && active+=(nginx)
    spr_process_running '^(apache2|httpd)$' && active+=(apache)
    if spr_process_running '^(openlitespeed)$'; then
        active+=(openlitespeed)
    elif spr_process_running '^(lshttpd|litespeed)$'; then
        # lshttpd is used by both OpenLiteSpeed and LiteSpeed Enterprise. A
        # RunCloud owner log layout is OpenLiteSpeed unless a product binary
        # gives us stronger evidence.
        if [[ -d "$HOME/logs" && ! -d "$HOME/logs/nginx" && ! -d "$HOME/logs/apache2" ]]; then
            active+=(openlitespeed)
        else
            active+=(litespeed)
        fi
    fi
    if (( ${#active[@]} )); then
        found=("${active[@]}")
    else
        if command -v nginx >/dev/null 2>&1 || [[ -d /etc/nginx || -d /var/log/nginx || -d "$HOME/logs/nginx" ]]; then
            found+=(nginx)
        fi
        if command -v apache2 >/dev/null 2>&1 || command -v httpd >/dev/null 2>&1 || \
           [[ -d /etc/apache2 || -d /etc/httpd || -d /var/log/apache2 || -d /var/log/httpd || -d "$HOME/logs/apache2" ]]; then
            found+=(apache)
        fi
        if command -v openlitespeed >/dev/null 2>&1; then
            found+=(openlitespeed)
        elif command -v lshttpd >/dev/null 2>&1 || [[ -d /usr/local/lsws ]]; then
            if [[ -d "$HOME/logs" && ! -d "$HOME/logs/nginx" && ! -d "$HOME/logs/apache2" ]]; then
                found+=(openlitespeed)
            else
                found+=(litespeed)
            fi
        fi
    fi
    if (( ${#found[@]} == 0 )); then
        printf '%s\n' unknown
    elif (( ${#found[@]} == 1 )); then
        printf '%s\n' "${found[0]}"
    else
        printf '%s\n' multiple
    fi
}

spr_discover_nginx_access_logs() {
    {
        if command -v nginx >/dev/null 2>&1; then
            nginx -T 2>&1 | awk '
                /^[[:space:]]*access_log[[:space:]]+/ {
                    value=$2
                    gsub(/[;"\047]/, "", value)
                    if (value ~ /^\// && value != "/dev/null") print value
                }
            ' || true
        fi
        if [[ -d /var/log/nginx ]]; then
            find /var/log/nginx -maxdepth 2 \( -type f -o -type l \) \
                \( -name '*access*.log' -o -name 'access.log' \) -print 2>/dev/null || true
        fi
        if [[ -d "$HOME/logs/nginx" ]]; then
            find "$HOME/logs/nginx" -maxdepth 2 \( -type f -o -type l \) \
                \( -name '*access*.log' -o -name 'access.log' \) -print 2>/dev/null || true
        fi
    } | awk 'NF && !seen[$0]++' | sort
}

spr_discover_apache_access_logs() {
    {
        local directory
        for directory in "$HOME/logs/apache2" /var/log/apache2 /var/log/httpd; do
            [[ -d "$directory" ]] || continue
            find "$directory" -maxdepth 2 \( -type f -o -type l \) \
                \( -name '*access*.log' -o -name 'access_log' \) -print 2>/dev/null || true
        done
    } | awk 'NF && !seen[$0]++' | sort
}

spr_discover_litespeed_access_logs() {
    {
        if [[ -d "$HOME/logs" ]]; then
            find "$HOME/logs" -maxdepth 1 \( -type f -o -type l \) \
                \( -name '*access*.log' -o -name 'access.log' \) -print 2>/dev/null || true
        fi
        if [[ -d /usr/local/lsws ]]; then
            find /usr/local/lsws -maxdepth 4 \( -type f -o -type l \) \
                \( -name 'access.log' -o -name '*access*.log' -o -name 'access_log' \) -print 2>/dev/null || true
        fi
    } | awk 'NF && !seen[$0]++' | sort
}

spr_discover_access_logs() {
    local server=${1:-all}
    {
        [[ "$server" == all || "$server" == auto || "$server" == multiple || "$server" == nginx ]] && spr_discover_nginx_access_logs
        [[ "$server" == all || "$server" == auto || "$server" == multiple || "$server" == openlitespeed || "$server" == litespeed ]] && spr_discover_litespeed_access_logs
        [[ "$server" == all || "$server" == auto || "$server" == multiple || "$server" == apache ]] && spr_discover_apache_access_logs
    } | awk 'NF && !seen[$0]++'
}

spr_suggest_access_log() {
    local observed_site=${1:-} server=${2:-all} candidate
    local site_key site_host site_label
    site_key=$(spr_normalise_site_name "$observed_site")
    site_host=$(printf '%s' "$observed_site" | tr '[:upper:]' '[:lower:]' | sed -E 's#^[a-z]+://##; s#/.*$##; s/^www\.//')
    site_label=${site_host%%.*}
    local readable=() matching=()
    while IFS= read -r candidate; do
        [[ -n "$candidate" && -r "$candidate" ]] || continue
        # A dated/compressed rotation is useful to the analyser but should not
        # become the base-path default while the live log exists.
        [[ "$candidate" =~ (\.gz|\.log[.-][0-9])$ ]] && continue
        readable+=("$candidate")
        if [[ -n "$site_key" ]]; then
            local path_key
            path_key=$(spr_normalise_site_name "$(basename -- "$candidate")")
            if [[ "$path_key" == *"$site_key"* || ( ${#site_label} -ge 3 && "$path_key" == *"$site_label"* ) ]]; then
                matching+=("$candidate")
            fi
        fi
    done < <(spr_discover_access_logs "$server")

    if (( ${#matching[@]} )); then
        printf '%s\n' "${matching[0]}"
    elif (( ${#readable[@]} == 1 )); then
        printf '%s\n' "${readable[0]}"
    fi
}

spr_infer_web_server_from_log() {
    local path=${1:-} fallback=${2:-unknown}
    case "$path" in
        */nginx/*) printf '%s\n' nginx ;;
        */apache2/*|*/httpd/*) printf '%s\n' apache ;;
        /usr/local/lsws/*) printf '%s\n' litespeed ;;
        "$HOME"/logs/*) printf '%s\n' openlitespeed ;;
        *) printf '%s\n' "$fallback" ;;
    esac
}

spr_discover_nginx_error_logs() {
    {
        if command -v nginx >/dev/null 2>&1; then
            nginx -T 2>&1 | awk '
                /^[[:space:]]*error_log[[:space:]]+/ {
                    value=$2
                    gsub(/[;"\047]/, "", value)
                    if (value ~ /^\// && value != "/dev/null") print value
                }
            ' || true
        fi
        if [[ -d /var/log/nginx ]]; then
            find /var/log/nginx -maxdepth 2 \( -type f -o -type l \) \
                \( -name '*error*.log' -o -name 'error.log' \) -print 2>/dev/null || true
        fi
        if [[ -d "$HOME/logs/nginx" ]]; then
            find "$HOME/logs/nginx" -maxdepth 2 \( -type f -o -type l \) \
                \( -name '*error*.log' -o -name 'error.log' \) -print 2>/dev/null || true
        fi
    } | awk 'NF && !seen[$0]++' | sort
}

spr_discover_apache_error_logs() {
    local directory
    for directory in "$HOME/logs/apache2" /var/log/apache2 /var/log/httpd; do
        [[ -d "$directory" ]] || continue
        find "$directory" -maxdepth 2 \( -type f -o -type l \) \
            \( -name '*error*.log' -o -name 'error_log' \) -print 2>/dev/null || true
    done | awk 'NF && !seen[$0]++' | sort
}

spr_discover_litespeed_error_logs() {
    {
        if [[ -d "$HOME/logs" ]]; then
            find "$HOME/logs" -maxdepth 1 \( -type f -o -type l \) -name '*error*.log' -print 2>/dev/null || true
        fi
        if [[ -d /usr/local/lsws ]]; then
            find /usr/local/lsws -maxdepth 4 \( -type f -o -type l \) \
                \( -name 'error.log' -o -name '*error*.log' -o -name 'error_log' \) -print 2>/dev/null || true
        fi
    } | awk 'NF && !seen[$0]++' | sort
}

spr_discover_error_logs() {
    local server=${1:-all}
    {
        [[ "$server" == all || "$server" == auto || "$server" == multiple || "$server" == nginx ]] && spr_discover_nginx_error_logs
        [[ "$server" == all || "$server" == auto || "$server" == multiple || "$server" == openlitespeed || "$server" == litespeed ]] && spr_discover_litespeed_error_logs
        [[ "$server" == all || "$server" == auto || "$server" == multiple || "$server" == apache ]] && spr_discover_apache_error_logs
    } | awk 'NF && !seen[$0]++'
}
