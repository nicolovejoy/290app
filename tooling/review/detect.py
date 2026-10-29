"""Stage 1 (detect): which assignment folder did this MR change?

Writes detect.env (ASSIGNMENT=<dir> or none) for later jobs. If the MR
touches zero or several assignments, posts a note and leaves it empty.
"""
import os
import re
import subprocess
from pathlib import Path

import gitlab

ASSIGNMENT_RE = re.compile(r"^(assignments/\d{2}-[a-z0-9-]+)/")

# Hidden marker so re-pushes edit our note instead of adding a new one.
MARKER = "<!-- claude-detect -->"

NONE_NOTE = (
    "No assignment detected in this merge request — changes must be inside "
    "an `assignments/<nn>-<slug>/` folder."
)
MANY_NOTE = (
    "This merge request changes more than one assignment ({found}). "
    "Please split it into one merge request per assignment."
)
DETECTED_NOTE = (
    "Detected assignment `{assignment}`. "
    "Earlier notes about this merge request's assignment no longer apply."
)


def detect_assignments(changed_paths):
    found = {m.group(1) for p in changed_paths if (m := ASSIGNMENT_RE.match(p))}
    return sorted(found)


def find_note(mr, bot_id):
    """Our note on this MR, matched by marker and author so students can't spoof it."""
    for note in mr.notes.list(get_all=True):
        if note.body.startswith(MARKER) and note.author.get("id") == bot_id:
            return note
    return None


def run(changed_paths, mr, out_path, bot_id):
    """Write detect.env; keep our MR note in step with what was detected."""
    found = detect_assignments(changed_paths)
    assignment = found[0] if len(found) == 1 else "none"  # sentinel: dotenv parsers can reject empty values; later jobs skip
    Path(out_path).write_text(f"ASSIGNMENT={assignment}\n")  # first, so a failed note call can't lose it
    note = find_note(mr, bot_id)
    if assignment != "none":
        body = DETECTED_NOTE.format(assignment=assignment)
        if note is None:
            return  # nothing earlier to correct
    else:
        body = NONE_NOTE if not found else MANY_NOTE.format(
            found=", ".join(f"`{a}`" for a in found))
    body = f"{MARKER}\n{body}"
    if note:
        note.body = body
        note.save()
    else:
        mr.notes.create({"body": body})


def main():
    base = os.environ["CI_MERGE_REQUEST_DIFF_BASE_SHA"]
    diff = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...HEAD"],
        check=True, capture_output=True, text=True).stdout
    gl = gitlab.Gitlab(os.environ["CI_SERVER_URL"],
                       private_token=os.environ["REVIEW_BOT_TOKEN"])
    project = gl.projects.get(os.environ["CI_PROJECT_ID"])
    mr = project.mergerequests.get(os.environ["CI_MERGE_REQUEST_IID"])
    gl.auth()
    run(diff.splitlines(), mr, "detect.env", gl.user.id)


if __name__ == "__main__":
    main()
