"""Parser agent: masked CV text -> structured CandidateProfile."""

from __future__ import annotations

from ..llm import LLMClient
from ..schemas import CandidateProfile
from . import prompts


def run_parser(llm: LLMClient, masked_cv: str) -> CandidateProfile:
    user = f"<cv>\n{masked_cv}\n</cv>\n\nExtract the candidate profile."
    return llm.structured("parser", CandidateProfile, prompts.PARSER, user)
