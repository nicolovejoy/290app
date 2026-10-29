# How to submit

1. Create a branch: `git checkout -b 01-warmup`.
2. Do your work inside one `assignments/<nn>-<slug>/` folder. Only that folder is reviewed.
3. Push the branch and open a merge request into `main`. You cannot push to `main` directly.
4. Wait for the pipeline. It runs the assignment's tests, then Claude posts a review comment on your merge request.
5. Read the comments, fix what they point out, and push again. The same comment is updated on each run.

One merge request per assignment. If it touches more than one assignment folder, the pipeline asks you to split it.
