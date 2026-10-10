"""Every test gets its own Viewfinder home and a fake $HOME, so nothing touches the real ones."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from viewfinder import state


@pytest.fixture(autouse=True)
def vf_home(tmp_path, monkeypatch):
    home = tmp_path / "vfhome"
    monkeypatch.setattr(state, "HOME", home)
    monkeypatch.setattr(state, "QUEUE", home / "queue")
    monkeypatch.setattr(state, "HISTORY", home / "history.jsonl")
    monkeypatch.setattr(state, "CACHE", home / "cache")
    monkeypatch.setattr(state, "PIDFILE", home / "pane.pid")
    monkeypatch.setenv("HOME", str(tmp_path / "user"))
    (tmp_path / "user").mkdir()
    return home


@pytest.fixture
def media(tmp_path) -> dict[str, Path]:
    """A folder of small real media files, plus a corrupt one."""
    d = tmp_path / "media"
    d.mkdir()
    out = {}
    Image.new("RGB", (320, 180), (125, 249, 255)).save(d / "a.png")
    Image.new("RGB", (200, 300), (255, 43, 214)).save(d / "b.jpg")
    Image.new("RGBA", (64, 64), (212, 255, 58, 128)).save(d / "alpha.webp")
    (d / "broken.png").write_bytes(b"not an image")
    (d / "notes.txt").write_text("hello")
    out.update({p.stem: p for p in d.iterdir()})
    if shutil.which("ffmpeg"):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=10",
                        "-t", "3", "-pix_fmt", "yuv420p", str(d / "clip.mp4")], check=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=220:duration=2",
                        str(d / "tone.wav")], check=True)
        out["clip"], out["tone"] = d / "clip.mp4", d / "tone.wav"
    out["dir"] = d
    return out


needs_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
