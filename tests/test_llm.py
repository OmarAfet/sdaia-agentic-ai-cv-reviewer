"""Retry and fallback tests for GroqLLM (offline), plus live tests (-m live)."""

import os

import pytest
from pydantic import BaseModel

from cv_reviewer.llm import GroqLLM, LLMUnavailableError
from cv_reviewer.observability import Tracer
from cv_reviewer.pipeline import review_cv

from .conftest import CVS


class Out(BaseModel):
    value: int


class FakeChat:
    """Stands in for ChatGroq.with_structured_output(...)."""

    def __init__(self, behaviour):
        self.behaviour = behaviour

    def with_structured_output(self, *a, **k):
        return self

    def invoke(self, _messages):
        return self.behaviour()


def make_llm(settings, script):
    """script: model name -> list of behaviours, consumed in order."""
    tracer = Tracer("t")
    llm = GroqLLM(settings, tracer)
    llm._chat = lambda model: FakeChat(script[model].pop(0))
    return llm, tracer


def ok():
    class Raw:
        usage_metadata = {"input_tokens": 10, "output_tokens": 5}
    return {"parsed": Out(value=1), "raw": Raw()}


def boom():
    raise TimeoutError("read timeout")


def unparseable():
    return {"parsed": None, "raw": None, "parsing_error": "bad json"}


def test_retry_then_success_on_primary(settings):
    llm, tracer = make_llm(settings, {settings.primary_model: [boom, ok]})
    assert llm.structured("parser", Out, "s", "u").value == 1
    calls = [e for e in tracer.events if e["event"] == "llm_call"]
    assert [c["status"] for c in calls] == ["error", "ok"]


def test_fallback_model_after_primary_fails(settings):
    llm, tracer = make_llm(settings, {settings.primary_model: [boom, unparseable],
                                      settings.fallback_model: [ok]})
    assert llm.structured("parser", Out, "s", "u").value == 1
    assert tracer.summary()["models_used"] == [settings.fallback_model]
    assert tracer.summary()["llm_failures"] == 2


def test_all_models_fail_raises(settings):
    llm, _ = make_llm(settings, {settings.primary_model: [boom, boom],
                                 settings.fallback_model: [boom, boom],
                                 settings.fallback2_model: [boom, boom]})
    with pytest.raises(LLMUnavailableError):
        llm.structured("parser", Out, "s", "u")


class RateLimitError(Exception):
    """Same class name as groq.RateLimitError; retry_after_seconds checks the name."""


def test_rate_limit_waits_then_retries(settings, monkeypatch):
    slept = []
    monkeypatch.setattr("cv_reviewer.llm.time.sleep", slept.append)

    def limited():
        raise RateLimitError("Rate limit reached. Please try again in 2.5s.")
    llm, _ = make_llm(settings, {settings.primary_model: [limited, ok]})
    assert llm.structured("parser", Out, "s", "u").value == 1
    assert slept == [2.5]


def test_long_rate_limit_skips_to_next_model(settings, monkeypatch):
    slept = []
    monkeypatch.setattr("cv_reviewer.llm.time.sleep", slept.append)

    def limited():
        raise RateLimitError("Please try again in 1m30.2s.")
    llm, tracer = make_llm(settings, {settings.primary_model: [limited],
                                      settings.fallback_model: [ok]})
    assert llm.structured("parser", Out, "s", "u").value == 1
    assert slept == [] and tracer.summary()["models_used"] == [settings.fallback_model]


# ------------------------------------------------------------------- live
live = pytest.mark.skipif(not os.getenv("GROQ_API_KEY"), reason="GROQ_API_KEY not set")


@pytest.mark.live
@live
def test_live_strong_cv(settings):
    state = review_cv(CVS / "strong_backend.pdf", "backend-engineer", settings=settings)["state"]
    assert state["status"] == "proposed_shortlist", state.get("review_history")
    assert state["match"]["score"] >= settings.shortlist_threshold


@pytest.mark.live
@live
def test_live_weak_cv(settings):
    state = review_cv(CVS / "marketing_to_backend.txt", "backend-engineer", settings=settings)["state"]
    assert state["status"] == "proposed_reject"


@pytest.mark.live
@live
def test_live_safeguard_catches_subtle_attack(settings):
    state = review_cv(CVS / "injection_subtle.txt", "backend-engineer", settings=settings)["state"]
    assert state["status"] == "quarantined"
    assert state["injection"]["policy_violation"] is True


@pytest.mark.live
@live
def test_live_normal_cv_not_flagged(settings):
    state = review_cv(CVS / "junior_backend.docx", "backend-engineer", settings=settings)["state"]
    assert state["status"] != "quarantined", state["injection"]
