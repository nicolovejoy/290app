import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from flag import LABEL, add_label, should_flag


def review(score=0.9, needs_human=False, correctness=1.0, reason=None):
    scores = {} if correctness is None else {"correctness": {"score": correctness, "max": 1.0, "reason": "x"}}
    return {"recommended_score": score, "needs_human": needs_human,
            "needs_human_reason": reason, "rubric_scores": scores}


def test_clean_run_not_flagged():
    assert should_flag(review(), 10, 10) == (False, "")


def test_needs_human_flags_with_reason():
    assert should_flag(review(needs_human=True, reason="odd diff"), 10, 10) == (True, "odd diff")


def test_needs_human_without_reason_still_flags():
    flagged, reason = should_flag(review(needs_human=True), 10, 10)
    assert flagged and reason


def test_low_score_flags():
    flagged, reason = should_flag(review(score=0.59), 10, 10)
    assert flagged and "below" in reason


def test_score_exactly_point_six_not_flagged():
    assert should_flag(review(score=0.6), 10, 10)[0] is False


def test_disagreement_flags():
    flagged, reason = should_flag(review(correctness=0.5), 10, 10)  # |1.0 - 0.5| = 0.5
    assert flagged and "tests say" in reason


def test_disagreement_exactly_point_two_not_flagged():
    # 4/5 = 0.8 vs 1.0 -> 0.2 (floats: 0.19999.. or 0.2, never > 0.2)
    assert should_flag(review(correctness=1.0), 4, 5)[0] is False


def test_disagreement_just_over_flags():
    assert should_flag(review(correctness=1.0), 3, 5)[0] is True


def test_zero_total_skips_disagreement():
    assert should_flag(review(correctness=0.0), 0, 0)[0] is False


def test_missing_correctness_skips_disagreement():
    assert should_flag(review(correctness=None), 0, 10)[0] is False


def test_correctness_max_scaled():
    r = review()
    r["rubric_scores"]["correctness"] = {"score": 5, "max": 10, "reason": "x"}
    assert should_flag(r, 5, 10)[0] is False


def test_add_label_adds_once():
    mr = MagicMock(labels=["bug"])
    add_label(mr)
    assert mr.labels == ["bug", LABEL]
    mr.save.assert_called_once()
    add_label(mr)
    mr.save.assert_called_once()


def test_integrity_flag_flags():
    r = review()
    r["integrity_flag"] = True
    assert should_flag(r, 10, 10) == (True, "integrity flag")


# Same file is read by dashboard/app.test.mjs, so both flag rules stay in parity.
CASES = json.loads((Path(__file__).parent / "fixtures" / "flag_cases.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_shared_flag_cases(case):
    r = case["record"]
    assert should_flag(r, r["test_passed"], r["test_total"])[0] == case["flagged"]
