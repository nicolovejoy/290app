# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Current state

Pre-code. The only source of truth is `init.md`, the v0.1 platform spec for "Coding with Claude" (Duke, ~100 students). There is no code, no build/test tooling, and no git repo yet. Read `init.md` before doing anything; update this file with real commands once components exist.

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

## Unresolved in the spec

Don't silently pick an answer for these; ask:

- Section 7 OPEN items: session-log opt-in timeline, flag threshold, student project choice, integrity-check scope.
- Flag disagreement rule conflicts: §3.4 says "> 2 rubric points", §7 proposes "> 0.2" (scores are 0–1).
- Results file layout conflicts: §3.5 says one file per `(netid, assignment, mr_iid)`, §4.3 says `staff/results/<assignment>/<netid>.json` holding an array of runs.
- §6 prerequisites (Duke IT data policy/FERPA, capped API key, GitLab runner and token availability) are unconfirmed.
