#!/usr/bin/env python3
"""
run_log.py — per-run diagnostic log shared by the CLI and the Windows app.

Writes a plain-text log of each embed run (no media, just filenames/sizes/outcomes)
to a per-platform logs folder, keeps the last N, and exposes the latest for the
"Report a Problem" flow. The Mac Swift app has its own equivalent (RunLog.swift).
"""
from __future__ import annotations
import os, sys, platform, datetime

APP = "ShotMarkEmbedder"


def log_dir() -> str:
    override = os.environ.get("SHOTMARK_LOG_DIR")
    if override:
        return override
    if sys.platform == "darwin":
        return os.path.expanduser(f"~/Library/Logs/{APP}")
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, APP, "logs")
    return os.path.expanduser(f"~/.local/state/{APP}/logs")


def _human(n) -> str:
    if n is None or n < 0:
        return "unknown"
    f, units = float(n), ["B", "KB", "MB", "GB", "TB"]
    i = 0
    while f >= 1024 and i < len(units) - 1:
        f /= 1024; i += 1
    return f"{f:.1f} {units[i]}"


class RunLog:
    def __init__(self, app_version: str = "0.2.0"):
        self.started = datetime.datetime.now()
        self.app_version = app_version
        self.lines: list[str] = []

    def w(self, line: str = "") -> None:
        self.lines.append(line)

    def header(self, output: str, dest_volume=None, dest_free=None) -> None:
        self.w("Shot Mark Embedder — run log")
        self.w(f"when: {self.started.isoformat(timespec='seconds')}")
        self.w(f"app: {self.app_version}  os: {platform.platform()}  python: {platform.python_version()}")
        self.w(f"output: {output}")
        if dest_volume is not None or dest_free is not None:
            self.w(f"dest volume: {dest_volume or '?'}  free: {_human(dest_free)}")

    def inputs(self, files) -> None:
        self.w(f"inputs ({len(files)}):")
        for f in files:
            try:
                sz = os.path.getsize(f)
            except OSError:
                sz = -1
            self.w(f"  {os.path.basename(f)}  {_human(sz)}")
        self.w("results:")

    def result(self, msg: str) -> None:
        self.w("  " + msg)

    def summary(self, text: str) -> None:
        elapsed = (datetime.datetime.now() - self.started).total_seconds()
        self.w(f"summary: {text}  (elapsed {elapsed:.1f}s)")

    def write(self, keep: int = 30) -> str:
        d = log_dir()
        os.makedirs(d, exist_ok=True)
        base = self.started.strftime("run-%Y-%m-%d_%H-%M-%S")
        path = os.path.join(d, base + ".log")
        n = 2
        while os.path.exists(path):          # don't clobber a same-second run
            path = os.path.join(d, f"{base}-{n}.log"); n += 1
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(self.lines) + "\n")
        _prune(d, keep)
        return path


def _logs(d: str):
    try:
        return [os.path.join(d, f) for f in os.listdir(d) if f.startswith("run-") and f.endswith(".log")]
    except OSError:
        return []


def _prune(d: str, keep: int) -> None:
    for old in sorted(_logs(d), key=os.path.getmtime)[:-keep]:
        try:
            os.unlink(old)
        except OSError:
            pass


def latest_log() -> str | None:
    files = _logs(log_dir())
    return max(files, key=os.path.getmtime) if files else None
