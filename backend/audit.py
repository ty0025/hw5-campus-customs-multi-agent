"""Append-only audit trail stored as one JSON array in output/audit_trail.json."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.app.settings import HW5_DIR
from backend.models import AuditStep


AUDIT_PATH = HW5_DIR / "output" / "audit_trail.json"
_FILE_LOCK = threading.RLock()


def load_records(path: Path | None = None) -> list[dict[str, Any]]:
    """Read every record. Older runs were written as JSON Lines, so accept both formats."""
    path = path or AUDIT_PATH
    if not path.is_file():
        return []
    for attempt in range(5):
        try:
            text = path.read_text(encoding="utf-8")
            break
        except PermissionError:  # Windows: the writer is swapping the file in.
            time.sleep(0.05 * (attempt + 1))
    else:
        return []
    if not text.strip():
        return []
    try:
        parsed = json.loads(text)
        rows = parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    return [row for row in rows if isinstance(row, dict)]


def append_record(record: dict[str, Any], path: Path | None = None) -> None:
    """Add one record to the end of the array. Earlier records are never dropped or edited."""
    path = path or AUDIT_PATH
    with _FILE_LOCK:
        records = load_records(path)
        records.append(record)
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent,
            prefix=f".{path.name}.", suffix=".tmp", delete=False,
        ) as stream:
            json.dump(records, stream, ensure_ascii=False, indent=1)
            stream.write("\n")
            temp_name = stream.name
        for attempt in range(10):
            try:
                os.replace(temp_name, path)
                return
            except PermissionError:  # a reader has the file open for a moment
                time.sleep(0.05 * (attempt + 1))
        os.unlink(temp_name)
        raise PermissionError(f"Could not update {path.name}; the file stayed locked.")


class AppendOnlyAuditLogger:
    """Numbers the steps of one run and appends each as a validated AuditStep."""

    def __init__(self, run_id: str, path: Path | None = None) -> None:
        self.run_id = run_id
        self.path = path or AUDIT_PATH
        self._sequence = 0
        self._lock = threading.Lock()

    def append(self, *, ticket_id: int | None, agent_name: str, action: str, **fields: Any) -> AuditStep:
        with self._lock:
            self._sequence += 1
            step = AuditStep(
                timestamp=datetime.now(timezone.utc),
                run_id=self.run_id,
                sequence=self._sequence,
                ticket_id=ticket_id,
                agent_name=agent_name,
                action=action,
                **fields,
            )
            append_record(step.model_dump(mode="json", exclude_none=True), self.path)
            return step
