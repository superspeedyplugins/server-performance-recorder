# Analyse web-server access logs

The server recording measures whole-server CPU, RAM, load, disk I/O and network activity. Nginx, Apache, OpenLiteSpeed and LiteSpeed access logs provide the second half of the before-and-after evidence: request volume, HTTP responses and, when logged, cache outcomes.

## Find the logs

Run:

```bash
./record web-logs
```

This detects the active web-server family and lists access and error log paths. It checks expanded Nginx configuration plus common Nginx, Apache and LiteSpeed locations. For RunCloud application owners it checks `~/logs/nginx`, `~/logs/apache2` and the OpenLiteSpeed files directly under `~/logs`.

The cache analysis uses an access log. Error logs remain useful for diagnosing faults, but they are not used to calculate cache ratios.

The recorder only lists and reads existing logs. It does not edit or reload the web server.

## Standard request analysis

Standard combined logs have the timestamp, request method and HTTP response status needed for request analysis. The analyser also accepts JSON logs with conventional field names.

Cache analysis needs cache outcomes to be included in the configured log format. Nginx commonly uses `$upstream_cache_status`; LiteSpeed and Apache need the equivalent cache value added to their format. Without it, request and response counts remain valid, while cache ratios are shown as unavailable.

## Collect and analyse

Preview the exact files and on-disk input size without reading log contents or writing analysis files:

```bash
./collect --analyse-access-log --dry-run
```

After the recording reaches a terminal state, run:

```bash
./collect --analyse-access-log
```

The real analysis prints the current file, lines scanned, requests found in the recorder window and lines per second. Limit the on-disk input when a server retains more history than you want to scan:

```bash
./collect --analyse-access-log --max-input-bytes 2147483648
```

The example ceiling is 2 GiB. Compressed logs count at their compressed on-disk size. Reaching the ceiling produces an explicitly incomplete analysis rather than silently presenting it as complete.

To override the stored path:

```bash
./collect --analyse-access-log --access-log /var/log/nginx/example.com.access.log
```

If a shared access log contains several virtual hosts, add a host filter:

```bash
./collect --analyse-access-log \
  --access-log /var/log/nginx/access.log \
  --host shop.example.com
```

The analyser reads the named log plus dot-suffixed rotations such as `.1` and `.2.gz` and RunCloud date-suffixed rotations such as `-20260817.gz`. It streams them one line at a time at CPU nice level 19 and idle I/O priority where available. It does not unpack whole files into memory, copy the raw logs or run during the observation.

Analysis is limited to the exact start and end epochs stored in `run.json`. Results are written to:

```text
analysis/access-log-summary.json
analysis/access-log-report.md
```

Both files are included in the evidence checksums and archive.

## Reading the result

The report first gives requests, methods and exact HTTP response-status counts. When cache outcomes are present, it keeps these seven values separate:

- `HIT`
- `MISS`
- `BYPASS`
- `EXPIRED`
- `STALE`
- `UPDATING`
- `REVALIDATED`

HIT ratio is `HIT / all requests with one recognised cache status`.

MISS and BYPASS use the same denominator. The report also shows a cache-served ratio combining HIT, STALE, UPDATING and REVALIDATED. It does not silently treat unclassified access-log lines as cache misses.

If the log is site-specific, the request counts and ratios are site-specific. If the log is shared and no host filter is used, they cover everything in that log. Keep that scope qualification with any client-facing before-and-after result.
