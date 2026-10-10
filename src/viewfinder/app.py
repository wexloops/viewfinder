"""The Viewfinder pane: header, viewer, folder tree or history below, one-line key bar."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from collections import OrderedDict
from pathlib import Path

from rich.markup import escape
from textual import work
from textual.app import App, ComposeResult, SuspendNotSupported
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import ListItem, ListView, Static

from . import __version__, state
from .media import Info, MediaError, contact_sheet, extract_frames, load_image, probe, waveform

# HYPRGRPH palette: bone on black, one accent per media kind.
BONE = "#ece8df"
CYAN = "#7DF9FF"
MAGENTA = "#FF2BD6"
LIME = "#D4FF3A"
VIOLET = "#8B5CFF"
ORANGE = "#FF7A1A"
DIM = "#8a857c"
FAINT = "#4a4741"
RULE = "#24221f"
ACCENT = CYAN

KIND_COLOR = {"image": CYAN, "video": MAGENTA, "audio": LIME}
KIND_GLYPH = {"image": "◆", "video": "▶", "audio": "♪"}
KIND_LABEL = {"image": "IMAGE", "video": "VIDEO", "audio": "AUDIO"}
FILTERS = ["all", "image", "video", "audio"]
FILTER_LABEL = {"all": "all media", "image": "images", "video": "video", "audio": "audio"}
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

BRAND = "VIEWFINDER " + ".".join(__version__.split(".")[:2])

KEY_HELP = [
    ("arrows", "browse; the viewer previews as you move"),
    ("enter", "open a folder, show a file, fold a project"),
    ("space", "play video or audio with mpv (esc returns)"),
    ("n / p", "next / previous in history"),
    ("c", "compare: flip to the previous file and back"),
    ("m", "filter the list: all, images, video, audio"),
    ("h", "switch the list: folder tree / history"),
    ("y / Y", "copy the path / the Windows path"),
    ("o / O", "open in the default app / reveal in folder"),
    ("r", "reload the current file"),
    ("t / f", "hide the list / fullscreen the viewer"),
    ("backspace", "up one folder"),
    ("?", "this help"),
    ("q", "quit"),
]


def _image_widget_class(protocol: str):
    from textual_image import widget as w
    return {
        "auto": w.Image, "sixel": w.SixelImage, "tgp": w.TGPImage,
        "halfcell": w.HalfcellImage, "unicode": w.UnicodeImage,
    }[protocol]


def _glyph(p: Path) -> str:
    k = state.kind(p)
    return f"[{KIND_COLOR[k]}]{KIND_GLYPH[k]}[/] " if k else "  "


def _short(p: Path | str, width: int) -> str:
    """Home-relative path, middle-truncated to WIDTH characters."""
    s = str(p)
    home = str(Path.home())
    if s == home or s.startswith(home + os.sep):
        s = "~" + s[len(home):]
    if len(s) <= width:
        return s
    keep = max(width - 1, 4)
    head = keep // 3
    return s[:head] + "…" + s[-(keep - head):]


def open_command(path: Path, *, reveal: bool = False, platform: str | None = None,
                 wsl: bool | None = None, wslpath=None) -> list[str]:
    """The OS command that opens PATH (or reveals it in its folder). Pure, so it can be tested."""
    platform = platform or sys.platform
    wsl = bool(os.environ.get("WSL_DISTRO_NAME")) if wsl is None else wsl
    if wsl:
        to_win = wslpath or _wslpath
        if reveal:
            win = to_win(str(path))
            if win:
                return ["explorer.exe", f"/select,{win}"]
            return ["cmd.exe", "/c", "start", "", to_win(str(path.parent)) or str(path.parent)]
        return ["cmd.exe", "/c", "start", "", to_win(str(path)) or str(path)]
    if platform == "darwin":
        return ["open", "-R", str(path)] if reveal else ["open", str(path)]
    if platform.startswith("win"):
        return ["explorer.exe", f"/select,{path}"] if reveal else ["explorer.exe", str(path)]
    # Linux: no portable "select the file", so reveal opens the directory
    return ["xdg-open", str(path.parent if reveal else path)]


def _wslpath(p: str) -> str:
    try:
        return subprocess.run(["wslpath", "-w", p], capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


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
    Screen {{ background: black; color: {BONE}; }}
    #header {{ height: 2; padding: 0 1; background: black; }}
    #title {{ width: 1fr; height: 2; text-wrap: nowrap; text-overflow: ellipsis; }}
    #where {{ width: auto; height: 2; text-align: right; color: {DIM}; }}
    #stage {{ height: 1fr; width: 100%; align: center middle; background: black; }}
    #viewer {{ width: auto; height: auto; }}
    #placeholder {{ width: auto; height: auto; color: {DIM}; padding: 1 2; }}
    #tree {{
        height: 12; background: black; border-top: solid {RULE};
        border-title-color: {DIM}; border-title-align: left; border-title-style: none;
        border-subtitle-color: {FAINT}; border-subtitle-align: right;
        scrollbar-size: 1 1; scrollbar-color: {FAINT}; scrollbar-color-hover: {DIM};
        scrollbar-color-active: {CYAN}; scrollbar-background: black;
        scrollbar-background-hover: black; scrollbar-background-active: black;
    }}
    #tree:focus {{ background-tint: black 0%; border-top: solid #2e2c28; }}
    #tree > ListItem {{ background: black; color: #bdb8ae; padding: 0 1; }}
    #tree > ListItem.-hovered {{ background: #121110; }}
    #tree > ListItem.-highlight {{ background: #0f2a2c; color: {CYAN}; text-style: none; }}
    #tree:focus > ListItem.-highlight {{ background: {CYAN}; color: black; text-style: bold; }}
    #footer {{ height: 1; padding: 0 1; background: #0b0b0a; }}
    #keys {{ width: 1fr; color: {DIM}; overflow: hidden; text-wrap: nowrap; text-overflow: ellipsis; }}
    #brand {{ width: auto; text-align: right; }}
    .hidden {{ display: none; }}
    """
    BINDINGS = [
        Binding("space", "play", "play", show=False),
        Binding("n", "next", "next", show=False),
        Binding("p", "prev", "prev", show=False),
        Binding("c", "compare", "compare", show=False),
        Binding("m", "filter", "filter", show=False),
        Binding("o", "open", "open", show=False),
        Binding("O", "reveal", "reveal in folder", show=False),
        Binding("t", "toggle_tree", "tree", show=False),
        Binding("h", "toggle_history", "history", show=False),
        Binding("f", "fullscreen", "full", show=False),
        Binding("backspace", "up_dir", "up", show=False),
        Binding("r", "reload", "reload", show=False),
        Binding("y", "copy_path", "copy path", show=False),
        Binding("Y", "copy_path_native", "copy native path", show=False),
        Binding("question_mark", "help", "keys", show=False),
        Binding("escape", "dismiss", "close", show=False),
        Binding("q", "quit", "quit", show=False),
    ]

    def __init__(self, protocol: str = "auto") -> None:
        super().__init__()
        self._ImageWidget = _image_widget_class(protocol)
        self.protocol = protocol
        self.current: Path | None = None
        self.previous: Path | None = None
        self.info: Info | None = None
        self.idx = -1
        self.tree_dir: Path | None = None
        self.filter = "all"
        self.frames: list[Path] = []
        self.frame_i = 0
        self._player = None
        self._sig: object = None
        self._fullscreen = False
        self._help = False
        self.panel = "tree"  # or "history"
        self._preview_timer = None
        self._flash_timer = None
        self._spin_timer = None
        self._spin_i = 0
        self._loading = ""
        self.collapsed: set[str] = set()
        self._decoded: OrderedDict[tuple, object] = OrderedDict()
        self.error: str = ""

    # ----- layout -------------------------------------------------------
    def compose(self) -> ComposeResult:
        with Horizontal(id="header"):
            yield Static("", id="title")
            yield Static("", id="where")
        with Vertical(id="stage"):
            yield self._ImageWidget(None, id="viewer")
            yield Static("", id="placeholder")
        yield ListView(id="tree")
        with Horizontal(id="footer"):
            yield Static("", id="keys")
            yield Static("", id="brand")

    def on_mount(self) -> None:
        state.ensure_dirs()
        try:
            state.PIDFILE.write_text(str(os.getpid()))
        except OSError:
            pass
        self._sig = state.signature()  # baseline before reading history, so nothing pushed after this is missed
        self._fit_tree()
        self._render_footer()
        self.set_interval(0.25, self._poll_queue)
        self.query_one("#tree", ListView).focus()
        hist = state.history()
        for i in range(len(hist) - 1, -1, -1):
            if hist[i].exists():
                self.idx = i
                self.show(hist[i])
                return
        self._show_empty()
        self._populate_tree(Path.cwd())

    def on_resize(self) -> None:
        self._fit_tree()
        self._render_footer()
        if self.current:
            self._render_header(self.current)
        self._render_panel_title()
        try:
            self.query_one("#viewer").refresh(layout=True)
        except Exception:
            pass

    def _fit_tree(self) -> None:
        h = self.size.height
        self.query_one("#tree").styles.height = max(5, min(14, h // 3))

    def on_unmount(self) -> None:
        try:
            if state.PIDFILE.read_text().strip() == str(os.getpid()):
                state.PIDFILE.unlink()
        except (FileNotFoundError, OSError):
            pass

    # ----- queue --------------------------------------------------------
    def _poll_queue(self) -> None:
        sig = state.signature()
        if sig == self._sig:
            return
        self._sig = sig
        p = state.read_queue()
        if p and p.exists():
            hist = state.history()
            self.idx = len(hist) - 1
            self.show(p)

    # ----- stage states -------------------------------------------------
    def _stage(self, image=None, text: str | None = None) -> None:
        """Show an image in the viewer, or a text block (empty, loading, error, help) in its place."""
        viewer = self.query_one("#viewer")
        ph = self.query_one("#placeholder", Static)
        try:
            viewer.image = image
        except Exception as e:  # never let one bad file take the pane down
            viewer.image = None
            text = self._error_text(f"couldn't draw this image: {e}")
        viewer.set_class(image is None, "hidden")
        ph.set_class(text is None, "hidden")
        if text is not None:
            ph.update(text)

    def _show_empty(self) -> None:
        self._stop_spinner()
        self.query_one("#title", Static).update(f"[{BONE} bold]Viewfinder[/]\n[{DIM}]waiting for media[/]")
        self.query_one("#where", Static).update(f"[{FAINT}]{self._effective_protocol()}[/]\n")
        rows = [
            ("vf show FILE", "push any image, video or audio here"),
            ("vf setup claude", "show what Claude Code reads, automatically"),
            ("vf doctor", "check ffmpeg, mpv and terminal graphics"),
        ]
        body = "\n".join(f"[{CYAN}]{cmd:<17}[/][{DIM}]{desc}[/]" for cmd, desc in rows)
        self._stage(None, f"[{BONE} bold]◎  VIEWFINDER[/]\n[{FAINT}]a media pane beside your agent[/]\n\n{body}")

    def _error_text(self, reason: str, path: Path | None = None) -> str:
        where = f"\n[{FAINT}]{escape(_short(path, 70))}[/]" if path else ""
        return f"[{ORANGE} bold]can't show this file[/]\n[{DIM}]{escape(reason)}[/]{where}"

    def _show_error(self, path: Path, reason: str) -> None:
        self._stop_spinner()
        self.error = reason
        self._render_header(path)
        self._stage(None, self._error_text(reason, path))

    def _start_spinner(self, what: str) -> None:
        self._loading = what
        self._spin_i = 0
        self._tick_spinner()
        if not self._spin_timer:
            self._spin_timer = self.set_interval(0.08, self._tick_spinner)

    def _tick_spinner(self) -> None:
        if not self._loading:
            return
        color = KIND_COLOR.get(state.kind(self.current), CYAN) if self.current else CYAN
        frame = SPINNER[self._spin_i % len(SPINNER)]
        self._spin_i += 1
        self._stage(None, f"[{color}]{frame}[/]  [{DIM}]{self._loading}[/]")

    def _stop_spinner(self) -> None:
        self._loading = ""
        if self._spin_timer:
            self._spin_timer.stop()
            self._spin_timer = None

    # ----- showing ------------------------------------------------------
    def show(self, path: Path, refresh_tree: bool = True) -> None:
        self._stop_player()
        self._help = False
        if path != self.current and self.current is not None:
            self.previous = self.current
        self.current = path
        self.info = None
        self.error = ""
        hist = state.history()
        if not (0 <= self.idx < len(hist) and hist[self.idx] == path) and path in hist:
            self.idx = len(hist) - 1 - hist[::-1].index(path)
        self._render_header(path)
        if not path.exists():
            self._show_error(path, "file not found: it was moved or deleted")
        else:
            self._probe(path)
            k = state.kind(path)
            if k == "video":
                self._start_spinner("building contact sheet")
                self._load_sheet(path)
            elif k == "audio":
                self._start_spinner("drawing waveform")
                self._load_wave(path)
            else:
                cached = self._decoded_get(path)
                if cached is not None:
                    self._stop_spinner()
                    self._stage(cached)
                else:
                    self._start_spinner("decoding")
                    self._load_image(path)
        self._render_footer()
        if self.panel == "history":
            self._populate_history()
        elif refresh_tree and self.tree_dir != path.parent:
            self._populate_tree(path.parent)
        self._highlight_in_tree(path)

    def _key(self, path: Path) -> tuple | None:
        try:
            st = path.stat()
        except OSError:
            return None
        return (str(path), st.st_mtime_ns, st.st_size)

    def _decoded_get(self, path: Path):
        key = self._key(path)
        if key and key in self._decoded:
            self._decoded.move_to_end(key)
            return self._decoded[key]
        return None

    def _decoded_put(self, path: Path, img) -> None:
        key = self._key(path)
        if key:
            self._decoded[key] = img
            while len(self._decoded) > 8:
                self._decoded.popitem(last=False)

    @work(thread=True, exclusive=True, group="probe")
    def _probe(self, path: Path) -> None:
        info = probe(path)
        if self.current == path:
            self.call_from_thread(self._apply_info, path, info)

    def _apply_info(self, path: Path, info: Info) -> None:
        if self.current != path:
            return
        if self.info and not info.width:  # keep Pillow's dimensions when ffprobe is missing
            info.width, info.height = self.info.width, self.info.height
        self.info = info
        self._render_header(path)

    @work(thread=True, exclusive=True, group="load")
    def _load_image(self, path: Path) -> None:
        try:
            img, w, h = load_image(path)
        except MediaError as e:
            if self.current == path:
                self.call_from_thread(self._show_error, path, str(e))
            return
        if self.current == path:
            self.call_from_thread(self._image_ready, path, img, w, h)

    def _image_ready(self, path: Path, img, w: int, h: int) -> None:
        self._decoded_put(path, img)
        if self.current != path:
            return
        self._stop_spinner()
        if self.info is None:
            self.info = Info(width=w, height=h, size=path.stat().st_size if path.exists() else 0)
        elif not self.info.width:
            self.info.width, self.info.height = w, h
        self._render_header(path)
        self._stage(img)

    @work(thread=True, exclusive=True, group="load")
    def _load_sheet(self, path: Path) -> None:
        sheet = contact_sheet(path)
        self.call_from_thread(self._derived_ready, path, sheet, "contact sheet")

    @work(thread=True, exclusive=True, group="load")
    def _load_wave(self, path: Path) -> None:
        wave = waveform(path)
        self.call_from_thread(self._derived_ready, path, wave, "waveform")

    def _derived_ready(self, path: Path, out: Path | None, what: str) -> None:
        if self.current != path:
            return
        if out is None:
            if not shutil.which("ffmpeg"):
                self._show_error(path, f"ffmpeg not found, so no {what}. Run: vf doctor")
            else:
                self._show_error(path, f"ffmpeg couldn't read this file to build a {what}")
            return
        try:
            img, _, _ = load_image(out)
        except MediaError as e:
            self._show_error(path, str(e))
            return
        self._stop_spinner()
        self._stage(img)

    # ----- header / footer ------------------------------------------------
    def _render_header(self, path: Path | None) -> None:
        if path is None:
            return
        k = state.kind(path)
        color = KIND_COLOR.get(k, BONE)
        badge = f"[{color} bold]{KIND_LABEL.get(k, 'FILE')}[/]"
        if self.error:
            meta = f"[{DIM}]{self.info.human() if self.info else ''}[/]   [{ORANGE}]can't show[/]"
        else:
            meta = f"[{DIM}]{self.info.human() if self.info else '…'}[/]"
            note = {"video": "space to play", "audio": "space to listen"}.get(k, "")
            if self._player:
                note = "playing preview · space to stop"
            if note:
                meta += f"   [{FAINT}]{note}[/]"
        name_w = max(self.size.width - 22, 12)
        name = escape(path.name if len(path.name) <= name_w else path.name[: name_w - 1] + "…")
        self.query_one("#title", Static).update(f"{badge}  [{BONE} bold]{name}[/]\n{meta}")
        hist = state.history()
        pos = f"{self.idx + 1} / {len(hist)}" if hist and 0 <= self.idx < len(hist) and hist[self.idx] == path else ""
        prev = self.previous.name if self.previous else ""
        prev = prev if len(prev) <= 18 else prev[:17] + "…"
        cmp = f"[{VIOLET}]c ⇄[/] [{DIM}]{escape(prev)}[/]" if prev and self.size.width >= 70 else ""
        self.query_one("#where", Static).update(f"[{DIM}]{pos}[/]\n{cmp}")

    def _render_footer(self) -> None:
        hints = [("space", "play"), ("?", "keys"), ("n/p", "history"), ("c", "compare"), ("m", "filter"),
                 ("h", "tree/history"), ("y", "copy"), ("o/O", "open/reveal"), ("f", "full"), ("q", "quit")]
        w = self.size.width
        brand = BRAND if w >= 64 else "VF"
        room = w - len(brand) - 4
        parts, used = [], 0
        for key, label in hints:
            seg = len(key) + 1 + len(label) + 2
            if used + seg > room:
                break
            parts.append(f"[{BONE}]{escape(key)}[/] [{DIM}]{label}[/]")
            used += seg
        if not self._flash_timer:
            self.query_one("#keys", Static).update("  ".join(parts))
        self.query_one("#brand", Static).update(f"[{CYAN}]{brand}[/]")

    def flash(self, msg: str, level: str = "ok") -> None:
        """A transient message in the key bar."""
        color = {"ok": LIME, "warn": ORANGE, "info": CYAN}[level]
        mark = {"ok": "✓", "warn": "!", "info": "·"}[level]
        if self._flash_timer:
            self._flash_timer.stop()
        self.query_one("#keys", Static).update(f"[{color}]{mark}[/] [{BONE}]{escape(msg)}[/]")
        self._flash_timer = self.set_timer(3.5, self._end_flash)

    def _end_flash(self) -> None:
        self._flash_timer = None
        self._render_footer()

    # ----- list panel ---------------------------------------------------
    def _passes(self, p: Path) -> bool:
        return self.filter == "all" or state.kind(p) == self.filter

    def _render_panel_title(self) -> None:
        tree = self.query_one("#tree", ListView)
        flt = "" if self.filter == "all" else f"  [{CYAN}]{FILTER_LABEL[self.filter]}[/]"
        if self.panel == "history":
            tree.border_title = f"[{BONE}]history[/] [{FAINT}]by project[/]{flt}"
        else:
            where = _short(self.tree_dir, max(self.size.width - 30, 16)) if self.tree_dir else ""
            tree.border_title = f"[{BONE}]folder[/] [{DIM}]{escape(where)}[/]{flt}"
        tree.border_subtitle = ""

    def _populate_tree(self, d: Path) -> None:
        self.tree_dir = d
        tree = self.query_one("#tree", ListView)
        tree.clear()
        try:
            entries = list(d.iterdir())
        except OSError:
            entries = []

        def mtime(e: Path) -> float:
            try:
                return e.stat().st_mtime
            except OSError:
                return 0.0

        dirs = sorted((e for e in entries if not e.name.startswith(".") and e.is_dir()), key=lambda e: e.name.lower())
        files = [e for e in entries if state.is_media(e) and self._passes(e) and e.is_file()]
        files.sort(key=mtime, reverse=True)
        items = [Entry(d.parent, "[dim]‥  up[/]")] if d.parent != d else []
        items += [Entry(e, f"[{VIOLET}]▸[/] [b]{escape(e.name)}/[/]") for e in dirs[:40]]
        items += [Entry(e, f"{_glyph(e)}{escape(e.name)}") for e in files[:300]]
        if not files:
            what = "media" if self.filter == "all" else FILTER_LABEL[self.filter]
            items.append(ListItem(Static(f"[dim]no {what} in this folder[/]"), disabled=True))
        tree.extend(items)
        self._render_panel_title()

    def _populate_history(self) -> None:
        """Bottom panel as history: one group per project (agent cwd), newest first, session as a tag."""
        tree = self.query_one("#tree", ListView)
        tree.clear()
        entries = [e for e in state.history_entries() if self._passes(e.path)]
        groups: dict[str, list] = {}
        for e in reversed(entries):
            groups.setdefault(e.project or "(no project)", []).append(e)
        items = []
        for project, rows in groups.items():
            arrow = "▸" if project in self.collapsed else "▾"
            items.append(GroupRow(project, f"[{VIOLET}]{arrow}[/] [b]{escape(project)}[/]  [dim]{len(rows)}[/]"))
            if project in self.collapsed:
                continue
            seen: set[Path] = set()
            for e in rows:
                if e.path in seen:
                    continue  # collapse repeats of the same file within a project
                seen.add(e.path)
                when = time.strftime("%H:%M", time.localtime(e.ts)) if e.ts else "--:--"
                tag = f"{e.agent}·{e.session_tag}" if e.session_tag else e.agent
                tag = f"  [dim]{escape(tag)}[/]" if tag else ""
                name = escape(e.path.name)
                if not e.path.exists():
                    name = f"[dim strike]{name}[/] [dim]missing[/]"
                items.append(Entry(e.path, f"  [dim]{when}[/]  {_glyph(e.path)}{name}{tag}"))
        if not items:
            hint = "vf show FILE or an agent read" if self.filter == "all" else f"no {FILTER_LABEL[self.filter]} yet"
            items = [ListItem(Static(f"[dim]history is empty: {hint}[/]"), disabled=True)]
        tree.extend(items)
        self._render_panel_title()

    def action_toggle_history(self) -> None:
        self.panel = "history" if self.panel == "tree" else "tree"
        self._refresh_panel()
        self.query_one("#tree").remove_class("hidden")
        self.query_one("#tree", ListView).focus()

    def _refresh_panel(self) -> None:
        if self.panel == "history":
            self._populate_history()
        else:
            self._populate_tree(self.tree_dir or (self.current.parent if self.current else Path.cwd()))
        if self.current:
            self._highlight_in_tree(self.current)

    def action_filter(self) -> None:
        self.filter = FILTERS[(FILTERS.index(self.filter) + 1) % len(FILTERS)]
        self._refresh_panel()
        self.flash(f"showing {FILTER_LABEL[self.filter]}", "info")

    def _highlight_in_tree(self, path: Path) -> None:
        """Select PATH in the list. Runs after the next refresh so freshly added rows exist."""
        def apply() -> None:
            tree = self.query_one("#tree", ListView)
            for i, item in enumerate(tree.children):
                if isinstance(item, Entry) and item.path == path:
                    tree.index = i
                    break
        self.call_after_refresh(apply)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        item = event.item
        if not isinstance(item, Entry) or item.path == self.current or not item.path.is_file():
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
            self.query_one("#tree", ListView).index = 0
        else:
            self.show(item.path, refresh_tree=False)

    # ----- actions ------------------------------------------------------
    def _step(self, delta: int) -> None:
        hist = state.history()
        i = self.idx + delta
        while 0 <= i < len(hist):
            if hist[i].exists() and hist[i] != self.current:
                self.idx = i
                self.show(hist[i])
                return
            i += delta
        self.flash("end of history" if delta > 0 else "start of history", "info")

    def action_next(self) -> None:
        self._step(+1)

    def action_prev(self) -> None:
        self._step(-1)

    def action_compare(self) -> None:
        if not self.previous:
            self.flash("nothing to compare yet: show a second file first", "info")
            return
        if not self.previous.exists():
            self.flash(f"{self.previous.name} is gone", "warn")
            return
        self.show(self.previous)

    def action_reload(self) -> None:
        if self.current:
            self.show(self.current)

    def _launch(self, reveal: bool) -> None:
        if not self.current:
            return
        cmd = open_command(self.current, reveal=reveal)
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError) as e:
            self.flash(f"{'reveal' if reveal else 'open'} failed: {e}", "warn")
            return
        if reveal:
            self.flash(f"revealed in {_short(self.current.parent, 60)}")
        else:
            self.flash(f"opened {self.current.name}")

    def action_open(self) -> None:
        self._launch(reveal=False)

    def action_reveal(self) -> None:
        """Open the containing folder, selecting the file where the OS supports it."""
        self._launch(reveal=True)

    def _copy(self, text: str, label: str) -> None:
        self.copy_to_clipboard(text)  # OSC 52: works in Windows Terminal, WezTerm, kitty, iTerm2
        for tool in (["clip.exe"], ["pbcopy"], ["wl-copy"], ["xclip", "-selection", "clipboard"]):
            if shutil.which(tool[0]):
                try:
                    subprocess.run(tool, input=text.encode(), timeout=3, check=False)
                except (OSError, subprocess.SubprocessError):
                    pass
                break
        self.flash(f"copied {label}: {text}")

    def action_copy_path(self) -> None:
        if self.current:
            self._copy(str(self.current), "path")

    def action_copy_path_native(self) -> None:
        if not self.current:
            return
        text = str(self.current)
        if os.environ.get("WSL_DISTRO_NAME"):
            text = _wslpath(text) or text
        self._copy(text, "windows path" if os.environ.get("WSL_DISTRO_NAME") else "path")

    def action_toggle_tree(self) -> None:
        self.query_one("#tree").toggle_class("hidden")

    def action_fullscreen(self) -> None:
        self._fullscreen = not self._fullscreen
        for sel in ("#header", "#tree", "#footer"):
            self.query_one(sel).set_class(self._fullscreen, "hidden")

    def action_up_dir(self) -> None:
        if self.panel != "tree":
            return
        if self.tree_dir and self.tree_dir.parent != self.tree_dir:
            came_from = self.tree_dir
            self._populate_tree(self.tree_dir.parent)
            self._highlight_in_tree(came_from)

    def action_help(self) -> None:
        if self._help:
            self.action_dismiss()
            return
        self._help = True
        self._stop_spinner()
        rows = "\n".join(f"[{CYAN}]{k:<11}[/][{DIM}]{v}[/]" for k, v in KEY_HELP)
        self._stage(None, f"[{BONE} bold]keys[/]\n\n{rows}\n\n[{FAINT}]? or esc to close[/]")

    def action_dismiss(self) -> None:
        if self._fullscreen:
            self.action_fullscreen()
        if self._help:
            self._help = False
            if self.current:
                self.show(self.current, refresh_tree=False)
            else:
                self._show_empty()

    # ----- playback -----------------------------------------------------
    def action_play(self) -> None:
        if not self.current or not self.current.exists():
            return
        k = state.kind(self.current)
        if k not in ("video", "audio"):
            self.flash("space plays video and audio", "info")
            return
        if self._player:
            self._stop_player()
            self.show(self.current, refresh_tree=False)
            return
        mpv = shutil.which("mpv")
        if mpv:
            self._play_with_mpv(mpv, self.current)
            return
        if k == "audio":
            self.flash("install mpv to play audio (vf doctor)", "warn")
            return
        if not shutil.which("ffmpeg"):
            self.flash("install mpv or ffmpeg to play video (vf doctor)", "warn")
            return
        self._start_spinner("extracting preview frames (install mpv for full playback)")
        self._prepare_frames(self.current)

    def _play_with_mpv(self, mpv: str, path: Path) -> None:
        """Hand the pane to mpv (real frame rate), return to the app when it quits."""
        vo = {"sixel": "sixel", "tgp": "kitty"}.get(self._effective_protocol(), "tct")
        conf = state.HOME / "mpv-input.conf"
        if not conf.exists():
            conf.write_text("ESC quit\nq quit\nSPACE cycle pause\n")
        base = [mpv, "--loop=inf", "--osd-level=0", "--input-terminal=yes", f"--input-conf={conf}"]
        if state.is_audio(path):
            cmd = [*base, "--vo=null", "--no-video", "--msg-level=all=error", "--term-osd=force",
                   "--term-status-msg=♪ ${time-pos} / ${duration}   esc to stop"]
        else:
            cmd = [*base, f"--vo={vo}", "--really-quiet", "--term-osd=no"]
        if not state.is_audio(path) and vo == "sixel":
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
        cmd.append(str(path))
        rc = 0
        try:
            with self.suspend():
                sys.stdout.write("\x1b[2J\x1b[H")  # clear the pane before mpv paints
                sys.stdout.flush()
                try:
                    rc = subprocess.run(cmd).returncode
                except OSError:
                    rc = -1
        except SuspendNotSupported:
            self.flash("this terminal can't hand the pane to mpv; o opens it in the default app", "warn")
            return
        self.action_reload()
        if rc not in (0, 4):  # 4 = quit by signal
            self.flash(f"mpv exited with an error ({rc}); try o to open it in the default app", "warn")

    def _effective_protocol(self) -> str:
        if self.protocol != "auto":
            return self.protocol
        name = self._ImageWidget.__name__.lower()
        for key in ("sixel", "tgp", "halfcell", "unicode"):
            if key in name:
                return key
        return "auto"

    @work(thread=True, exclusive=True, group="frames")
    def _prepare_frames(self, path: Path) -> None:
        frames = extract_frames(path, fps=10)
        if self.current == path:
            self.call_from_thread(self._start_player, path, frames)

    def _start_player(self, path: Path, frames: list[Path]) -> None:
        if self.current != path:
            return
        self._stop_spinner()
        if not frames:
            self._show_error(path, "ffmpeg couldn't extract frames from this file")
            return
        self.frames, self.frame_i = frames, 0
        self._player = self.set_interval(1 / 10, self._tick)
        self._render_header(path)

    def _tick(self) -> None:
        if not self.frames:
            return
        self._stage(str(self.frames[self.frame_i]))
        self.frame_i = (self.frame_i + 1) % len(self.frames)

    def _stop_player(self) -> None:
        if self._player:
            self._player.stop()
            self._player = None
            self.frames = []
