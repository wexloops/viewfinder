# Viewfinder

**A media pane beside your AI coding agent.** When Claude Code reads an image, it shows up here, sharp, in your terminal. Video plays at full frame rate. Audio gets a waveform. Nothing to type.

*VIEWFINDER by STRANGELOOP*

```
┌──────────────────────────────┬─────────────────────────┐
│                              │ frame_042.png     7/12  │
│   claude code                │ 1920x1080  2.1M         │
│                              │                         │
│   > read the render and      │   ┌───────────────┐     │
│     tell me what's wrong     │   │               │     │
│                              │   │   the image   │     │
│   Reading frame_042.png…     │   │               │     │
│                              │   └───────────────┘     │
│                              │ ────────────────────────│
│                              │ ..  renders/            │
│                              │ ▶ take_03.mp4           │
│                              │ █ frame_042.png         │
│                              │   frame_041.png         │
│                              │ space play  n/p  h  y  o│
└──────────────────────────────┴─────────────────────────┘
```

## Why

Claude Code and Codex own the whole terminal and repaint it constantly. Any image drawn into their screen is wiped within a second, so inline previews are impossible there ([anthropics/claude-code #36476](https://github.com/anthropics/claude-code/issues/36476)). Viewfinder is a pane the agent does not own. A hook tells it what the agent just looked at.

## Install

Requires Python 3.12+ and [ffmpeg](https://ffmpeg.org). [mpv](https://mpv.io) is optional but recommended for video and audio playback.

```sh
uv tool install viewfinder        # or: pipx install viewfinder
```

Until the PyPI release lands you can install straight from GitHub:

```sh
uv tool install git+https://github.com/wexloops/viewfinder
```

## Use

```sh
vf open              # split the current terminal and start the pane
vf show FILE...      # push anything to it, from any shell, script, or agent
vf setup claude      # add the Claude Code hook so reads show up automatically
vf                   # run the pane in the current terminal (any split you made yourself)
```

`vf open` knows Windows Terminal, tmux, WezTerm, and kitty. Anywhere else, make a split and run `vf`.

`vf setup claude` adds one PostToolUse hook to `~/.claude/settings.json`. It is equivalent to:

```json
"hooks": {"PostToolUse": [{"matcher": "Read|mcp__filesystem__read_media_file",
  "hooks": [{"type": "command", "command": "vf hook claude", "async": true}]}]}
```

Codex CLI: `vf hook codex` accepts the same payload shape. Experimental, untested against a live Codex hook.

## Keys

| key | action |
|---|---|
| arrows | move through the list, the viewer previews as you go |
| enter / click | open a folder, or show a file |
| space | play video or audio with mpv. ESC or q comes back |
| n / p | next / previous in history |
| h | toggle the bottom panel between folder tree and history grouped by project |
| y / Y | copy the path / the native (Windows) path |
| o | open in the OS default app |
| t | show or hide the bottom panel |
| f | fullscreen the viewer |
| backspace | tree: up one directory |
| q | quit |

## What it shows

- **Images**: png, jpg, gif, webp, bmp, tiff. Rendered with Sixel or the Kitty graphics protocol, letterboxed on black.
- **Video**: a 4x3 contact sheet on arrival, mpv playback on space.
- **Audio**: a waveform on arrival, mpv playback on space.

History is stored as JSONL in `~/.viewfinder/history.jsonl`, tagged with the agent's working directory and session, so parallel agents on different projects stay separated.

## Terminals

| terminal | images | status |
|---|---|---|
| Windows Terminal 1.22+ (incl. WSL) | Sixel | tested |
| WezTerm | Sixel | tested |
| kitty, Ghostty | Kitty graphics | should work, untested |
| iTerm2 | Sixel | should work, untested |
| others | half-cell blocks | fallback |

Force a protocol with `vf --protocol sixel|tgp|halfcell|unicode` if detection gets it wrong.

## How it works

`vf show` writes a path to `~/.viewfinder/queue`. The pane polls that file four times a second and renders whatever lands there. The Claude Code hook is a five-line adapter that extracts the file path from the tool call and calls `vf show`. That is the whole protocol, so any tool can drive Viewfinder with one line of shell.

## License

MIT. Built by [David Wexler](https://strangeloop-studios.com) at Strangeloop Studios.
