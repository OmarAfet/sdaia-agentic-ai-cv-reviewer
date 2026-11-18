"""PII masking guardrail.

Runs before the Parser, so no LLM sees contact data, IDs, or protected
attributes. Masking protected attributes (gender, age, nationality, religion,
marital status) also removes the main source of hiring bias: the model cannot
use what it never sees. The report returns counts per type, never the values.
"""

from __future__ import annotations

import re
from collections import Counter

PROTECTED_LABELS = (
    r"gender|sex|age|date of birth|d\.?o\.?b|birth ?date|place of birth|nationality|citizenship|"
    r"religion|marital status|family status|number of children|dependents|"
    r"الجنس|العمر|تاريخ الميلاد|الجنسية|الديانة|الحالة الاجتماعية"
)

PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("protected_attribute", re.compile(rf"(?im)^(\s*(?:{PROTECTED_LABELS})\s*[:\-–]\s*)(.+)$")),
    ("email", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("url", re.compile(r"(?i)\b(?:https?://|www\.)\S+|\b(?:linkedin|github)\.com/\S+")),
    ("iban", re.compile(r"\bSA\d{2}(?:\s?[0-9A-Z]{4}){5}\b")),
    # Saudi national ID / Iqama: 10 digits starting with 1 (citizen) or 2 (resident).
    ("national_id", re.compile(r"(?<!\d)[12]\d{9}(?!\d)")),
    ("phone", re.compile(r"(?<![\w])(?:\+|00)?\d{1,3}?[\s.-]?\(?\d{2,4}\)?(?:[\s.-]?\d{3,4}){2}(?![\w])")),
]

NAME_LABEL = re.compile(r"(?im)^(\s*(?:full )?name\s*[:\-–]\s*)(.+)$")
HEADINGS = {"curriculum vitae", "resume", "cv", "profile", "summary"}


def _mask_name_line(text: str, counts: Counter) -> str:
    text, n = NAME_LABEL.subn(lambda m: m.group(1) + "[NAME]", text)
    counts["name"] += n
    # Most CVs start with the candidate name: 2-4 capitalised words, no digits.
    lines = text.split("\n")
    for i, line in enumerate(lines[:3]):
        words = line.strip().split()
        if line.strip().lower() in HEADINGS or not words:
            continue
        if 2 <= len(words) <= 4 and all(re.fullmatch(r"[A-Z][a-zA-Z'\-]+|[؀-ۿ]+", w) for w in words):
            lines[i] = "[NAME]"
            counts["name"] += 1
        break
    return "\n".join(lines)


def mask_pii(text: str) -> tuple[str, dict[str, int]]:
    counts: Counter = Counter()
    text = _mask_name_line(text, counts)
    for kind, pattern in PATTERNS:
        if kind == "protected_attribute":
            text, n = pattern.subn(lambda m: m.group(1) + "[REDACTED_PROTECTED]", text)
        else:
            text, n = pattern.subn(f"[{kind.upper()}]", text)
        counts[kind] += n
    return text, {k: v for k, v in counts.items() if v}
