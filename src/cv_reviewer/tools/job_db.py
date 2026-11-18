"""Job database tool (SQLite).

Job descriptions live in data/jobs.json (easy to edit and review in git) and
load into an in-memory SQLite database at start. Agents read jobs only through
get_job(), so the access path is one place to log and control.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class JobDB:
    def __init__(self, jobs_file: Path) -> None:
        self.conn = sqlite3.connect(":memory:", check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """CREATE TABLE jobs (
                   id TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
                   required_skills TEXT NOT NULL, nice_to_have TEXT NOT NULL,
                   min_years REAL NOT NULL)"""
        )
        for job in json.loads(Path(jobs_file).read_text(encoding="utf-8")):
            self.conn.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?)",
                (job["id"], job["title"], job["description"], json.dumps(job["required_skills"]),
                 json.dumps(job.get("nice_to_have", [])), float(job.get("min_years", 0))),
            )

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            return None
        job = dict(row)
        job["required_skills"] = json.loads(job["required_skills"])
        job["nice_to_have"] = json.loads(job["nice_to_have"])
        return job

    def list_jobs(self) -> list[dict[str, str]]:
        rows = self.conn.execute("SELECT id, title FROM jobs ORDER BY id").fetchall()
        return [dict(r) for r in rows]
