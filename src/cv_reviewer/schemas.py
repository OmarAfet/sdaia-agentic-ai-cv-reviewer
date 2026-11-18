"""Typed outputs of the three LLM agents (enforced with structured output)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Role(BaseModel):
    title: str
    organisation: str = Field(default="", description="Employer name, or empty")
    years: float = Field(default=0, description="Years in this role, 0 if unknown")


class CandidateProfile(BaseModel):
    """Parser agent output. Facts only, taken from the CV text."""

    summary: str = Field(description="Two-sentence professional summary, no personal data")
    skills: list[str] = Field(description="Technical and professional skills named in the CV")
    total_years_experience: float = Field(description="Total years of relevant work experience")
    roles: list[Role] = Field(default_factory=list)
    education: list[str] = Field(default_factory=list)
    certifications: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)


class Evidence(BaseModel):
    point: str = Field(description="A strength of the candidate for this job")
    evidence: str = Field(description="A short quote or fact from the CV that supports the point")


class MatchResult(BaseModel):
    """Matcher agent output."""

    score: int = Field(ge=0, le=100, description="Fit for the job, 0-100")
    recommendation: Literal["strong_match", "possible_match", "not_a_match"]
    strengths: list[Evidence] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list, description="Job requirements the CV does not show")
    rationale: str = Field(description="Why this score; job-related reasons only")
    candidate_feedback: str = Field(
        description="Polite, specific advice to the candidate on how to improve the CV for this job"
    )


class ReviewResult(BaseModel):
    """Reviewer agent output."""

    approved: bool
    issues: list[str] = Field(
        default_factory=list,
        description="Concrete problems: unsupported claims, wrong score, bias, missing gaps",
    )
    revision_instructions: str = Field(default="", description="What the Matcher must change")
