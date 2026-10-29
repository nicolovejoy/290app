import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / "review"))
from detect import detect_assignments, run  # noqa: E402


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
    out = tmp_path / "detect.env"
    run(["assignments/01-warmup/solution.py"], mr, out)
    assert out.read_text() == "ASSIGNMENT=assignments/01-warmup\n"
    mr.notes.create.assert_not_called()


def test_run_none_posts_note_and_empty(tmp_path):
    mr = MagicMock()
    out = tmp_path / "detect.env"
    run(["README.md"], mr, out)
    assert out.read_text() == "ASSIGNMENT=\n"
    body = mr.notes.create.call_args[0][0]["body"]
    assert "No assignment detected" in body


def test_run_many_posts_split_note_and_empty(tmp_path):
    mr = MagicMock()
    out = tmp_path / "detect.env"
    run(["assignments/01-a/x.py", "assignments/02-b/x.py"], mr, out)
    assert out.read_text() == "ASSIGNMENT=\n"
    body = mr.notes.create.call_args[0][0]["body"]
    assert "one merge request per assignment" in body
    assert "assignments/01-a" in body and "assignments/02-b" in body
