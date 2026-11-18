"""Runtime settings. Every value can be overridden with an environment variable."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


@dataclass(frozen=True)
class Settings:
    # Models verified on the Groq /models endpoint on 2026-11-18.
    # llama-3.3-70b-versatile (used in the course labs) is no longer served.
    # Each model uses the structured-output method that worked in a probe on
    # 2026-11-18 (MatcherResult schema, 2 calls each): gpt-oss-20b failed 2/2
    # with function_calling (output_parse_failed) and passed 2/2 with json_schema.
    primary_model: str = _env("CV_PRIMARY_MODEL", "openai/gpt-oss-120b")
    primary_method: str = _env("CV_PRIMARY_METHOD", "function_calling")
    fallback_model: str = _env("CV_FALLBACK_MODEL", "openai/gpt-oss-20b")
    fallback_method: str = _env("CV_FALLBACK_METHOD", "json_schema")
    fallback2_model: str = _env("CV_FALLBACK2_MODEL", "qwen/qwen3.8-27b")
    fallback2_method: str = _env("CV_FALLBACK2_METHOD", "function_calling")
    guard_model: str = _env("CV_GUARD_MODEL", "meta-llama/llama-prompt-guard-2-86m")
    # Policy classifier: catches polite, rephrased injections that Prompt Guard
    # scored 0.0038 in the live test (see docs/decisions.md, ADR-004).
    safeguard_model: str = _env("CV_SAFEGUARD_MODEL", "openai/gpt-oss-safeguard-20b")

    llm_timeout_s: float = float(_env("CV_LLM_TIMEOUT_S", "45"))
    llm_attempts_per_model: int = int(_env("CV_LLM_ATTEMPTS", "2"))
    llm_backoff_s: float = float(_env("CV_LLM_BACKOFF_S", "1.0"))
    # On HTTP 429, wait the time Groq asks for, up to this cap; above it, go to
    # the next model at once (each model has its own rate-limit bucket).
    rate_limit_max_wait_s: float = float(_env("CV_RATE_LIMIT_MAX_WAIT_S", "30"))

    @property
    def model_chain(self) -> list[tuple[str, str]]:
        return [(self.primary_model, self.primary_method),
                (self.fallback_model, self.fallback_method),
                (self.fallback2_model, self.fallback2_method)]

    # Workflow thresholds.
    shortlist_threshold: int = int(_env("CV_SHORTLIST_THRESHOLD", "70"))
    max_revisions: int = int(_env("CV_MAX_REVISIONS", "2"))
    # The Matcher score may differ from the deterministic tool score by at most
    # this many points before the Reviewer asks for a revision.
    max_score_drift: int = int(_env("CV_MAX_SCORE_DRIFT", "25"))

    # Input validation limits.
    max_file_bytes: int = int(_env("CV_MAX_FILE_BYTES", str(5 * 1024 * 1024)))
    min_text_chars: int = int(_env("CV_MIN_TEXT_CHARS", "200"))
    max_text_chars: int = int(_env("CV_MAX_TEXT_CHARS", "30000"))

    # Prompt Guard returns a probability that the text is an attack.
    injection_threshold: float = float(_env("CV_INJECTION_THRESHOLD", "0.5"))

    jobs_file: Path = PROJECT_ROOT / "data" / "jobs.json"
    log_dir: Path = PROJECT_ROOT / "logs"
    report_dir: Path = PROJECT_ROOT / "reports"
