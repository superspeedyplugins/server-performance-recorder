# Server Performance Recorder data contract

## Scope

`telemetry/system.csv` records whole-server Linux kernel counters. It does not claim to isolate one site. `OBSERVED_SITE`, `SERVER_SITE_COUNT` and `ENVIRONMENT_NOTE` preserve the qualification needed later, for example: the optimised site was one of ten sites sharing the observed server.

Existing Nginx logs remain the source for site-specific request and cache counts. `run.json` supplies the exact UTC epoch window. `config/effective.json` records the intended Nginx log path without reading or copying that log during the live recording.

## Sampling cost

The sampler opens these read-only kernel interfaces once every configured interval:

- `/proc/stat`
- `/proc/meminfo`
- `/proc/loadavg`
- `/proc/net/dev`
- `/sys/class/block/<device>/stat`
- its own `/proc/<pid>/stat`

It appends one CSV row and sleeps. The default interval is 10 seconds and the program rejects shorter intervals. It performs no process enumeration, stack sampling, recursive directory scan, database query, HTTP request, log tail, compression or `fsync` in the sampling loop. On Linux, it runs at CPU nice level 19 and idle I/O priority by default.

## `telemetry/system.csv`

The CSV is deliberately presentation-neutral. Cumulative counters allow later calculations to be changed or audited without recollecting the 24-hour window.

| Column | Meaning | Unit/source |
|---|---|---|
| `epoch` | Sample time | Unix seconds, UTC |
| `cpu_user` through `cpu_steal` | Whole-server cumulative CPU states | Linux USER_HZ ticks from `/proc/stat` |
| `mem_total_kb` | Physical RAM | KiB from `/proc/meminfo` |
| `mem_available_kb` | RAM available without swapping | KiB from `/proc/meminfo` |
| `swap_total_kb`, `swap_free_kb` | Swap capacity and availability | KiB from `/proc/meminfo` |
| `load1`, `load5`, `load15` | Whole-server runnable/uninterruptible load averages | `/proc/loadavg` |
| `disk_read_sectors`, `disk_write_sectors` | Cumulative I/O summed across the selected block devices | 512-byte sectors from sysfs |
| `disk_io_ms` | Cumulative I/O-busy time summed across selected devices | milliseconds from sysfs |
| `network_rx_bytes`, `network_tx_bytes` | Cumulative traffic across non-loopback interfaces | bytes from `/proc/net/dev` |
| `recorder_*_ticks` | Sampler process CPU time | Linux USER_HZ ticks |
| `recorder_rss_kb` | Sampler resident set | KiB |

From adjacent rows we can later calculate CPU busy and I/O-wait percentages, read/write MiB/s, disk busy percentage, network throughput and the recorder's own CPU cost. RAM used is derived as `MemTotal - MemAvailable`, not the misleading `free` column alone. When multiple block devices are selected, throughput counters are summed and disk-busy percentage is normalised by the number of selected devices, so 100% means their combined sampled I/O capacity was fully occupied. Virtual network interfaces may represent the same traffic at more than one layer, so network totals are diagnostic rather than billing-grade.

## Evidence bundle

Every launch creates a new directory and refuses to overwrite an old one. The important files are:

- `run.json`: state, exact start/end epochs, PIDs and errors
- `config/effective.json`: non-secret effective settings and scope qualification
- `telemetry/system.csv`: raw timestamped counters
- `inventory/start.txt` and `inventory/end.txt`: host/filesystem/software context
- `summary.json`: small integrity and descriptive-statistics summary, not a presentation format
- `report.md`: human-readable spot check
- `analysis/nginx-summary.json`: optional machine-readable cache counts and ratios
- `analysis/nginx-report.md`: optional human-readable cache report
- `validation.txt`: coverage and safety-floor validation
- `SHA256SUMS`: evidence integrity inventory

The original shell config is never copied into the evidence bundle.

## Later Nginx analysis

For cache analysis, the access-log format needs a parseable timestamp and `$upstream_cache_status`. If the server uses a shared access log, its format must also include `$host` for `--host` filtering. Preserve every rotated and gzipped log that overlaps the `start_epoch` to `end_epoch` window.

`collect --analyse-nginx` streams the selected log and normal rotations, filters rows to the exact recorder window and keeps HIT, MISS, BYPASS, EXPIRED, STALE, UPDATING and REVALIDATED separate. Its denominator is requests in the window containing exactly one recognised cache status. Lines without a recognised status are reported as unclassified and are not silently treated as misses.

The derived report also provides a cache-served ratio combining HIT, STALE, UPDATING and REVALIDATED. Raw logs remain outside the evidence bundle. The recorder does not prescribe the later chart or client-reporting system.
