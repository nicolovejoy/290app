# Review Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Clear the small-fixes backlog from the week-one build reviews without changing behavior beyond what each item states.

**Architecture:** Five independent tasks, one per file area. Each one is a small, readable teaching diff with its own tests and commit. Branch `review-polish` starts from `compute-recommended-score` (PR #11 is unmerged).

**Tech Stack:** Python 3.12+ (uv, pytest, python-gitlab 8.6.0), plain ES modules (`node --test`), GitLab CI YAML.

**Spec:** Nico's backlog message (session 1183), restated item by item under each task below. `CLAUDE.md` holds the invariants.

## Global Constraints

- Behavior stays the same except where an item says otherwise.
- Flag rule semantics must not change. `tooling/review/flag.py` and `dashboard/app.js` stay in parity.
- Results path stays `<slug>/<netid>.json`.
- `uv run pytest` and `node --test dashboard/` stay green. `tooling/tests/test_pipeline.py::test_pip_pins_match_uv_lock` keeps passing.
- The code is shown live to a class: small diffs, match the surrounding comment density and naming, no refactors beyond the item.
- If an item turns out not to be cheap, or needs a design call, skip it and report it as skipped with the reason. Don't expand it.
- One commit per task, message `<area>: <summary>`, ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

- Job guard with ASSIGNMENT unset or empty: test and review exit 0 before any apt/pip. Pinned by a test_pipeline test in Task B.
- Review fails after an earlier good review: the good review note body is untouched and the failure note is separate. Pinned in Task C.
- A Claude reason containing `|` or a newline doesn't add table columns or rows. Pinned in Task C.
- A hashchange while results are loading: only the newest route renders. Pinned in Task D (pure helper test).
- A transcript whose last assistant entry is tool_use only: the logged text comes from the previous assistant text. Pinned in Task E.

---

### Task 1 (A): tooling/provision.py

**Files:** Modify `tooling/provision.py` and `tooling/tests/test_provision.py`

Items:
1. When an existing member's access level is changed, the action label becomes `"updated member"` (new members stay `"added member"`). Update `test_member_at_wrong_level_is_updated`.
2. Project exists (or is created) but the user isn't found: append the action `"user not found"`, so `main()` prints that instead of `ok`. Keep the warning print. `test_user_not_found_warns_but_still_forks` expects `["created", "set ci config", ..., "user not found"]`. Add a test where the project already exists and is fully configured, the user is missing, and the result is `["user not found"]`.
3. Rename `l` in `is_protected_correctly` to `level`.
4. and 5. Project settings. Generalize the ci_config_path block to a module-level dict of desired settings, checked in one loop with a single `project.save()`:
   ```python
   # label -> (attribute, desired value)
   ```
   - `ci_config_path` = `f"ci/review-pipeline.yml@{group}/staff/tooling"`, label `"set ci config"` (keep it)
   - `mr_default_target_self` = `True`, label `"set mr target"`. MRs target the student's own project, not staff/template.
   - `ci_pipeline_variables_minimum_override_role` = `"no_one_allowed"`, label `"locked pipeline variables"`. The GitLab REST name is verified: python-gitlab 8.6.0 doesn't list it in `_update_attrs`, but `save()` sends every changed attribute, so plain assignment works. Comment: hardening for issue #7.
   - The dict needs `group`, so build it inside the function, or as a function `desired_settings(group)`. Your choice; keep it readable.
   - Same idempotence and dry-run rules as today: compare with `getattr(project, attr, None)`, append the label per differing attribute, mutate and save only when not dry-run. The dry-run new-project path reports all three labels.
   - Update `make_gl` so an "already provisioned" project has all three attributes at their desired values. Add a param such as `settings=None` overriding them. Existing exact-list assertions grow by the two new labels where the project is new.
   - New tests: `test_mr_target_and_variable_role_set_when_wrong` (one save, both labels) and a dry-run counterpart (labels reported, nothing saved).
6. Add to the `provision_student` docstring: forks are created asynchronously, so if protecting `main` fails right after a fork, re-run provision.

- [ ] Write or adjust the tests above; run `uv run pytest tooling/tests/test_provision.py` and see the new ones fail.
- [ ] Implement.
- [ ] `uv run pytest` passes.
- [ ] Commit: `provision: updated-member label, user-not-found, MR target and variable lockdown`

### Task 2 (B): CI and detect

**Files:** Modify `tooling/ci/review-pipeline.yml`, `tooling/review/detect.py`, `tooling/tests/test_detect.py` and `tooling/tests/test_pipeline.py`

Items:
1. Guards skip when ASSIGNMENT is empty or unset as well as `none`. Use this exact line in both `test` and `review`:
   `- 'case "$ASSIGNMENT" in ""|none) exit 0;; esac  # dotenv vars aren''t available to rules:, so skip here'`
   (one comment is enough; the review copy can drop it)
2. Move the review job's guard ahead of apt-get and pip by giving `review` its own `before_script` that starts with the guard. Reuse the shared setup through a YAML anchor, not `!reference`, because `yaml.safe_load` in test_pipeline can't parse custom tags:
   - `.with-tooling.before_script: &tooling-setup` [apt git, clone, `pip install -q python-gitlab==8.6.0`]
   - `review.before_script`: [guard, `*tooling-setup`, `pip install -q anthropic==2.3.0 pyyaml==6.0.3`]. GitLab flattens nested script lists.
   - detect keeps the anchor only, with no anthropic or pyyaml (detect.py imports only gitlab). Check that detect.py and its imports really don't need pyyaml. Pins otherwise unchanged, and `test_pip_pins_match_uv_lock` still passes.
   - New test in test_pipeline: `test_guards_run_before_setup` asserts the review job's `before_script[0]` and the test job's `script[0]` both contain `""|none) exit 0`, and that "anthropic" doesn't appear in `yaml.safe_dump(job("detect"))`.
3. `run()` writes detect.env before any note call.
4. and 5. Note lookup by author. Add `find_note(mr, bot_id)` returning the note whose body starts with MARKER **and** whose `author.get("id") == bot_id`, else None. This mirrors `review.find_review_note`, using `.get` per Task C item 5. `run(changed_paths, mr, out_path, bot_id)`:
   - exactly one assignment: if our note exists, edit it to `DETECTED_NOTE = "Detected assignment \`{assignment}\`. Earlier notes about this merge request's assignment no longer apply."` Exact wording is up to you, but it must name the assignment. Never create a note in this case.
   - zero or several: edit our note if present, else create one (as today).
   - `main()` calls `gl.auth()` and passes `gl.user.id`.
   - Tests: update the existing run calls with `bot_id=BOT_ID` and give fake notes `author={"id": BOT_ID}`. New tests: `test_one_after_none_edits_note_to_detected` (body contains `assignments/01-warmup`, no create), `test_marker_note_from_non_bot_is_ignored` (a student note with the marker isn't edited; a new note is created), and `test_env_written_before_note` (`mr.notes.list.side_effect` asserts that out_path already exists).

- [ ] Tests first and see them fail; implement; `uv run pytest` passes.
- [ ] Commit: `ci/detect: guard empty ASSIGNMENT early, update detect note by author`

### Task 3 (C): tooling/review/review.py

**Files:** Modify `tooling/review/review.py`, `tooling/tests/test_review.py` and `pyproject.toml`, plus the `sys.path` lines in every `tooling/tests/test_*.py`

Items:
1. Failure note. Add `FAIL_MARKER = "<!-- claude-review-failed -->"`. Generalize `find_review_note(mr, bot_id, marker=MARKER)`. `fail(gl, env, assignment, mr, tests, error)` drops `existing`: it finds its own note with `find_review_note(mr, getattr(gl.user, "id", None), FAIL_MARKER)` (gl.auth may itself have failed) and posts or edits `f"{FAIL_MARKER}\n{FAILED_NOTE}"`. The good review note is never touched. Leave a stale failure note in place after a later success; that's out of scope (list it as skipped in the PR). Update `test_main_failure_*`. New test `test_failure_does_not_overwrite_good_review`: mr.notes.list returns a bot review note older than 10 minutes, main fails, that note's `save` is not called, and a create with FAIL_MARKER is.
2. `render_note(review, tests, diff_cut, session_cut=False)` adds `_The session log was truncated at {MAX_SESSION_BYTES} bytes; only the newest part was reviewed._` when session_cut is set. main passes it. Extend `test_render_note_mentions_truncation_and_human`.
3. `truncate(text, limit, keep="head")`: `keep="tail"` returns the last `limit` bytes (`raw[-limit:].decode(errors="ignore")`). main uses `keep="tail"` for session logs. New test `test_truncate_tail_keeps_newest_bytes`, including a multibyte boundary.
4. `read_session_logs`: `.split()` becomes `.splitlines()`. Test with a fake `git` returning a path with a space.
5. `find_review_note` uses `n.author.get("id")`. Test: a note with `author={}` is skipped, no KeyError.
6. Add a helper `cell(text)` that replaces `|` with `\|` and newlines (`\r\n`, `\n`) with a space. Apply it to the rubric `name` and `reason` cells in render_note. Test: a reason `"a | b\nc"` yields a rubric row with exactly 4 unescaped pipes and no newline in it.
7. `test_append_result_creates_missing_file`: use `"01-warmup/alice.json"`.
8. pyproject `[tool.pytest.ini_options]` gets `pythonpath = ["tooling", "tooling/review"]`. Remove every `sys.path.insert` (and now-unused `sys`/`Path` imports and `# noqa: E402` comments) from `tooling/tests/test_*.py`. test_log_session uses `sys.executable` and keeps `import sys`.

- [ ] Tests first and see them fail; implement; `uv run pytest` passes.
- [ ] Commit: `review: separate failure note, tail-truncated session log, safe rubric cells`

### Task 4 (D): dashboard/

**Files:** Modify `dashboard/app.js`, `dashboard/index.html` and `dashboard/app.test.mjs`, create `tooling/tests/fixtures/flag_cases.json`, modify `tooling/tests/test_flag.py`

Items:
1. index.html CSS: `nav[hidden] { display: none; }`
2. Stale renders: in the DOM bootstrap, keep `let renderSeq = 0;`. Each `render()` takes `const seq = ++renderSeq;` and after the `await` returns early if `seq !== renderSeq`. Keep it inline; it's a 3-line change. Nothing exported, so this one has no unit test; say so in the report.
3. Branch: `fetchResults(cfg)` reads `default_branch` once with `(await (await api(cfg, "")).json()).default_branch` and uses `ref=${encodeURIComponent(branch)}`.
4. Rename the tree-loop variable `e` in `listJsonPaths` to `item`.
5. Batches: add an exported pure helper `chunk(arr, n)` and fetch with `for (const batch of chunk(paths, 10)) await Promise.all(...)`. Test: `chunk([1,2,3,4,5], 2)` deep-equals `[[1,2],[3,4],[5]]`, and `chunk([], 10)` is `[]`.
6. Test `views escape a <script> summary`: a record with `summary: "<script>alert(1)</script>"` (and the same in needs_human_reason with needs_human true). gridView, flaggedView and studentView contain no `<script` and do contain `&lt;script&gt;` where the text appears.
7. Shared parity fixture `tooling/tests/fixtures/flag_cases.json`: an array of `{"name", "record", "flagged"}`. `record` is a stored run with `recommended_score`, `needs_human`, `needs_human_reason`, `integrity_flag`, `rubric_scores`, `test_passed` and `test_total`. Cover clean, integrity, needs_human, score 0.59, score exactly 0.6, disagreement 0.5, disagreement exactly 0.2 (4/5 vs 1.0), 3/5 vs 1.0, zero tests, and no correctness criterion. Use the values from the existing tests in test_flag.py; don't invent new boundaries. Leave `status: "failed"` out, since it's dashboard-only (review.py never calls should_flag on a failure).
   - Python: `test_shared_flag_cases` parametrized over the file, asserting `should_flag(r, r["test_passed"], r["test_total"])[0] == case["flagged"]`.
   - JS: read with `readFileSync(new URL("../tooling/tests/fixtures/flag_cases.json", import.meta.url))` and assert `isFlagged(case.record) === case.flagged` for each.
   - If either side disagrees with the fixture, **stop and report**. Don't change flag logic.

- [ ] Tests first; `node --test dashboard/` and `uv run pytest` pass.
- [ ] Commit: `dashboard: hidden nav, stale-render guard, default branch, batched fetch, parity fixture`

### Task 5 (E): template/.claude/hooks/log_session.py

**Files:** Modify `template/.claude/hooks/log_session.py` and `tooling/tests/test_log_session.py`

Items:
1. `last_assistant_text`: if an assistant entry's joined text is empty (tool_use only), keep walking back. Test `test_stop_skips_tool_only_assistant_entry`: entries are assistant text "answer", then assistant tool_use only, and "answer" gets logged.
2. In build_row the row text is `str(text)[:MAX_CHARS]`.
3. `read_text(encoding="utf-8")`.
4. Test `test_empty_payload_message_falls_back_to_transcript`: `last_assistant_message: ""` plus a transcript logs the transcript text.

- [ ] Tests first; `uv run pytest` passes.
- [ ] Commit: `hook: skip tool-only replies, utf-8 transcript`
