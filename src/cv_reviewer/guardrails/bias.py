"""Output filter: blocks agent output that reasons with protected attributes.

PII masking removes these attributes from the input, but a model can still
infer them (for example from a university name or a graduation year). This
check runs on the Matcher output, and a hit makes the Reviewer reject it.
"""

from __future__ import annotations

import re

PROTECTED_TERMS = re.compile(
    # Pronouns are included: the name is masked, so "he"/"she" means the model
    # guessed a gender. "single", "old" and "man" are excluded because they
    # match technical text ("single sign-on", "man-hours").
    r"\b(gender|male|female|woman|he|she|his|her|hers|age|aged|young|older|younger|"
    r"years old|nationality|national origin|foreigner|expat|race|ethnic\w*|religio\w*|muslim|"
    r"christian|married|pregnan\w*|children|kids|disabilit\w*|appearance|photo)\b",
    re.IGNORECASE,
)


def bias_scan(text: str) -> list[str]:
    return sorted({m.group(0).lower() for m in PROTECTED_TERMS.finditer(text)})
