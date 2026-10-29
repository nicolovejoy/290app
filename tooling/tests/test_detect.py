import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "review"))
from detect import MARKER, detect_assignments, run  # noqa: E402

BOT_ID = 42


def test_none():
    assert detect_assignments(["README.md", "src/x.py"]) == []


def test_one_with_nested_files():
    paths = ["assignments/01-warmup/solution.py", "assignments/01-warmup/tests/test_a.py"]
    assert detect_assignments(paths) == ["assignments/01-warmup"]


def test_two_sorted_unique():
    paths = ["assignments/02-lists/a.py", "assignments/01-warmup/a.py", "assignments/02-lists/b.py"]
    assert detect_assignments(paths) == ["assignments/01-warmup", "assignments/02-lists"]


def test_non_matching_ignored():
    paths = ["assignments/README.md", "assignments/x/a.py", "assignments/1-a/a.py",
             "assignments/01-Warmup/a.py", "assignments/01-warmup"]
    assert detect_assignments(paths) == []


def test_run_one_writes_env_no_note(tmp_path):
    mr = MagicMock()
    mr.notes.list.return_value = []
    out = tmp_path / "detect.env"
    run(["assignments/01-warmup/solution.py"], mr, out, bot_id=BOT_ID)
    assert out.read_text() == "ASSIGNMENT=assignments/01-warmup\n"
    mr.notes.create.assert_not_called()


def test_run_none_posts_note_and_empty(tmp_path):
    mr = MagicMock()
    mr.notes.list.return_value = []
    out = tmp_path / "detect.env"
    run(["README.md"], mr, out, bot_id=BOT_ID)
    assert out.read_text() == "ASSIGNMENT=none\n"
    body = mr.notes.create.call_args[0][0]["body"]
    assert "No assignment detected" in body


def test_run_many_posts_split_note_and_empty(tmp_path):
    mr = MagicMock()
    mr.notes.list.return_value = []
    out = tmp_path / "detect.env"
    run(["assignments/01-a/x.py", "assignments/02-b/x.py"], mr, out, bot_id=BOT_ID)
    assert out.read_text() == "ASSIGNMENT=none\n"
    body = mr.notes.create.call_args[0][0]["body"]
    assert "one merge request per assignment" in body
    assert "assignments/01-a" in body and "assignments/02-b" in body


def test_note_body_starts_with_marker(tmp_path):
    mr = MagicMock()
    mr.notes.list.return_value = []
    run(["README.md"], mr, tmp_path / "detect.env", bot_id=BOT_ID)
    assert mr.notes.create.call_args[0][0]["body"].startswith(MARKER + "\n")
    assert MARKER == "<!-- claude-detect -->"


def test_existing_marker_note_is_edited_not_duplicated(tmp_path):
    other = MagicMock(body="a human comment")
    ours = MagicMock(body=f"{MARKER}\nold text", author={"id": BOT_ID})
    mr = MagicMock()
    mr.notes.list.return_value = [other, ours]
    run(["README.md"], mr, tmp_path / "detect.env", bot_id=BOT_ID)
    mr.notes.create.assert_not_called()
    assert "No assignment detected" in ours.body
    assert ours.body.startswith(MARKER)
    ours.save.assert_called_once()
    other.save.assert_not_called()


def test_one_after_none_edits_note_to_detected(tmp_path):
    ours = MagicMock(body=f"{MARKER}\nNo assignment detected", author={"id": BOT_ID})
    mr = MagicMock()
    mr.notes.list.return_value = [ours]
    run(["assignments/01-warmup/a.py"], mr, tmp_path / "detect.env", bot_id=BOT_ID)
    assert "assignments/01-warmup" in ours.body
    assert ours.body.startswith(MARKER)
    ours.save.assert_called_once()
    mr.notes.create.assert_not_called()


def test_marker_note_from_non_bot_is_ignored(tmp_path):
    student = MagicMock(body=f"{MARKER}\nspoofed", author={"id": 7})
    mr = MagicMock()
    mr.notes.list.return_value = [student]
    run(["README.md"], mr, tmp_path / "detect.env", bot_id=BOT_ID)
    student.save.assert_not_called()
    mr.notes.create.assert_called_once()


def test_env_written_before_note(tmp_path):
    out = tmp_path / "detect.env"
    mr = MagicMock()

    def list_notes(**kwargs):
        assert out.exists()
        return []

    mr.notes.list.side_effect = list_notes
    run(["README.md"], mr, out, bot_id=BOT_ID)
    mr.notes.create.assert_called_once()
