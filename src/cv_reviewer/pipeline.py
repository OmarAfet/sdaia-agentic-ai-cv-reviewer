"""Public entry point: review_cv() runs one CV through the graph.

The approver callback is the human. It gets the interrupt payload and returns
{"decision": "shortlist" | "reject" | "hold", "by": str, "note": str}.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any, Callable

from langgraph.types import Command

from .config import Settings
from .graph import build_graph
from .llm import GroqLLM, LLMClient
from .observability import Tracer
from .tools.job_db import JobDB

Approver = Callable[[dict[str, Any]], dict[str, Any]]
logger = logging.getLogger("cv_reviewer")


def auto_approver(payload: dict[str, Any]) -> dict[str, Any]:
    """Accepts the AI proposal. Quarantined or escalated CVs stay on hold."""
    return {"decision": payload["proposal"], "by": "auto-approver (demo)",
            "note": "non-interactive run"}


def review_cv(
    file_path: str | Path,
    job_id: str,
    approver: Approver = auto_approver,
    llm: LLMClient | None = None,
    settings: Settings | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    settings = settings or Settings()
    run_id = run_id or f"cv-{uuid.uuid4().hex[:8]}"
    tracer = Tracer(run_id, settings.log_dir)
    llm = llm or GroqLLM(settings, tracer)
    if hasattr(llm, "tracer"):
        llm.tracer = tracer
    graph = build_graph(llm, tracer, settings, JobDB(settings.jobs_file))
    config = {"configurable": {"thread_id": run_id}}

    tracer.log("run_start", file=str(file_path), job_id=job_id)
    result = graph.invoke({"run_id": run_id, "file_path": str(file_path), "job_id": job_id,
                           "status": "running", "errors": [], "revision_count": 0}, config)
    while result.get("__interrupt__"):
        payload = result["__interrupt__"][0].value
        result = graph.invoke(Command(resume=approver(payload)), config)
    summary = tracer.summary()
    tracer.log("run_end", status=result.get("status"), total_ms=summary["total_ms"])
    return {"state": result, "summary": summary, "graph": graph}
