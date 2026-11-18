"""LangGraph workflow.

    intake ──invalid──────────────────────────────────────────────┐
      │ok                                                         │
    security ──injection──────────────────────┐                   │
      │clean                                  │                   │
    parser ──llm down──────────────────────────┼──────────────────┤
      │                                       │                   │
    matcher ◄──revise (max 2)── reviewer      │                   │
      │                           │approved   │                   │
      └──────────► reviewer       ▼           ▼                   ▼
                    │ exhausted  decide ──► hr_review ──► finalize ──► END
                    └─────────────────────────┘

Decision points (conditional edges): after intake, after security, after
parser/matcher/reviewer (LLM failure), after reviewer (approve / revise /
escalate), and in decide (score vs. shortlist threshold).
"""

from __future__ import annotations

import functools
from typing import Any, Callable, Literal, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from .agents.matcher import run_matcher
from .agents.parser import run_parser
from .agents.reviewer import run_reviewer
from .config import Settings
from .guardrails.injection import detect_injection
from .guardrails.pii import mask_pii
from .guardrails.validation import validate_file, validate_text
from .llm import LLMClient, LLMUnavailableError
from .observability import Tracer
from .report import build_report
from .tools.file_reader import read_cv
from .tools.job_db import JobDB
from .tools.skill_match import skill_match

Status = Literal[
    "running", "invalid", "quarantined", "system_error", "needs_human_review",
    "proposed_shortlist", "proposed_reject", "done",
]


class ReviewState(TypedDict, total=False):
    run_id: str
    file_path: str
    job_id: str
    status: Status
    errors: list[str]
    job: dict[str, Any]
    visible_text: str
    hidden_text: str
    zero_width_chars: int
    masked_text: str
    pii_counts: dict[str, int]
    injection: dict[str, Any]
    profile: dict[str, Any]
    tool_result: dict[str, Any]
    match: dict[str, Any]
    review: dict[str, Any]
    revision_count: int
    review_history: list[dict[str, Any]]
    hr_decision: dict[str, Any]
    report_markdown: str
    report_path: str


def build_graph(llm: LLMClient, tracer: Tracer, settings: Settings, jobs: JobDB, checkpointer=None):
    def node(name: str) -> Callable:
        """Wrap a node: time it, log it, turn LLM outages into a system_error state."""

        def deco(fn: Callable[[ReviewState], dict]) -> Callable[[ReviewState], dict]:
            @functools.wraps(fn)
            def wrapper(state: ReviewState) -> dict:
                with tracer.span("node", name) as extra:
                    try:
                        update = fn(state)
                    except LLMUnavailableError as exc:
                        extra["handled_error"] = str(exc)[:300]
                        update = {"status": "system_error",
                                  "errors": state.get("errors", []) + [f"{name}: {exc}"]}
                    extra["status_after"] = update.get("status", state.get("status"))
                    return update
            return wrapper
        return deco

    # ---------------------------------------------------------------- nodes
    @node("intake")
    def intake(state: ReviewState) -> dict:
        errors = validate_file(state["file_path"], settings)
        job = jobs.get_job(state["job_id"])
        tracer.log("tool_call", tool="job_db.get_job", args={"job_id": state["job_id"]},
                   found=job is not None)
        if job is None:
            errors.append(f"unknown job id '{state['job_id']}'; known: "
                          f"{[j['id'] for j in jobs.list_jobs()]}")
        if errors:
            tracer.log("guardrail", name="input_validation", passed=False, errors=errors)
            return {"status": "invalid", "errors": errors}
        try:
            with tracer.span("tool_call", "file_reader.read_cv", file=state["file_path"]) as extra:
                doc = read_cv(state["file_path"])
                extra.update(chars=len(doc.visible_text), hidden_chars=len(doc.hidden_text))
        except Exception as exc:  # corrupt or encrypted files
            errors = [f"cannot read file: {type(exc).__name__}: {exc}"]
            tracer.log("guardrail", name="input_validation", passed=False, errors=errors)
            return {"status": "invalid", "errors": errors}
        errors = validate_text(doc.visible_text, settings)
        tracer.log("guardrail", name="input_validation", passed=not errors, errors=errors)
        if errors:
            return {"status": "invalid", "errors": errors}
        return {"status": "running", "job": job, "visible_text": doc.visible_text,
                "hidden_text": doc.hidden_text, "zero_width_chars": doc.zero_width_chars}

    @node("security")
    def security(state: ReviewState) -> dict:
        # Mask first: the guard models run on Groq too, so they get masked text.
        masked, counts = mask_pii(state["visible_text"])
        masked_hidden, _ = mask_pii(state["hidden_text"])
        tracer.log("guardrail", name="pii_masking", masked=counts)
        result = detect_injection(
            masked, masked_hidden, state["zero_width_chars"],
            classifier=llm.injection_score, threshold=settings.injection_threshold,
            policy_classifier=llm.policy_violation,
        )
        tracer.log("guardrail", name="prompt_injection", detected=result["detected"],
                   rule_hits=result["rule_hits"], classifier_score=result["classifier_score"],
                   classifier_status=result["classifier_status"],
                   policy_violation=result["policy_violation"], policy_status=result["policy_status"],
                   hidden_text_found=result["hidden_text_found"])
        update: dict[str, Any] = {"masked_text": masked, "pii_counts": counts, "injection": result}
        if result["detected"]:
            update["status"] = "quarantined"
        return update

    @node("parser")
    def parser(state: ReviewState) -> dict:
        profile = run_parser(llm, state["masked_text"])
        return {"profile": profile.model_dump()}

    @node("matcher")
    def matcher(state: ReviewState) -> dict:
        tool_result = state.get("tool_result")
        if tool_result is None:
            with tracer.span("tool_call", "skill_match") as extra:
                tool_result = skill_match(state["job"], state["profile"]["skills"],
                                          state["profile"]["total_years_experience"],
                                          state["masked_text"])
                extra["tool_score"] = tool_result["tool_score"]
        revising = state.get("revision_count", 0) > 0
        match = run_matcher(
            llm, state["profile"], state["job"], tool_result,
            previous=state.get("match") if revising else None,
            critique=state.get("review") if revising else None,
        )
        return {"tool_result": tool_result, "match": match.model_dump()}

    @node("reviewer")
    def reviewer(state: ReviewState) -> dict:
        review, hard = run_reviewer(llm, state["profile"], state["job"], state["tool_result"],
                                    state["match"], settings.max_score_drift)
        tracer.log("guardrail", name="bias_and_consistency_filter", passed=not hard, issues=hard)
        history = state.get("review_history", []) + [{
            "revision": state.get("revision_count", 0), "score": state["match"]["score"],
            "approved": review.approved, "issues": review.issues,
        }]
        update: dict[str, Any] = {"review": review.model_dump(), "review_history": history}
        if not review.approved:
            update["revision_count"] = state.get("revision_count", 0) + 1
            if update["revision_count"] > settings.max_revisions:
                update["status"] = "needs_human_review"
        return update

    @node("decide")
    def decide(state: ReviewState) -> dict:
        score = state["match"]["score"]
        status = "proposed_shortlist" if score >= settings.shortlist_threshold else "proposed_reject"
        tracer.log("decision", score=score, threshold=settings.shortlist_threshold, status=status)
        return {"status": status}

    @node("hr_review")
    def hr_review(state: ReviewState) -> dict:
        # Human-in-the-loop: the graph pauses here until a human answers.
        # No CV is shortlisted, rejected or released from quarantine by AI alone.
        proposal = {
            "proposed_shortlist": "shortlist", "proposed_reject": "reject",
        }.get(state["status"], "hold")
        answer = interrupt({
            "status": state["status"],
            "proposal": proposal,
            "job": state["job"]["title"],
            "score": state.get("match", {}).get("score"),
            "recommendation": state.get("match", {}).get("recommendation"),
            "security": state.get("injection", {}),
            "review_issues": state.get("review", {}).get("issues", []),
        })
        decision = answer.get("decision", proposal)
        if decision not in {"shortlist", "reject", "hold"}:
            decision = "hold"
        record = {"decision": decision, "proposal": proposal, "by": answer.get("by", "unknown"),
                  "note": answer.get("note", ""), "overridden": decision != proposal}
        tracer.log("human_approval", **record)
        return {"hr_decision": record}

    @node("finalize")
    def finalize(state: ReviewState) -> dict:
        md, path = build_report(state, tracer, settings)
        return {"report_markdown": md, "report_path": str(path)}

    # ---------------------------------------------------------------- routing
    def after_intake(state: ReviewState) -> str:
        return "finalize" if state["status"] == "invalid" else "security"

    def after_security(state: ReviewState) -> str:
        return "hr_review" if state.get("status") == "quarantined" else "parser"

    def after_llm(next_node: str) -> Callable[[ReviewState], str]:
        return lambda state: "finalize" if state.get("status") == "system_error" else next_node

    def after_reviewer(state: ReviewState) -> str:
        if state.get("status") == "system_error":
            return "finalize"
        if state["review"]["approved"]:
            return "decide"
        if state.get("status") == "needs_human_review":
            return "hr_review"
        return "matcher"  # reflection loop

    g = StateGraph(ReviewState)
    for name, fn in [("intake", intake), ("security", security), ("parser", parser),
                     ("matcher", matcher), ("reviewer", reviewer), ("decide", decide),
                     ("hr_review", hr_review), ("finalize", finalize)]:
        g.add_node(name, fn)
    g.add_edge(START, "intake")
    g.add_conditional_edges("intake", after_intake, ["security", "finalize"])
    g.add_conditional_edges("security", after_security, ["parser", "hr_review"])
    g.add_conditional_edges("parser", after_llm("matcher"), ["matcher", "finalize"])
    g.add_conditional_edges("matcher", after_llm("reviewer"), ["reviewer", "finalize"])
    g.add_conditional_edges("reviewer", after_reviewer, ["decide", "matcher", "hr_review", "finalize"])
    g.add_edge("decide", "hr_review")
    g.add_edge("hr_review", "finalize")
    g.add_edge("finalize", END)
    return g.compile(checkpointer=checkpointer or InMemorySaver())
