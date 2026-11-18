"""Test fixtures. FakeLLM replaces Groq so the workflow runs offline and the
tests can check exactly what each agent received."""

from __future__ import annotations

import dataclasses
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import pytest

from cv_reviewer.config import Settings
from cv_reviewer.llm import LLMUnavailableError

ROOT = Path(__file__).resolve().parents[1]
CVS = ROOT / "data" / "cvs"


@pytest.fixture(scope="session", autouse=True)
def sample_cvs() -> Path:
    if not (CVS / "strong_backend.pdf").exists():
        subprocess.run([sys.executable, str(ROOT / "scripts" / "make_samples.py")], check=True)
    return CVS


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return dataclasses.replace(Settings(), log_dir=tmp_path / "logs",
                               report_dir=tmp_path / "reports", llm_backoff_s=0)


class FakeLLM:
    """Scripted LLM. responses[agent] is a list; each call pops the next item
    (the last item repeats). An Exception item is raised."""

    def __init__(self, responses: dict[str, list[Any]],
                 injection: float | Callable[[str], float] = 0.0,
                 policy: bool | Callable[[str], bool] = False) -> None:
        self.responses = {k: list(v) for k, v in responses.items()}
        self.injection = injection
        self.policy = policy
        self.calls: list[dict[str, str]] = []

    def structured(self, agent, schema, system, user):
        self.calls.append({"agent": agent, "system": system, "user": user})
        queue = self.responses[agent]
        item = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(item, Exception):
            raise item
        return schema.model_validate(item)

    def injection_score(self, text: str) -> float:
        self.calls.append({"agent": "prompt_guard", "system": "", "user": text})
        return self.injection(text) if callable(self.injection) else self.injection

    def policy_violation(self, text: str) -> bool:
        self.calls.append({"agent": "safeguard", "system": "", "user": text})
        return self.policy(text) if callable(self.policy) else self.policy

    def agents_called(self) -> list[str]:
        return [c["agent"] for c in self.calls]


STRONG_PROFILE = {
    "summary": "Backend engineer with 5 years of Python REST API experience.",
    "skills": ["Python", "Django", "Django REST Framework", "PostgreSQL", "Redis", "Docker", "Git",
               "AWS", "CI/CD", "pytest", "REST API"],
    "total_years_experience": 5,
    "roles": [{"title": "Senior Backend Engineer", "organisation": "PayWave Fintech", "years": 3},
              {"title": "Backend Developer", "organisation": "Souq Online", "years": 2}],
    "education": ["BSc Computer Science"],
    "certifications": ["AWS Certified Developer - Associate"],
    "languages": [],
}

WEAK_PROFILE = {
    "summary": "Digital marketing specialist with 6 years of campaign experience.",
    "skills": ["Social media marketing", "Google Ads", "Canva", "Excel", "Copywriting"],
    "total_years_experience": 6, "roles": [], "education": ["BA Business Administration"],
    "certifications": [], "languages": [],
}


def match(score: int, rec: str, rationale: str = "Job-related evidence supports this score.",
          feedback: str = "Add measurable results for each backend project.") -> dict:
    return {"score": score, "recommendation": rec,
            "strengths": [{"point": "Python REST APIs", "evidence": "Designed Django REST Framework APIs"}],
            "gaps": ["Kubernetes"], "rationale": rationale, "candidate_feedback": feedback}


APPROVE = {"approved": True, "issues": [], "revision_instructions": ""}
REJECT = {"approved": False, "issues": ["strength not supported"], "revision_instructions": "fix it"}


def outage() -> LLMUnavailableError:
    return LLMUnavailableError("parser: all models failed; last error: APIConnectionError")
