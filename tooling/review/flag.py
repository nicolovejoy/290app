"""Stage 4 (flag): decide whether a human should look at this review."""

LABEL = "needs-review"
MIN_SCORE = 0.6
MAX_DISAGREEMENT = 0.2


def should_flag(review, test_passed, test_total):
    """Return (flag, reason). Pure: no network, easy to test."""
    if review.get("integrity_flag"):  # possible copying or tampering: always a human call
        return True, "integrity flag"
    if review.get("needs_human"):
        return True, review.get("needs_human_reason") or "reviewer asked for a human"
    if review["recommended_score"] < MIN_SCORE:
        return True, f"recommended score {review['recommended_score']:.2f} is below {MIN_SCORE}"
    correctness = review["rubric_scores"].get("correctness")
    # No tests ran, or no correctness criterion: nothing to compare against.
    if test_total and correctness and correctness["max"]:
        test_score = test_passed / test_total
        claude_score = correctness["score"] / correctness["max"]
        if abs(test_score - claude_score) > MAX_DISAGREEMENT:
            return True, (f"tests say {test_score:.2f}, reviewer says "
                          f"{claude_score:.2f} on correctness")
    return False, ""


def add_label(mr):
    """Add the label; never remove it (clearing it is a human decision)."""
    if LABEL not in mr.labels:
        mr.labels = mr.labels + [LABEL]
        mr.save()
