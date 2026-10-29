"""Log prompts and responses to .claude-sessions/<date>.jsonl.

Runs on UserPromptSubmit and Stop. Must never disturb the student's session:
any error means we log nothing, and we never print (stdout goes to Claude).
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

MAX_CHARS = 2000


def last_assistant_text(transcript_path):
    # Walk backwards to the last assistant entry with text; tool-only entries are skipped.
    for line in reversed(Path(transcript_path).read_text(encoding="utf-8").splitlines()):
        entry = json.loads(line)
        if entry.get("type") == "assistant":
            parts = entry["message"]["content"]
            text = "".join(p["text"] for p in parts if p.get("type") == "text")
            if text:
                return text
    return ""


def build_row(data):
    if data["hook_event_name"] == "UserPromptSubmit":
        event, text = "prompt", data["prompt"]
    else:
        # Newer Claude Code sends the message directly; the transcript can lag.
        event = "response"
        text = data.get("last_assistant_message") or last_assistant_text(data["transcript_path"])
    now = datetime.now().astimezone()
    return now, {"ts": now.isoformat(), "session_id": data["session_id"],
                 "event": event, "text": str(text)[:MAX_CHARS]}


def main():
    data = json.load(sys.stdin)
    now, row = build_row(data)
    project = Path(os.environ.get("CLAUDE_PROJECT_DIR") or data["cwd"])
    folder = project / ".claude-sessions"
    folder.mkdir(exist_ok=True)
    with open(folder / f"{now:%Y-%m-%d}.jsonl", "a") as f:
        f.write(json.dumps(row) + "\n")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
