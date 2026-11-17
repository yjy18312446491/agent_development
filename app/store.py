"""Persist screened batches in SQLite and export their records as CSV."""

import csv
from pathlib import Path, PureWindowsPath
import sqlite3
from typing import List, Sequence, Union

from app.screen import BatchRecord


_SCORE_COLUMNS = ("skill", "project", "years", "education", "bonus")
_CSV_COLUMNS = (
    "batch_id", "source_name", "extract_status", "guard_status", "status",
    "jd_id", "jd_title", "tier", "total", *_SCORE_COLUMNS,
    "project_source", "reason_code", "reason",
)
_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS screen_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    source_name TEXT NOT NULL,
    extract_status TEXT,
    guard_status TEXT,
    status TEXT,
    jd_id TEXT,
    jd_title TEXT,
    tier TEXT,
    total INTEGER,
    skill INTEGER,
    project INTEGER,
    years INTEGER,
    education INTEGER,
    bonus INTEGER,
    project_source TEXT,
    reason_code TEXT,
    reason TEXT,
    UNIQUE (batch_id, source_name)
)
"""
_INSERT_RESULT = """
INSERT INTO screen_results (
    batch_id, source_name, extract_status, guard_status, status,
    jd_id, jd_title, tier, total, skill, project, years, education, bonus,
    project_source, reason_code, reason
) VALUES (
    :batch_id, :source_name, :extract_status, :guard_status, :status,
    :jd_id, :jd_title, :tier, :total, :skill, :project, :years, :education, :bonus,
    :project_source, :reason_code, :reason
)
"""
_FETCH_BATCH = """
SELECT batch_id, source_name, extract_status, guard_status, status,
       jd_id, jd_title, tier, total, skill, project, years, education, bonus,
       project_source, reason_code, reason
FROM screen_results
WHERE batch_id = ?
ORDER BY id
"""


def connect(db_path: Union[str, Path]) -> sqlite3.Connection:
    """Open the caller's database and initialize the screen-results table."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(_CREATE_TABLE)
    except sqlite3.Error:
        conn.close()
        raise
    conn.row_factory = sqlite3.Row
    return conn


def save_batch(conn: sqlite3.Connection, batch_id: str, records: Sequence[BatchRecord]) -> int:
    """Write one batch atomically, rejecting duplicate file names within it."""
    if not batch_id.strip():
        raise ValueError("batch_id must not be blank")

    rows = []
    for record in records:
        row = record.as_dict()
        source_name = row["source_name"]
        if (
            not source_name
            or source_name != Path(source_name).name
            or source_name != PureWindowsPath(source_name).name
        ):
            raise ValueError("source_name must be a file name")
        scores = row.pop("scores")
        row.update(dict.fromkeys(_SCORE_COLUMNS) if scores is None else scores)
        row["batch_id"] = batch_id
        rows.append(row)

    with conn:
        conn.executemany(_INSERT_RESULT, rows)
    return len(rows)


def fetch_batch(conn: sqlite3.Connection, batch_id: str) -> List[dict]:
    """Fetch the batch in insertion order with null fields preserved."""
    return [dict(row) for row in conn.execute(_FETCH_BATCH, (batch_id,))]


def export_csv(rows: Sequence[dict], csv_path: Union[str, Path]) -> None:
    """Write fetched records with a fixed header and blank cells for nulls."""
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
