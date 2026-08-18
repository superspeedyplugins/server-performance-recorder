#!/usr/bin/env python3

"""Backward-compatible entry point for the original Nginx-only command."""

from __future__ import annotations

import os
import sys
from pathlib import Path


target = Path(__file__).with_name("analyse-access-log.py")
os.execv(sys.executable, [sys.executable, str(target), "--server", "nginx", *sys.argv[1:]])
