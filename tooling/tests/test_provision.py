import sys
from types import SimpleNamespace
from pathlib import Path
import pytest
from unittest.mock import MagicMock

from gitlab.exceptions import GitlabGetError

sys.path.insert(0, str(Path(__file__).parent.parent))
from provision import provision_student  # noqa: E402

GROUP = "duke-cs-claude-fa26"
ROW = {"netid": "abc12", "name": "Ada", "email": "abc12@duke.edu"}


def make_gl(project_exists=True, user_found=True, member_level=None, protection=None):
    """Fake gitlab.Gitlab. member_level=None means not a member;
    protection=(push, merge) levels, or None for unprotected."""
    gl = MagicMock()
    project = MagicMock()
    fork = SimpleNamespace(id=99)  # like the real ProjectFork: no managers
    full_project = MagicMock()
    template = MagicMock()
    template.forks.create.return_value = fork
    missing = GitlabGetError("404", response_code=404)

    def get_project(path):
        if path == 99:
            return full_project
        if path.endswith("/staff/template"):
            return template
        if project_exists:
            return project
        raise missing

    gl.projects.get.side_effect = get_project
    gl.users.list.return_value = [MagicMock(id=7)] if user_found else []

    target = project if project_exists else full_project
    if member_level is None:
        target.members.get.side_effect = missing
    else:
        target.members.get.return_value = MagicMock(access_level=member_level)
    if protection is None:
        target.protectedbranches.get.side_effect = missing
    else:
        push, merge = protection
        target.protectedbranches.get.return_value = MagicMock(
            push_access_levels=[{"access_level": push}],
            merge_access_levels=[{"access_level": merge}],
        )
    gl.target, gl.template = target, template
    return gl


def no_mutations(gl):
    t = gl.target
    assert not gl.template.forks.create.called
    assert not t.members.create.called
    assert not t.protectedbranches.create.called
    assert not t.protectedbranches.delete.called


def test_new_student_gets_everything():
    gl = make_gl(project_exists=False)
    actions = provision_student(gl, GROUP, ROW, dry_run=False)
    assert actions == ["created", "added member", "protected main"]
    gl.template.forks.create.assert_called_once_with(
        {"namespace_path": f"{GROUP}/students", "path": "abc12", "name": "abc12"}
    )
    gl.target.members.create.assert_called_once_with({"user_id": 7, "access_level": 30})
    gl.target.protectedbranches.create.assert_called_once_with(
        {"name": "main", "push_access_level": 0, "merge_access_level": 30}
    )


def test_already_provisioned_is_ok_and_mutates_nothing():
    gl = make_gl(member_level=30, protection=(0, 30))
    assert provision_student(gl, GROUP, ROW, dry_run=False) == []
    no_mutations(gl)


def test_user_not_found_warns_but_still_forks(capsys):
    gl = make_gl(project_exists=False, user_found=False)
    actions = provision_student(gl, GROUP, ROW, dry_run=False)
    assert actions == ["created"]
    assert "warning" in capsys.readouterr().out.lower()
    assert not gl.target.members.create.called
    gl.template.forks.create.assert_called_once()


def test_dry_run_calls_no_mutating_api():
    gl = make_gl(project_exists=False)
    actions = provision_student(gl, GROUP, ROW, dry_run=True)
    assert actions == ["created", "added member", "protected main"]
    no_mutations(gl)


def test_member_at_wrong_level_is_updated():
    gl = make_gl(member_level=20, protection=(0, 30))
    member = gl.target.members.get.return_value
    actions = provision_student(gl, GROUP, ROW, dry_run=False)
    assert actions == ["added member"]
    assert member.access_level == 30
    member.save.assert_called_once()


def test_wrong_protection_is_recreated():
    gl = make_gl(member_level=30, protection=(40, 40))
    actions = provision_student(gl, GROUP, ROW, dry_run=False)
    assert actions == ["protected main"]
    gl.target.protectedbranches.delete.assert_called_once_with("main")
    gl.target.protectedbranches.create.assert_called_once()


def test_server_error_on_project_lookup_propagates():
    gl = make_gl()
    gl.projects.get.side_effect = GitlabGetError("boom", response_code=500)
    with pytest.raises(GitlabGetError):
        provision_student(gl, GROUP, ROW, dry_run=False)
    assert not gl.template.forks.create.called
