import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import gitlab
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "review"))
from review import (MARKER, MAX_DIFF_BYTES, MAX_SESSION_BYTES, append_result,  # noqa: E402
                    build_prompt, call_claude, extract_rubric, find_review_note,
                    is_debounced, parse_junit, post_note, render_note, results_location,
                    truncate, REVIEW_EFFORT, MAX_TOKENS, SYSTEM_PROMPT)
from schema import build_schema  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def sample_review(**over):
    r = {
        "summary": "Solid start.",
        "comments": [
            {"file": "solution.py", "line": 3, "severity": "issue", "text": "Off by one."},
            {"file": "solution.py", "line": 9, "severity": "suggest", "text": "Use Counter."},
            {"file": "solution.py", "line": 12, "severity": "info", "text": "Nice naming."},
        ],
        "rubric_scores": {"correctness": {"score": 1.0, "max": 1.0, "reason": "all pass"}},
        "recommended_score": 0.85,
        "needs_human": False,
        "needs_human_reason": None,
        "integrity_flag": False,
    }
    r.update(over)
    return r


# --- pure helpers -----------------------------------------------------

def test_truncate_short_text_untouched():
    assert truncate("hello", 10) == ("hello", False)


def test_truncate_cuts_at_byte_limit():
    text, cut = truncate("a" * 100, 10)
    assert text == "a" * 10 and cut is True


def test_truncate_does_not_split_multibyte_char():
    text, cut = truncate("é" * 10, 5)  # 2 bytes each; 5 bytes lands mid-character
    assert cut and text == "é" * 2


def test_parse_junit_counts_failures_errors_skips(tmp_path):
    xml = tmp_path / "junit.xml"
    xml.write_text("""<testsuites><testsuite>
      <testcase name="a"/><testcase name="b"><failure/></testcase>
      <testcase name="c"><error/></testcase><testcase name="d"><skipped/></testcase>
    </testsuite></testsuites>""")
    assert parse_junit(xml) == (1, 4)


def test_parse_junit_missing_file(tmp_path):
    assert parse_junit(tmp_path / "nope.xml") == (0, 0)


def test_parse_junit_garbage(tmp_path):
    xml = tmp_path / "junit.xml"
    xml.write_text("not xml")
    assert parse_junit(xml) == (0, 0)


def test_extract_rubric_from_sample_readme():
    rubric = extract_rubric((FIXTURES / "warmup-README.md").read_text())
    assert [c["id"] for c in rubric] == [
        "correctness", "data_structure_choice", "code_quality", "process"]
    assert rubric[1]["weight"] == 0.2


def test_extract_rubric_missing_block_raises():
    with pytest.raises(ValueError):
        extract_rubric("# no rubric here")


def test_schema_has_one_key_per_rubric_id():
    schema = build_schema(["correctness", "process"])
    scores = schema["properties"]["rubric_scores"]
    assert scores["required"] == ["correctness", "process"]
    assert set(schema["required"]) == {
        "summary", "comments", "rubric_scores", "recommended_score",
        "needs_human", "needs_human_reason", "integrity_flag"}


# --- prompt -----------------------------------------------------------

def test_build_prompt_caches_system_and_rubric_not_diff():
    rubric = [{"id": "correctness"}]
    system, messages = build_prompt(rubric, "DIFF", (3, 4), "LOG", diff_cut=False, session_cut=False)
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert "recommended" in system[0]["text"].lower()
    blocks = messages[0]["content"]
    assert "correctness" in blocks[0]["text"] and blocks[0]["cache_control"]
    assert "cache_control" not in blocks[1]
    assert "DIFF" in blocks[1]["text"] and "3 of 4" in blocks[1]["text"] and "LOG" in blocks[1]["text"]


def test_prompt_asks_for_own_correctness_judgment():
    # flag.py compares correctness with the pass rate; copying it would make that check a no-op.
    assert "passed/total" not in SYSTEM_PROMPT
    assert "do not copy the test pass rate" in SYSTEM_PROMPT.lower()


def test_build_prompt_notes_truncation_and_missing_log():
    _, messages = build_prompt([], "D", (0, 0), "LOG", diff_cut=True, session_cut=True)
    text = messages[0]["content"][1]["text"]
    assert "diff was truncated" in text and "session log was truncated" in text
    _, messages = build_prompt([], "D", (0, 0), "", diff_cut=False, session_cut=False)
    assert "No session log" in messages[0]["content"][1]["text"]


# --- note rendering ---------------------------------------------------

def test_render_note_has_marker_and_recommendation_wording():
    note = render_note(sample_review(), (9, 10), diff_cut=False)
    assert note.startswith(MARKER)
    assert "recommendation, not a grade" in note
    assert "`solution.py:3` — issue — Off by one." in note
    assert "| correctness | 1.00 / 1.0 | all pass |" in note
    assert "0.85" in note
    assert "Next steps" in note and "Off by one." in note and "Nice naming." not in note.split("Next steps")[1]
    assert "truncated" not in note


def test_render_note_mentions_truncation_and_human():
    note = render_note(sample_review(needs_human=True, needs_human_reason="odd"), (0, 0),
                       diff_cut=True)
    assert "truncated" in note and "TA" in note and "odd" in note


# --- gitlab notes -----------------------------------------------------

BOT_ID = 7


def make_note(body, minutes_ago, author_id=BOT_ID):
    ts = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return SimpleNamespace(body=body, updated_at=ts.isoformat().replace("+00:00", "Z"), save=MagicMock(),
                           author={"id": author_id})


def test_find_review_note_ignores_other_notes():
    mr = MagicMock()
    mine = make_note(f"{MARKER}\nhi", 1)
    mr.notes.list.return_value = [make_note("<!-- claude-detect -->\nx", 1), mine]
    assert find_review_note(mr, BOT_ID) is mine


def test_find_review_note_ignores_marker_from_non_bot():
    mr = MagicMock()
    forged = make_note(f"{MARKER}\nfake", 1, author_id=99)
    mine = make_note(f"{MARKER}\nreal", 30)
    mr.notes.list.return_value = [forged, mine]
    assert find_review_note(mr, BOT_ID) is mine
    mr.notes.list.return_value = [forged]
    assert find_review_note(mr, BOT_ID) is None


def test_find_review_note_none():
    mr = MagicMock()
    mr.notes.list.return_value = []
    assert find_review_note(mr, BOT_ID) is None


def test_debounce_recent_note_skips():
    assert is_debounced(make_note(MARKER, 3)) is True


def test_debounce_old_note_or_none_runs():
    assert is_debounced(make_note(MARKER, 11)) is False
    assert is_debounced(None) is False


def test_post_note_creates_when_absent():
    mr = MagicMock()
    post_note(mr, None, "body")
    mr.notes.create.assert_called_once_with({"body": "body"})


def test_post_note_edits_when_present():
    mr = MagicMock()
    existing = make_note(MARKER, 20)
    post_note(mr, existing, "new body")
    assert existing.body == "new body"
    existing.save.assert_called_once()
    mr.notes.create.assert_not_called()


# --- results ----------------------------------------------------------

def test_results_location_uses_slug_only():
    path, message = results_location("assignments/01-warmup", "abc123", "5")
    assert path == "01-warmup/abc123.json"
    assert message == "review: abc123 01-warmup !5"


def test_append_result_creates_missing_file():
    project = MagicMock(default_branch="main")
    project.files.get.side_effect = gitlab.GitlabGetError("nope", 404)
    append_result(project, "assignments/01-warmup/alice.json", {"a": 1}, "msg")
    data = project.files.create.call_args[0][0]
    assert data["file_path"] == "assignments/01-warmup/alice.json"
    assert data["branch"] == "main" and data["commit_message"] == "msg"
    assert json.loads(data["content"]) == [{"a": 1}]


def test_append_result_updates_existing_array():
    project = MagicMock(default_branch="main")
    f = project.files.get.return_value
    f.decode.return_value = json.dumps([{"old": 1}]).encode()
    append_result(project, "p.json", {"new": 2}, "msg")
    assert json.loads(f.content) == [{"old": 1}, {"new": 2}]
    f.save.assert_called_once_with(branch="main", commit_message="msg")
    project.files.create.assert_not_called()


def test_append_result_other_errors_propagate():
    project = MagicMock(default_branch="main")
    project.files.get.side_effect = gitlab.GitlabGetError("boom", 500)
    with pytest.raises(gitlab.GitlabGetError):
        append_result(project, "p.json", {}, "m")


# --- Claude call (mocked client) -------------------------------------

def fake_client(text, stop_reason="end_turn"):
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(
        stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)])
    return client


def test_call_claude_returns_parsed_review_and_sends_schema():
    client = fake_client(json.dumps(sample_review()))
    schema = build_schema(["correctness"])
    out = call_claude(client, [{"type": "text", "text": "sys"}], [], schema)
    assert out["recommended_score"] == 0.85
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["model"] == "claude-sonnet-5-5"
    assert kwargs["max_tokens"] == MAX_TOKENS == 16000
    assert kwargs["output_config"]["effort"] == REVIEW_EFFORT == "medium"
    assert "thinking" not in kwargs  # Sonnet 5.5: disabled is a 400; effort is the control
    assert kwargs["output_config"]["format"] == {"type": "json_schema", "schema": schema}


def test_call_claude_bad_json_raises():
    with pytest.raises(ValueError):
        call_claude(fake_client("{nope"), [], [], build_schema(["correctness"]))


def test_call_claude_missing_keys_raises():
    with pytest.raises(ValueError, match="missing"):
        call_claude(fake_client(json.dumps({"summary": "x"})), [], [], build_schema([]))


def test_call_claude_missing_rubric_id_raises():
    with pytest.raises(ValueError, match="rubric"):
        call_claude(fake_client(json.dumps(sample_review())), [], [],
                    build_schema(["correctness", "process"]))


def test_call_claude_cut_off_raises():
    with pytest.raises(ValueError, match="max_tokens"):
        call_claude(fake_client("{}", stop_reason="max_tokens"), [], [], build_schema([]))


def test_limits_match_brief():
    assert MAX_DIFF_BYTES == 60_000 and MAX_SESSION_BYTES == 20_000
