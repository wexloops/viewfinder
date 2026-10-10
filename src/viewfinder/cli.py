"""vf: run the pane, push files to it, open it beside the current terminal, agent hooks."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import shlex
import signal
import subprocess
import sys

from . import __version__, state


def detect_terminal() -> tuple[str, str]:
    """(terminal name, protocol) from the environment alone. Protocol 'auto' means unidentified."""
    env = os.environ
    if env.get("KITTY_WINDOW_ID"):
        return "kitty", "tgp"
    if env.get("TERM_PROGRAM") == "ghostty" or env.get("GHOSTTY_RESOURCES_DIR"):
        return "Ghostty", "tgp"
    if env.get("WT_SESSION"):
        return "Windows Terminal", "sixel"
    if env.get("TERM_PROGRAM") == "WezTerm" or env.get("WEZTERM_PANE"):
        return "WezTerm", "sixel"
    if env.get("TERM_PROGRAM") == "iTerm.app":
        return "iTerm2", "sixel"
    return env.get("TERM_PROGRAM") or env.get("TERM") or "unknown", "auto"


def resolve_protocol(requested: str) -> str:
    """'auto' = trust the terminal we can identify from the environment; probe only as a last resort.
    The library's own probe waits 100ms for a reply that Windows Terminal over WSL often misses."""
    if requested != "auto":
        return requested
    return detect_terminal()[1]


def cmd_run(args: argparse.Namespace) -> int:
    import logging
    logging.getLogger("textual_image").setLevel(logging.CRITICAL)  # terminal probes that time out are normal
    from .app import Viewfinder
    if hasattr(signal, "SIGHUP"):
        # a closed terminal must take the pane with it, even if SIGHUP was ignored by a parent (nohup, some launchers)
        signal.signal(signal.SIGHUP, lambda *_: os._exit(0))
    Viewfinder(protocol=resolve_protocol(args.protocol)).run()
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    accepted = state.push(args.files, cwd=os.getcwd(), agent="cli")
    missing = [f for f in args.files if state.normalize(f) not in accepted]
    for m in missing:
        print(f"vf show: skipped (missing or not media): {m}", file=sys.stderr)
    if accepted and not _pane_running() and sys.stderr.isatty():
        print("vf show: queued, but no pane is running. Start one with: vf open", file=sys.stderr)
    return 0 if accepted else 1


def _pane_running() -> bool:
    try:
        pid = int(state.PIDFILE.read_text().strip())
        os.kill(pid, 0)
        return True
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return False


def _pane_argv(protocol: str) -> list[str]:
    vf = shutil.which("vf")
    base = [vf] if vf else [sys.executable, "-m", "viewfinder"]
    return [*base, "--protocol", resolve_protocol(protocol)]


def cmd_open(args: argparse.Namespace) -> int:
    """Split the current terminal and start the pane in the new half."""
    if _pane_running():
        if args.files:
            return cmd_show(args)
        print("vf: pane already running")
        return 0
    argv = _pane_argv(args.protocol)
    size = args.size
    env = os.environ
    if env.get("TMUX"):
        cmd = ["tmux", "split-window", "-h", "-l", f"{int(size * 100)}%", shlex.join(argv)]
    elif env.get("WT_SESSION") and shutil.which("wt.exe"):
        inner = argv
        if env.get("WSL_DISTRO_NAME"):
            inner = ["wsl.exe", "-d", env["WSL_DISTRO_NAME"], "--cd", os.getcwd(), "--", "bash", "-lc", shlex.join(argv)]
        cmd = ["wt.exe", "-w", "0", "split-pane", "-V", "--size", str(size), "--title", "Viewfinder", *inner]
    elif env.get("TERM_PROGRAM") == "WezTerm" and shutil.which("wezterm") and not env.get("WSL_DISTRO_NAME"):
        cmd = ["wezterm", "cli", "split-pane", "--right", "--percent", str(int(size * 100)), "--", *argv]
    elif env.get("KITTY_WINDOW_ID") and shutil.which("kitten"):
        cmd = ["kitten", "@", "launch", "--location=vsplit", "--cwd=current", *argv]
    else:
        print("vf open: this terminal can't be split automatically (supported: Windows Terminal, tmux, WezTerm, kitty).\n"
              "         Open a split yourself and run: vf", file=sys.stderr)
        return 2
    try:
        subprocess.run(cmd, check=True)
    except (subprocess.CalledProcessError, OSError) as e:
        print(f"vf open: split failed: {e}", file=sys.stderr)
        return 2
    if args.files:
        return cmd_show(args)
    return 0


def cmd_hook(args: argparse.Namespace) -> int:
    """Agent hook adapters. Read JSON on stdin, push any media path found. Always exit 0."""
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return 0
    paths: list[str] = []
    if args.agent == "claude":
        ti = payload.get("tool_input") or {}
        for key in ("file_path", "path"):
            if isinstance(ti.get(key), str):
                paths.append(ti[key])
    elif args.agent == "codex":
        # Codex CLI hook payloads carry the tool call; accept the same shapes.
        ti = payload.get("tool_input") or payload.get("input") or {}
        for key in ("file_path", "path"):
            if isinstance(ti.get(key), str):
                paths.append(ti[key])
    if paths:
        state.push(paths, cwd=payload.get("cwd") or os.getcwd(), session=str(payload.get("session_id") or ""), agent=args.agent)
    return 0


CLAUDE_SETTINGS = "~/.claude/settings.json"
HOOK_MATCHER = "Read|mcp__filesystem__read_media_file"


def _is_vf_hook(h: object) -> bool:
    return isinstance(h, dict) and "hook claude" in str(h.get("command", "")) and "vf" in str(h.get("command", ""))


def claude_hook_installed(settings: "Path") -> bool:
    try:
        data = json.loads(settings.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    post = (data.get("hooks") or {}).get("PostToolUse") or []
    return any(_is_vf_hook(h) for e in post if isinstance(e, dict) for h in e.get("hooks") or [])


def _write_json(path: "Path", data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".vf-tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


def cmd_setup(args: argparse.Namespace) -> int:
    """Install (or remove) the agent hook in the user's Claude Code settings."""
    from pathlib import Path
    settings = Path(CLAUDE_SETTINGS).expanduser()
    try:
        data = json.loads(settings.read_text()) if settings.exists() else {}
    except json.JSONDecodeError as e:
        print(f"vf setup: {settings} is not valid JSON ({e}); fix it first", file=sys.stderr)
        return 2
    if not isinstance(data, dict):
        print(f"vf setup: {settings} is not a JSON object; fix it first", file=sys.stderr)
        return 2
    hooks = data.get("hooks") if isinstance(data.get("hooks"), dict) else {}
    post = hooks.get("PostToolUse") if isinstance(hooks.get("PostToolUse"), list) else []

    if args.remove:
        kept = []
        for e in post:
            if isinstance(e, dict) and isinstance(e.get("hooks"), list):
                inner = [h for h in e["hooks"] if not _is_vf_hook(h)]
                if not inner:
                    continue
                e = {**e, "hooks": inner}
            kept.append(e)
        if len(kept) == len(post) and kept == post:
            print(f"vf setup: no Viewfinder hook in {settings}")
            return 0
        if args.dry_run:
            print(f"vf setup: would remove the Viewfinder hook from {settings}")
            return 0
        if kept:
            hooks["PostToolUse"] = kept
        else:
            hooks.pop("PostToolUse", None)
        if hooks:
            data["hooks"] = hooks
        else:
            data.pop("hooks", None)
        _write_json(settings, data)
        print(f"vf setup: removed the Viewfinder hook from {settings}")
        return 0

    if claude_hook_installed(settings):
        print(f"vf setup: hook already present in {settings}")
        return 0
    vf = shutil.which("vf") or "vf"
    entry = {"matcher": HOOK_MATCHER,
             "hooks": [{"type": "command", "command": f"{vf} hook claude 2>/dev/null || true", "timeout": 5, "async": True}]}
    if args.dry_run:
        print(f"vf setup: would add this PostToolUse hook to {settings}:")
        print(json.dumps(entry, indent=2))
        return 0
    data["hooks"] = hooks
    hooks["PostToolUse"] = [*post, entry]
    settings.parent.mkdir(parents=True, exist_ok=True)
    _write_json(settings, data)
    print(f"vf setup: added the Claude Code hook to {settings}. Restart Claude Code (or open /hooks once) to load it.")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Check everything Viewfinder depends on and say how to fix what's missing."""
    from pathlib import Path
    color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

    def paint(code: str, text: str) -> str:
        return f"\x1b[{code}m{text}\x1b[0m" if color else text

    marks = {"ok": paint("38;2;212;255;58", "✓"), "warn": paint("38;2;255;122;26", "!"),
             "fail": paint("38;2;255;43;214", "✗"), "info": paint("2", "·")}
    failed = 0

    def line(status: str, label: str, detail: str = "", fix: str = "") -> None:
        nonlocal failed
        failed += status == "fail"
        print(f"  {marks[status]} {label:<16}{paint('2', detail) if detail else ''}")
        if fix:
            print(f"    {'':<16}{paint('38;2;125;249;255', fix)}")

    if sys.platform == "darwin":
        pkg = "brew install {}"
    elif sys.platform.startswith("win"):
        pkg = "winget install {}"
    else:
        pkg = "sudo apt install {}  (or your distro's package manager)"

    print(paint("1", f"viewfinder {__version__}") + paint("2", f"  ·  python {sys.version.split()[0]}  ·  {sys.platform}"))
    print()
    for tool, need in (("ffmpeg", "fail"), ("ffprobe", "fail")):
        path = shutil.which(tool)
        if path:
            line("ok", tool, path)
        else:
            line(need, tool, "not found: video contact sheets and audio waveforms are off", pkg.format("ffmpeg"))
    mpv = shutil.which("mpv")
    if mpv:
        line("ok", "mpv", mpv)
    else:
        line("warn", "mpv", "not found: space falls back to a 10 fps preview, audio can't play", pkg.format("mpv"))

    name, proto = detect_terminal()
    label = {"sixel": "Sixel", "tgp": "Kitty graphics", "auto": "probed at startup"}[proto]
    if proto == "auto":
        line("warn", "terminal", f"{name}: not recognized, graphics {label}",
             "if images look blocky, try: vf --protocol sixel   (or tgp for kitty/Ghostty)")
    else:
        line("ok", "terminal", f"{name}: {label}")
    if os.environ.get("TMUX"):
        line("warn", "tmux", "graphics inside tmux need passthrough",
             "tmux 3.4+: set -g allow-passthrough on   (Sixel also needs a sixel-enabled build)")

    settings = Path(CLAUDE_SETTINGS).expanduser()
    if claude_hook_installed(settings):
        line("ok", "claude hook", str(settings))
    else:
        line("warn", "claude hook", "not installed: agent reads won't appear automatically", "vf setup claude")

    try:
        state.ensure_dirs()
        probe_file = state.HOME / ".doctor"
        probe_file.write_text("ok")
        probe_file.unlink()
        line("ok", "state dir", str(state.HOME))
    except OSError as e:
        line("fail", "state dir", f"{state.HOME} is not writable ({e})", "set VIEWFINDER_HOME to a writable folder")

    line("info", "pane", "running" if _pane_running() else "not running (start it with: vf open, or vf in a split)")
    print()
    print(paint("2", "all good") if not failed else paint("38;2;255;43;214", f"{failed} problem{'s' * (failed != 1)} to fix"))
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="vf", description="Viewfinder: a media pane beside your AI coding agent.")
    ap.add_argument("--version", action="version", version=f"viewfinder {__version__}")
    ap.add_argument("--protocol", choices=["auto", "sixel", "tgp", "halfcell", "unicode"], default="auto",
                    help="image protocol for the pane (default: auto-detect)")
    sub = ap.add_subparsers(dest="cmd")
    s = sub.add_parser("show", help="push files to the pane"); s.add_argument("files", nargs="+")
    o = sub.add_parser("open", help="split the current terminal and start the pane")
    o.add_argument("files", nargs="*"); o.add_argument("--size", type=float, default=0.42)
    o.add_argument("--protocol", choices=["auto", "sixel", "tgp", "halfcell", "unicode"], default="auto")
    h = sub.add_parser("hook", help="agent hook adapter (reads JSON on stdin)"); h.add_argument("agent", choices=["claude", "codex"])
    st = sub.add_parser("setup", help="install the Claude Code hook into ~/.claude/settings.json")
    st.add_argument("agent", choices=["claude"])
    st.add_argument("--dry-run", action="store_true", help="print what would change, write nothing")
    st.add_argument("--remove", action="store_true", help="remove the Viewfinder hook")
    sub.add_parser("doctor", help="check ffmpeg, mpv, terminal graphics and the agent hook")
    args = ap.parse_args(argv)
    cmds = {"show": cmd_show, "open": cmd_open, "hook": cmd_hook, "setup": cmd_setup, "doctor": cmd_doctor}
    return cmds.get(args.cmd, cmd_run)(args)


if __name__ == "__main__":
    sys.exit(main())
