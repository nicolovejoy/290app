import json
import os
import subprocess
import sys
from pathlib import Path

HOOK = Path(__file__).parent.parent.parent / "template/.claude/hooks/log_session.py"


def run_hook(project, stdin, env_dir=True):
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    if env_dir:
        env["CLAUDE_PROJECT_DIR"] = str(project)
    return subprocess.run([sys.executable, str(HOOK)], input=stdin, env=env,
                          capture_output=True, text=True)


def lines(project):
    files = list((project / ".claude-sessions").glob("*.jsonl"))
    assert len(files) == 1
    return [json.loads(x) for x in files[0].read_text().splitlines()]


def prompt_payload(text="hi", cwd="/nowhere"):
    return json.dumps({"session_id": "s1", "hook_event_name": "UserPromptSubmit",
                       "cwd": cwd, "prompt": text})


def write_transcript(path, entries):
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n")


def test_prompt_event(tmp_path):
    r = run_hook(tmp_path, prompt_payload("write fizzbuzz"))
    assert r.returncode == 0 and r.stdout == ""
    [row] = lines(tmp_path)
    assert row["event"] == "prompt" and row["text"] == "write fizzbuzz"
    assert row["session_id"] == "s1" and "T" in row["ts"]


def test_prompt_truncated(tmp_path):
    run_hook(tmp_path, prompt_payload("x" * 5000))
    assert len(lines(tmp_path)[0]["text"]) == 2000


def test_stop_reads_last_assistant_from_transcript(tmp_path):
    t = tmp_path / "t.jsonl"
    write_transcript(t, [
        {"type": "user", "message": {"content": "q"}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "old"}]}},
        {"type": "assistant", "message": {"content": [
            {"type": "text", "text": "Hello "},
            {"type": "tool_use", "name": "Bash"},
            {"type": "text", "text": "world"}]}},
        {"type": "user", "message": {"content": "tool result"}},
    ])
    payload = json.dumps({"session_id": "s1", "hook_event_name": "Stop",
                          "transcript_path": str(t), "cwd": "/nowhere"})
    assert run_hook(tmp_path, payload).returncode == 0
    [row] = lines(tmp_path)
    assert row["event"] == "response" and row["text"] == "Hello world"


def test_stop_payload_field_preferred(tmp_path):
    # the transcript is written asynchronously and can lag the payload
    payload = json.dumps({"session_id": "s1", "hook_event_name": "Stop",
                          "last_assistant_message": "fresh", "cwd": "/nowhere"})
    run_hook(tmp_path, payload)
    assert lines(tmp_path)[0]["text"] == "fresh"


def test_malformed_stdin_writes_nothing(tmp_path):
    r = run_hook(tmp_path, "not json{")
    assert r.returncode == 0 and r.stdout == ""
    assert not (tmp_path / ".claude-sessions").exists()


def test_stop_missing_transcript_writes_nothing(tmp_path):
    payload = json.dumps({"session_id": "s1", "hook_event_name": "Stop",
                          "transcript_path": str(tmp_path / "nope"), "cwd": "/nowhere"})
    assert run_hook(tmp_path, payload).returncode == 0
    assert not (tmp_path / ".claude-sessions").exists()


def test_append_only(tmp_path):
    run_hook(tmp_path, prompt_payload("one"))
    run_hook(tmp_path, prompt_payload("two"))
    assert [r["text"] for r in lines(tmp_path)] == ["one", "two"]


def test_falls_back_to_cwd(tmp_path):
    run_hook(tmp_path, prompt_payload("hi", cwd=str(tmp_path)), env_dir=False)
    assert lines(tmp_path)[0]["text"] == "hi"
