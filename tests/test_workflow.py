"""End-to-end workflow tests with the scripted FakeLLM.

Scenarios: normal (strong, weak), reflection loop, escalation, HR override,
invalid inputs, LLM outage, and prompt-injection attacks.
"""

import json

import pytest

from cv_reviewer.pipeline import review_cv

from .conftest import (APPROVE, CVS, REJECT, STRONG_PROFILE, WEAK_PROFILE, FakeLLM, match,
                       outage)


def run(name, llm, settings, job="backend-engineer", **kw):
    out = review_cv(CVS / name, job, llm=llm, settings=settings, run_id="cv-test", **kw)
    return out["state"], out["summary"]


# ------------------------------------------------------------------ normal
def test_strong_cv_is_shortlisted_after_hr_approval(settings):
    llm = FakeLLM({"parser": [STRONG_PROFILE], "matcher": [match(92, "strong_match")],
                   "reviewer": [APPROVE]})
    state, summary = run("strong_backend.pdf", llm, settings)
    assert state["tool_result"]["tool_score"] == 95
    assert state["hr_decision"]["decision"] == "shortlist"
    assert llm.agents_called() == ["prompt_guard", "safeguard", "parser", "matcher", "reviewer"]
    assert "SHORTLISTED" in state["report_markdown"]
    assert (settings.report_dir / "cv-test.md").exists()
    assert summary["tool_calls"] >= 3 and summary["errors"] == []


def test_no_pii_reaches_any_llm(settings):
    llm = FakeLLM({"parser": [STRONG_PROFILE], "matcher": [match(92, "strong_match")],
                   "reviewer": [APPROVE]})
    run("strong_backend.pdf", llm, settings)
    assert {"prompt_guard", "safeguard", "parser"} <= set(llm.agents_called())
    sent = "\n".join(c["user"] for c in llm.calls)  # includes the guard models
    for secret in ["Sara", "Al-Harbi", "sara.harbi.dev@example.com", "123 4567", "1098765432",
                   "Female", "Saudi", "14/03/1996", "linkedin.com"]:
        assert secret not in sent, f"PII leaked to LLM: {secret}"


def test_weak_cv_is_proposed_for_rejection(settings):
    llm = FakeLLM({"parser": [WEAK_PROFILE], "matcher": [match(10, "not_a_match")],
                   "reviewer": [APPROVE]})
    state, _ = run("marketing_to_backend.txt", llm, settings)
    assert state["status"] == "proposed_reject"
    assert state["hr_decision"]["decision"] == "reject"


# ------------------------------------------------------ reflection loop
def test_reviewer_rejects_biased_output_then_matcher_revises(settings):
    biased = match(92, "strong_match", rationale="She is young and energetic.")
    llm = FakeLLM({"parser": [STRONG_PROFILE], "matcher": [biased, match(92, "strong_match")],
                   "reviewer": [APPROVE]})
    state, _ = run("strong_backend.pdf", llm, settings)
    hist = state["review_history"]
    assert [h["approved"] for h in hist] == [False, True]
    assert "bias filter" in hist[0]["issues"][0]
    revision_prompt = [c for c in llm.calls if c["agent"] == "matcher"][1]["user"]
    assert "REVIEWER CRITIQUE" in revision_prompt  # the critique is fed back
    assert state["hr_decision"]["decision"] == "shortlist"


def test_score_drift_and_wrong_band_are_rejected(settings):
    inflated = match(40, "strong_match")  # tool score is 95, band is wrong
    llm = FakeLLM({"parser": [STRONG_PROFILE], "matcher": [inflated, match(90, "strong_match")],
                   "reviewer": [APPROVE]})
    state, _ = run("strong_backend.pdf", llm, settings)
    issues = " ".join(state["review_history"][0]["issues"])
    assert "differs from tool_score" in issues and "does not match score" in issues


def test_loop_stops_after_max_revisions_and_escalates(settings):
    llm = FakeLLM({"parser": [STRONG_PROFILE], "matcher": [match(92, "strong_match")],
                   "reviewer": [REJECT]})
    state, _ = run("strong_backend.pdf", llm, settings)
    assert state["status"] == "needs_human_review"
    assert llm.agents_called().count("matcher") == settings.max_revisions + 1
    assert state["hr_decision"]["decision"] == "hold"


def test_hr_can_override_the_ai(settings):
    llm = FakeLLM({"parser": [STRONG_PROFILE], "matcher": [match(92, "strong_match")],
                   "reviewer": [APPROVE]})
    state, _ = run("strong_backend.pdf", llm, settings,
                   approver=lambda p: {"decision": "reject", "by": "hr.lead", "note": "role filled"})
    assert state["hr_decision"] == {"decision": "reject", "proposal": "shortlist", "by": "hr.lead",
                                    "note": "role filled", "overridden": True}


# ------------------------------------------------------ invalid inputs
@pytest.mark.parametrize("name, job, expected", [
    ("invalid_fake.pdf", "backend-engineer", "does not match"),
    ("invalid_empty.pdf", "backend-engineer", "minimum"),
    ("invalid_file.exe", "backend-engineer", "unsupported"),
    ("invalid_not_a_cv.txt", "backend-engineer", "does not look like a CV"),
    ("strong_backend.pdf", "astronaut", "unknown job id"),
])
def test_invalid_input_stops_before_any_llm_call(settings, name, job, expected):
    llm = FakeLLM({})
    state, _ = run(name, llm, settings, job=job)
    assert state["status"] == "invalid"
    assert expected in " ".join(state["errors"])
    assert llm.calls == []
    assert "REJECTED INPUT" in state["report_markdown"]


# ------------------------------------------------------ failure cases
def test_llm_outage_ends_in_system_error_not_a_crash(settings):
    llm = FakeLLM({"parser": [outage()]})
    state, summary = run("strong_backend.pdf", llm, settings)
    assert state["status"] == "system_error"
    assert "SYSTEM ERROR" in state["report_markdown"]
    assert "hr_decision" not in state  # no decision without an assessment
    node_log = [e for e in summary["nodes"] if e["name"] == "parser"]
    assert node_log and node_log[0]["status"] == "ok"  # handled, logged with handled_error


def test_trace_log_is_written(settings):
    llm = FakeLLM({"parser": [STRONG_PROFILE], "matcher": [match(92, "strong_match")],
                   "reviewer": [APPROVE]})
    run("strong_backend.pdf", llm, settings)
    events = [json.loads(line) for line in (settings.log_dir / "cv-test.jsonl").read_text().splitlines()]
    kinds = {e["event"] for e in events}
    assert {"run_start", "node", "tool_call", "guardrail", "decision", "human_approval", "run_end"} <= kinds


# ------------------------------------------------------ security attacks
@pytest.mark.parametrize("name", ["injection_hidden_text.pdf", "injection_hidden_text.docx",
                                  "injection_visible.txt"])
def test_injection_is_quarantined_and_never_scored(settings, name):
    llm = FakeLLM({})
    state, _ = run(name, llm, settings)
    assert state["status"] == "quarantined"
    assert state["injection"]["rule_hits"]
    assert llm.agents_called() == ["prompt_guard", "safeguard"]  # scoring agents never ran
    assert state["hr_decision"]["decision"] == "hold"
    assert "Prompt injection detected:** True" in state["report_markdown"]


def test_subtle_injection_caught_by_classifier_layer(settings):
    llm = FakeLLM({}, injection=lambda text: 0.97 if "directive" in text else 0.001)
    state, _ = run("injection_subtle.txt", llm, settings)
    assert state["status"] == "quarantined"
    assert state["injection"]["rule_hits"] == []
    assert state["injection"]["classifier_score"] == 0.97


def test_rephrased_injection_caught_by_policy_layer(settings):
    # Prompt Guard misses it (live score 0.0038); the safeguard policy layer flags it.
    llm = FakeLLM({}, injection=0.0038, policy=lambda text: "directive" in text)
    state, _ = run("injection_subtle.txt", llm, settings)
    assert state["status"] == "quarantined"
    assert state["injection"]["policy_violation"] is True
    assert state["injection"]["classifier_score"] == 0.0038


def test_guard_models_down_rules_still_block(settings):
    def down(_):
        raise ConnectionError("guard down")
    llm = FakeLLM({}, injection=down, policy=down)
    state, _ = run("injection_visible.txt", llm, settings)
    assert state["status"] == "quarantined"
    assert state["injection"]["policy_status"].startswith("unavailable")
