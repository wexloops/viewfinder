"""ffprobe / ffmpeg / Pillow helpers: metadata, decoded stills, contact sheets, waveforms. All cached."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from . import state
from .state import is_audio, is_video

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
WAVE_COLOR = "#ece8df"
MAX_DECODE = 2560  # longest side kept in memory; terminal panes never need more


class MediaError(Exception):
    """A file that exists but can't be shown. The message is written for the user."""


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
            parts.append(f"{self.width}×{self.height}")
        if self.duration:
            parts.append(_human_duration(self.duration))
        if self.fps:
            parts.append(f"{self.fps:g} fps")
        parts.append(_human_size(self.size))
        return "  ·  ".join(parts)


def _human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def _human_duration(s: float) -> str:
    if s < 60:
        return f"{s:.1f}s"
    m, sec = divmod(int(round(s)), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def probe(path: Path) -> Info:
    """Size always; dimensions, duration and fps when ffprobe is available. Never raises."""
    try:
        info = Info(size=path.stat().st_size)
    except OSError:
        return Info()
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
    except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError):
        pass
    return info


def load_image(path: Path, max_side: int = MAX_DECODE):
    """Decode an image once, upright and flattened on black, scaled to a sane working size.

    Returns (PIL image, original width, original height). Raises MediaError with a readable message."""
    from PIL import Image, ImageOps, UnidentifiedImageError

    try:
        with Image.open(path) as im:
            im.seek(0)  # first frame of animated formats
            w, h = im.size
            im = ImageOps.exif_transpose(im) or im
            if max(im.size) > max_side:
                im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            if im.mode in ("RGBA", "LA", "P", "PA") or "transparency" in im.info:
                rgba = im.convert("RGBA")
                bg = Image.new("RGB", rgba.size, (0, 0, 0))
                bg.paste(rgba, mask=rgba.getchannel("A"))
                im = bg
            elif im.mode != "RGB":
                im = im.convert("RGB")
            im.load()
            return im, w, h
    except FileNotFoundError as e:
        raise MediaError("file not found") from e
    except UnidentifiedImageError as e:
        raise MediaError("not a readable image (corrupt, or a format Pillow can't decode)") from e
    except (OSError, ValueError, SyntaxError) as e:
        raise MediaError(f"couldn't decode this image: {e}") from e


def _cache_dir(path: Path) -> Path:
    st = path.stat()
    key = hashlib.sha1(f"{path}:{st.st_mtime_ns}:{st.st_size}".encode()).hexdigest()[:16]
    d = state.CACHE / key
    d.mkdir(parents=True, exist_ok=True)
    return d


def _grab_frame(path: Path, t: float, cell_w: int):
    """One frame near time t. Input seeking keeps this fast on long files."""
    from io import BytesIO

    from PIL import Image
    r = subprocess.run(
        [FFMPEG, "-v", "error", "-ss", f"{t:.3f}", "-i", str(path), "-frames:v", "1",
         "-vf", f"scale={cell_w}:-2", "-f", "image2pipe", "-c:v", "png", "-"],
        capture_output=True, timeout=60,
    )
    if r.returncode != 0 or not r.stdout:
        return None
    try:
        return Image.open(BytesIO(r.stdout)).convert("RGB")
    except OSError:
        return None


def contact_sheet(path: Path, cols: int = 4, rows: int = 3, cell_w: int = 400, pad: int = 4) -> Path | None:
    """Evenly sampled COLSxROWS grid of a video on black. Seeks per frame in parallel, so a
    two-hour film costs about the same as a ten-second clip. Cached."""
    if not FFMPEG:
        return None
    from PIL import Image
    try:
        out = _cache_dir(path) / f"sheet_v2_{cols}x{rows}.png"
    except OSError:
        return None
    if out.exists():
        return out
    dur = probe(path).duration
    n = cols * rows
    times = [dur * (i + 0.5) / n for i in range(n)] if dur > 0 else [0.0] * n
    with ThreadPoolExecutor(max_workers=6) as ex:
        frames = list(ex.map(lambda t: _grab_frame(path, t, cell_w), times))
    good = [f for f in frames if f is not None]
    if not good:
        return None
    cw, ch = good[0].size
    sheet = Image.new("RGB", (cols * cw + (cols - 1) * pad, rows * ch + (rows - 1) * pad), (0, 0, 0))
    for i, f in enumerate(frames):
        if f is None:
            continue
        if f.size != (cw, ch):
            f = f.resize((cw, ch))
        sheet.paste(f, ((i % cols) * (cw + pad), (i // cols) * (ch + pad)))
    tmp = out.with_suffix(".tmp.png")
    sheet.save(tmp)
    tmp.replace(out)
    return out


def extract_frames(path: Path, fps: int = 10, max_w: int = 640, max_seconds: int = 120) -> list[Path]:
    """Playback frames as JPEGs, for terminals without mpv. Cached per (file, fps, width)."""
    if not FFMPEG:
        return []
    try:
        d = _cache_dir(path) / f"frames_{fps}_{max_w}"
        if not d.exists() or not any(d.iterdir()):
            d.mkdir(parents=True, exist_ok=True)
            subprocess.run([FFMPEG, "-v", "error", "-y", "-t", str(max_seconds), "-i", str(path),
                            "-vf", f"fps={fps},scale='min({max_w},iw)':-2", "-q:v", "4", str(d / "f%05d.jpg")],
                           capture_output=True, timeout=300)
    except (OSError, subprocess.SubprocessError):
        return []
    return sorted(d.glob("f*.jpg"))


def waveform(path: Path, width: int = 1600, height: int = 400) -> Path | None:
    """Waveform on black for audio files. Cached."""
    if not FFMPEG:
        return None
    try:
        out = _cache_dir(path) / f"wave_v3_{width}x{height}.png"
        if out.exists():
            return out
        fc = (f"[0:a]showwavespic=s={width}x{height}:colors={WAVE_COLOR}:scale=sqrt[w];"
              f"color=c=black:s={width}x{height}[bg];[bg][w]overlay=format=auto")
        r = subprocess.run([FFMPEG, "-v", "error", "-y", "-i", str(path), "-filter_complex", fc,
                            "-frames:v", "1", str(out)], capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    return out if r.returncode == 0 and out.exists() else None
