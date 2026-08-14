# Analyse Nginx cache outcomes

The server recording measures whole-server CPU, RAM, load, disk I/O and network activity. Nginx access logs provide the second half of the before-and-after evidence: request volume and the cache outcome for each logged request.

## Find the logs

Run:

```bash
./record nginx-logs
```

This lists access and error log paths found in the expanded Nginx configuration and under `/var/log/nginx`. Setup runs the same access-log discovery before asking which log belongs to the site being measured.

The cache analysis uses an access log. Error logs remain useful for diagnosing faults, but they are not used to calculate cache ratios.

The recorder only lists and reads existing logs. It does not edit or reload Nginx.

## Required log field

The selected access-log format must contain `$upstream_cache_status`. A standard combined log normally has timestamps, URLs, status codes, referrers and user agents, but no cache outcome.

Without `$upstream_cache_status`, the recorder cannot determine later whether a request was a HIT, MISS or BYPASS. In that case it preserves the server evidence and writes a clear insufficient-data warning in the Nginx analysis report.

## Collect and analyse

After the recording reaches a terminal state, run:

```bash
./collect --analyse-nginx
```

To override the stored path:

```bash
./collect --analyse-nginx --nginx-log /var/log/nginx/example.com.access.log
```

If a shared access log contains several virtual hosts, add a host filter:

```bash
./collect --analyse-nginx \
  --nginx-log /var/log/nginx/access.log \
  --host shop.example.com
```

The analyser reads the named log plus normal rotations such as `.1` and `.2.gz`. It streams them one line at a time at CPU nice level 19 and idle I/O priority where available. It does not unpack whole files into memory, copy the raw logs or run during the 24-hour observation.

Analysis is limited to the exact start and end epochs stored in `run.json`. Results are written to:

```text
analysis/nginx-summary.json
analysis/nginx-report.md
```

Both files are included in the evidence checksums and archive.

## Reading the ratios

The report keeps the seven Nginx cache outcomes separate:

- `HIT`
- `MISS`
- `BYPASS`
- `EXPIRED`
- `STALE`
- `UPDATING`
- `REVALIDATED`

HIT ratio is `HIT / all requests with one recognised cache status`.

MISS and BYPASS use the same denominator. The report also shows a cache-served ratio combining HIT, STALE, UPDATING and REVALIDATED. It does not silently treat unclassified access-log lines as cache misses.

If the log is site-specific, the ratios are site-specific. If the log is shared and no host filter is used, the ratios cover everything in that log. Keep that scope qualification with any client-facing before-and-after result.
