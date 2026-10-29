# How to submit

1. Create a branch: `git checkout -b 01-warmup`.
2. Do your work inside one `assignments/<nn>-<slug>/` folder. Only that folder is reviewed.
3. Push the branch and open a merge request into `main`. You cannot push to `main` directly.
4. Wait for the pipeline. It runs the assignment's tests, then Claude posts a review comment on your merge request.
5. Read the comments, fix what they point out, and push again. The same comment is updated on each run.

One merge request per assignment. If it touches more than one assignment folder, the pipeline asks you to split it.

## Session logs

A hook in `.claude/` records your prompts and Claude's replies to `.claude-sessions/<date>.jsonl`. Commit these files with your work. The reviewer reads them to comment on how you worked, not only on the result. If you don't commit them, the process score is 0.
