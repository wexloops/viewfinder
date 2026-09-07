"""The Viewfinder pane: viewer on top, neighborhood tree below, one-line footer."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
import time

from textual.widgets import ListItem, ListView, Static

from . import __version__, state
from .media import Info, contact_sheet, extract_frames, probe, waveform

ACCENT = "#7dd3fc"
_UNSET = object()
BRAND = "VIEWFINDER v" + ".".join(__version__.split(".")[:2]) + " by STRANGELOOP"


def _image_widget_class(protocol: str):
    from textual_image import widget as w
    return {
        "auto": w.Image, "sixel": w.SixelImage, "tgp": w.TGPImage,
        "halfcell": w.HalfcellImage, "unicode": w.UnicodeImage,
    }[protocol]


def _mark(p: Path) -> str:
    return "▶ " if state.is_video(p) else ("♪ " if state.is_audio(p) else "  ")


class Entry(ListItem):
    def __init__(self, path: Path, label: str) -> None:
        super().__init__(Static(label))
        self.path = path


class GroupRow(ListItem):
    def __init__(self, project: str, label: str) -> None:
        super().__init__(Static(label))
        self.project = project


class Viewfinder(App):
    TITLE = BRAND
    CSS = f"""
    Screen {{ background: black; color: #d4d4d4; }}
    #header {{ height: 2; padding: 0 1; color: #d4d4d4; }}
    #header .name {{ color: {ACCENT}; text-style: bold; }}
    #stage {{ height: 1fr; width: 100%; align: center middle; background: black; }}
    #viewer {{ width: auto; height: auto; }}
    #tree {{ height: 12; background: black; border-top: solid #333333; scrollbar-size: 1 1; }}
    #tree ListItem {{ background: black; color: #a0a0a0; padding: 0 1; }}
    #tree ListItem.--highlight {{ background: #1e293b; color: {ACCENT}; }}
    #footer {{ height: 1; padding: 0 1; background: black; }}
    #keys {{ width: 1fr; color: #6b7280; overflow: hidden; }}
    #brand {{ width: auto; color: #6b7280; text-align: right; }}
    .hidden {{ display: none; }}
    """
    BINDINGS = [
        Binding("space", "play", "play", show=False),
        Binding("n", "next", "next", show=False),
        Binding("p", "prev", "prev", show=False),
        Binding("o", "open", "open", show=False),
        Binding("t", "toggle_tree", "tree", show=False),
        Binding("h", "toggle_history", "history", show=False),
        Binding("f", "fullscreen", "full", show=False),
        Binding("backspace", "up_dir", "up", show=False),
        Binding("r", "reload", "reload", show=False),
        Binding("y", "copy_path", "copy path", show=False),
        Binding("Y", "copy_path_native", "copy native path", show=False),
        Binding("q", "quit", "quit", show=False),
    ]

    def __init__(self, protocol: str = "auto") -> None:
        super().__init__()
        self._ImageWidget = _image_widget_class(protocol)
        self.protocol = protocol
        self.current: Path | None = None
        self.info: Info | None = None
        self.idx = -1
        self.tree_dir: Path | None = None
        self.frames: list[Path] = []
        self.frame_i = 0
        self._player = None
        self._sig: object = _UNSET
        self._fullscreen = False
        self.panel = "tree"  # or "history"
        self._preview_timer = None
        self.collapsed: set[str] = set()

    # ----- layout -------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Static("", id="header")
        with Vertical(id="stage"):
            yield self._ImageWidget(None, id="viewer")
        yield ListView(id="tree")
        with Horizontal(id="footer"):
            yield Static("", id="keys")
            yield Static("", id="brand")

    def on_mount(self) -> None:
        state.ensure_dirs()
        state.PIDFILE.write_text(str(os.getpid()))
        self._render_footer("waiting for media")
        self._render_header(None)
        self.set_interval(0.25, self._poll_queue)
        hist = state.history()
        if hist and hist[-1].exists():
            self.idx = len(hist) - 1
            self.show(hist[-1])
        elif self.size.width < 60:
            self.query_one("#tree").add_class("hidden")

    def on_resize(self) -> None:
        self._render_footer()
        try:
            self.query_one("#viewer").refresh(layout=True)
        except Exception:
            pass

    def on_unmount(self) -> None:
        try:
            state.PIDFILE.unlink()
        except FileNotFoundError:
            pass

    # ----- queue --------------------------------------------------------
    def _poll_queue(self) -> None:
        sig = state.signature()
        if sig == self._sig:
            return
        first = self._sig is _UNSET
        self._sig = sig
        if first:
            return  # the initial state was handled in on_mount
        p = state.read_queue()
        if p and p.exists():
            hist = state.history()
            self.idx = len(hist) - 1
            self.show(p)

    # ----- showing ------------------------------------------------------
    def show(self, path: Path, refresh_tree: bool = True) -> None:
        self._stop_player()
        self.current = path
        self.info = None
        self._probe(path)
        viewer = self.query_one("#viewer")
        if state.is_video(path):
            self._render_header(path, "contact sheet · space to play")
            viewer.image = None
            self._load_sheet(path)
        elif state.is_audio(path):
            self._render_header(path, "waveform · space to play")
            viewer.image = None
            self._load_wave(path)
        else:
            self._render_header(path)
            viewer.image = str(path)
        self._render_footer()
        if self.panel == "history":
            self._populate_history()
        elif refresh_tree and self.tree_dir != path.parent:
            self._populate_tree(path.parent)
        self._highlight_in_tree(path)

    @work(thread=True, exclusive=True, group="probe")
    def _probe(self, path: Path) -> None:
        info = probe(path)
        if self.current == path:
            self.call_from_thread(self._apply_info, path, info)

    def _apply_info(self, path: Path, info: Info) -> None:
        if self.current != path:
            return
        self.info = info
        note = "contact sheet · space to play" if state.is_video(path) else ("waveform · space to play" if state.is_audio(path) else "")
        self._render_header(path, note)

    @work(thread=True, exclusive=True, group="sheet")
    def _load_sheet(self, path: Path) -> None:
        sheet = contact_sheet(path)
        if sheet and self.current == path:
            self.call_from_thread(self._set_image, str(sheet))

    @work(thread=True, exclusive=True, group="sheet")
    def _load_wave(self, path: Path) -> None:
        wave = waveform(path)
        if wave and self.current == path:
            self.call_from_thread(self._set_image, str(wave))

    def _set_image(self, src: str | None) -> None:
        self.query_one("#viewer").image = src

    def _render_header(self, path: Path | None, note: str = "") -> None:
        hdr = self.query_one("#header", Static)
        if path is None:
            hdr.update(f"[{ACCENT} bold]VIEWFINDER[/] [dim]by STRANGELOOP[/]   [dim]waiting for media · {self._effective_protocol()}[/]\n[dim]vf show FILE, or let your agent read an image[/]")
            return
        meta = self.info.human() if self.info else "…"
        hist = state.history()
        pos = f"{self.idx + 1}/{len(hist)}" if hist and 0 <= self.idx < len(hist) else ""
        hdr.update(f"[{ACCENT} bold]{path.name}[/]  [dim]{pos}[/]\n[dim]{meta}   {note}[/]")

    def _render_footer(self, msg: str = "") -> None:
        keys = "space play  n/p  h hist  y copy  o open  t tree  f full  q quit"
        self.query_one("#keys", Static).update(msg or keys)
        w = self.size.width
        brand = BRAND if w >= 90 else ("VIEWFINDER v" + ".".join(__version__.split(".")[:2]) if w >= 60 else "VF")
        self.query_one("#brand", Static).update(f"[{ACCENT}]{brand}[/]")

    # ----- tree ---------------------------------------------------------
    def _populate_tree(self, d: Path) -> None:
        self.tree_dir = d
        tree = self.query_one("#tree", ListView)
        tree.clear()
        try:
            entries = list(d.iterdir())
        except OSError:
            entries = []
        dirs = sorted((e for e in entries if e.is_dir() and not e.name.startswith(".")), key=lambda e: e.name.lower())
        files = [e for e in entries if e.is_file() and state.is_media(e)]
        files.sort(key=lambda e: e.stat().st_mtime, reverse=True)
        items = [Entry(d.parent, f"[dim]..[/]  [dim]{d}[/]")]
        items += [Entry(e, f"[{ACCENT}]{e.name}/[/]") for e in dirs[:20]]
        items += [Entry(e, f"{_mark(e)}{e.name}") for e in files[:200]]
        tree.extend(items)

    def _populate_history(self) -> None:
        """Bottom panel as history: one group per project (agent cwd), newest first, session as a tag."""
        tree = self.query_one("#tree", ListView)
        tree.clear()
        entries = state.history_entries()
        groups: dict[str, list] = {}
        for e in reversed(entries):
            groups.setdefault(e.project or "(no project)", []).append(e)
        items = []
        for project, rows in groups.items():
            arrow = "▸" if project in self.collapsed else "▾"
            items.append(GroupRow(project, f"[{ACCENT} bold]{arrow} {project}[/]  [dim]{len(rows)}[/]"))
            if project in self.collapsed:
                continue
            seen: set[Path] = set()
            for e in rows:
                if e.path in seen:
                    continue  # collapse repeats of the same file within a project
                seen.add(e.path)
                when = time.strftime("%H:%M", time.localtime(e.ts)) if e.ts else "--:--"
                tag = f"  [dim]{e.agent}·{e.session_tag}[/]" if e.session_tag else (f"  [dim]{e.agent}[/]" if e.agent else "")
                items.append(Entry(e.path, f"  [dim]{when}[/]  {_mark(e.path)}{e.path.name}{tag}"))
        tree.extend(items or [Entry(Path.cwd(), "[dim]no history yet[/]")])

    def action_toggle_history(self) -> None:
        self.panel = "history" if self.panel == "tree" else "tree"
        if self.panel == "history":
            self._populate_history()
        else:
            self._populate_tree(self.current.parent if self.current else Path.cwd())
        if self.current:
            self._highlight_in_tree(self.current)
        self.query_one("#tree").remove_class("hidden")

    def _highlight_in_tree(self, path: Path) -> None:
        tree = self.query_one("#tree", ListView)
        for i, item in enumerate(tree.children):
            if isinstance(item, Entry) and item.path == path:
                tree.index = i
                break

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        item = event.item
        if not isinstance(item, Entry) or item.path.is_dir() or item.path == self.current:
            return
        if self._preview_timer:
            self._preview_timer.stop()
        self._preview_timer = self.set_timer(0.12, lambda: self._preview(item.path))

    def _preview(self, path: Path) -> None:
        self._preview_timer = None
        if path.exists() and path != self.current:
            self.show(path, refresh_tree=False)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, GroupRow):
            self.collapsed ^= {item.project}
            self._populate_history()
            return
        if not isinstance(item, Entry):
            return
        if item.path.is_dir():
            self._populate_tree(item.path)
        elif item.path.exists():
            self.show(item.path, refresh_tree=False)

    # ----- actions ------------------------------------------------------
    def action_next(self) -> None:
        hist = state.history()
        if self.idx < len(hist) - 1:
            self.idx += 1
            self.show(hist[self.idx])

    def action_prev(self) -> None:
        hist = state.history()
        if self.idx > 0:
            self.idx -= 1
            self.show(hist[self.idx])

    def action_reload(self) -> None:
        if self.current:
            self.show(self.current)

    def action_open(self) -> None:
        if not self.current:
            return
        p = str(self.current)
        try:
            if os.environ.get("WSL_DISTRO_NAME"):
                win = subprocess.run(["wslpath", "-w", p], capture_output=True, text=True).stdout.strip()
                subprocess.Popen(["cmd.exe", "/c", "start", "", win], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", p])
            elif sys.platform.startswith("win"):
                os.startfile(p)  # type: ignore[attr-defined]
            else:
                subprocess.Popen(["xdg-open", p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            self._render_footer(f"open failed: {e}")

    def _copy(self, text: str, label: str) -> None:
        self.copy_to_clipboard(text)  # OSC 52: works in Windows Terminal, WezTerm, kitty, iTerm2
        for tool in (["clip.exe"], ["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"]):
            if shutil.which(tool[0]):
                try:
                    subprocess.run(tool, input=text.encode(), timeout=3, check=False)
                except (OSError, subprocess.SubprocessError):
                    pass
                break
        self._render_footer(f"copied {label}: {text}")

    def action_copy_path(self) -> None:
        if self.current:
            self._copy(str(self.current), "path")

    def action_copy_path_native(self) -> None:
        if not self.current:
            return
        text = str(self.current)
        if os.environ.get("WSL_DISTRO_NAME"):
            try:
                text = subprocess.run(["wslpath", "-w", text], capture_output=True, text=True, timeout=3).stdout.strip() or text
            except (OSError, subprocess.SubprocessError):
                pass
        self._copy(text, "windows path")

    def action_toggle_tree(self) -> None:
        self.query_one("#tree").toggle_class("hidden")

    def action_fullscreen(self) -> None:
        self._fullscreen = not self._fullscreen
        for sel in ("#header", "#tree", "#footer"):
            self.query_one(sel).set_class(self._fullscreen, "hidden")

    def action_up_dir(self) -> None:
        if self.tree_dir and self.tree_dir.parent != self.tree_dir:
            self._populate_tree(self.tree_dir.parent)

    # ----- video playback -----------------------------------------------
    def action_play(self) -> None:
        if not self.current or not (state.is_video(self.current) or state.is_audio(self.current)):
            return
        if self._player:
            self._stop_player()
            self._render_header(self.current, "paused · space to play")
            return
        mpv = shutil.which("mpv")
        if mpv:
            self._play_with_mpv(mpv, self.current)
            return
        self._render_header(self.current, "extracting frames…")
        self._prepare_frames(self.current)

    def _play_with_mpv(self, mpv: str, path: Path) -> None:
        """Hand the pane to mpv (real frame rate), return to the app when it quits."""
        vo = {"sixel": "sixel", "tgp": "kitty"}.get(self._effective_protocol(), "tct")
        conf = state.HOME / "mpv-input.conf"
        if not conf.exists():
            conf.write_text("ESC quit\nq quit\nSPACE cycle pause\n")
        cmd = [mpv, f"--vo={vo}", "--really-quiet", "--loop=inf", "--osd-level=0",
               "--input-terminal=yes", "--term-osd=no", f"--input-conf={conf}", str(path)]
        if state.is_audio(path):
            cmd += ["--vo=null", "--term-osd=yes", "--term-status-msg=♪ ${time-pos} / ${duration}   ESC to stop"]
        elif vo == "sixel":
            cols, rows = self.size.width, self.size.height
            try:
                from textual_image._terminal import get_cell_size
                cs = get_cell_size()
                cw, ch = int(cs.width), int(cs.height)
            except Exception:
                cw, ch = 10, 20
            cmd += ["--vo-sixel-config-clear=yes", "--vo-sixel-alt-screen=yes",
                    f"--vo-sixel-width={max(cols - 1, 20) * cw}", f"--vo-sixel-height={max(rows - 2, 10) * ch}",
                    "--vo-sixel-dither=none"]
        with self.suspend():
            sys.stdout.write("\x1b[2J\x1b[H")  # clear the pane before mpv paints
            sys.stdout.flush()
            try:
                subprocess.run(cmd)
            except OSError:
                pass
        self._render_header(path, f"played with mpv ({vo}) · space to play again")
        self.action_reload()

    def _effective_protocol(self) -> str:
        if self.protocol != "auto":
            return self.protocol
        name = self._ImageWidget.__name__.lower()
        for key in ("sixel", "tgp", "halfcell", "unicode"):
            if key in name:
                return key
        return "unicode"

    @work(thread=True, exclusive=True, group="frames")
    def _prepare_frames(self, path: Path) -> None:
        frames = extract_frames(path, fps=10)
        if self.current == path:
            self.call_from_thread(self._start_player, frames)

    def _start_player(self, frames: list[Path]) -> None:
        if not frames:
            self._render_header(self.current, "no frames (ffmpeg missing?)")
            return
        self.frames, self.frame_i = frames, 0
        self._render_header(self.current, f"playing {len(frames)} frames · space to pause")
        self._player = self.set_interval(1 / 10, self._tick)

    def _tick(self) -> None:
        if not self.frames:
            return
        self._set_image(str(self.frames[self.frame_i]))
        self.frame_i = (self.frame_i + 1) % len(self.frames)

    def _stop_player(self) -> None:
        if self._player:
            self._player.stop()
            self._player = None
