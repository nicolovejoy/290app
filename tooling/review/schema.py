"""Spec 4.2: the JSON shape Claude must return, built per assignment.

rubric_scores has one required key per rubric id, so the model can't
invent or skip criteria.
"""

TOP_KEYS = ["summary", "comments", "rubric_scores", "recommended_score",
            "needs_human", "needs_human_reason", "integrity_flag"]


def build_schema(rubric_ids):
    score = {
        "type": "object",
        "properties": {"score": {"type": "number"}, "max": {"type": "number"},
                       "reason": {"type": "string"}},
        "required": ["score", "max", "reason"],
        "additionalProperties": False,
    }
    comment = {
        "type": "object",
        "properties": {"file": {"type": "string"}, "line": {"type": "integer"},
                       "severity": {"type": "string", "enum": ["info", "suggest", "issue"]},
                       "text": {"type": "string"}},
        "required": ["file", "line", "severity", "text"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "comments": {"type": "array", "items": comment},
            "rubric_scores": {
                "type": "object",
                "properties": {i: score for i in rubric_ids},
                "required": list(rubric_ids),
                "additionalProperties": False,
            },
            "recommended_score": {"type": "number"},
            "needs_human": {"type": "boolean"},
            "needs_human_reason": {"type": ["string", "null"]},
            "integrity_flag": {"type": "boolean"},
        },
        "required": TOP_KEYS,
        "additionalProperties": False,
    }
