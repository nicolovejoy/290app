# Course Platform Spec — "Coding with Claude" (Duke, ~100 students)

Status: v0.1 draft. Build this in Claude Code. Sections marked **OPEN** need a decision before that component is built; everything else is buildable now.

## 1. Purpose

A review layer on top of Duke GitLab that lets students submit coursework as merge requests and get automated, Claude-written feedback within minutes, with a dashboard for the instructor and TAs to triage.

Non-goals: a custom submission UI, LMS replacement, grade-book sync, authentication of our own. GitLab is the system of record for code and identity.

## 2. Actors

| Actor             | Count | Does                                                                     |
| ----------------- | ----- | ------------------------------------------------------------------------ |
| Student           | ~100  | Pushes to own repo, opens MR, reads feedback, iterates                   |
| Instructor (Owen) | 1     | GitLab group admin; sets assignments; reviews flagged work; final grades |
| TA                | 1–3   | Works the flagged queue                                                  |
| Reviewer bot      | 1     | CI job that runs tests and calls Claude                                  |

## 3. Components

### 3.1 GitLab layout

- Group: `duke-cs-claude-<term>` (Owen is Owner).
- Subgroup `staff/` — private, holds `template`, `tooling`, `dashboard`.
- Subgroup `students/` — one project per student, named `<netid>`, forked from `template`. Student has Developer role on own project only.
- Protected `main` on each student project: no direct push; MR required.

### 3.2 Template repo (`staff/template`)

Forked to each student. Contains:

- `assignments/<nn>-<slug>/` — one folder per assignment: `README.md` (prompt + rubric), starter code, `tests/`.
- `.gitlab-ci.yml` — the review pipeline (see 3.4). Students cannot edit it: CI config is pulled from `staff/tooling` via `include:`, and the pipeline runs with a project token held at group level.
- `.claude/` — course Claude Code settings and the session-logging hook (see 3.6).
- `CONTRIBUTING.md` — how to submit (branch → MR → read comments → iterate).

### 3.3 Provisioning script (`staff/tooling/provision.py`)

- Input: CSV of `netid,name,email`.
- For each row: fork `template` into `students/<netid>`, add user as Developer, protect `main`, set MR-required.
- Idempotent: safe to re-run when students add/drop.
- Uses `python-gitlab`; token in env var `GITLAB_TOKEN`.
- ~100 lines. This is a week-one live build.

### 3.4 Review pipeline (`.gitlab-ci.yml` + `staff/tooling/review/`)

Triggered on MR open and on every push to an open MR.

Stages:

1. **detect** — which `assignments/<nn>-*/` folders changed in this MR. If none, post a comment "no assignment detected" and stop.
2. **test** — run that assignment's `tests/` (pytest / jest per assignment). Produce JUnit XML. Score = passed/total.
3. **review** — `review.py`:
   - Inputs: MR diff (assignment folder only), assignment `README.md` rubric, test results, session log if present.
   - Calls Claude with a fixed system prompt (`review/prompt.md`) and a structured-output schema (see 4.2).
   - Posts one MR note: summary, 3–6 specific comments with file:line, rubric scores, next steps. Edits the same note on re-run (don't spam).
   - Writes result JSON to the results store (3.5).
4. **flag** — if `needs_human` is true, or score < threshold, or test score and Claude score disagree by > 2 rubric points, add label `needs-review` to the MR.

Constraints:

- Max 1 review run per MR per 10 minutes (debounce via pipeline rules on commit timestamp). Students can still push freely; only the review step is rate-limited.
- Model: Sonnet-class. Token budget per review capped; truncate diff beyond N KB with a note.
- Reviewer never changes a grade; it writes a _recommended_ score. Final grade is Owen's.

### 3.5 Results store

- v1: one JSON file per `(netid, assignment, mr_iid)` committed to `staff/results` repo by the CI job. No database. Git history is the audit log.
- v2 (end of course, as a lesson): migrate to SQLite/Postgres. The migration is itself course content.

### 3.6 Session logging (student side)

- A Claude Code hook in `.claude/settings.json` that appends each prompt/response summary to `.claude-sessions/<date>.jsonl` in the repo.
- Students commit this with their work. The reviewer reads it to comment on _process_ (did they test, did they question the first answer, did they read errors).
- **OPEN:** opt-in for assignments 1–2, required after? Decide with Owen and announce in syllabus.

### 3.7 Dashboard (`staff/dashboard`)

Single-page web app, static, reads `staff/results` JSON (via GitLab raw API with a read token) — no server.
Views:

- Assignment × student grid: submitted / not, test score, Claude score, flagged.
- Flagged queue: sorted by urgency, one-click to the MR.
- Per-student page: all submissions, trend.
  Stack: whatever Claude Code picks; keep it one HTML file + one JS file if possible. Hosted on GitLab Pages for the `staff` group.

## 4. Data

### 4.1 Rubric format (`assignments/<nn>/README.md`, fenced `rubric` block)

```yaml
rubric:
  - id: correctness # from tests, weight 0.5
  - id: data_structure_choice # "used a hash map where lookup is the bottleneck"
    weight: 0.2
  - id: code_quality
    weight: 0.15
  - id: process # from session log; 0 if absent and assignment is opt-in
    weight: 0.15
```

### 4.2 Review output schema (Claude structured output)

```json
{
  "summary": "string, <= 3 sentences",
  "comments": [
    {
      "file": "str",
      "line": 0,
      "severity": "info|suggest|issue",
      "text": "str"
    }
  ],
  "rubric_scores": { "<id>": { "score": 0.0, "max": 1.0, "reason": "str" } },
  "recommended_score": 0.0,
  "needs_human": false,
  "needs_human_reason": "str|null",
  "integrity_flag": false
}
```

### 4.3 Result record (`staff/results/<assignment>/<netid>.json`)

Array of review outputs, one per pipeline run, plus `mr_iid`, `commit_sha`, `timestamp`, `test_passed`, `test_total`.

## 5. Build order (week-one live builds, in front of the class)

1. `provision.py` — fork 100 repos. Demonstrates: APIs, idempotency, pagination.
2. `.gitlab-ci.yml` with test stage only. Demonstrates: CI, exit codes, JUnit.
3. `review.py` with Claude call. Demonstrates: structured output, prompt as code, caching.
4. Dashboard v1. Demonstrates: reading JSON, rendering a table, no backend.
5. Session-logging hook. Demonstrates: hooks, JSONL, append-only logs.

Each step is a commit in `staff/tooling` with a tag `week1-step-<n>` so students can diff.

## 6. Prerequisites for Owen (before build)

- [ ] **Data policy:** confirm with Duke IT that student code and session logs may be sent to Anthropic's API. Ask about FERPA and whether Duke has or needs a DPA.
- [ ] **API key:** department-owned Anthropic key with a hard monthly spend cap. Estimate: 100 students × 10 assignments × ~4 runs × ~20k tokens ≈ 80M tokens/semester — low hundreds of dollars at Sonnet pricing. Verify current pricing.
- [ ] **GitLab:** confirm group-level CI variables and project tokens are enabled on Duke's instance; confirm shared runners exist (or we need a runner).
- [ ] **Syllabus:** state that an AI reviewer reads submissions and that session logs may be collected.

## 7. OPEN decisions

1. Session logs: opt-in → required timeline (3.6).
2. Flag threshold for `needs-review` (3.4 step 4). Proposed: recommended_score < 0.6 or disagreement > 0.2.
3. Student project ("B"): dog-walk app vs other. Platform is project-agnostic; decide when lesson plans start.
4. Does the reviewer see other students' submissions for integrity checks, or only the one MR? Proposed v1: only the one MR, `integrity_flag` based on internal signals only.

## 8. Out of scope (v1)

Grade-book export, late-penalty automation, peer review, plagiarism-against-corpus, multi-section support, any custom auth.
