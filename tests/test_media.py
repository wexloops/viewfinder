import pytest
from PIL import Image

from viewfinder import media as m
from viewfinder.media import MediaError

from .conftest import needs_ffmpeg


def test_load_image_flattens_alpha_on_black(media):
    img, w, h = m.load_image(media["alpha"])
    assert img.mode == "RGB" and (w, h) == (64, 64)
    r, g, b = img.getpixel((10, 10))
    assert r < 212 and g < 255  # blended toward black, not white


def test_load_image_downscales_big_images(tmp_path):
    p = tmp_path / "big.png"
    Image.new("RGB", (6000, 3000)).save(p)
    img, w, h = m.load_image(p, max_side=1000)
    assert (w, h) == (6000, 3000) and max(img.size) == 1000


def test_load_image_errors_are_readable(media, tmp_path):
    with pytest.raises(MediaError, match="not a readable image"):
        m.load_image(media["broken"])
    with pytest.raises(MediaError, match="not found"):
        m.load_image(tmp_path / "gone.png")


def test_probe_never_raises_on_missing(tmp_path):
    assert m.probe(tmp_path / "gone.mp4").size == 0


def test_info_human():
    assert m.Info(1920, 1080, 75.0, 30, 3 * 1024 * 1024).human() == "1920×1080  ·  1:15  ·  30 fps  ·  3.0 MB"


@needs_ffmpeg
def test_contact_sheet_and_cache(media):
    sheet = m.contact_sheet(media["clip"])
    assert sheet and sheet.exists()
    w, h = Image.open(sheet).size
    assert w > h  # 4x3 grid of 16:9 frames
    assert m.contact_sheet(media["clip"]) == sheet  # cached


@needs_ffmpeg
def test_waveform(media):
    wave = m.waveform(media["tone"])
    assert wave and Image.open(wave).size == (1600, 400)


def test_no_ffmpeg_degrades(media, monkeypatch):
    monkeypatch.setattr(m, "FFMPEG", None)
    assert m.contact_sheet(media["a"]) is None
    assert m.waveform(media["a"]) is None
    assert m.extract_frames(media["a"]) == []
