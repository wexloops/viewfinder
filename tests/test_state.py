import json

from viewfinder import state


def test_kind(tmp_path):
    assert state.kind(tmp_path / "x.PNG") == "image"
    assert state.kind(tmp_path / "x.mov") == "video"
    assert state.kind(tmp_path / "x.flac") == "audio"
    assert state.kind(tmp_path / "x.txt") == ""


def test_push_filters_and_records(media):
    accepted = state.push([str(media["a"]), str(media["notes"]), "/nope/missing.png"],
                          cwd="/work/projA", session="abcdef123", agent="claude")
    assert accepted == [media["a"]]
    assert state.read_queue() == media["a"]
    (entry,) = state.history_entries()
    assert entry.project == "projA" and entry.agent == "claude" and entry.session_tag == "abcdef"


def test_push_resolves_relative_to_agent_cwd(media):
    accepted = state.push(["a.png"], cwd=str(media["dir"]))
    assert accepted == [media["a"]]


def test_history_skips_bad_lines(media):
    state.ensure_dirs()
    state.HISTORY.write_text("garbage\n\n" + json.dumps({"path": str(media["a"]), "ts": 1}) + "\n{\"nopath\": 1}\n")
    assert state.history() == [media["a"]]


def test_signature_changes_on_push(media):
    before = state.signature()
    state.push([str(media["a"])])
    assert state.signature() != before
