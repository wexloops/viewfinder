"""vf: run the pane, push files to it, open it beside the current terminal, agent hooks."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

from . import __version__, state


def resolve_protocol(requested: str) -> str:
    """'auto' = trust the terminal we can identify from the environment; probe only as a last resort.
    The library's own probe waits 100ms for a reply that Windows Terminal over WSL often misses."""
    if requested != "auto":
        return requested
    env = os.environ
    if env.get("KITTY_WINDOW_ID") or env.get("TERM_PROGRAM") == "ghostty" or env.get("GHOSTTY_RESOURCES_DIR"):
        return "tgp"
    if env.get("WT_SESSION") or env.get("TERM_PROGRAM") in ("WezTerm", "iTerm.app") or env.get("WEZTERM_PANE"):
        return "sixel"
    return "auto"


def cmd_run(args: argparse.Namespace) -> int:
    import logging
    logging.getLogger("textual_image").setLevel(logging.CRITICAL)  # terminal probes that time out are normal
    from .app import Viewfinder
    Viewfinder(protocol=resolve_protocol(args.protocol)).run()
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    accepted = state.push(args.files, cwd=os.getcwd(), agent="cli")
    missing = [f for f in args.files if state.normalize(f) not in accepted]
    for m in missing:
        print(f"vf show: skipped (missing or not media): {m}", file=sys.stderr)
    return 0 if accepted else 1


def _pane_running() -> bool:
    try:
        pid = int(state.PIDFILE.read_text().strip())
        os.kill(pid, 0)
        return True
    except (FileNotFoundError, ValueError, ProcessLookupError, PermissionError):
        return False


def cmd_open(args: argparse.Namespace) -> int:
    """Split the current terminal and start the pane in the new half."""
    if _pane_running():
        if args.files:
            return cmd_show(args)
        print("vf: pane already running")
        return 0
    vf = shutil.which("vf") or f"{sys.executable} -m viewfinder"
    run = f"{vf} --protocol {resolve_protocol(args.protocol)}"
    size = args.size
    env = os.environ
    if env.get("TMUX"):
        cmd = ["tmux", "split-window", "-h", "-l", f"{int(size * 100)}%", run]
    elif env.get("WT_SESSION") and shutil.which("wt.exe"):
        inner = run
        if env.get("WSL_DISTRO_NAME"):
            inner = f"wsl.exe -d {env['WSL_DISTRO_NAME']} --cd {os.getcwd()} -- bash -lc '{run}'"
        cmd = ["wt.exe", "-w", "0", "split-pane", "-V", "--size", str(size), "--title", "Viewfinder", *inner.split()]
    elif env.get("TERM_PROGRAM") == "WezTerm" and shutil.which("wezterm") and not env.get("WSL_DISTRO_NAME"):
        cmd = ["wezterm", "cli", "split-pane", "--right", "--percent", str(int(size * 100)), "--", *run.split()]
    elif env.get("KITTY_WINDOW_ID") and shutil.which("kitten"):
        cmd = ["kitten", "@", "launch", "--location=vsplit", "--cwd=current", *run.split()]
    else:
        print("vf open: don't know how to split this terminal. Open a split yourself and run: vf", file=sys.stderr)
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


def cmd_setup(args: argparse.Namespace) -> int:
    """Install the agent hook into the user's settings."""
    from pathlib import Path
    if args.agent != "claude":
        print("vf setup: only 'claude' is supported for now", file=sys.stderr)
        return 2
    settings = Path.home() / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = json.loads(settings.read_text()) if settings.exists() else {}
    except json.JSONDecodeError as e:
        print(f"vf setup: {settings} is not valid JSON ({e}); fix it first", file=sys.stderr)
        return 2
    vf = shutil.which("vf") or "vf"
    entry = {"matcher": "Read|mcp__filesystem__read_media_file",
             "hooks": [{"type": "command", "command": f"{vf} hook claude 2>/dev/null || true", "timeout": 5, "async": True}]}
    post = data.setdefault("hooks", {}).setdefault("PostToolUse", [])
    if any("hook claude" in json.dumps(e) for e in post):
        print(f"vf setup: hook already present in {settings}")
        return 0
    post.append(entry)
    settings.write_text(json.dumps(data, indent=2) + "\n")
    print(f"vf setup: added Claude Code hook to {settings}. Restart Claude Code or open /hooks once.")
    return 0


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
    st = sub.add_parser("setup", help="install the agent hook"); st.add_argument("agent", choices=["claude"])
    args = ap.parse_args(argv)
    return {"show": cmd_show, "open": cmd_open, "hook": cmd_hook, "setup": cmd_setup}.get(args.cmd, cmd_run)(args)


if __name__ == "__main__":
    sys.exit(main())
