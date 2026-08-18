# Quick Start Guide

Run these commands from the `server-performance-recorder` directory.

## Install

```bash
git clone https://github.com/superspeedyplugins/server-performance-recorder.git
cd server-performance-recorder
```

## Update

```bash
git pull --ff-only
```

## Start a recording

```bash
./setup
```

For a before recording, accept the `before-change` run-label default. For an after recording, enter `after-change`.

## Use the most recent run in later commands

```bash
RUN=$(<"${XDG_STATE_HOME:-$HOME/.local/state}/server-performance-recorder/runs/.last-run")
```

## Check progress once

```bash
./record status "$RUN"
```

## Watch progress every 10 seconds

```bash
watch -n 10 './record status "$(cat "${XDG_STATE_HOME:-$HOME/.local/state}/server-performance-recorder/runs/.last-run")"'
```

Press `Ctrl+C` to stop watching.

## Wait until the recording finishes

```bash
./record wait "$RUN"
```

The completed status is `validated`.

## Stop a recording early

```bash
./record stop "$RUN"
```

## List detected web-server logs

```bash
./record web-logs
```

## Validate a completed recording

```bash
./record validate "$RUN"
```

## Add access-log analysis and build the ZIP

```bash
./collect --analyse-access-log
```

## Preview access-log work without reading logs or writing files

```bash
./collect --analyse-access-log --dry-run
```

Check the `READ` rows. Date-suffixed RunCloud rotations outside the recorder window are automatically shown as `SKIP (outside window)`.

## Limit access-log input to 2 GiB

```bash
./collect --analyse-access-log --max-input-bytes 2147483648
```

## Use a specific access log

```bash
./collect --analyse-access-log --access-log /path/to/site_access.log
```

## Backfill retained logs without changing the original run or ZIP

```bash
./collect --analyse-access-log --derived-output "$RUN-derived-access-analysis"
```

Download the derived directory separately. The original run directory and ZIP are unchanged.

## Filter a shared access log to one hostname

```bash
./collect --analyse-access-log --access-log /path/to/access.log --host example.com
```

## Build the ZIP without access-log analysis

```bash
./collect
```

## Show the ZIP to download

```bash
ls -lh "$RUN.zip"
```

Download `$RUN.zip`, not the run directory.

## Inspect a failed recording

```bash
./record status "$RUN"
tail -n 100 "$RUN/logs/runner.log"
```
