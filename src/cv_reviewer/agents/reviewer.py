"""Reviewer agent: checks the Matcher output (Reflection / self-critique).

Two parts:
1. Deterministic checks that the LLM cannot talk its way past: protected-
   attribute terms (bias output filter), score drift from the tool score, and
   the score-to-recommendation band.
2. An LLM review for unsupported claims and missing gaps.
The result is approved only if both parts approve.
"""

from __future__ import annotations

import json
from typing import Any

from ..guardrails.bias import bias_scan
from ..llm import LLMClient
from ..schemas import ReviewResult
from . import prompts


def band(score: int) -> str:
    return "strong_match" if score >= 80 else "possible_match" if score >= 60 else "not_a_match"


def deterministic_checks(match: dict[str, Any], tool_result: dict[str, Any], max_drift: int) -> list[str]:
    issues = []
    text = " ".join([
        match["rationale"], match["candidate_feedback"], " ".join(match["gaps"]),
        " ".join(f"{s['point']} {s['evidence']}" for s in match["strengths"]),
    ])
    terms = bias_scan(text)
    if terms:
        issues.append(f"bias filter: output uses protected-attribute terms {terms}; "
                      "remove them and refer to 'the candidate'")
    drift = abs(match["score"] - tool_result["tool_score"])
    if drift > max_drift:
        issues.append(f"score {match['score']} differs from tool_score {tool_result['tool_score']} "
                      f"by {drift} (> {max_drift}); align it or justify with evidence")
    if match["recommendation"] != band(match["score"]):
        issues.append(f"recommendation '{match['recommendation']}' does not match score "
                      f"{match['score']} (expected '{band(match['score'])}')")
    return issues


def run_reviewer(
    llm: LLMClient,
    profile: dict[str, Any],
    job: dict[str, Any],
    tool_result: dict[str, Any],
    match: dict[str, Any],
    max_drift: int,
) -> tuple[ReviewResult, list[str]]:
    hard_issues = deterministic_checks(match, tool_result, max_drift)
    user = "\n\n".join([
        "JOB:\n" + json.dumps({k: job[k] for k in ("title", "required_skills", "nice_to_have",
                                                    "min_years")}, indent=2),
        "CANDIDATE PROFILE:\n<cv>\n" + json.dumps(profile, indent=2) + "\n</cv>",
        "SKILL-MATCH TOOL RESULT:\n" + json.dumps(tool_result, indent=2),
        "MATCHER OUTPUT TO REVIEW:\n" + json.dumps(match, indent=2),
        "Review the Matcher output.",
    ])
    review = llm.structured("reviewer", ReviewResult, prompts.REVIEWER, user)
    if hard_issues:
        review = ReviewResult(
            approved=False,
            issues=hard_issues + review.issues,
            revision_instructions=(review.revision_instructions + " " + " ".join(hard_issues)).strip(),
        )
    return review, hard_issues
