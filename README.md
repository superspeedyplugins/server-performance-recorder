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
- where the existing Nginx access log lives, if relevant;
- where to keep the evidence.

Accept the default to launch immediately. The recorder detaches from the terminal, so you can close SSH and return after 24 hours.

Then run:

```bash
./collect --analyse-nginx
```

`./collect --analyse-nginx` finds the remembered run, reads the selected access log and its rotations for the exact recording window, then creates a `.tar.gz` evidence archive ready to download. It leaves the uncompressed run directory and the original Nginx logs intact. Use plain `./collect` if you only want the server recording.

To list the access and error logs the recorder can detect:

```bash
./record nginx-logs
```

If setup did not store the right access log, specify it when collecting:

```bash
./collect --analyse-nginx --nginx-log /var/log/nginx/example.com.access.log
```

The access-log format must include `$upstream_cache_status`. Standard combined logs usually do not, so finding a log file does not guarantee that cache HIT, MISS and BYPASS ratios can be calculated. The recorder reports this clearly and never changes Nginx configuration.

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

Existing access logs can later provide site-specific request and cache HIT, MISS, BYPASS, EXPIRED, STALE, UPDATING and REVALIDATED counts for the exact epoch window recorded in `run.json`. The recorder stores the intended Nginx log path as provenance but does not read that log while recording. Analysis happens only when `collect --analyse-nginx` is run, at low CPU and I/O priority, and the raw logs are not copied into the evidence bundle.

See the [data contract](docs/data-contract.md) for every raw field and unit.

For the full server installation walkthrough, including why GitHub's SSH clone URL requires a key even for a public repository, see [Install and run Server Performance Recorder](.kb/install-and-run.md).

For log discovery, rotations, cache-ratio definitions and command options, see [Analyse Nginx cache outcomes](.kb/analyse-nginx-cache.md).

## Tests

The test suite includes:

- a real Linux collection, validation and archive lifecycle test;
- an interactive pseudo-terminal setup test;
- shell and Python syntax checks.

Run the Linux tests from a suitable Linux host or container:

```bash
./tests/setup-smoke.sh
./tests/nginx-discovery-smoke.sh
./tests/smoke.sh
```
