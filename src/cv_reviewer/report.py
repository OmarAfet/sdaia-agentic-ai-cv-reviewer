"""Final report (Markdown + JSON). The PII mask runs again on the output."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import Settings
from .guardrails.pii import mask_pii
from .observability import Tracer

OUTCOME = {
    "shortlist": "SHORTLISTED (HR approved)",
    "reject": "NOT SHORTLISTED (HR approved)",
    "hold": "ON HOLD for manual HR review",
}


def outcome(state: dict[str, Any]) -> str:
    if state.get("status") == "invalid":
        return "REJECTED INPUT (validation failed)"
    if state.get("status") == "system_error":
        return "SYSTEM ERROR (no decision made; retry later)"
    hr = state.get("hr_decision")
    return OUTCOME.get(hr["decision"], "UNKNOWN") if hr else "PENDING HR APPROVAL"


def build_report(state: dict[str, Any], tracer: Tracer, settings: Settings) -> tuple[str, Path]:
    run_id = state["run_id"]
    lines = [
        f"# CV Review Report: candidate {run_id}",
        "",
        f"- **Job:** {state.get('job', {}).get('title', state.get('job_id'))}",
        f"- **Outcome:** {outcome(state)}",
        f"- **Workflow status:** `{state.get('status')}`",
    ]
    if state.get("errors"):
        lines += ["", "## Errors", *[f"- {e}" for e in state["errors"]]]

    match = state.get("match")
    if match and state.get("status") not in ("quarantined",):
        tool = state.get("tool_result", {})
        lines += [
            "", "## Assessment",
            f"- **Score:** {match['score']}/100 (deterministic tool score: {tool.get('tool_score')})",
            f"- **Recommendation:** {match['recommendation']}",
            f"- **Rationale:** {match['rationale']}",
            "", "### Strengths",
            *[f"- {s['point']} (evidence: \"{s['evidence'].strip().strip('\"')}\")"
              for s in match["strengths"]],
            "", "### Gaps", *([f"- {g}" for g in match["gaps"]] or ["- none"]),
            "", "### Feedback for the candidate", match["candidate_feedback"],
        ]
    if state.get("review_history"):
        lines += ["", "## Reviewer loop (reflection)"]
        for h in state["review_history"]:
            verdict = "approved" if h["approved"] else "revision requested"
            lines.append(f"- Round {h['revision'] + 1}: score {h['score']} -> {verdict}")
            lines += [f"  - {i}" for i in h["issues"]]

    inj = state.get("injection")
    if inj:
        lines += [
            "", "## Security",
            f"- **Prompt injection detected:** {inj['detected']}",
            f"- **Rule hits:** {[h['rule'] for h in inj['rule_hits']] or 'none'}",
            f"- **Prompt Guard score:** {inj['classifier_score']} ({inj['classifier_status']})",
            f"- **Safeguard policy violation:** {inj.get('policy_violation')} ({inj.get('policy_status')})",
            f"- **Hidden text found:** {inj['hidden_text_found']}",
            f"- **Zero-width characters removed:** {inj['zero_width_chars']}",
        ]
        if inj["hidden_text_found"]:
            lines.append(f"- **Hidden text preview:** `{inj['hidden_text_preview'][:200]}`")
    if state.get("pii_counts") is not None:
        lines.append(f"- **PII masked before any LLM call:** {state['pii_counts'] or 'none found'}")

    hr = state.get("hr_decision")
    if hr:
        lines += ["", "## Human approval",
                  f"- Proposal: {hr['proposal']} -> decision: **{hr['decision']}** by {hr['by']}"
                  + (" (override)" if hr["overridden"] else ""),
                  f"- Note: {hr['note'] or '-'}"]

    s = tracer.summary()
    lines += [
        "", "## Run metrics",
        f"- Total time: {s['total_ms']} ms; LLM calls: {s['llm_calls']} "
        f"({s['llm_failures']} failed); tokens in/out: {s['input_tokens']}/{s['output_tokens']}",
        f"- Models used: {', '.join(s['models_used']) or 'none'}; tool calls: {s['tool_calls']}",
        f"- Trace log: `{s['log_file']}`",
    ]

    # Output filter: mask again, in case an agent copied PII into its text.
    markdown, leaked = mask_pii("\n".join(lines))
    leaked.pop("name", None)  # the report title line is not a person's name
    if leaked:
        tracer.log("guardrail", name="output_pii_filter", masked=leaked)

    settings.report_dir.mkdir(parents=True, exist_ok=True)
    path = settings.report_dir / f"{run_id}.md"
    path.write_text(markdown + "\n", encoding="utf-8")
    data = {k: v for k, v in state.items()
            if k not in ("visible_text", "hidden_text", "masked_text", "report_markdown")}
    data["outcome"] = outcome(state)
    (settings.report_dir / f"{run_id}.json").write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return markdown, path
