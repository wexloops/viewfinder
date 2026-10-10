import io
import json
from pathlib import Path

from viewfinder import cli, state


def settings_path() -> Path:
    return Path("~/.claude/settings.json").expanduser()


def test_show_rc(media, capsys):
    assert cli.main(["show", str(media["a"])]) == 0
    assert cli.main(["show", str(media["notes"])]) == 1
    assert "skipped" in capsys.readouterr().err


def test_hook_claude(media, monkeypatch):
    payload = {"tool_input": {"file_path": str(media["b"])}, "cwd": "/w/proj", "session_id": "s123456"}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    assert cli.main(["hook", "claude"]) == 0
    assert state.read_queue() == media["b"]
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert cli.main(["hook", "claude"]) == 0


def test_setup_dry_run_writes_nothing(capsys):
    assert cli.main(["setup", "claude", "--dry-run"]) == 0
    assert not settings_path().exists()
    assert "would add" in capsys.readouterr().out


def test_setup_install_idempotent_and_remove(capsys):
    s = settings_path()
    s.parent.mkdir(parents=True)
    other = {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo hi"}]}
    s.write_text(json.dumps({"model": "x", "hooks": {"PostToolUse": [other]}}))
    assert cli.main(["setup", "claude"]) == 0
    assert cli.main(["setup", "claude"]) == 0
    data = json.loads(s.read_text())
    assert data["model"] == "x" and len(data["hooks"]["PostToolUse"]) == 2
    assert cli.claude_hook_installed(s)
    assert cli.main(["setup", "claude", "--remove"]) == 0
    data = json.loads(s.read_text())
    assert data["hooks"]["PostToolUse"] == [other]
    assert not cli.claude_hook_installed(s)


def test_setup_refuses_invalid_json(capsys):
    s = settings_path()
    s.parent.mkdir(parents=True)
    s.write_text("{oops")
    assert cli.main(["setup", "claude"]) == 2
    assert s.read_text() == "{oops"


def test_doctor_reports(monkeypatch, capsys):
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    rc = cli.main(["doctor"])
    out = capsys.readouterr().out
    assert rc == 1 and "ffmpeg" in out and "vf setup claude" in out


def test_detect_terminal(monkeypatch):
    for k in ("KITTY_WINDOW_ID", "TERM_PROGRAM", "GHOSTTY_RESOURCES_DIR", "WT_SESSION", "WEZTERM_PANE"):
        monkeypatch.delenv(k, raising=False)
    assert cli.detect_terminal()[1] == "auto"
    monkeypatch.setenv("WT_SESSION", "1")
    assert cli.detect_terminal() == ("Windows Terminal", "sixel")
    assert cli.resolve_protocol("halfcell") == "halfcell"
