"""ffprobe / ffmpeg helpers: metadata, contact sheets, playback frames. All cached."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .state import CACHE, is_audio, is_video

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


@dataclass
class Info:
    width: int = 0
    height: int = 0
    duration: float = 0.0
    fps: float = 0.0
    size: int = 0

    def human(self) -> str:
        parts = []
        if self.width:
            parts.append(f"{self.width}x{self.height}")
        if self.duration:
            parts.append(f"{self.duration:.1f}s")
        if self.fps:
            parts.append(f"{self.fps:g}fps")
        parts.append(_human_size(self.size))
        return "  ".join(parts)


def _human_size(n: int) -> str:
    for unit in ("B", "K", "M", "G"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.1f}T"


def probe(path: Path) -> Info:
    info = Info(size=path.stat().st_size)
    if not FFPROBE:
        return info
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
             "stream=width,height,r_frame_rate:format=duration", "-of", "json", str(path)]
            if not is_audio(path) else
            [FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=10,
        ).stdout
        d = json.loads(out or "{}")
        st = (d.get("streams") or [{}])[0]
        info.width = int(st.get("width") or 0)
        info.height = int(st.get("height") or 0)
        if is_video(path):
            num, _, den = (st.get("r_frame_rate") or "0/1").partition("/")
            info.fps = round(float(num) / float(den or 1), 2) if float(den or 1) else 0.0
        if is_video(path) or is_audio(path):
            info.duration = float((d.get("format") or {}).get("duration") or 0)
    except (subprocess.SubprocessError, ValueError, json.JSONDecodeError):
        pass
    return info


def _cache_dir(path: Path) -> Path:
    st = path.stat()
    key = hashlib.sha1(f"{path}:{st.st_mtime_ns}:{st.st_size}".encode()).hexdigest()[:16]
    d = CACHE / key
    d.mkdir(parents=True, exist_ok=True)
    return d


def contact_sheet(path: Path, cols: int = 4, rows: int = 3, cell_w: int = 400) -> Path | None:
    """Evenly sampled COLSxROWS grid of a video on black. Cached."""
    if not FFMPEG:
        return None
    out = _cache_dir(path) / f"sheet_{cols}x{rows}.png"
    if out.exists():
        return out
    dur = probe(path).duration or 1.0
    n = cols * rows
    vf = f"fps={n}/{dur},scale={cell_w}:-2,tile={cols}x{rows}:padding=2:color=black"
    r = subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(path), "-vf", vf, "-frames:v", "1", str(out)],
                       capture_output=True, timeout=120)
    return out if r.returncode == 0 and out.exists() else None


def extract_frames(path: Path, fps: int = 10, max_w: int = 640, max_seconds: int = 120) -> list[Path]:
    """Playback frames as JPEGs. Cached per (file, fps, width)."""
    if not FFMPEG:
        return []
    d = _cache_dir(path) / f"frames_{fps}_{max_w}"
    if not d.exists() or not any(d.iterdir()):
        d.mkdir(parents=True, exist_ok=True)
        subprocess.run([FFMPEG, "-v", "error", "-y", "-t", str(max_seconds), "-i", str(path),
                        "-vf", f"fps={fps},scale='min({max_w},iw)':-2", "-q:v", "4", str(d / "f%05d.jpg")],
                       capture_output=True, timeout=300)
    return sorted(d.glob("f*.jpg"))


def waveform(path: Path, width: int = 1600, height: int = 400) -> Path | None:
    """Waveform on black for audio files. Cached."""
    if not FFMPEG:
        return None
    out = _cache_dir(path) / f"wave_{width}x{height}.png"
    if out.exists():
        return out
    fc = (f"[0:a]showwavespic=s={width}x{height}:colors=#7dd3fc:scale=sqrt[w];"
          f"color=c=black:s={width}x{height}[bg];[bg][w]overlay=format=auto")
    r = subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(path), "-filter_complex", fc, "-frames:v", "1", str(out)],
                       capture_output=True, timeout=120)
    return out if r.returncode == 0 and out.exists() else None
