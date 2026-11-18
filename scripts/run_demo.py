"""Run every sample CV through the real workflow and print a results table.

    uv run python scripts/run_demo.py                 # all samples
    uv run python scripts/run_demo.py strong injection # only names that contain a word

Uses the auto-approver (no prompts). Each row prints the single-CV command
that repeats it. Free-tier Groq limits (8k tokens/min on gpt-oss-120b,
3 requests/min on gpt-oss-safeguard-20b) make the full run take a few minutes.
"""

from __future__ import annotations

import sys
from pathlib import Path

from cv_reviewer.pipeline import review_cv

ROOT = Path(__file__).resolve().parents[1]
CASES = [
    ("strong_backend.pdf", "backend-engineer", "normal: strong match"),
    ("junior_backend.docx", "backend-engineer", "normal: partial match"),
    ("marketing_to_backend.txt", "backend-engineer", "normal: weak match"),
    ("injection_hidden_text.pdf", "backend-engineer", "attack: white 1pt text in PDF"),
    ("injection_hidden_text.docx", "backend-engineer", "attack: white 1pt text in DOCX"),
    ("injection_visible.txt", "backend-engineer", "attack: visible instruction"),
    ("injection_subtle.txt", "backend-engineer", "attack: rephrased, no keywords"),
    ("invalid_not_a_cv.txt", "backend-engineer", "invalid: not a CV"),
    ("invalid_fake.pdf", "backend-engineer", "invalid: text renamed .pdf"),
    ("invalid_empty.pdf", "backend-engineer", "invalid: empty PDF"),
    ("invalid_file.exe", "backend-engineer", "invalid: .exe"),
    ("strong_backend.pdf", "astronaut", "invalid: unknown job"),
]


def main() -> None:
    filters = sys.argv[1:]
    rows = []
    for name, job, label in CASES:
        if filters and not any(f in name or f in label for f in filters):
            continue
        cmd = f"uv run cv-review review data/cvs/{name} --job {job} --auto-approve"
        print(f"$ {cmd}    # {label}", flush=True)
        out = review_cv(ROOT / "data" / "cvs" / name, job)
        st, sm = out["state"], out["summary"]
        rows.append((label, st["status"], st.get("match", {}).get("score", "-"),
                     st.get("hr_decision", {}).get("decision", "-"), sm["llm_calls"],
                     f"{sm['total_ms'] / 1000:.1f}s", st["report_path"]))
    print()
    header = ("case", "status", "score", "decision", "llm", "time", "report")
    widths = [max(len(str(r[i])) for r in rows + [header]) for i in range(len(header))]
    for r in [header] + rows:
        print("  ".join(str(v).ljust(w) for v, w in zip(r, widths)))


if __name__ == "__main__":
    main()
