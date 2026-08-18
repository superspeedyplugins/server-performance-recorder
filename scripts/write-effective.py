#!/usr/bin/env python3

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--config", required=True)
    parser.add_argument("--disk-devices", default="")
    args = parser.parse_args()
    env = os.environ
    value = {
        "schema_version": 2,
        "run_label": env.get("RUN_LABEL", "recording"),
        "observed_site": env.get("OBSERVED_SITE", ""),
        "server_site_count": env.get("SERVER_SITE_COUNT", "unknown"),
        "environment_note": env.get("ENVIRONMENT_NOTE", ""),
        "duration_seconds": int(env.get("DURATION_SECONDS", "86400")),
        "sample_interval_seconds": int(env.get("SAMPLE_INTERVAL_SECONDS", "10")),
        "disk_devices": args.disk_devices.split(),
        "clock_ticks": os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else None,
        "page_size_bytes": os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else None,
        "web_server_type": env.get("WEB_SERVER_TYPE", "auto"),
        "access_log_path": env.get("ACCESS_LOG_PATH", "") or env.get("NGINX_LOG_PATH", ""),
        "low_priority": env.get("LOW_PRIORITY", "1") == "1",
        "secrets_copied": False,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
