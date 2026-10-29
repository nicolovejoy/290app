"""Provision one GitLab project per student from the roster. Safe to re-run."""
import argparse
import csv
import os

import gitlab
from gitlab.exceptions import GitlabGetError

DEVELOPER, NO_ONE = 30, 0


def get_or_none(manager, key):
    try:
        return manager.get(key)
    except GitlabGetError as e:
        if e.response_code != 404:  # 401/403/500 mean something is wrong, not "absent"
            raise
        return None


def is_protected_correctly(branch):
    pushes = [l["access_level"] for l in branch.push_access_levels]
    merges = [l["access_level"] for l in branch.merge_access_levels]
    return pushes == [NO_ONE] and merges == [DEVELOPER]


def provision_student(gl, group, row, dry_run):
    """Bring one student's project to the desired state; return what changed."""
    netid, actions = row["netid"], []

    project = get_or_none(gl.projects, f"{group}/students/{netid}")
    if project is None:
        actions.append("created")
        if not dry_run:
            template = gl.projects.get(f"{group}/staff/template")
            fork = template.forks.create(
                {"namespace_path": f"{group}/students", "path": netid, "name": netid}
            )
            # The fork result is a bare object; re-fetch for members/protectedbranches.
            project = gl.projects.get(fork.id)

    # An MR pipeline would otherwise run the source branch's own .gitlab-ci.yml,
    # which the student can edit. Developers can't change this project setting.
    ci_config = f"ci/review-pipeline.yml@{group}/staff/tooling"
    if project is None or project.ci_config_path != ci_config:
        actions.append("set ci config")
        if not dry_run:
            project.ci_config_path = ci_config
            project.save()

    # The student may not have logged in to GitLab yet; re-running later fixes it.
    users = gl.users.list(username=netid)
    if not users:
        print(f"warning: no GitLab user '{netid}' yet, skipping membership")
        return actions

    if project is None:  # dry run, project doesn't exist yet: nothing to inspect
        return actions + ["added member", "protected main"]

    member = get_or_none(project.members, users[0].id)
    if member is None:
        actions.append("added member")
        if not dry_run:
            project.members.create({"user_id": users[0].id, "access_level": DEVELOPER})
    elif member.access_level != DEVELOPER:
        actions.append("added member")
        if not dry_run:
            member.access_level = DEVELOPER
            member.save()

    # Students submit via merge request, so nobody pushes straight to main.
    branch = get_or_none(project.protectedbranches, "main")
    if branch is None or not is_protected_correctly(branch):
        actions.append("protected main")
        if not dry_run:
            if branch is not None:
                project.protectedbranches.delete("main")
            project.protectedbranches.create(
                {"name": "main", "push_access_level": NO_ONE, "merge_access_level": DEVELOPER}
            )
    return actions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, help="roster with netid,name,email")
    env_group = os.environ.get("COURSE_GROUP")
    parser.add_argument("--group", default=env_group, required=not env_group)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    gl = gitlab.Gitlab(
        os.environ.get("GITLAB_URL", "https://gitlab.oit.duke.edu"),
        private_token=os.environ["GITLAB_TOKEN"],
    )
    with open(args.csv, newline="") as f:
        for row in csv.DictReader(f):
            actions = provision_student(gl, args.group, row, args.dry_run)
            prefix = "[dry-run] " if args.dry_run else ""
            print(f"{prefix}{row['netid']}: {', '.join(actions) or 'ok'}")


if __name__ == "__main__":
    main()
