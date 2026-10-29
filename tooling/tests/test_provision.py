from types import SimpleNamespace
import pytest
from unittest.mock import MagicMock

from gitlab.exceptions import GitlabGetError

from provision import provision_student

GROUP = "duke-cs-claude-fa26"
ROW = {"netid": "abc12", "name": "Ada", "email": "abc12@duke.edu"}
CI_CONFIG = f"ci/review-pipeline.yml@{GROUP}/staff/tooling"


GOOD_SETTINGS = {
    "ci_config_path": CI_CONFIG,
    "mr_default_target_self": True,
    "ci_pipeline_variables_minimum_override_role": "no_one_allowed",
}
NEW_LABELS = ["set ci config", "set mr target", "locked pipeline variables"]


def make_gl(project_exists=True, user_found=True, member_level=None, protection=None,
            ci_config=CI_CONFIG, settings=None):
    """Fake gitlab.Gitlab. member_level=None means not a member;
    protection=(push, merge) levels, or None for unprotected.
    ci_config is the existing project's ci_config_path (a new fork has none).
    settings overrides other project settings (default: all at desired values)."""
    gl = MagicMock()
    project = MagicMock(**{**GOOD_SETTINGS, "ci_config_path": ci_config, **(settings or {})})
    fork = SimpleNamespace(id=99)  # like the real ProjectFork: no managers
    full_project = MagicMock(**{k: None for k in GOOD_SETTINGS})
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
    assert not t.save.called


def test_new_student_gets_everything():
    gl = make_gl(project_exists=False)
    actions = provision_student(gl, GROUP, ROW, dry_run=False)
    assert actions == ["created", *NEW_LABELS, "added member", "protected main"]
    assert gl.target.ci_config_path == CI_CONFIG
    gl.target.save.assert_called_once()
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
    assert actions == ["created", *NEW_LABELS, "user not found"]
    assert "warning" in capsys.readouterr().out.lower()
    assert not gl.target.members.create.called
    gl.template.forks.create.assert_called_once()


def test_user_not_found_on_existing_project_is_reported(capsys):
    gl = make_gl(user_found=False)
    assert provision_student(gl, GROUP, ROW, dry_run=False) == ["user not found"]
    assert "warning" in capsys.readouterr().out.lower()
    no_mutations(gl)


def test_dry_run_calls_no_mutating_api():
    gl = make_gl(project_exists=False)
    actions = provision_student(gl, GROUP, ROW, dry_run=True)
    assert actions == ["created", *NEW_LABELS, "added member", "protected main"]
    no_mutations(gl)


def test_wrong_ci_config_is_set():
    # Without this, an MR pipeline runs the student branch's own .gitlab-ci.yml.
    gl = make_gl(member_level=30, protection=(0, 30), ci_config="")
    assert provision_student(gl, GROUP, ROW, dry_run=False) == ["set ci config"]
    assert gl.target.ci_config_path == CI_CONFIG
    gl.target.save.assert_called_once()


def test_dry_run_reports_ci_config_but_does_not_save():
    gl = make_gl(member_level=30, protection=(0, 30), ci_config="")
    assert provision_student(gl, GROUP, ROW, dry_run=True) == ["set ci config"]
    assert gl.target.ci_config_path == ""
    no_mutations(gl)


def test_mr_target_and_variable_role_set_when_wrong():
    gl = make_gl(member_level=30, protection=(0, 30),
                 settings={"mr_default_target_self": False,
                           "ci_pipeline_variables_minimum_override_role": "developer"})
    actions = provision_student(gl, GROUP, ROW, dry_run=False)
    assert actions == ["set mr target", "locked pipeline variables"]
    assert gl.target.mr_default_target_self is True
    assert gl.target.ci_pipeline_variables_minimum_override_role == "no_one_allowed"
    gl.target.save.assert_called_once()


def test_dry_run_reports_settings_but_does_not_save():
    wrong = {"mr_default_target_self": False,
             "ci_pipeline_variables_minimum_override_role": "developer"}
    gl = make_gl(member_level=30, protection=(0, 30), settings=wrong)
    actions = provision_student(gl, GROUP, ROW, dry_run=True)
    assert actions == ["set mr target", "locked pipeline variables"]
    assert gl.target.mr_default_target_self is False
    no_mutations(gl)


def test_member_at_wrong_level_is_updated():
    gl = make_gl(member_level=20, protection=(0, 30))
    member = gl.target.members.get.return_value
    actions = provision_student(gl, GROUP, ROW, dry_run=False)
    assert actions == ["updated member"]
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
