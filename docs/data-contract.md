# Server Performance Recorder data contract

## Scope

`telemetry/system.csv` records whole-server Linux kernel counters. It does not claim to isolate one site. `OBSERVED_SITE`, `SERVER_SITE_COUNT` and `ENVIRONMENT_NOTE` preserve the qualification needed later, for example: the optimised site was one of ten sites sharing the observed server.

Existing Nginx, Apache, OpenLiteSpeed or LiteSpeed logs remain the source for site-specific request counts. `run.json` supplies the exact UTC epoch window. `config/effective.json` records the detected server type and intended access-log path without reading or copying that log during the live recording.

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
- `analysis/access-log-summary.json`: optional machine-readable request, HTTP response, claimed-automation, heuristic request-class, cache and available timing aggregates
- `analysis/access-log-report.md`: optional human-readable access-log report
- `validation.txt`: coverage and safety-floor validation
- `SHA256SUMS`: evidence integrity inventory

The original shell config is never copied into the evidence bundle.

After successful validation, the recorder creates `<run-directory>.zip` beside the evidence directory. It writes to a private temporary file and renames it only after the ZIP is complete, so an incomplete archive never appears under the download filename. The ZIP contains the run directory as its top-level folder. Running `collect --analyse-access-log` refreshes it after adding access-log analysis.

## Later access-log analysis

The analyser accepts standard combined logs from Nginx, Apache, OpenLiteSpeed and LiteSpeed, plus JSON logs with conventional timestamp, response-status and method keys. If the server uses a shared access log, its format must also include the host for `--host` filtering. Preserve every rotated and gzipped log that overlaps the `start_epoch` to `end_epoch` window.

`collect --analyse-access-log` streams the selected log and normal rotations, filters rows to the exact recorder window, then counts requests, methods, HTTP response statuses, claimed-automation classes and heuristic request classes. Raw request targets and user agents are discarded immediately after classification and never enter the evidence bundle. RunCloud date suffixes are treated as rotation boundaries: rotations that can contain the window are included, while older rotations and a current log known to begin after the window are skipped. Unfamiliar and numbered rotation schemes remain conservatively included.

`collect --analyse-access-log --dry-run` reads file metadata only. It lists the ordered files, on-disk sizes, window-selection decisions and input ceiling without reading log contents or creating analysis/archive output. The real analyser reports file and line progress to standard error. `--max-input-bytes` applies to on-disk bytes after window selection, so a compressed rotation counts at its compressed size; reaching the ceiling marks the result incomplete.

`collect --analyse-access-log --derived-output DIR` writes `access-log-summary.json` and `access-log-report.md` to a separate directory. It does not update the original run, checksums or ZIP. Use it for retained-log backfills where the downloaded evidence must remain immutable.

When the log format includes a recognised cache-status field, the analyser keeps HIT, MISS, BYPASS, EXPIRED, STALE, UPDATING and REVALIDATED separate. The cache-ratio denominator is requests in the window containing exactly one recognised cache status. A standard combined log without cache status still produces valid request and HTTP response counts; missing cache data is never treated as a miss.

Automation is inferred from bounded user-agent token checks and is reported as claimed search crawler, claimed shopping crawler, claimed generic crawler/scraper, or not identified as automation. The denominator is all requests in the exact recorder window. "Not identified as automation" never means human. Cache outcomes are also cross-tabulated by automation class when the log supplies them.

`request_groups` cross-tabulates automation class, method, HTTP status, request class and cache status. Each group includes its request count plus request-time and upstream-time aggregates when available. This is the denominator used to isolate, for example, claimed crawler deep-filter work without retaining URLs or user agents.

`archive_protection_proxy` is explicitly heuristic: it counts claimed-automation GET/HEAD requests classified as deep filters, reports their share of claimed automation, projects that count to 24 hours and carries any log-provided timings. It is not presented as SSF's policy decision. Performance Analysis uses SSF's installed pure archive-gate function for exact future collection classifications.

Request classes are heuristic path aggregates: static, product single, product archive, shop, deep filter, admin AJAX, REST, cart, checkout, account, other private method, other or unknown. Site-specific slugs may remain `other`; the WordPress collector supplies authoritative page classes for requests which reach WordPress.

When `request_time`/`rt` or `upstream_response_time`/`urt` fields exist, their sample count, sum, average and p95 milliseconds are reported. Missing timing or cache fields have quality `unavailable` and null measurements, never zero. The derived report also provides a cache-served ratio combining HIT, STALE, UPDATING and REVALIDATED. Cloudflare edge hits remain unavailable from origin logs and require a matching Cloudflare analytics export. Raw logs remain outside the evidence bundle.
