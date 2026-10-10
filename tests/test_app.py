"""Drive the real pane headlessly with Textual's pilot."""
from pathlib import Path

import pytest

from viewfinder import state
from viewfinder.app import Viewfinder, open_command

from .conftest import needs_ffmpeg

def text(app, sel: str) -> str:
    return str(app.query_one(sel).render())


async def wait_for(pilot, cond, timeout=8.0):
    waited = 0.0
    while not cond() and waited < timeout:
        await pilot.pause(0.1)
        waited += 0.1
    assert cond()


async def test_empty_state_then_queue(media, monkeypatch):
    monkeypatch.chdir(media["dir"])
    app = Viewfinder(protocol="halfcell")
    async with app.run_test(size=(100, 36)) as pilot:
        await pilot.pause(0.2)
        assert "vf setup claude" in text(app, "#placeholder")
        state.push([str(media["a"])])
        await wait_for(pilot, lambda: app.current == media["a"] and not app._loading)
        assert app.query_one("#placeholder").has_class("hidden")
        assert "320×180" in text(app, "#title")


async def test_corrupt_and_missing_files_do_not_crash(media, tmp_path):
    app = Viewfinder(protocol="halfcell")
    async with app.run_test(size=(100, 36)) as pilot:
        state.push([str(media["broken"])])
        await wait_for(pilot, lambda: app.error != "")
        assert "can't show" in text(app, "#placeholder")
        gone = tmp_path / "gone.png"
        gone.write_bytes(media["a"].read_bytes())
        state.push([str(gone)])
        await wait_for(pilot, lambda: app.current == gone and not app.error and not app._loading)
        gone.unlink()
        await pilot.press("r")
        await wait_for(pilot, lambda: "moved or deleted" in app.error)
        assert app.is_running


async def test_history_navigation_skips_missing_and_compare(media, tmp_path):
    gone = tmp_path / "gone.png"
    gone.write_bytes(media["a"].read_bytes())
    state.push([str(media["a"]), str(gone), str(media["b"])])
    gone.unlink()
    app = Viewfinder(protocol="halfcell")
    async with app.run_test(size=(100, 36)) as pilot:
        await wait_for(pilot, lambda: app.current == media["b"])
        await pilot.press("p")
        await wait_for(pilot, lambda: app.current == media["a"])
        await pilot.press("c")
        await wait_for(pilot, lambda: app.current == media["b"])
        await pilot.press("c")
        await wait_for(pilot, lambda: app.current == media["a"])


async def test_filter_and_history_panel(media):
    state.push([str(media["a"])], cwd="/w/projA")
    state.push([str(media["b"])], cwd="/w/projB")
    app = Viewfinder(protocol="halfcell")
    async with app.run_test(size=(100, 36)) as pilot:
        await wait_for(pilot, lambda: app.current == media["b"])
        await pilot.press("h")
        await pilot.pause(0.2)
        assert "history" in str(app.query_one("#tree").border_title)
        await pilot.press("h", "m", "m")  # tree, filter images -> video
        await pilot.pause(0.2)
        assert app.filter == "video"
        names = [getattr(i, "path", None) for i in app.query_one("#tree").children]
        assert media["a"] not in names


async def test_help_and_fullscreen(media):
    state.push([str(media["a"])])
    app = Viewfinder(protocol="halfcell")
    async with app.run_test(size=(100, 36)) as pilot:
        await wait_for(pilot, lambda: app.current == media["a"])
        await pilot.press("question_mark")
        assert "compare" in text(app, "#placeholder")
        await pilot.press("escape")
        await wait_for(pilot, lambda: app.query_one("#placeholder").has_class("hidden"))
        await pilot.press("f")
        assert app.query_one("#tree").has_class("hidden")
        await pilot.press("f")
        assert not app.query_one("#tree").has_class("hidden")


@needs_ffmpeg
async def test_video_and_audio_render(media):
    app = Viewfinder(protocol="halfcell")
    async with app.run_test(size=(100, 36)) as pilot:
        state.push([str(media["clip"])])
        await wait_for(pilot, lambda: app.current == media["clip"] and not app._loading)
        assert "VIDEO" in text(app, "#title") and not app.error
        state.push([str(media["tone"])])
        await wait_for(pilot, lambda: app.current == media["tone"] and not app._loading)
        assert "AUDIO" in text(app, "#title") and not app.error


def test_open_command_variants():
    p = Path("/home/u/renders/shot 01.png")
    assert open_command(p, reveal=True, platform="darwin", wsl=False) == ["open", "-R", str(p)]
    assert open_command(p, reveal=True, platform="linux", wsl=False) == ["xdg-open", "/home/u/renders"]
    assert open_command(p, platform="linux", wsl=False) == ["xdg-open", str(p)]
    win = lambda s: "\\\\wsl.localhost\\Ubuntu" + s.replace("/", "\\")
    assert open_command(p, reveal=True, wsl=True, wslpath=win) == ["explorer.exe", "/select," + win(str(p))]
    assert open_command(p, reveal=True, wsl=True, wslpath=lambda s: "")[:3] == ["cmd.exe", "/c", "start"]
