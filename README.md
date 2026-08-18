# Server Performance Recorder

Server Performance Recorder is a small Bash tool for recording whole-server CPU, RAM, load, disk I/O and network activity over a fixed period.

It is intended for before-and-after performance work where you need evidence of what changed without installing a permanent monitoring service. The default recording lasts 24 hours and takes one sample every 10 seconds.

## How lightweight is it?

The active sampling loop reads Linux kernel counters and appends one compact CSV row every 10 seconds. It runs at CPU nice level 19 and idle I/O priority where supported.

It does not:

- generate HTTP traffic;
- intercept requests;
- query MySQL or Redis;
- tail or copy access logs;
- scan files or cache directories;
- profile application code;
- install Netdata or another service;
- generate charts while the recording is running.

A 24-hour recording contains 8,641 samples and normally occupies only a few MiB. The recorder also measures its own CPU and resident memory usage.

## Requirements

- Linux with `/proc` and `/sys` mounted
- Bash
- Python 3
- permission to write the selected evidence directory

No package installation is required. `nice` and `ionice` are used when available.

## Quick start

```bash
git clone https://github.com/superspeedyplugins/server-performance-recorder.git
cd server-performance-recorder
./setup
```

Setup asks:

- what to call the run;
- which site or workload is being investigated;
- how many websites share the server;
- how long to record;
- which block devices contain the relevant data;
- which web server is in use and where its existing access log lives;
- where to keep the evidence.

Accept the default to launch immediately. The recorder detaches from the terminal, so you can close SSH and return after 24 hours. A successful recording is validated and then atomically packaged as a sibling `<run-id>.zip`; download that one file rather than the run directory.

Then run:

```bash
./collect --analyse-access-log
```

`./collect --analyse-access-log` finds the remembered run, reads the selected access log and its rotations for the exact recording window, then refreshes the `.zip` so it includes the added analysis. It leaves the uncompressed run directory and original logs intact. Plain `./collect` creates the ZIP on demand for recordings made with an older recorder version.

Preview the exact input without reading log contents or writing analysis/archive output:

```bash
./collect --analyse-access-log --dry-run
```

The preview reports every selected rotation, rotations skipped as outside the recorder window, compressed on-disk sizes and the input ceiling. Date-suffixed RunCloud rotations are treated as rotation boundaries, so a completed four-hour window does not scan days of unrelated history. Real analysis streams compressed logs without extracting or copying them and prints file/line progress to the terminal. Use `--max-input-bytes 2147483648` to impose a 2 GiB on-disk ceiling; a limited result is marked incomplete.

For a historical backfill which must not alter the original run directory or ZIP, write a separate derived evidence directory:

```bash
./collect --analyse-access-log --derived-output "$RUN-derived-access-analysis"
```

To list the access and error logs the recorder can detect:

```bash
./record web-logs
```

If setup did not store the right access log, specify it when collecting:

```bash
./collect --analyse-access-log --access-log /var/log/nginx/example.com.access.log
```

Nginx, Apache, OpenLiteSpeed and LiteSpeed Enterprise standard combined logs all provide request and HTTP response-status counts. The analysis also reports claimed search/shopping/generic crawlers versus traffic not identified as automation, plus heuristic WordPress/WooCommerce request classes. "Not identified as automation" does not mean human. Cache HIT, MISS and BYPASS ratios and request/upstream timing averages and p95s are reported when those fields exist. Missing fields remain unavailable rather than becoming zero. Cloudflare edge hits require a matching Cloudflare analytics export. The recorder never changes web-server configuration.

RunCloud owner-account layouts are detected without root access:

| Stack | RunCloud access-log layout |
|---|---|
| Nginx | `~/logs/nginx/<app>_access.log` |
| Apache | `~/logs/apache2/<app>_access.log` |
| OpenLiteSpeed | `~/logs/<app>_access.log` |

Common system layouts under `/var/log/nginx`, `/var/log/apache2`, `/var/log/httpd` and `/usr/local/lsws` are also detected. If both Nginx and Apache logs match a RunCloud site, setup defaults to the front-end Nginx log so cached requests are not omitted.

Existing configs using `NGINX_LOG_PATH` and existing commands using `--analyse-nginx` or `--nginx-log` remain supported as aliases.

## Checking progress

Setup prints the unique run directory. Use it with the lower-level `record` command:

```bash
./record status /path/to/run
./record wait /path/to/run
./record validate /path/to/run
./record stop /path/to/run
```

A server reboot or killed recorder produces an incomplete run rather than silently presenting partial data as a complete recording.

## Private configuration and evidence

By default, setup writes a mode-600 configuration file here:

```text
~/.config/server-performance-recorder/config.conf
```

Evidence goes here:

```text
~/.local/state/server-performance-recorder/runs/
```

Neither location is inside the Git clone. The original shell configuration is not copied into the evidence bundle.

For a non-interactive workflow, copy [config/example.conf](config/example.conf), edit it and run:

```bash
./record check client-before.conf
./record launch client-before.conf
```

If root permissions are required to read the selected devices or create the evidence directory, run `launch` through `sudo`. Run interactive setup as your normal account so its private configuration is stored under the intended home directory.

## Public and private repository material

The public repository contains code and operating documentation. `.private` and `.docs` are gitignored and may be private directories or symlinks to directories outside this clone. Keep client material in `.private` and private plans in `.docs`. Do not commit the symlinks themselves.

Each run contains a snapshot of the exact recorder runtime it started with. Pulling or deleting the public clone cannot alter an active run. When launched from a Git checkout, `run.json` records the source commit and whether the runtime files had uncommitted changes.

## Understanding the result

The recorded CPU, RAM, load, disk and network measurements cover the whole server. They do not pretend to isolate one website. Record the number of hosted sites during setup so reports can say, for example, that the changed site was one of ten sites sharing the measured server.

Existing access logs can later provide site-specific request and HTTP response counts for the exact epoch window recorded in `run.json`. If the log format contains cache status, HIT, MISS, BYPASS, EXPIRED, STALE, UPDATING and REVALIDATED counts are included too. The recorder stores the intended log path as provenance but does not read that log while recording. Analysis happens only when `collect --analyse-access-log` is run, at low CPU and I/O priority, and raw logs are not copied into the evidence bundle.

See the [data contract](docs/data-contract.md) for every raw field and unit.

For the full server installation walkthrough, including why GitHub's SSH clone URL requires a key even for a public repository, see [Install and run Server Performance Recorder](.kb/install-and-run.md).

For command-only installation, progress, collection and download instructions, see the [Quick Start Guide](.kb/quick-start-guide.md).

For log discovery, rotations, request counts, cache-ratio definitions and command options, see [Analyse web-server access logs](.kb/analyse-nginx-cache.md).

## Tests

The test suite includes:

- a real Linux collection, validation and archive lifecycle test;
- an interactive pseudo-terminal setup test;
- Nginx, Apache, OpenLiteSpeed and LiteSpeed discovery/combined-log fixtures;
- shell and Python syntax checks.

Run the Linux tests from a suitable Linux host or container:

```bash
./tests/setup-smoke.sh
./tests/nginx-discovery-smoke.sh
./tests/access-log-smoke.sh
./tests/smoke.sh
```
