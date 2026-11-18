"""Matcher agent: profile + job + tool score -> MatchResult.

On a revision, the Matcher also gets its previous answer and the Reviewer's
critique (the Reflection loop).
"""

from __future__ import annotations

import json
from typing import Any

from ..llm import LLMClient
from ..schemas import MatchResult
from . import prompts


def run_matcher(
    llm: LLMClient,
    profile: dict[str, Any],
    job: dict[str, Any],
    tool_result: dict[str, Any],
    previous: dict[str, Any] | None = None,
    critique: dict[str, Any] | None = None,
) -> MatchResult:
    parts = [
        "JOB:\n" + json.dumps({k: job[k] for k in ("title", "description", "required_skills",
                                                    "nice_to_have", "min_years")}, indent=2),
        "CANDIDATE PROFILE (extracted by the Parser agent from the CV):\n<cv>\n"
        + json.dumps(profile, indent=2) + "\n</cv>",
        "SKILL-MATCH TOOL RESULT:\n" + json.dumps(tool_result, indent=2),
    ]
    if previous and critique:
        parts.append("YOUR PREVIOUS ANSWER:\n" + json.dumps(previous, indent=2))
        parts.append("REVIEWER CRITIQUE - fix every issue:\n" + json.dumps(critique, indent=2))
    parts.append("Score the candidate for this job.")
    return llm.structured("matcher", MatchResult, prompts.MATCHER, "\n\n".join(parts))
