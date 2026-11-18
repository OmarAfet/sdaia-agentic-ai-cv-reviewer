"""Input validation guardrail. Runs before any text reaches an LLM."""

from __future__ import annotations

import re
from pathlib import Path

from ..config import Settings

ALLOWED = {".pdf": b"%PDF", ".docx": b"PK\x03\x04", ".txt": None}
CV_SECTIONS = re.compile(
    r"\b(experience|employment|work history|education|skills|projects|certifications?|"
    r"summary|profile|qualifications|الخبرات|المهارات|التعليم)\b",
    re.IGNORECASE,
)


def validate_file(path: str | Path, settings: Settings) -> list[str]:
    """Checks that do not need the file content to be parsed."""
    path = Path(path)
    if not path.exists() or not path.is_file():
        return [f"file not found: {path.name}"]
    suffix = path.suffix.lower()
    if suffix not in ALLOWED:
        return [f"unsupported file type '{suffix}'; allowed: {', '.join(ALLOWED)}"]
    size = path.stat().st_size
    if size == 0:
        return ["file is empty"]
    if size > settings.max_file_bytes:
        return [f"file is {size} bytes; limit is {settings.max_file_bytes}"]
    magic = ALLOWED[suffix]
    if magic is not None:
        with path.open("rb") as fh:
            if fh.read(len(magic)) != magic:
                return [f"file content does not match its '{suffix}' extension"]
    return []


def validate_text(text: str, settings: Settings) -> list[str]:
    """Checks on the extracted visible text."""
    errors = []
    n = len(text.strip())
    if n < settings.min_text_chars:
        errors.append(f"CV has {n} readable characters; minimum is {settings.min_text_chars} "
                      "(the file may be empty or a scanned image)")
    if n > settings.max_text_chars:
        errors.append(f"CV has {n} characters; limit is {settings.max_text_chars}")
    sections = {m.lower() for m in CV_SECTIONS.findall(text)}
    if n >= settings.min_text_chars and len(sections) < 2:
        errors.append("document does not look like a CV (fewer than 2 CV sections found)")
    return errors
