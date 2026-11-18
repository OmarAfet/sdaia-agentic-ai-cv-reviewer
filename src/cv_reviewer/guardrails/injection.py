"""Prompt-injection detection guardrail (three layers).

Layer 1: regex rules. Fast, offline, explainable; catches known phrasings.
Layer 2: Meta Prompt Guard 2 (86M) classifier. Catches jailbreak-style text.
Layer 3: gpt-oss-safeguard-20b with a CV-specific policy. Catches polite,
rephrased instructions. In the live test on 2026-11-18, Prompt Guard scored
data/cvs/injection_subtle.txt at 0.0038 (missed); safeguard labelled it 1.

All layers scan visible AND hidden text. Hidden text is suspicious in itself,
so any hidden text is a finding. A detected injection quarantines the CV: it
goes to a human, never to the scoring agents.
"""

from __future__ import annotations

import re
from typing import Any, Callable

RULES: list[tuple[str, str]] = [
    ("override_instructions", r"\b(ignore|disregard|forget|override)\b.{0,40}\b(previous|prior|above|all|earlier|system)\b.{0,20}\b(instructions?|prompts?|rules?|directions?)"),
    ("role_hijack", r"\b(you are now|act as|pretend to be|new instructions?|system prompt|developer mode)\b"),
    ("score_manipulation", r"\b(rank|rate|score|mark|grade)\b.{0,40}\b(this|the|me|candidate)\b.{0,40}\b(first|highest|top|100|10/10|perfect|best)\b"),
    ("score_manipulation", r"\b(give|assign)\b.{0,30}\b(score|rating)\b.{0,20}\b(100|10/10|maximum|perfect)\b"),
    ("forced_outcome", r"\b(must|should|always)\b.{0,30}\b(be )?(shortlist|hire|hired|selected|approved|recommended)\b"),
    ("ai_addressing", r"\b(note to|attention|dear)\b.{0,20}\b(ai|llm|gpt|chatgpt|assistant|screener|ats|bot)\b"),
    ("delimiter_escape", r"(</?(system|cv|instructions?)>|\[/?inst\]|<\|im_start\|>)"),
    ("override_instructions_ar", r"(تجاهل|انسَ|انس)\s.{0,30}(التعليمات|الأوامر)"),
]
_COMPILED = [(name, re.compile(rx, re.IGNORECASE | re.DOTALL)) for name, rx in RULES]


def rule_scan(text: str) -> list[dict[str, str]]:
    hits = []
    for name, rx in _COMPILED:
        for m in rx.finditer(text):
            hits.append({"rule": name, "match": m.group(0)[:120]})
    return hits


def detect_injection(
    visible_text: str,
    hidden_text: str,
    zero_width_chars: int,
    classifier: Callable[[str], float] | None,
    threshold: float,
    policy_classifier: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    full = f"{visible_text}\n{hidden_text}".strip()
    hits = rule_scan(full)
    result: dict[str, Any] = {
        "rule_hits": hits,
        "hidden_text_found": bool(hidden_text.strip()),
        "hidden_text_preview": hidden_text.strip()[:300],
        "zero_width_chars": zero_width_chars,
        "classifier_score": None,
        "classifier_status": "skipped",
        "policy_violation": None,
        "policy_status": "skipped",
    }
    # Layers 2 and 3 fail open: if a model is down, the other layers still
    # decide, and the report shows the layer as unavailable.
    if classifier is not None:
        try:
            result["classifier_score"] = round(classifier(full), 4)
            result["classifier_status"] = "ok"
        except Exception as exc:
            result["classifier_status"] = f"unavailable: {type(exc).__name__}"
    if policy_classifier is not None:
        try:
            result["policy_violation"] = bool(policy_classifier(full))
            result["policy_status"] = "ok"
        except Exception as exc:
            result["policy_status"] = f"unavailable: {type(exc).__name__}"

    score = result["classifier_score"]
    hidden_hits = bool(hidden_text.strip()) and (bool(rule_scan(hidden_text)) or (score or 0) >= threshold)
    result["detected"] = (bool(hits) or (score is not None and score >= threshold)
                          or bool(result["policy_violation"]) or hidden_hits)
    return result
