"""Append-only JSON/JSONL audit logging for observable agent operations."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.app.settings import HW5_DIR
from backend.models import AuditStep, AgentName


_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: dict[str, threading.RLock] = {}


def _path_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.RLock())


class AppendOnlyAuditLogger:
    """Append sanitized event records without discarding earlier audit entries.

    The preferred format is JSON Lines: each independently valid AuditStep is
    appended with one newline. Existing JSON arrays are loaded and extended
    atomically with every prior element preserved. Malformed files fail closed.
    """

    def __init__(self, run_id: str, path: Path | None = None) -> None:
        self.run_id = run_id
        self.path = path or (HW5_DIR / "output" / "audit_trail.json")
        self._sequence = 0
        self._lock = _path_lock(self.path)

    def append(
        self,
        *,
        ticket_id: int | None,
        agent_name: AgentName | str,
        action: str,
        delegation_target: AgentName | None = None,
        tool_called: str | None = None,
        reasoning_notes: str = "",
        depth: int = 0,
    ) -> AuditStep:
        """Validate and persist one concise operational event."""
        with self._lock:
            step = AuditStep(
                timestamp=datetime.now(timezone.utc),
                run_id=self.run_id,
                sequence=self._sequence + 1,
                ticket_id=ticket_id,
                agent_name=agent_name,
                action=action,
                delegation_target=delegation_target,
                tool_called=tool_called,
                reasoning_notes=reasoning_notes[:1600],
                depth=depth,
            )
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = step.model_dump(mode="json")
            self._append_preserving_history(payload)
            self._sequence += 1
            return step

    def _append_preserving_history(self, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        if not self.path.exists() or self.path.stat().st_size == 0:
            self._append_line(line)
            return

        existing = self.path.read_text(encoding="utf-8")
        nonempty_lines = [row for row in existing.splitlines() if row.strip()]
        try:
            # The normal JSONL case (including a one-record file) stays strictly
            # append-only and never rewrites earlier bytes.
            parsed_lines = [json.loads(row) for row in nonempty_lines]
        except json.JSONDecodeError:
            try:
                decoded = json.loads(existing)
            except json.JSONDecodeError as exc:
                raise ValueError("Audit trail is malformed; refusing to alter prior records.") from exc
            if isinstance(decoded, list):
                decoded.append(payload)
            else:
                decoded = [decoded, payload]
            self._replace_array_without_losing_records(decoded)
        else:
            if len(parsed_lines) == 1 and isinstance(parsed_lines[0], list):
                parsed_lines[0].append(payload)
                self._replace_array_without_losing_records(parsed_lines[0])
            else:
                self._append_line(line)

    def _append_line(self, line: str) -> None:
        needs_separator = self.path.exists() and self.path.stat().st_size > 0 and not self.path.read_bytes().endswith(b"\n")
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            if needs_separator:
                stream.write("\n")
            stream.write(line + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    def _replace_array_without_losing_records(self, records: list[Any]) -> None:
        """Extend an existing JSON array while retaining each prior value."""
        temp_name: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", dir=self.path.parent,
                prefix=f".{self.path.name}.", suffix=".tmp", delete=False,
            ) as stream:
                temp_name = stream.name
                json.dump(records, stream, ensure_ascii=False, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, self.path)
        finally:
            if temp_name and os.path.exists(temp_name):
                os.unlink(temp_name)
