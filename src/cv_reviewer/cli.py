"""Command line interface.

    cv-review review data/cvs/strong_backend.pdf --job backend-engineer
    cv-review review data/cvs/strong_backend.pdf --job backend-engineer --auto-approve
    cv-review jobs
    cv-review graph            # print the workflow as a Mermaid diagram
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any

from .config import Settings
from .pipeline import auto_approver, review_cv
from .tools.job_db import JobDB


def interactive_approver(payload: dict[str, Any]) -> dict[str, Any]:
    print("\n" + "=" * 60)
    print("HUMAN APPROVAL REQUIRED")
    print(f"  Job:            {payload['job']}")
    print(f"  Status:         {payload['status']}")
    print(f"  Score:          {payload['score']}  ({payload['recommendation']})")
    if payload["security"].get("detected"):
        sec = payload["security"]
        print(f"  SECURITY:       injection detected; rules={[h['rule'] for h in sec['rule_hits']]}"
              f" guard_score={sec['classifier_score']} safeguard={sec.get('policy_violation')}"
              f" hidden_text={sec['hidden_text_found']}")
    for issue in payload["review_issues"]:
        print(f"  Reviewer issue: {issue}")
    print(f"  AI proposal:    {payload['proposal']}")
    print("=" * 60)
    choice = input(f"Decision [shortlist/reject/hold] (Enter = {payload['proposal']}): ").strip().lower()
    by = input("Your name/role: ").strip() or "HR"
    note = input("Note (optional): ").strip()
    return {"decision": choice or payload["proposal"], "by": by, "note": note}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cv-review", description="Multi-agent AI CV reviewer")
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("review", help="review one CV file")
    r.add_argument("file")
    r.add_argument("--job", required=True, help="job id (see: cv-review jobs)")
    r.add_argument("--auto-approve", action="store_true",
                   help="accept the AI proposal without a prompt (demo / CI only)")
    r.add_argument("--json", action="store_true", help="print the metrics summary as JSON")
    r.add_argument("-v", "--verbose", action="store_true")
    sub.add_parser("jobs", help="list job ids")
    sub.add_parser("graph", help="print the workflow graph (Mermaid)")
    args = parser.parse_args(argv)

    settings = Settings()
    if args.cmd == "jobs":
        for job in JobDB(settings.jobs_file).list_jobs():
            print(f"{job['id']:28} {job['title']}")
        return 0
    if args.cmd == "graph":
        from .graph import build_graph
        from .observability import Tracer

        g = build_graph(llm=None, tracer=Tracer("graph"), settings=settings,
                        jobs=JobDB(settings.jobs_file))
        print(g.get_graph().draw_mermaid())
        return 0

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    if not os.getenv("GROQ_API_KEY"):
        print("GROQ_API_KEY is not set. Copy .env.example to .env and add your key.", file=sys.stderr)
        return 2
    out = review_cv(args.file, args.job,
                    approver=auto_approver if args.auto_approve else interactive_approver,
                    settings=settings)
    print("\n" + out["state"]["report_markdown"])
    print(f"\nReport saved: {out['state']['report_path']}")
    if args.json:
        print(json.dumps(out["summary"], indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
