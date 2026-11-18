"""Structured run tracing.

Each run writes one JSON Lines file to logs/<run_id>.jsonl. One line is one
event: a node step, an LLM call, a tool call, a guardrail result or an error.
The Tracer also keeps the events in memory so the report can show a summary.
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

logger = logging.getLogger("cv_reviewer")


class Tracer:
    def __init__(self, run_id: str, log_dir: Path | None = None) -> None:
        self.run_id = run_id
        self.events: list[dict[str, Any]] = []
        self._t0 = time.perf_counter()
        self.log_path: Path | None = None
        if log_dir is not None:
            log_dir.mkdir(parents=True, exist_ok=True)
            self.log_path = log_dir / f"{run_id}.jsonl"

    def log(self, event: str, **data: Any) -> dict[str, Any]:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "run_id": self.run_id,
            "event": event,
            **data,
        }
        self.events.append(record)
        if self.log_path is not None:
            with self.log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        logger.debug("%s %s", event, data)
        return record

    @contextmanager
    def span(self, kind: str, name: str, **data: Any) -> Iterator[dict[str, Any]]:
        """Time a block. The caller can add fields to the yielded dict."""
        extra: dict[str, Any] = {}
        t0 = time.perf_counter()
        try:
            yield extra
        except Exception as exc:
            # LangGraph pauses for human input by raising GraphInterrupt. That is
            # a pause, not a failure.
            paused = type(exc).__name__ == "GraphInterrupt"
            self.log(kind, name=name, status="paused" if paused else "error",
                     error=None if paused else f"{type(exc).__name__}: {exc}",
                     duration_ms=round((time.perf_counter() - t0) * 1000, 1), **data, **extra)
            raise
        self.log(kind, name=name, status="ok",
                 duration_ms=round((time.perf_counter() - t0) * 1000, 1), **data, **extra)

    def summary(self) -> dict[str, Any]:
        llm = [e for e in self.events if e["event"] == "llm_call"]
        ok_llm = [e for e in llm if e.get("status") == "ok"]
        return {
            "run_id": self.run_id,
            "total_ms": round((time.perf_counter() - self._t0) * 1000, 1),
            "nodes": [
                {"name": e["name"], "ms": e["duration_ms"], "status": e["status"]}
                for e in self.events if e["event"] == "node"
            ],
            "llm_calls": len(llm),
            "llm_failures": len(llm) - len(ok_llm),
            "input_tokens": sum(e.get("input_tokens", 0) for e in ok_llm),
            "output_tokens": sum(e.get("output_tokens", 0) for e in ok_llm),
            "models_used": sorted({e["model"] for e in ok_llm}),
            "tool_calls": sum(1 for e in self.events if e["event"] == "tool_call"),
            "guardrail_events": sum(1 for e in self.events if e["event"] == "guardrail"),
            "errors": [e for e in self.events if e.get("status") == "error"],
            "log_file": str(self.log_path) if self.log_path else None,
        }
