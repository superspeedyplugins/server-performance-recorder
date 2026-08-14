# Install and run Server Performance Recorder

Server Performance Recorder is public, so you don't need a GitHub account or SSH key to install it. Use the HTTPS clone URL unless the server already has a GitHub SSH key configured.

## Clone the repository

Log in to the server, move to the directory where you want to keep the recorder, then run:

```bash
git clone https://github.com/superspeedyplugins/server-performance-recorder.git
cd server-performance-recorder
```

## Why does the SSH clone URL fail for a public repository?

This command uses GitHub's SSH transport:

```bash
git clone git@github.com:superspeedyplugins/server-performance-recorder.git
```

GitHub requires an authenticated SSH key for every SSH connection, even when the repository itself is public. If the current server user doesn't have a public key registered with GitHub, the clone fails with:

```text
git@github.com: Permission denied (publickey).
fatal: Could not read from remote repository.
```

Being able to open the repository in an incognito browser confirms that the repository is public. It does not give the server an SSH identity.

The first SSH attempt may also ask you to confirm GitHub's host fingerprint. Accepting it adds GitHub to that user's `known_hosts` file. It confirms which server you are talking to, but it does not authenticate your server to GitHub.

Linux users have separate SSH configurations. For example, a key configured under `/home/dave/.ssh/` is not automatically available when you run Git as `root`, because root uses `/root/.ssh/`.

For a public repository that only needs to be cloned and pulled, HTTPS is the simplest choice:

```bash
git clone https://github.com/superspeedyplugins/server-performance-recorder.git
```

Only configure a GitHub SSH key if this server genuinely needs to push changes.

## Start the recording

From inside the cloned directory, run:

```bash
./setup
```

Setup asks what you are measuring, how many websites share the server, how long to record, which disks to measure and where to store the evidence. The default recording lasts 24 hours and samples the server every 10 seconds.

At the final question, accept the default to start recording immediately. The recorder detaches from your terminal, so you can close the SSH session after it confirms the run directory and process IDs.

The recorder does not generate website traffic, query the database, copy Nginx logs or scan files. It reads Linux kernel counters and writes one compact CSV row every 10 seconds at low CPU and I/O priority.

## Return after 24 hours

Reconnect to the server and run:

```bash
cd server-performance-recorder
git pull
./collect --analyse-nginx
```

This finds the remembered run, analyses the selected Nginx access log and its rotations for the same time window, verifies the evidence and creates a `.tar.gz` archive ready to download. The original evidence directory and Nginx logs remain intact.

If setup did not store an access-log path, the collector lists the paths it can detect. When there is more than one, it asks which one to use. You can also specify it directly:

```bash
./collect --analyse-nginx --nginx-log /var/log/nginx/example.com.access.log
```

The access-log format needs to contain `$upstream_cache_status` for the cache-ratio report. If it does not, the server recording is still valid, but HIT, MISS and BYPASS cannot be recovered from that log after the fact.

If you want to check progress before the recording finishes, use the exact run directory printed by setup:

```bash
./record status /path/to/run
```

A server reboot interrupts the recording. The run will be marked incomplete rather than silently treating partial data as a complete 24-hour result.
