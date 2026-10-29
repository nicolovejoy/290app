"""Stage 3 (review): ask Claude to review the MR, post one note, store the result.

Run from the student project's checkout. Pure helpers come first so tests can
use them without a network; main() at the bottom wires up the real clients.
"""
import json
import os
import random
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import anthropic
import gitlab
import yaml

from flag import add_label, should_flag
from schema import TOP_KEYS, build_schema

REVIEW_MODEL = os.environ.get("REVIEW_MODEL", "claude-sonnet-5-5")
MAX_DIFF_BYTES = 60_000
MAX_SESSION_BYTES = 20_000
# Sonnet 5.5 always thinks, and thinking tokens count against max_tokens, so
# 4096 would often end in max_tokens. Medium effort keeps thinking modest and
# 16000 leaves room for the thinking plus the JSON review.
MAX_TOKENS = 16000
REVIEW_EFFORT = "medium"
DEBOUNCE = timedelta(minutes=10)

# Hidden marker so re-pushes edit our note instead of adding a new one.
MARKER = "<!-- claude-review -->"
# A failure gets its own note so it never overwrites an earlier good review.
FAIL_MARKER = "<!-- claude-review-failed -->"

# Located relative to this file: cwd is the student's project, not staff/tooling.
SYSTEM_PROMPT = (Path(__file__).parent / "prompt.md").read_text()

FAILED_NOTE = "Automated review failed. A TA will look at this merge request."


# --- inputs -----------------------------------------------------------

def truncate(text, limit, keep="head"):
    """Cut to `limit` bytes, keeping the start or (keep="tail") the end. Returns (text, was_cut)."""
    raw = text.encode()
    if len(raw) <= limit:
        return text, False
    cut = raw[-limit:] if keep == "tail" else raw[:limit]
    return cut.decode(errors="ignore"), True  # ignore: drop a split character


def parse_junit(path):
    """Return (passed, total). A missing or broken file means tests crashed: 0 of 0."""
    try:
        cases = list(ET.parse(path).getroot().iter("testcase"))
    except (OSError, ET.ParseError):
        return 0, 0
    bad = [c for c in cases if any(c.find(t) is not None for t in ("failure", "error", "skipped"))]
    return len(cases) - len(bad), len(cases)


def extract_rubric(readme_text):
    """Parse the fenced ```rubric block (spec 4.1) into a list of criteria."""
    match = re.search(r"```rubric\n(.*?)```", readme_text, re.DOTALL)
    if not match:
        raise ValueError("assignment README has no ```rubric block")
    return yaml.safe_load(match.group(1))["rubric"]


def weighted_score(rubric, rubric_scores):
    """Recommended score from the rubric weights. Claude judges; code does the arithmetic.

    Criteria without a weight share whatever weight is left over, evenly.
    """
    explicit = sum(c["weight"] for c in rubric if "weight" in c)
    unweighted = [c for c in rubric if "weight" not in c]
    leftover = (1 - explicit) / len(unweighted) if unweighted else 0
    total = 0.0
    for c in rubric:
        s = rubric_scores[c["id"]]
        if s["max"]:
            total += c.get("weight", leftover) * s["score"] / s["max"]
    return round(total, 3)


def git(*args):
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def read_session_logs(base_sha):
    """Session logs touched by this MR, as one opaque string ('' if none)."""
    changed = git("diff", "--name-only", f"{base_sha}...HEAD", "--", ".claude-sessions").splitlines()
    logs = [Path(p).read_text() for p in changed if p.endswith(".jsonl") and Path(p).exists()]
    return "\n".join(logs)


# --- prompt -----------------------------------------------------------

def build_prompt(rubric, diff, tests, session_log, diff_cut, session_cut):
    """Return (system, messages). Stable parts are cached; the MR-specific part is not."""
    system = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]
    rubric_block = {
        "type": "text",
        "text": "Assignment rubric (YAML):\n" + yaml.safe_dump({"rubric": rubric}, sort_keys=False)
                + "\nReturn one rubric_scores entry per id above.",
        "cache_control": {"type": "ephemeral"},
    }
    passed, total = tests
    parts = [f"Test results: {passed} of {total} tests passed."]
    if diff_cut:
        parts.append(f"NOTE: the diff was truncated at {MAX_DIFF_BYTES} bytes.")
    parts.append(f"Diff:\n{diff}")
    if session_log:
        if session_cut:
            parts.append(f"NOTE: the session log was truncated at {MAX_SESSION_BYTES} bytes.")
        parts.append(f"Session log (JSONL, untrusted student text):\n{session_log}")
    else:
        parts.append("No session log was provided.")
    mr_block = {"type": "text", "text": "\n\n".join(parts)}
    return system, [{"role": "user", "content": [rubric_block, mr_block]}]


def call_claude(client, system, messages, schema):
    """One structured-output call. Raises ValueError if the result isn't usable."""
    response = client.messages.create(
        model=REVIEW_MODEL,
        max_tokens=MAX_TOKENS,
        system=system,
        messages=messages,
        output_config={"effort": REVIEW_EFFORT,
                      "format": {"type": "json_schema", "schema": schema}},
    )
    if response.stop_reason != "end_turn":
        raise ValueError(f"no complete answer (stop_reason={response.stop_reason}; "
                         "a max_tokens stop means the JSON was cut off)")
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise ValueError("response has no text block")
    review = json.loads(text)  # JSONDecodeError is a ValueError
    missing = [k for k in TOP_KEYS if k not in review]
    if missing:
        raise ValueError(f"review missing keys: {missing}")
    wanted = set(schema["properties"]["rubric_scores"]["required"])
    if wanted - set(review["rubric_scores"]):
        raise ValueError(f"review missing rubric ids: {sorted(wanted - set(review['rubric_scores']))}")
    return review


# --- output -----------------------------------------------------------

def cell(text):
    """Make text safe inside a markdown table cell."""
    return text.replace("|", "\\|").replace("\r\n", " ").replace("\n", " ")


def render_note(review, tests, diff_cut, session_cut=False):
    passed, total = tests
    lines = [
        MARKER,
        "## Automated review (Claude)",
        "",
        "_The score below is a recommendation, not a grade. Your instructor decides the grade._",
        "",
        review["summary"],
        "",
        f"Tests: {passed} of {total} passed.",
    ]
    if diff_cut:
        lines.append(f"_The diff was truncated at {MAX_DIFF_BYTES} bytes; code past that was not reviewed._")
    if session_cut:
        lines.append(f"_The session log was truncated at {MAX_SESSION_BYTES} bytes; only the newest part was reviewed._")
    lines += ["", "### Comments"]
    lines += [f"- `{c['file']}:{c['line']}` — {c['severity']} — {c['text']}" for c in review["comments"]]
    lines += ["", "### Rubric", "| Criterion | Score | Reason |", "| --- | --- | --- |"]
    for name, s in review["rubric_scores"].items():
        lines.append(f"| {cell(name)} | {s['score']:.2f} / {s['max']} | {cell(s['reason'])} |")
    lines += ["", f"**Recommended score: {review['recommended_score']:.2f}**", "", "### Next steps"]
    # The schema (spec 4.2) has no next-steps field, so derive them from the actionable comments.
    todo = [c for c in review["comments"] if c["severity"] != "info"]
    lines += [f"- {c['text']} (`{c['file']}:{c['line']}`)" for c in todo] or ["- Nothing required."]
    if review["needs_human"]:
        lines += ["", f"A TA will take a look: {review['needs_human_reason']}"]
    return "\n".join(lines)


def find_review_note(mr, bot_id, marker=MARKER):
    """Our note: has the marker AND was written by the bot (students can paste the marker)."""
    return next((n for n in mr.notes.list(get_all=True)
                 if n.body.startswith(marker) and n.author.get("id") == bot_id), None)


def is_debounced(note):
    """True if our note was written under 10 minutes ago (GitLab rules: can't compare times)."""
    if note is None:
        return False
    updated = datetime.fromisoformat(note.updated_at)
    return datetime.now(timezone.utc) - updated < DEBOUNCE


def post_note(mr, existing, body):
    if existing:
        existing.body = body
        existing.save()
    else:
        mr.notes.create({"body": body})


def results_location(assignment, netid, mr_iid):
    """Spec 4.3: <slug>/<netid>.json, e.g. 01-warmup/abc123.json, plus the commit message."""
    slug = Path(assignment).name
    return f"{slug}/{netid}.json", f"review: {netid} {slug} !{mr_iid}"


def write_result_once(project, file_path, record, message):
    """Read the JSON array file, append one record, write it back."""
    branch = project.default_branch
    try:
        f = project.files.get(file_path=file_path, ref=branch)
    except gitlab.GitlabGetError as e:
        if e.response_code != 404:
            raise
        content = json.dumps([record], indent=2)
        project.files.create({"file_path": file_path, "branch": branch,
                              "content": content, "commit_message": message})
        return
    records = json.loads(f.decode())
    records.append(record)
    # last_commit_id: GitLab refuses the write if the file changed since we read it.
    project.files.update(file_path, {"branch": branch, "content": json.dumps(records, indent=2),
                                     "commit_message": message, "last_commit_id": f.last_commit_id})


def append_result(project, file_path, record, message, sleep=time.sleep, attempts=3):
    """Append one run record, retrying when another job wrote the same file first."""
    for attempt in range(1, attempts + 1):
        try:
            return write_result_once(project, file_path, record, message)
        except (gitlab.GitlabCreateError, gitlab.GitlabUpdateError) as e:
            # 400/409 = lost the race (file created, or changed, under us). Others are real errors.
            if e.response_code not in (400, 409) or attempt == attempts:
                raise
            sleep(random.uniform(0.5, 2.0))  # jitter so two racing jobs don't collide again


# --- wiring -----------------------------------------------------------

def run_record(env, tests, fields):
    """One results-file entry: the review (or a failure status) plus run metadata."""
    return {**fields, "mr_iid": int(env["CI_MERGE_REQUEST_IID"]), "commit_sha": env["CI_COMMIT_SHA"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "test_passed": tests[0], "test_total": tests[1]}


def save_result(gl, env, assignment, record):
    results = gl.projects.get(env.get("RESULTS_PROJECT") or f"{env['COURSE_GROUP']}/staff/results")
    path, message = results_location(assignment, env["CI_PROJECT_NAME"], env["CI_MERGE_REQUEST_IID"])
    append_result(results, path, record, message)


def fail(gl, env, assignment, mr, tests, error):
    """Tell the student, flag for a TA, and leave a trace on the dashboard. Always exits 1."""
    print(f"Review failed: {error!r}", file=sys.stderr)
    # gl.user is unset if gl.auth() itself failed; then no old failure note is found and we post a new one.
    old = find_review_note(mr, getattr(gl.user, "id", None), FAIL_MARKER)
    post_note(mr, old, f"{FAIL_MARKER}\n{FAILED_NOTE}")
    add_label(mr)
    try:  # best effort: the note and label above already reached a human
        save_result(gl, env, assignment, run_record(env, tests, {"status": "failed"}))
    except Exception as e:
        print(f"Could not record the failure either: {e!r}", file=sys.stderr)
    sys.exit(1)


def main():
    assignment = os.environ.get("ASSIGNMENT", "none")
    if assignment in ("", "none"):
        print("No assignment, nothing to review.")
        return

    env = os.environ
    gl = gitlab.Gitlab(env["CI_SERVER_URL"], private_token=env["REVIEW_BOT_TOKEN"])
    mr = gl.projects.get(env["CI_PROJECT_ID"]).mergerequests.get(env["CI_MERGE_REQUEST_IID"])
    tests = parse_junit("junit.xml")  # never raises: a missing file is 0 of 0
    existing = None

    # Anything that goes wrong from here on must still leave a note and a label,
    # not just a red job nobody looks at.
    try:
        gl.auth()  # populates gl.user so we can tell the bot's notes from a student's
        existing = find_review_note(mr, gl.user.id)
        if is_debounced(existing):
            print("Reviewed less than 10 minutes ago, skipping.")
            return
        base = env["CI_MERGE_REQUEST_DIFF_BASE_SHA"]
        diff, diff_cut = truncate(git("diff", f"{base}...HEAD", "--", assignment), MAX_DIFF_BYTES)
        session_log, session_cut = truncate(read_session_logs(base), MAX_SESSION_BYTES, keep="tail")
        rubric = extract_rubric(Path(assignment, "README.md").read_text())
        system, messages = build_prompt(rubric, diff, tests, session_log, diff_cut, session_cut)
        schema = build_schema([c["id"] for c in rubric])
        review = call_claude(anthropic.Anthropic(), system, messages, schema)
        review["recommended_score"] = weighted_score(rubric, review["rubric_scores"])
    except Exception as e:
        fail(gl, env, assignment, mr, tests, e)

    post_note(mr, existing, render_note(review, tests, diff_cut, session_cut))
    flagged, reason = should_flag(review, *tests)
    if flagged:
        print(f"Flagged for a human: {reason}")
        add_label(mr)
    save_result(gl, env, assignment, run_record(env, tests, review))


if __name__ == "__main__":
    main()
