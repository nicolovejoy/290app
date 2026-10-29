"""Guard rails on the CI file: secrets stay out of the job that runs student code."""
import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent.parent
PIPELINE = yaml.safe_load((ROOT / "tooling/ci/review-pipeline.yml").read_text())
SECRETS = ("REVIEW_BOT_TOKEN", "ANTHROPIC_API_KEY")


def job(name):
    """The job with its `extends:` template merged in (one level is all we use)."""
    j = PIPELINE[name]
    return {**PIPELINE.get(j.get("extends", ""), {}), **j}


def test_secret_jobs_use_review_bot_environment():
    for name in ("detect", "review"):
        assert job(name)["environment"] == {"name": "review-bot", "action": "prepare"}


def test_test_job_has_no_environment_and_no_secrets():
    test = job("test")
    assert "environment" not in test
    text = yaml.safe_dump(test)
    assert not any(s in text for s in SECRETS)


def test_pip_pins_match_uv_lock():
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    locked = {p["name"]: p["version"] for p in lock["package"]}
    pins = re.findall(r"([\w-]+)==([\d.]+)", yaml.safe_dump(PIPELINE))
    assert {n for n, _ in pins} == {"python-gitlab", "anthropic", "pyyaml", "pytest"}
    for name, version in pins:
        assert locked[name] == version, name
