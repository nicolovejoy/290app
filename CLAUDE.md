# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

`init.md` is the v0.1 platform spec for "Coding with Claude" (Duke, ~100 students) and is binding. The week-one build (spec §5 steps 1–5) lives on branch `week1-build`; plan in `docs/superpowers/plans/2026-10-29-week1-build.md`. Nothing has run against a real GitLab or the live Claude API yet. Repo: github.com/nicolovejoy/290app (public); open questions for Owen are GitHub issues #1–#8 — #7 (CI secrets isolation) blocks the first real student pipeline.

## Commands

- `uv sync` — set up the Python env (Python >= 3.12, deps in root `pyproject.toml`)
- `uv run pytest` — all tooling tests (only `tooling/tests/`; assignment tests under `template/` are student tests run by CI)
- `uv run pytest tooling/tests/test_review.py -k debounce` — single test
- `node --test dashboard/` — dashboard logic tests (no npm deps)
- `uv run python tooling/provision.py --csv roster.csv --group duke-cs-claude-<term> --dry-run` — needs `GITLAB_TOKEN`
- Tests never touch the network; GitLab and Anthropic clients are faked.

## What is being built

A review layer on top of Duke GitLab: students submit coursework as merge requests, a CI job runs tests and calls Claude, and posts structured feedback on the MR. GitLab is the system of record for code and identity. No custom auth, no database in v1, no server for the dashboard.

The pieces live in separate GitLab projects under `duke-cs-claude-<term>/staff/`, not in one repo:

- `staff/template` — forked per student. `assignments/<nn>-<slug>/` (README with fenced `rubric` block, starter code, `tests/`), `.claude/` settings plus session-logging hook, `CONTRIBUTING.md`. Its `.gitlab-ci.yml` only `include:`s config from `staff/tooling` so students can't edit the pipeline.
- `staff/tooling` — `provision.py` (python-gitlab, `GITLAB_TOKEN` env var, idempotent fork + Developer role + protected `main` per CSV row) and `review/` (`review.py`, `prompt.md`).
- `staff/results` — v1 results store: JSON files committed by CI; git history is the audit log.
- `staff/dashboard` — static single page (ideally one HTML + one JS file) on GitLab Pages, reading `staff/results` via the GitLab raw API with a read token.

## Pipeline data flow

detect (which `assignments/<nn>-*/` changed; none → comment and stop) → test (pytest/jest per assignment, JUnit XML, score = passed/total) → review (`review.py`: diff of the assignment folder only + rubric + test results + `.claude-sessions/*.jsonl` if present → Claude structured output per spec §4.2) → flag (`needs-review` label).

Invariants to preserve:

- The review note is edited in place on re-runs, never re-posted.
- Review step is debounced to 1 run per MR per 10 minutes; tests are not.
- The reviewer only writes a recommended score. It never sets a grade.
- Sonnet-class model, capped token budget, diff truncated beyond N KB with a note.
- v1 reviewer sees only the one MR; `integrity_flag` comes from internal signals only.

## Build order

The spec's week-one builds are done live in front of the class, each as a commit in `staff/tooling` tagged `week1-step-<n>`: provision.py → CI test stage → review.py → dashboard v1 → session-logging hook. Keep each step small and readable as a teaching diff; don't fold later steps into earlier ones.

## Decisions taken (pending Owen's confirmation in issue #2)

- Flag (`needs-review`) when `integrity_flag`, `needs_human`, failed review, `recommended_score < 0.6`, or `|test pass rate − Claude's correctness| > 0.2`. Rule lives in `tooling/review/flag.py` and is mirrored in `dashboard/app.js` — change both together.
- Claude's `correctness` score is its own judgment from reading code, not the test pass rate (otherwise the disagreement check is a no-op).
- Results path: `staff/results/<slug>/<netid>.json` (e.g. `01-warmup/abc123.json`), a JSON array of runs.
- One assignment per MR; `detect.env` writes `ASSIGNMENT=none` as the skip sentinel.
- Secrets (`REVIEW_BOT_TOKEN`, `ANTHROPIC_API_KEY`) only reach the `detect`/`review` jobs via the `review-bot` environment; the `test` job runs student code and must never reference a secret. `provision.py` sets each student project's `ci_config_path` so branch `.gitlab-ci.yml` edits are ignored.
- CI pip versions are pinned to `uv.lock` (`tooling/tests/test_pipeline.py` enforces it) — bump both together.

## Still open

- Spec §7 items and §6 prerequisites (data policy/FERPA, capped API key, GitLab tier/runners) — issues #1, #3–#6.
- #7: environment-scoped group variables need GitLab Premium+; if Duke is on Free, secret isolation needs a different design.
- #8: trailing debounce for deadline pushes; running canonical tests instead of students' copies.
