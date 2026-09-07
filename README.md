# Viewfinder

**by STRANGELOOP**

A media pane beside your AI coding agent. When the agent reads or renders an image, it shows up here. Sharp images via Sixel or Kitty graphics, real-framerate video via mpv, a tree of the file's neighborhood, and a history grouped by project.

Why: Claude Code (and Codex) repaint the whole terminal, so inline images never survive. Viewfinder is a pane the agent does not own.

## Install

    pipx install viewfinder      # or: uv tool install viewfinder
    # optional, for video playback:  brew install mpv | apt install mpv

## Use

    vf open            # split the current terminal and start the pane
    vf show FILE...    # push files to it, from any shell, script, or agent
    vf                 # run the pane in the current terminal

Claude Code hook (in `~/.claude/settings.json`):

    "hooks": {"PostToolUse": [{"matcher": "Read", "hooks": [{"type": "command", "command": "vf hook claude", "async": true}]}]}

## Keys

| key | action |
|---|---|
| space | play video with mpv (q returns) |
| n / p | next / previous in history |
| h | toggle history panel (grouped by project) |
| y / Y | copy path / native (Windows) path |
| o | open in the OS viewer |
| t | show or hide the bottom panel |
| f | fullscreen the viewer |
| backspace | tree: up one directory |
| q | quit |

## Terminals

Tested: Windows Terminal (Sixel) over WSL, WezTerm. Should work: kitty, Ghostty (Kitty graphics), iTerm2 (Sixel). Falls back to half-cell blocks elsewhere.

MIT.
