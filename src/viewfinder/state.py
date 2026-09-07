"""Shared on-disk state: the queue file the pane watches, history, cache."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

HOME = Path(os.environ.get("VIEWFINDER_HOME", str(Path.home() / ".viewfinder")))
QUEUE = HOME / "queue"
HISTORY = HOME / "history.jsonl"
CACHE = HOME / "cache"
PIDFILE = HOME / "pane.pid"

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mxf"}
MEDIA_EXT = IMAGE_EXT | VIDEO_EXT

_WIN_PATH = re.compile(r"^[A-Za-z]:[\\/]")


def ensure_dirs() -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    HISTORY.touch(exist_ok=True)


def is_image(p: Path) -> bool:
    return p.suffix.lower() in IMAGE_EXT


def is_video(p: Path) -> bool:
    return p.suffix.lower() in VIDEO_EXT


def is_media(p: Path) -> bool:
    return p.suffix.lower() in MEDIA_EXT


def normalize(raw: str) -> Path:
    """Resolve a user/agent supplied path. Windows paths are converted under WSL."""
    s = raw.strip()
    if _WIN_PATH.match(s) and shutil.which("wslpath"):
        try:
            s = subprocess.run(["wslpath", "-u", s], capture_output=True, text=True, check=True).stdout.strip()
        except subprocess.CalledProcessError:
            pass
    return Path(s).expanduser().resolve()


@dataclass
class Entry:
    path: Path
    ts: float
    project: str
    cwd: str
    session: str
    agent: str

    @property
    def session_tag(self) -> str:
        return self.session[:6] if self.session else ""


def push(paths: list[str], *, cwd: str | None = None, session: str = "", agent: str = "cli") -> list[Path]:
    """Queue files for the pane. Last one wins on screen; all land in history, tagged."""
    ensure_dirs()
    cwd = cwd or os.getcwd()
    project = Path(cwd).name or cwd
    accepted: list[Path] = []
    for raw in paths:
        p = normalize(raw)
        if not p.exists() or not is_media(p):
            continue
        accepted.append(p)
        rec = {"path": str(p), "ts": time.time(), "project": project, "cwd": cwd, "session": session, "agent": agent}
        with HISTORY.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
        tmp = QUEUE.with_suffix(".tmp")
        tmp.write_text(f"{p}\n")
        os.replace(tmp, QUEUE)
    return accepted


def signature() -> tuple[int, int, int] | None:
    try:
        st = QUEUE.stat()
    except FileNotFoundError:
        return None
    return (st.st_ino, st.st_mtime_ns, st.st_size)


def read_queue() -> Path | None:
    try:
        s = QUEUE.read_text().strip()
    except FileNotFoundError:
        return None
    return Path(s) if s else None


def history_entries() -> list[Entry]:
    ensure_dirs()
    out: list[Entry] = []
    for line in HISTORY.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
            out.append(Entry(Path(d["path"]), float(d.get("ts", 0)), d.get("project", ""), d.get("cwd", ""),
                             d.get("session", ""), d.get("agent", "")))
        except (ValueError, KeyError):
            continue
    return out


def history() -> list[Path]:
    return [e.path for e in history_entries()]
