"""Skill match calculator tool: a deterministic score the LLM cannot change.

The Matcher gets this score as an anchor, and the Reviewer flags any LLM score
that drifts too far from it. This limits how much a persuasive CV, or a
hallucination, can move the result.

Weights: 70 % required skills, 20 % nice-to-have skills, 10 % years.
"""

from __future__ import annotations

import re
from typing import Any

ALIASES = {
    "js": "javascript", "ts": "typescript", "k8s": "kubernetes", "postgres": "postgresql",
    "psql": "postgresql", "py": "python", "golang": "go", "ml": "machine learning",
    "rest": "rest api", "restful": "rest api", "restful api": "rest api", "rest apis": "rest api",
    "ci/cd": "ci cd", "cicd": "ci cd", "aws cloud": "aws", "amazon web services": "aws",
    "ms excel": "excel", "microsoft excel": "excel", "powerbi": "power bi",
    "siem tools": "siem", "splunk siem": "splunk", "git/github": "git",
}


def normalise(skill: str) -> str:
    s = re.sub(r"\s+", " ", skill.lower().strip())
    s = s.replace("-", " ").replace("_", " ")
    s = re.sub(r"[()\.]", "", s).strip()
    return ALIASES.get(s, s)


def _has(skill: str, candidate: set[str], cv_text: str) -> bool:
    if skill in candidate:
        return True
    # Fall back to a whole-word search in the CV text (e.g. "Docker" in a sentence).
    return re.search(rf"(?<![a-z0-9]){re.escape(skill)}(?![a-z0-9])", cv_text) is not None


def skill_match(job: dict[str, Any], skills: list[str], years: float, cv_text: str) -> dict[str, Any]:
    candidate = {normalise(s) for s in skills}
    text = cv_text.lower()
    required = [normalise(s) for s in job["required_skills"]]
    nice = [normalise(s) for s in job.get("nice_to_have", [])]

    req_hit = [s for s in required if _has(s, candidate, text)]
    nice_hit = [s for s in nice if _has(s, candidate, text)]
    min_years = float(job.get("min_years", 0))
    years_ratio = 1.0 if min_years == 0 else min(years / min_years, 1.0)

    score = (
        70 * (len(req_hit) / len(required) if required else 1)
        + 20 * (len(nice_hit) / len(nice) if nice else 1)
        + 10 * years_ratio
    )
    return {
        "tool_score": round(score),
        "required_matched": req_hit,
        "required_missing": [s for s in required if s not in req_hit],
        "nice_to_have_matched": nice_hit,
        "years": years,
        "min_years": min_years,
    }
