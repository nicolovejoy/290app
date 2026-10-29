import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import gitlab
import pytest

import review
from flag import LABEL
from review import (FAIL_MARKER, FAILED_NOTE, MARKER, MAX_DIFF_BYTES, MAX_SESSION_BYTES, append_result,
                    build_prompt, call_claude, extract_rubric, find_review_note,
                    is_debounced, read_session_logs, parse_junit, post_note, render_note, results_location,
                    truncate, weighted_score, REVIEW_EFFORT, MAX_TOKENS, SYSTEM_PROMPT)
from schema import build_schema

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
                       diff_cut=True, session_cut=True)
    assert "truncated" in note and "TA" in note and "odd" in note
    assert f"session log was truncated at {MAX_SESSION_BYTES} bytes" in note


def test_truncate_tail_keeps_newest_bytes():
    assert truncate("abcdef", 3, keep="tail") == ("def", True)
    assert truncate("abc", 3, keep="tail") == ("abc", False)
    text, cut = truncate("é" * 10, 5, keep="tail")  # 5 bytes starts mid-character
    assert cut and text == "é" * 2


def test_read_session_logs_handles_paths_with_spaces(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    Path(".claude-sessions").mkdir()
    Path(".claude-sessions/my log.jsonl").write_text("{}")
    monkeypatch.setattr(review, "git", lambda *a: ".claude-sessions/my log.jsonl\n")
    assert read_session_logs("base") == "{}"


def test_render_note_escapes_pipes_and_newlines_in_rubric_cells():
    rs = {"corr|ect": {"score": 1.0, "max": 1.0, "reason": "a | b\nc"}}
    note = render_note(sample_review(rubric_scores=rs), (1, 1), diff_cut=False)
    [row] = [ln for ln in note.splitlines() if ln.startswith("| corr")]
    assert row.replace("\\|", "").count("|") == 4
    assert "a \\| b c" in row


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


def test_find_review_note_skips_note_without_author_id():
    mr = MagicMock()
    odd = make_note(f"{MARKER}\nx", 1)
    odd.author = {}
    mr.notes.list.return_value = [odd]
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
    append_result(project, "01-warmup/alice.json", {"a": 1}, "msg")
    data = project.files.create.call_args[0][0]
    assert data["file_path"] == "01-warmup/alice.json"
    assert data["branch"] == "main" and data["commit_message"] == "msg"
    assert json.loads(data["content"]) == [{"a": 1}]


def test_append_result_updates_existing_array():
    project = MagicMock(default_branch="main")
    f = project.files.get.return_value
    f.decode.return_value = json.dumps([{"old": 1}]).encode()
    f.last_commit_id = "c0ffee"
    append_result(project, "p.json", {"new": 2}, "msg")
    path, data = project.files.update.call_args[0]
    assert path == "p.json" and json.loads(data["content"]) == [{"old": 1}, {"new": 2}]
    # last_commit_id makes GitLab reject our write if someone else wrote first.
    assert data["branch"] == "main" and data["commit_message"] == "msg" and data["last_commit_id"] == "c0ffee"
    project.files.create.assert_not_called()


def test_append_result_retries_after_conflict():
    project = MagicMock(default_branch="main")
    project.files.get.return_value.decode.return_value = b"[]"
    project.files.update.side_effect = [gitlab.GitlabUpdateError("stale", 400), None]
    sleep = MagicMock()
    append_result(project, "p.json", {"new": 2}, "msg", sleep=sleep)
    assert project.files.get.call_count == 2  # re-read before retrying
    assert project.files.update.call_count == 2
    sleep.assert_called_once()


def test_append_result_retries_racing_create():
    project = MagicMock(default_branch="main")
    project.files.get.side_effect = [gitlab.GitlabGetError("nope", 404), MagicMock(
        decode=MagicMock(return_value=b"[]"))]
    project.files.create.side_effect = gitlab.GitlabCreateError("exists", 400)
    append_result(project, "p.json", {"a": 1}, "msg", sleep=MagicMock())
    assert project.files.update.call_count == 1


def test_append_result_gives_up_after_three_attempts():
    project = MagicMock(default_branch="main")
    project.files.get.return_value.decode.return_value = b"[]"
    project.files.update.side_effect = gitlab.GitlabUpdateError("stale", 409)
    sleep = MagicMock()
    with pytest.raises(gitlab.GitlabUpdateError):
        append_result(project, "p.json", {}, "msg", sleep=sleep)
    assert project.files.update.call_count == 3
    assert sleep.call_count == 2


def test_append_result_does_not_retry_other_errors():
    project = MagicMock(default_branch="main")
    project.files.get.return_value.decode.return_value = b"[]"
    project.files.update.side_effect = gitlab.GitlabUpdateError("forbidden", 403)
    with pytest.raises(gitlab.GitlabUpdateError):
        append_result(project, "p.json", {}, "msg", sleep=MagicMock())
    assert project.files.update.call_count == 1


def test_append_result_other_errors_propagate():
    project = MagicMock(default_branch="main")
    project.files.get.side_effect = gitlab.GitlabGetError("boom", 500)
    with pytest.raises(gitlab.GitlabGetError):
        append_result(project, "p.json", {}, "m")


# --- recommended score (computed in code, not trusted from Claude) ---

def scores(**kw):
    return {k: {"score": v, "max": 1.0, "reason": ""} for k, v in kw.items()}


def test_weighted_score_matches_smoke_test_rubric():
    # The live smoke test: Claude reported 0.85, the weights give 0.835.
    rubric = extract_rubric((FIXTURES / "warmup-README.md").read_text())
    got = weighted_score(rubric, scores(correctness=1.0, data_structure_choice=1.0,
                                        code_quality=0.9, process=0))
    assert got == pytest.approx(0.835)


def test_weighted_score_splits_leftover_weight_evenly():
    rubric = [{"id": "a", "weight": 0.4}, {"id": "b"}, {"id": "c"}]
    # b and c share the remaining 0.6
    assert weighted_score(rubric, scores(a=1.0, b=1.0, c=0.0)) == pytest.approx(0.7)


def test_weighted_score_normalizes_by_max_and_ignores_zero_max():
    rubric = [{"id": "a", "weight": 0.5}, {"id": "b", "weight": 0.5}]
    rs = {"a": {"score": 2.0, "max": 4.0, "reason": ""},
          "b": {"score": 0.0, "max": 0.0, "reason": ""}}
    assert weighted_score(rubric, rs) == pytest.approx(0.25)


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


def test_call_claude_no_text_block_raises():
    client = MagicMock()
    client.messages.create.return_value = SimpleNamespace(
        stop_reason="end_turn", content=[SimpleNamespace(type="thinking")])
    with pytest.raises(ValueError, match="no text"):
        call_claude(client, [], [], build_schema([]))


# --- main(): failure path (fake GitLab, no network) -------------------

def fake_main_env(monkeypatch, tmp_path):
    """A CI job whose assignment README is missing, so main() fails before calling Claude."""
    monkeypatch.chdir(tmp_path)
    for k, v in {"ASSIGNMENT": "assignments/01-warmup", "CI_SERVER_URL": "https://gl.example",
                 "REVIEW_BOT_TOKEN": "t", "CI_PROJECT_ID": "1", "CI_MERGE_REQUEST_IID": "5",
                 "CI_MERGE_REQUEST_DIFF_BASE_SHA": "base", "CI_PROJECT_NAME": "abc12",
                 "CI_COMMIT_SHA": "sha1", "COURSE_GROUP": "g"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("RESULTS_PROJECT", raising=False)
    monkeypatch.setattr(review, "git", lambda *a: "")
    gl, mr, results = MagicMock(), MagicMock(labels=[]), MagicMock(default_branch="main")
    gl.user.id = BOT_ID
    mr.notes.list.return_value = []
    results.files.get.side_effect = gitlab.GitlabGetError("nope", 404)
    student = MagicMock()
    student.mergerequests.get.return_value = mr
    gl.projects.get.side_effect = lambda p: results if p == "g/staff/results" else student
    monkeypatch.setattr(review.gitlab, "Gitlab", lambda *a, **k: gl)
    return mr, results


def test_main_failure_posts_note_labels_records_and_exits_nonzero(monkeypatch, tmp_path):
    mr, results = fake_main_env(monkeypatch, tmp_path)
    with pytest.raises(SystemExit) as exit_info:
        review.main()
    assert exit_info.value.code == 1
    mr.notes.create.assert_called_once_with({"body": f"{FAIL_MARKER}\n{FAILED_NOTE}"})
    assert LABEL in mr.labels
    data = results.files.create.call_args[0][0]
    assert data["file_path"] == "01-warmup/abc12.json"
    [record] = json.loads(data["content"])
    assert record["status"] == "failed" and record["mr_iid"] == 5 and record["commit_sha"] == "sha1"
    assert record["test_passed"] == 0 and record["test_total"] == 0 and record["timestamp"]


def test_main_failure_still_exits_nonzero_when_record_write_fails(monkeypatch, tmp_path):
    mr, results = fake_main_env(monkeypatch, tmp_path)
    results.files.create.side_effect = gitlab.GitlabCreateError("forbidden", 403)
    with pytest.raises(SystemExit) as exit_info:
        review.main()
    assert exit_info.value.code == 1
    mr.notes.create.assert_called_once()
    assert LABEL in mr.labels


def test_failure_does_not_overwrite_good_review(monkeypatch, tmp_path):
    mr, _ = fake_main_env(monkeypatch, tmp_path)
    good = make_note(f"{MARKER}\ngood review", 30)
    mr.notes.list.return_value = [good]
    with pytest.raises(SystemExit):
        review.main()
    good.save.assert_not_called()
    mr.notes.create.assert_called_once_with({"body": f"{FAIL_MARKER}\n{FAILED_NOTE}"})


def test_limits_match_brief():
    assert MAX_DIFF_BYTES == 60_000 and MAX_SESSION_BYTES == 20_000
