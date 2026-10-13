"""FastAPI bridge for the Campus Customs dashboard and agent team.

Run from the backend directory with:
    uvicorn main:app --reload --port 8000
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import math
import os
import sqlite3
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
DATA_DIR = PROJECT_ROOT / "data"
WORKING_DB = DATA_DIR / "campus_customs_new.db"
ORIGINAL_DB = DATA_DIR / "campus_customs.db"
AUDIT_FILE = PROJECT_ROOT / "output" / "audit_trail.json"

# The PydanticAI team owns its Portkey provider configuration; this route only
# passes this fixed model where the team's public runner accepts a model value.
AGENT_MODEL = "gpt-6-luna"
PORTKEY_GATEWAY_URL = "https://api.portkey.ai/v1"


def _load_portkey_key_from_project_env() -> None:
    """Load only the Portkey key needed by the agent runner without displaying it."""
    if os.getenv("PORTKEY_API_KEY"):
        return
    env_file = PROJECT_ROOT / ".env"
    if not env_file.is_file():
        return
    try:
        for line in env_file.read_text(encoding="utf-8").splitlines():
            candidate = line.strip()
            if candidate.startswith("export "):
                candidate = candidate[7:].lstrip()
            key, separator, value = candidate.partition("=")
            if separator and key.strip() == "PORTKEY_API_KEY":
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                    value = value[1:-1]
                if value:
                    os.environ.setdefault("PORTKEY_API_KEY", value)
                return
    except OSError:
        return


_load_portkey_key_from_project_env()

app = FastAPI(title="Campus Customs Operations API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*", "http://localhost:3000", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=False,
)

_AUDIT_LOCK = threading.Lock()


class PaymentApprovalRequest(BaseModel):
    payment_id: int
    amount: float = Field(gt=0)
    recipient: str = Field(min_length=1)
    reference_type: str = Field(min_length=1)
    reference_id: int


class PurchaseOrderApprovalRequest(BaseModel):
    approval_id: int = Field(gt=0)
    approved_by: str = Field(default="Human Operator", min_length=1, max_length=120)


def _connect(db_path: Path = WORKING_DB) -> sqlite3.Connection:
    if not db_path.exists():
        raise HTTPException(status_code=500, detail=f"Database file not found: {db_path.name}")
    connection = sqlite3.connect(str(db_path), timeout=15)
    connection.row_factory = sqlite3.Row
    return connection


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _columns(connection: sqlite3.Connection, table: str) -> dict[str, str]:
    rows = connection.execute(f"PRAGMA table_info({_quote(table)})").fetchall()
    return {str(row["name"]).casefold(): str(row["name"]) for row in rows}


def _pick(columns: dict[str, str], *candidates: str) -> Optional[str]:
    for candidate in candidates:
        if candidate.casefold() in columns:
            return columns[candidate.casefold()]
    return None


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND lower(name) = lower(?)",
        (table,),
    ).fetchone()
    return row is not None


def _read_ticket(connection: sqlite3.Connection, ticket_id: int) -> Optional[dict[str, Any]]:
    columns = _columns(connection, "tickets")
    ticket_key = _pick(columns, "ticket_id", "id")
    if not ticket_key:
        raise HTTPException(status_code=500, detail="The tickets table has no ticket identifier.")
    row = connection.execute(
        f"SELECT * FROM {_quote('tickets')} WHERE {_quote(ticket_key)} = ?",
        (ticket_id,),
    ).fetchone()
    return dict(row) if row else None


def _ticket_status(value: Any) -> str:
    if value is None:
        return "open"
    normalized = str(value).strip().casefold()
    return "resolved" if normalized in {"resolved", "closed", "complete", "completed"} else "open"


def _json_safe(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump(mode="json"))
    if hasattr(value, "dict") and callable(value.dict):
        return _json_safe(value.dict())
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _append_audit(record: dict[str, Any]) -> None:
    """Append without discarding prior JSON array or JSONL audit records."""
    AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    encoded_record = json.dumps(_json_safe(record), ensure_ascii=False, separators=(",", ":"))
    with _AUDIT_LOCK:
        if not AUDIT_FILE.exists():
            with AUDIT_FILE.open("a", encoding="utf-8") as stream:
                stream.write(encoded_record + "\n")
            return

        existing = AUDIT_FILE.read_text(encoding="utf-8")
        if not existing.strip():
            with AUDIT_FILE.open("a", encoding="utf-8") as stream:
                stream.write(encoded_record + "\n")
            return

        try:
            parsed = json.loads(existing)
        except json.JSONDecodeError:
            # Existing JSONL is append-only; keep every original line byte-for-byte.
            with AUDIT_FILE.open("a", encoding="utf-8") as stream:
                if not existing.endswith("\n"):
                    stream.write("\n")
                stream.write(encoded_record + "\n")
            return

        if isinstance(parsed, list):
            parsed.append(_json_safe(record))
        else:
            parsed = [parsed, _json_safe(record)]

        # Legacy JSON arrays are loaded and extended so existing entries remain intact.
        temporary = AUDIT_FILE.with_name(AUDIT_FILE.name + f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(parsed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, AUDIT_FILE)


def _load_audit_records() -> list[dict[str, Any]]:
    if not AUDIT_FILE.exists():
        return []
    text = AUDIT_FILE.read_text(encoding="utf-8")
    if not text.strip():
        return []
    try:
        parsed = json.loads(text)
        rows = parsed if isinstance(parsed, list) else [parsed]
    except json.JSONDecodeError:
        rows = []
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return [row for row in rows if isinstance(row, dict)]


def _event_view(row: dict[str, Any]) -> dict[str, Any]:
    tool_calls = row.get("tool_calls")
    if not isinstance(tool_calls, list):
        tool_name = row.get("tool_called") or row.get("tool_name")
        if tool_name:
            tool_calls = [{
                "name": tool_name,
                "arguments": row.get("tool_arguments", row.get("arguments", {})),
            }]
        else:
            tool_calls = []
    return {
        "timestamp": row.get("timestamp"),
        "ticket_id": row.get("ticket_id"),
        "active_agent": row.get("active_agent") or row.get("agent_name") or row.get("agent"),
        "thoughts": row.get("thoughts") or row.get("reasoning") or row.get("notes"),
        "speech": row.get("speech") or row.get("response") or row.get("message"),
        "action": row.get("action"),
        "tool_calls": tool_calls,
        "delegation_target": row.get("delegation_target"),
    }


def _cash_account(connection: sqlite3.Connection) -> tuple[sqlite3.Row, str, str, float]:
    columns = _columns(connection, "cash_accounts")
    name_column = _pick(columns, "account_name", "name")
    balance_column = _pick(columns, "balance", "current_balance", "cash_balance", "amount")
    id_column = _pick(columns, "cash_account_id", "account_id", "id")
    if not balance_column:
        raise HTTPException(status_code=500, detail="The cash_accounts table has no balance field.")

    rows = connection.execute(f"SELECT * FROM {_quote('cash_accounts')}").fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail="No cash account is available.")

    chosen = None
    actual_name = "Checking"
    if name_column:
        by_name = {str(row[name_column]).strip().casefold(): row for row in rows if row[name_column] is not None}
        chosen = by_name.get("checking")
        if chosen is not None:
            actual_name = str(chosen[name_column])
        else:
            # The supplied Campus Customs dataset may label its operating checking
            # account "Operating Cash"; use it only as the explicit checking alias.
            chosen = by_name.get("operating cash")
            if chosen is not None:
                actual_name = str(chosen[name_column])
    elif len(rows) == 1:
        chosen = rows[0]
    if chosen is None:
        raise HTTPException(status_code=404, detail="The Checking cash account could not be found.")

    try:
        balance = float(chosen[balance_column])
    except (TypeError, ValueError):
        raise HTTPException(status_code=500, detail="The checking account balance is not numeric.")
    return chosen, (name_column or id_column or "account"), balance_column, balance


def _resolve_liability_table(reference_type: str) -> tuple[str, str]:
    normalized = reference_type.strip().casefold()
    if normalized in {"invoice", "invoices", "vendor_invoice", "vendor invoice"}:
        return "invoices", "invoice_id"
    if normalized in {"lease", "leases", "rent", "lease_payment"}:
        return "leases", "lease_id"
    raise HTTPException(status_code=400, detail="reference_type must identify an invoice or lease.")


def _liability_updates(
    connection: sqlite3.Connection,
    reference_type: str,
    reference_id: int,
) -> tuple[str, str, dict[str, Any]]:
    table, preferred_key = _resolve_liability_table(reference_type)
    if not _table_exists(connection, table):
        raise HTTPException(status_code=500, detail=f"The {table} table is unavailable.")
    columns = _columns(connection, table)
    key_column = _pick(columns, preferred_key, "id")
    if not key_column:
        raise HTTPException(status_code=500, detail=f"The {table} table has no reference identifier.")
    row = connection.execute(
        f"SELECT * FROM {_quote(table)} WHERE {_quote(key_column)} = ?",
        (reference_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"{table} record {reference_id} was not found.")

    settled_status_names = (
        ("status", "payment_status", "invoice_status")
        if table == "invoices"
        else ("rent_status", "payment_status", "last_rent_payment_status")
    )
    settled_flag_names = (
        ("paid", "is_paid", "invoice_paid")
        if table == "invoices"
        else ("rent_paid", "is_rent_paid", "paid", "is_paid")
    )
    for name in settled_status_names:
        column = _pick(columns, name)
        if column and str(row[column] or "").strip().casefold() in {"paid", "settled", "cleared"}:
            raise HTTPException(status_code=409, detail=f"The {table} liability is already cleared.")
    for name in settled_flag_names:
        column = _pick(columns, name)
        if column and row[column] in (1, True, "1", "true", "TRUE"):
            raise HTTPException(status_code=409, detail=f"The {table} liability is already cleared.")

    values: dict[str, Any] = {}
    if table == "invoices":
        for name in ("status", "payment_status", "invoice_status"):
            column = _pick(columns, name)
            if column:
                values[column] = "PAID"
        for name in ("paid", "is_paid", "invoice_paid"):
            column = _pick(columns, name)
            if column:
                values[column] = 1
    else:
        for name in ("rent_status", "payment_status", "last_rent_payment_status"):
            column = _pick(columns, name)
            if column:
                values[column] = "PAID"
        for name in ("rent_paid", "is_rent_paid", "paid", "is_paid"):
            column = _pick(columns, name)
            if column:
                values[column] = 1
        # Some schemas keep a single status field for the current rent obligation.
        status_column = _pick(columns, "status")
        if status_column and str(row[status_column] or "").strip().casefold() in {
            "due", "overdue", "unpaid", "pending", "past due"
        }:
            values[status_column] = "PAID"

        # The supplied lease schema records amount and next due date but has no
        # payment state. Add an explicit state inside the approval transaction.
        if not values:
            payment_status = _pick(columns, "payment_status")
            if not payment_status:
                connection.execute(
                    "ALTER TABLE leases ADD COLUMN payment_status TEXT NOT NULL DEFAULT 'UNPAID'"
                )
                columns = _columns(connection, "leases")
                payment_status = _pick(columns, "payment_status")
            if payment_status:
                values[payment_status] = "PAID"

    now = datetime.now(timezone.utc).isoformat()
    for name in ("paid_at", "payment_date", "last_paid_date", "last_payment_date"):
        column = _pick(columns, name)
        if column:
            values[column] = now

    if not values:
        raise HTTPException(
            status_code=409,
            detail=f"The {table} schema has no payment field that can mark this liability cleared.",
        )
    return table, key_column, values


def _validate_staged_payment(
    connection: sqlite3.Connection,
    request: PaymentApprovalRequest,
) -> tuple[str, sqlite3.Row, str, dict[str, str]]:
    """Validate a pending MCP approval, with legacy staged payments as fallback.

    MCP proposals live in ``approvals`` until a human approves them. The supplied
    ``payments`` table is a completed ledger and therefore may have no status
    column or staged row yet.
    """
    liability_table, _ = _resolve_liability_table(request.reference_type)
    expected_reference_type = "invoice" if liability_table == "invoices" else "lease"
    if _table_exists(connection, "approvals"):
        approval_columns = _columns(connection, "approvals")
        approval_key = _pick(approval_columns, "approval_id", "id")
        if approval_key:
            approval = connection.execute(
                f"SELECT * FROM {_quote('approvals')} WHERE {_quote(approval_key)} = ?",
                (request.payment_id,),
            ).fetchone()
            if approval is not None:
                kind_column = _pick(approval_columns, "kind", "type")
                if kind_column and str(approval[kind_column]).strip().casefold() != "payment":
                    raise HTTPException(status_code=409, detail="Only a staged payment can be approved.")
                status_column = _pick(approval_columns, "status", "approval_status")
                if not status_column or str(approval[status_column] or "").strip().casefold() != "pending":
                    raise HTTPException(status_code=409, detail="This approval is no longer pending.")
                amount_column = _pick(approval_columns, "amount", "payment_amount")
                if not amount_column or not math.isclose(
                    float(approval[amount_column]), request.amount, rel_tol=0, abs_tol=0.005
                ):
                    raise HTTPException(status_code=409, detail="The approval amount does not match the staged payment.")
                ref_type_column = _pick(approval_columns, "ref_type", "reference_type")
                stored_type = str(approval[ref_type_column] or "").strip().casefold().rstrip("s") if ref_type_column else ""
                if stored_type != expected_reference_type:
                    raise HTTPException(status_code=409, detail="The reference type does not match the staged payment.")
                ref_id_column = _pick(approval_columns, "ref_id", "reference_id")
                if not ref_id_column or int(approval[ref_id_column]) != request.reference_id:
                    raise HTTPException(status_code=409, detail="The reference ID does not match the staged payment.")
                payload_column = _pick(approval_columns, "payload_json", "payload")
                try:
                    payload = json.loads(approval[payload_column] or "{}") if payload_column else {}
                except (TypeError, json.JSONDecodeError) as exc:
                    raise HTTPException(status_code=409, detail="The staged payment details are invalid.") from exc
                staged_recipient = payload.get("payee") or payload.get("recipient")
                if staged_recipient and str(staged_recipient).strip().casefold() != request.recipient.strip().casefold():
                    raise HTTPException(status_code=409, detail="The approval recipient does not match the staged payment.")
                return "approvals", approval, status_column, approval_columns

    if not _table_exists(connection, "payments"):
        raise HTTPException(status_code=500, detail="The payments table is unavailable.")
    columns = _columns(connection, "payments")
    payment_key = _pick(columns, "payment_id", "id")
    if not payment_key:
        raise HTTPException(status_code=500, detail="The payments table has no payment identifier.")
    row = connection.execute(
        f"SELECT * FROM {_quote('payments')} WHERE {_quote(payment_key)} = ?",
        (request.payment_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Staged payment {request.payment_id} was not found.")

    amount_column = _pick(columns, "amount", "payment_amount")
    if amount_column and not math.isclose(float(row[amount_column]), request.amount, rel_tol=0, abs_tol=0.005):
        raise HTTPException(status_code=409, detail="The approval amount does not match the staged payment.")
    recipient_column = _pick(columns, "recipient", "payee", "vendor_name")
    if recipient_column and row[recipient_column] is not None:
        if str(row[recipient_column]).strip().casefold() != request.recipient.strip().casefold():
            raise HTTPException(status_code=409, detail="The approval recipient does not match the staged payment.")

    ref_type_column = _pick(columns, "reference_type", "ref_type")
    ref_id_column = _pick(columns, "reference_id", "ref_id")
    if ref_type_column and row[ref_type_column] is not None:
        stored_type = str(row[ref_type_column]).strip().casefold().rstrip("s")
        requested_type = request.reference_type.strip().casefold().rstrip("s")
        if stored_type != requested_type:
            raise HTTPException(status_code=409, detail="The reference type does not match the staged payment.")
    if ref_id_column and row[ref_id_column] is not None and int(row[ref_id_column]) != request.reference_id:
        raise HTTPException(status_code=409, detail="The reference ID does not match the staged payment.")
    if not ref_id_column:
        relation_column = _pick(columns, "invoice_id") if request.reference_type.strip().casefold().rstrip("s") == "invoice" else _pick(columns, "lease_id")
        if relation_column and row[relation_column] is not None and int(row[relation_column]) != request.reference_id:
            raise HTTPException(status_code=409, detail="The liability reference does not match the staged payment.")

    status_column = _pick(columns, "status", "payment_status")
    if not status_column:
        raise HTTPException(status_code=500, detail="The payments table has no status field.")
    status = str(row[status_column] or "").strip().casefold().replace(" ", "_")
    if status in {"paid", "approved", "complete", "completed"}:
        raise HTTPException(status_code=409, detail="This payment has already been finalized.")
    if status not in {"pending_approval", "pending", "staged", "proposed", "awaiting_approval"}:
        raise HTTPException(status_code=409, detail="Only a staged payment can be approved.")
    return payment_key, row, status_column, columns


def _payment_ids_snapshot() -> set[str]:
    connection = _connect()
    try:
        if not _table_exists(connection, "payments"):
            return set()
        columns = _columns(connection, "payments")
        payment_key = _pick(columns, "payment_id", "id")
        if not payment_key:
            return set()
        rows = connection.execute(
            f"SELECT {_quote(payment_key)} FROM {_quote('payments')}"
        ).fetchall()
        return {str(row[payment_key]) for row in rows if row[payment_key] is not None}
    finally:
        connection.close()


def _pending_payments_added_since(before_ids: set[str], ticket_id: int) -> list[dict[str, Any]]:
    """Return payment rows newly staged by this ticket run for its HITL card."""
    connection = _connect()
    try:
        if not _table_exists(connection, "payments"):
            return []
        columns = _columns(connection, "payments")
        payment_key = _pick(columns, "payment_id", "id")
        status_column = _pick(columns, "status", "payment_status")
        amount_column = _pick(columns, "amount", "payment_amount")
        recipient_column = _pick(columns, "recipient", "payee", "vendor_name")
        reason_column = _pick(columns, "reason", "purpose", "description")
        if not payment_key or not status_column:
            return []
        rows = connection.execute(f"SELECT * FROM {_quote('payments')}").fetchall()
        proposals: list[dict[str, Any]] = []
        for row in rows:
            raw_payment_id = row[payment_key]
            if raw_payment_id is None or str(raw_payment_id) in before_ids:
                continue
            state = str(row[status_column] or "").strip().casefold().replace(" ", "_")
            if state not in {"pending_approval", "pending", "staged", "proposed", "awaiting_approval"}:
                continue
            try:
                payment_id = int(raw_payment_id)
                amount = float(row[amount_column]) if amount_column else 0.0
            except (TypeError, ValueError):
                continue
            if amount <= 0:
                continue
            invoice_column = _pick(columns, "invoice_id", "invoice_reference")
            lease_column = _pick(columns, "lease_id", "lease_reference")
            reference_type_column = _pick(columns, "reference_type", "ref_type", "reference_kind")
            reference_id_column = _pick(columns, "reference_id", "ref_id")
            reference_type = str(row[reference_type_column] or "") if reference_type_column else ""
            raw_reference_id = row[reference_id_column] if reference_id_column else None
            if raw_reference_id is None and invoice_column and row[invoice_column] is not None:
                reference_type = reference_type or "invoice"
                raw_reference_id = row[invoice_column]
            if raw_reference_id is None and lease_column and row[lease_column] is not None:
                reference_type = reference_type or "lease"
                raw_reference_id = row[lease_column]
            if not reference_type:
                reason_text = str(row[reason_column] or "").casefold() if reason_column else ""
                if "rent" in reason_text or "lease" in reason_text:
                    reference_type = "lease"
                elif "invoice" in reason_text or "vendor" in reason_text:
                    reference_type = "invoice"
            try:
                reference_id = int(raw_reference_id) if raw_reference_id is not None else None
            except (TypeError, ValueError):
                reference_id = None
            ticket_id_column = _pick(columns, "ticket_id")
            row_ticket_id = row[ticket_id_column] if ticket_id_column else ticket_id
            try:
                associated_ticket = int(row_ticket_id)
            except (TypeError, ValueError):
                associated_ticket = ticket_id
            if ticket_id_column and associated_ticket != ticket_id:
                continue
            proposals.append({
                "payment_id": payment_id,
                "amount": amount,
                "recipient": str(row[recipient_column] or "") if recipient_column else "",
                "reason": str(row[reason_column] or "Payment proposal") if reason_column else "Payment proposal",
                "reference_type": reference_type,
                "reference_id": reference_id,
                "ticket_id": associated_ticket,
                "status": "PENDING_APPROVAL",
            })
        return proposals
    finally:
        connection.close()


def _runner_candidates() -> list[tuple[Any, Any]]:
    modules_and_functions = (
        ("backend.agents", ("run_ticket", "process_ticket", "execute_ticket", "run_ticket_workflow", "execute_ticket_workflow", "run_agents_for_ticket", "run_ticket_agents", "run_agent_team", "run_ticket_team", "process_open_ticket", "orchestrate_ticket", "handle_ticket")),
        ("backend.agent_team", ("run_ticket", "process_ticket", "execute_ticket", "run_ticket_workflow", "run_agent_team", "run_ticket_team", "orchestrate_ticket", "handle_ticket")),
        ("backend.team", ("run_ticket", "process_ticket", "execute_ticket", "run_ticket_workflow", "run_agent_team", "run_ticket_team", "orchestrate_ticket", "handle_ticket")),
        ("backend.orchestrator", ("run_ticket", "process_ticket", "execute_ticket", "run_agent_team", "run_ticket_team", "orchestrate_ticket", "handle_ticket")),
        ("agents", ("run_ticket", "process_ticket", "execute_ticket", "run_ticket_workflow", "run_ticket_agents", "run_agent_team", "run_ticket_team", "orchestrate_ticket", "handle_ticket")),
        ("agent_team", ("run_ticket", "process_ticket", "execute_ticket", "run_ticket_agents", "run_agent_team", "run_ticket_team", "orchestrate_ticket", "handle_ticket")),
        ("team", ("run_ticket", "process_ticket", "execute_ticket", "run_agent_team", "run_ticket_team", "orchestrate_ticket", "handle_ticket")),
        ("orchestrator", ("run_ticket", "process_ticket", "execute_ticket", "run_agent_team", "run_ticket_team", "orchestrate_ticket", "handle_ticket")),
        ("backend.agents.orchestrator", ("run_ticket", "process_ticket", "execute_ticket", "run_agent_team", "run_ticket_team", "orchestrate_ticket", "handle_ticket")),
        ("backend.agents.team", ("run_ticket", "process_ticket", "execute_ticket", "run_agent_team", "run_ticket_team", "orchestrate_ticket", "handle_ticket")),
        ("agents.orchestrator", ("run_ticket", "process_ticket", "execute_ticket", "run_agent_team", "run_ticket_team", "orchestrate_ticket", "handle_ticket")),
        ("agents.team", ("run_ticket", "process_ticket", "execute_ticket", "run_agent_team", "run_ticket_team", "orchestrate_ticket", "handle_ticket")),
    )
    seen: set[str] = set()
    for module_name, function_names in modules_and_functions:
        if module_name in seen:
            continue
        seen.add(module_name)
        try:
            module = importlib.import_module(module_name)
        except (ImportError, ModuleNotFoundError):
            continue

        declared_model = getattr(module, "MODEL_NAME", getattr(module, "MODEL", None))
        if isinstance(declared_model, str) and declared_model != AGENT_MODEL:
            raise HTTPException(status_code=503, detail="The agent team's configured model does not meet the required model lock.")

        for function_name in function_names:
            runner = getattr(module, function_name, None)
            if callable(runner):
                return [(module, runner)]
        for object_name in ("agent_team", "team", "orchestrator"):
            team = getattr(module, object_name, None)
            if team is not None:
                for function_name in ("run_ticket", "process_ticket", "execute_ticket", "run"):
                    runner = getattr(team, function_name, None)
                    if callable(runner):
                        return [(module, runner)]
    return []


async def _invoke_team(ticket_id: int, ticket: dict[str, Any]) -> Any:
    if not os.getenv("PORTKEY_API_KEY"):
        raise HTTPException(status_code=503, detail="PORTKEY_API_KEY is not configured.")
    candidates = _runner_candidates()
    if not candidates:
        raise HTTPException(status_code=503, detail="No PydanticAI ticket runner is available in backend.")
    module, runner = candidates[0]

    declared_model = getattr(module, "MODEL_NAME", getattr(module, "MODEL", None))
    if isinstance(declared_model, str) and declared_model != AGENT_MODEL:
        raise HTTPException(status_code=503, detail="The agent team's configured model does not meet the required model lock.")

    signature = inspect.signature(runner)
    parameters = signature.parameters
    kwargs: dict[str, Any] = {}
    args: list[Any] = []
    if "ticket_id" in parameters:
        kwargs["ticket_id"] = ticket_id
    elif "ticket" in parameters:
        kwargs["ticket"] = ticket
    elif parameters:
        first = next(iter(parameters.values()))
        if first.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
            args.append(ticket_id)
    if "model" in parameters:
        kwargs["model"] = AGENT_MODEL
    elif "model_name" in parameters:
        kwargs["model_name"] = AGENT_MODEL
    if "base_url" in parameters:
        kwargs["base_url"] = PORTKEY_GATEWAY_URL
    elif "gateway_url" in parameters:
        kwargs["gateway_url"] = PORTKEY_GATEWAY_URL

    if inspect.iscoroutinefunction(runner):
        result = runner(*args, **kwargs)
    else:
        result = await asyncio.to_thread(runner, *args, **kwargs)
    if inspect.isawaitable(result):
        result = await result
    return result


@app.get("/api/tickets")
def get_tickets() -> list[dict[str, Any]]:
    """Return ticket-board entries for tickets 101, 102, and 103."""
    connection = _connect()
    try:
        if not _table_exists(connection, "tickets"):
            raise HTTPException(status_code=500, detail="The tickets table is unavailable.")
        columns = _columns(connection, "tickets")
        ticket_key = _pick(columns, "ticket_id", "id")
        description_column = _pick(columns, "description", "summary", "title")
        type_column = _pick(columns, "type", "category")
        status_column = _pick(columns, "status", "ticket_status")
        assigned_column = _pick(columns, "assigned_agent", "agent", "assigned_to")
        if not ticket_key:
            raise HTTPException(status_code=500, detail="The tickets table has no ticket identifier.")
        result = []
        for ticket_id in (101, 102, 103):
            row = connection.execute(
                f"SELECT * FROM {_quote('tickets')} WHERE {_quote(ticket_key)} = ?",
                (ticket_id,),
            ).fetchone()
            if row is None:
                continue
            record = dict(row)
            result.append({
                "ticket_id": int(record[ticket_key]),
                "description": record.get(description_column) if description_column else "",
                "type": record.get(type_column) if type_column else None,
                "status": _ticket_status(record.get(status_column) if status_column else None),
                "assigned_agent": record.get(assigned_column) if assigned_column else None,
            })
        return result
    finally:
        connection.close()


@app.post("/api/tickets/{ticket_id}/run")
async def run_ticket(ticket_id: int) -> dict[str, Any]:
    """Run the existing PydanticAI team against one ticket and return its outcome."""
    if ticket_id not in {101, 102, 103}:
        raise HTTPException(status_code=404, detail="Only tickets 101, 102, and 103 are available.")
    connection = _connect()
    try:
        ticket = _read_ticket(connection, ticket_id)
    finally:
        connection.close()
    if ticket is None:
        raise HTTPException(status_code=404, detail=f"Ticket {ticket_id} was not found.")

    started = datetime.now(timezone.utc).isoformat()
    _append_audit({
        "timestamp": started,
        "ticket_id": ticket_id,
        "agent_name": "Boss",
        "active_agent": "Boss",
        "action": "ticket_run_started",
        "reasoning": "Dashboard request started the ticket workflow.",
        "tool_calls": [],
    })
    payment_ids_before = _payment_ids_snapshot()
    try:
        result = await _invoke_team(ticket_id, ticket)
    except HTTPException as exc:
        _append_audit({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "ticket_id": ticket_id,
            "agent_name": "Boss",
            "active_agent": "Boss",
            "action": "ticket_run_blocked",
            "reasoning": exc.detail,
            "tool_calls": [],
        })
        raise
    except Exception as exc:
        _append_audit({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "ticket_id": ticket_id,
            "agent_name": "Boss",
            "active_agent": "Boss",
            "action": "ticket_run_failed",
            "reasoning": f"Agent execution failed ({type(exc).__name__}).",
            "tool_calls": [],
        })
        raise HTTPException(status_code=500, detail="Agent execution failed; inspect the local audit trail.") from exc

    safe_result = _json_safe(result)
    if isinstance(safe_result, dict):
        summary = safe_result.get("summary") or safe_result.get("message") or safe_result.get("result")
        resolution = safe_result.get("resolution") or safe_result.get("final_decision")
        final_status = safe_result.get("status")
    else:
        summary = str(safe_result)
        resolution = None
        final_status = None
    _append_audit({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "ticket_id": ticket_id,
        "agent_name": "Boss",
        "active_agent": "Boss",
        "action": "ticket_run_completed",
        "reasoning": str(resolution or summary or "Agent workflow completed."),
        "speech": str(summary or resolution or "Agent workflow completed."),
        "tool_calls": [],
    })
    staged_payments = _pending_payments_added_since(payment_ids_before, ticket_id)
    return {
        "ticket_id": ticket_id,
        "status": final_status,
        "summary": summary or resolution or safe_result,
        "resolution": resolution,
        "result": safe_result,
        "payment_proposals": staged_payments,
    }


@app.get("/api/events")
def get_events(limit: int = Query(default=100, ge=1, le=1000)) -> list[dict[str, Any]]:
    """Return normalized recent events from the persistent audit trail."""
    records = _load_audit_records()
    return [_event_view(row) for row in records[-limit:]]


@app.get("/api/approvals")
def get_approvals(limit: int = Query(default=100, ge=1, le=500)) -> list[dict[str, Any]]:
    """Return persisted pending and recent completed human-review requests."""
    connection = _connect()
    try:
        if not _table_exists(connection, "approvals"):
            return []
        rows = connection.execute("SELECT * FROM approvals ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        result: list[dict[str, Any]] = []
        for raw in rows:
            row = dict(raw)
            try:
                payload = json.loads(row.get("payload_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                payload = {}
            kind = str(row.get("kind") or "payment").strip().casefold()
            state = str(row.get("status") or "").strip().casefold()
            result.append({
                "approval_id": row.get("id"),
                "payment_id": row.get("id") if kind == "payment" else None,
                "ticket_id": row.get("ticket_id"),
                "kind": kind,
                "amount": row.get("amount"),
                "recipient": payload.get("payee") or payload.get("recipient") or payload.get("vendor_name"),
                "reason": payload.get("reason") or (
                    f"{payload.get('qty', '')} x {payload.get('sku', '')} {payload.get('size', '')}".strip()
                    if kind == "purchase_order" else "Recorded obligation"
                ),
                "reference_type": row.get("ref_type"),
                "reference_id": row.get("ref_id"),
                "sku": payload.get("sku"),
                "size": payload.get("size"),
                "quantity": payload.get("qty"),
                "lead_days": payload.get("lead_days"),
                "status": "PENDING_APPROVAL" if state == "pending" else state.upper(),
                "created_at": row.get("created_at"),
            })
        return list(reversed(result))
    finally:
        connection.close()


@app.post("/api/payments/approve")
def approve_payment(request: PaymentApprovalRequest) -> dict[str, Any]:
    """Finalize a staged payment after a transactional balance and reference check."""
    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        staged_table, payment_row, payment_status_column, payment_columns = _validate_staged_payment(connection, request)
        staged_ticket_id = None
        if staged_table == "approvals":
            staged_approval_columns = _columns(connection, "approvals")
            staged_ticket_column = _pick(staged_approval_columns, "ticket_id")
            if staged_ticket_column:
                staged_ticket_id = payment_row[staged_ticket_column]
        liability_table, liability_key, liability_values = _liability_updates(
            connection, request.reference_type, request.reference_id
        )
        account_row, _account_key_column, balance_column, balance = _cash_account(connection)
        if balance < request.amount:
            raise HTTPException(
                status_code=400,
                detail="Insufficient cash balance: negative balance strictly forbidden",
            )

        cash_columns = _columns(connection, "cash_accounts")
        account_selector = _pick(cash_columns, "account_name", "name")
        if account_selector:
            account_selector_value = account_row[account_selector]
        else:
            account_selector = _pick(cash_columns, "cash_account_id", "account_id", "id")
            if not account_selector:
                raise HTTPException(status_code=500, detail="The cash account has no update key.")
            account_selector_value = account_row[account_selector]

        connection.execute(
            f"UPDATE {_quote('cash_accounts')} SET {_quote(balance_column)} = ? "
            f"WHERE {_quote(account_selector)} = ?",
            (balance - request.amount, account_selector_value),
        )

        paid_at = datetime.now(timezone.utc).isoformat()
        if staged_table == "approvals":
            # A staged request becomes a completed ledger row only after this
            # transaction has passed cash and liability validation.
            payment_columns = _columns(connection, "payments")
            payment_status = _pick(payment_columns, "status", "payment_status")
            if not payment_status:
                connection.execute("ALTER TABLE payments ADD COLUMN status TEXT NOT NULL DEFAULT 'PAID'")
                payment_columns = _columns(connection, "payments")
                payment_status = _pick(payment_columns, "status", "payment_status")
            if not payment_status:
                raise HTTPException(status_code=500, detail="The payments table has no status field.")

            account_columns = _columns(connection, "cash_accounts")
            account_name_column = _pick(account_columns, "account_name", "name")
            account_name = str(account_row[account_name_column]) if account_name_column else "Checking"
            canonical_type = "invoice" if liability_table == "invoices" else "lease"
            values_by_name: dict[str, Any] = {
                "kind": canonical_type,
                "type": canonical_type,
                "ref_id": request.reference_id,
                "reference_id": request.reference_id,
                "amount": request.amount,
                "account": account_name,
                "account_name": account_name,
                "paid_at": paid_at,
                "payment_date": paid_at,
                "status": "PAID",
                "payment_status": "PAID",
                "approved_by": "Human Approval",
                "recipient": request.recipient.strip(),
                "payee": request.recipient.strip(),
                "reference_type": canonical_type,
                "ref_type": canonical_type,
            }
            insert_values: dict[str, Any] = {}
            for name, column in payment_columns.items():
                if name in values_by_name:
                    insert_values[column] = values_by_name[name]
            if not insert_values:
                raise HTTPException(status_code=500, detail="The payments table has no writable ledger fields.")
            columns_sql = ", ".join(_quote(column) for column in insert_values)
            placeholders = ", ".join("?" for _ in insert_values)
            cursor = connection.execute(
                f"INSERT INTO payments ({columns_sql}) VALUES ({placeholders})",
                tuple(insert_values.values()),
            )
            actual_payment_id = int(cursor.lastrowid)

            approval_columns = _columns(connection, "approvals")
            approval_key = _pick(approval_columns, "approval_id", "id")
            approval_status = _pick(approval_columns, "status", "approval_status")
            if not approval_key or not approval_status:
                raise HTTPException(status_code=500, detail="The staged approval record cannot be finalized.")
            approval_updates: dict[str, Any] = {approval_status: "approved"}
            for name, value in (("decided_at", paid_at), ("decided_by", "Human Approval"),
                                ("decision_note", f"Approved payment ledger row {actual_payment_id}.")):
                column = _pick(approval_columns, name)
                if column:
                    approval_updates[column] = value
            update_clause = ", ".join(f"{_quote(column)} = ?" for column in approval_updates)
            connection.execute(
                f"UPDATE approvals SET {update_clause} WHERE {_quote(approval_key)} = ?",
                (*approval_updates.values(), request.payment_id),
            )
        else:
            payment_key = _pick(payment_columns, "payment_id", "id")
            if not payment_key:
                raise HTTPException(status_code=500, detail="The payments table has no payment identifier.")
            payment_updates: dict[str, Any] = {payment_status_column: "PAID"}
            for name in ("paid_at", "payment_date", "updated_at"):
                column = _pick(payment_columns, name)
                if column:
                    payment_updates[column] = paid_at
            set_clause = ", ".join(f"{_quote(column)} = ?" for column in payment_updates)
            connection.execute(
                f"UPDATE {_quote('payments')} SET {set_clause} WHERE {_quote(payment_key)} = ?",
                (*payment_updates.values(), request.payment_id),
            )
            actual_payment_id = request.payment_id

        liability_set = ", ".join(f"{_quote(column)} = ?" for column in liability_values)
        connection.execute(
            f"UPDATE {_quote(liability_table)} SET {liability_set} WHERE {_quote(liability_key)} = ?",
            (*liability_values.values(), request.reference_id),
        )
        connection.commit()
        _append_audit({
            "timestamp": paid_at,
            "ticket_id": staged_ticket_id,
            "agent_name": "HumanApproval",
            "active_agent": "HumanApproval",
            "action": "payment_approved",
            "reasoning": f"Approval {request.payment_id} created paid ledger row {actual_payment_id} for {request.reference_type} {request.reference_id}.",
            "tool_calls": [],
        })
        return {
            "status": "success",
            "payment_id": actual_payment_id,
            "approval_id": request.payment_id if staged_table == "approvals" else None,
            "payment_status": "PAID",
            "balance": balance - request.amount,
        }
    except HTTPException:
        connection.rollback()
        raise
    except (TypeError, ValueError, sqlite3.Error) as exc:
        connection.rollback()
        raise HTTPException(status_code=500, detail="Payment approval could not be completed safely.") from exc
    finally:
        connection.close()


@app.post("/api/purchase-orders/approve")
def approve_purchase_order(request: PurchaseOrderApprovalRequest) -> dict[str, Any]:
    """Approve a staged local PO for staff handoff without contacting a vendor or moving cash."""
    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        if not _table_exists(connection, "approvals"):
            raise HTTPException(status_code=404, detail="Purchase-order approval was not found.")
        approval = connection.execute(
            "SELECT * FROM approvals WHERE id = ?", (request.approval_id,)
        ).fetchone()
        if approval is None:
            raise HTTPException(status_code=404, detail="Purchase-order approval was not found.")
        if str(approval["kind"]).strip().casefold() != "purchase_order":
            raise HTTPException(status_code=409, detail="This request is not a purchase order.")
        if str(approval["status"]).strip().casefold() != "pending":
            raise HTTPException(status_code=409, detail="This purchase order is no longer pending approval.")
        try:
            payload = json.loads(approval["payload_json"] or "{}")
            vendor_id = int(payload["vendor_id"])
            ticket_id = int(approval["ticket_id"])
            sku = str(payload["sku"]).strip().upper()
            size = str(payload["size"]).strip().upper()
            quantity = int(payload["qty"])
            unit_cost = float(payload["unit_cost"])
            amount = float(approval["amount"])
            lead_days = int(payload["lead_days"])
        except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=409, detail="The staged purchase-order details are invalid.") from exc
        if (not sku or not size or quantity <= 0 or unit_cost <= 0 or amount <= 0
                or lead_days < 0 or not math.isclose(amount, round(unit_cost * quantity, 2), rel_tol=0, abs_tol=0.005)):
            raise HTTPException(status_code=409, detail="The staged purchase-order amount or item details do not reconcile.")

        vendor = connection.execute("SELECT id, name, specialty, lead_days FROM vendors WHERE id = ?", (vendor_id,)).fetchone()
        if vendor is None or int(vendor["lead_days"]) != lead_days:
            raise HTTPException(status_code=409, detail="The recorded vendor or lead-time details have changed.")
        specialty = str(vendor["specialty"] or "").strip().casefold()
        if "apparel" not in specialty and "reprint" not in specialty:
            raise HTTPException(status_code=409, detail="The vendor is not recorded as an apparel supplier.")
        blockers = connection.execute(
            "SELECT id FROM invoices WHERE vendor_id = ? AND lower(trim(status)) IN ('open','unpaid','overdue','pending')",
            (vendor_id,),
        ).fetchall()
        if blockers:
            raise HTTPException(status_code=409, detail="The vendor still has an open unpaid invoice; no order was approved.")

        ticket = connection.execute(
            "SELECT sku, size, qty, invoice_id FROM tickets WHERE id = ?", (ticket_id,)
        ).fetchone()
        if ticket is None or str(ticket["sku"] or "").strip().upper() != sku or str(ticket["size"] or "").strip().upper() != size:
            raise HTTPException(status_code=409, detail="The purchase order no longer matches its ticket.")
        if ticket["invoice_id"] is not None:
            linked_invoice = connection.execute(
                "SELECT vendor_id FROM invoices WHERE id = ?", (ticket["invoice_id"],)
            ).fetchone()
            if linked_invoice is None or int(linked_invoice["vendor_id"]) != vendor_id:
                raise HTTPException(status_code=409, detail="The vendor no longer matches the ticket's linked invoice.")
        stock = connection.execute("SELECT qty FROM inventory WHERE sku = ? AND size = ?", (sku, size)).fetchone()
        price = connection.execute("SELECT unit_cost FROM pricing WHERE sku = ?", (sku,)).fetchone()
        if stock is None or price is None:
            raise HTTPException(status_code=409, detail="Current inventory or pricing facts are unavailable.")
        shortfall = max(0, int(ticket["qty"] or 0) - int(stock["qty"]))
        if quantity != shortfall or not math.isclose(float(price["unit_cost"]), unit_cost, rel_tol=0, abs_tol=0.005):
            raise HTTPException(status_code=409, detail="Stock shortfall or unit cost changed; stage a fresh request.")

        _, _, _, balance = _cash_account(connection)
        pending_total = float(connection.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM approvals WHERE status = 'pending' AND id <> ?",
            (request.approval_id,),
        ).fetchone()[0] or 0)
        if amount > round(balance - pending_total, 2):
            raise HTTPException(status_code=400, detail="Insufficient available cash for this purchase commitment; cash was not changed.")

        existing_order = connection.execute(
            "SELECT id FROM purchase_orders WHERE ticket_id = ? AND status = 'approved_for_staff' LIMIT 1",
            (ticket_id,),
        ).fetchone() if _table_exists(connection, "purchase_orders") else None
        if existing_order is not None:
            raise HTTPException(status_code=409, detail="This ticket already has an approved staff handoff.")

        connection.execute(
            "CREATE TABLE IF NOT EXISTS purchase_orders ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, approval_id INTEGER NOT NULL UNIQUE, ticket_id INTEGER NOT NULL, "
            "vendor_id INTEGER NOT NULL, sku TEXT NOT NULL, size TEXT NOT NULL, qty INTEGER NOT NULL, "
            "unit_cost REAL NOT NULL, total REAL NOT NULL, lead_days INTEGER NOT NULL, status TEXT NOT NULL, "
            "created_at TEXT NOT NULL, approved_by TEXT)"
        )
        decided_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        cursor = connection.execute(
            "INSERT INTO purchase_orders(approval_id,ticket_id,vendor_id,sku,size,qty,unit_cost,total,lead_days,status,created_at,approved_by) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (request.approval_id, ticket_id, vendor_id, sku, size, quantity, unit_cost, amount,
             lead_days, "approved_for_staff", decided_at, request.approved_by.strip()),
        )
        purchase_order_id = int(cursor.lastrowid)
        connection.execute(
            "UPDATE approvals SET status='approved', decided_at=?, decided_by=?, decision_note=? WHERE id=?",
            (decided_at, request.approved_by.strip(), f"Approved for internal staff handoff as PO {purchase_order_id}; no vendor was contacted.", request.approval_id),
        )
        # The local ticket work is complete once staff has an approved, recorded
        # handoff; this does not claim shipment or delivery.
        notes_column = _pick(_columns(connection, "tickets"), "notes", "description")
        if notes_column:
            connection.execute(
                f"UPDATE tickets SET status='resolved', {_quote(notes_column)} = COALESCE({_quote(notes_column)}, '') || ? WHERE id = ?",
                (f"\nResolution: Purchase order {purchase_order_id} was approved for internal staff handoff. No vendor contact or shipment is claimed.", ticket_id),
            )
        connection.commit()
        _append_audit({
            "timestamp": decided_at,
            "ticket_id": ticket_id,
            "agent_name": "HumanApproval",
            "active_agent": "HumanApproval",
            "action": "purchase_order_approved_for_staff",
            "reasoning": f"Purchase order {purchase_order_id} was approved for internal staff handoff; no external order was sent and cash was unchanged.",
            "tool_calls": [],
        })
        return {
            "status": "APPROVED",
            "approval_id": request.approval_id,
            "purchase_order_id": purchase_order_id,
            "amount": amount,
            "cash_changed": False,
            "balance": balance,
            "vendor": str(vendor["name"]),
        }
    except HTTPException:
        connection.rollback()
        raise
    except (TypeError, ValueError, sqlite3.Error) as exc:
        connection.rollback()
        raise HTTPException(status_code=500, detail="Purchase-order approval could not be completed safely.") from exc
    finally:
        connection.close()


@app.get("/api/cash/balance")
def get_cash_balance() -> dict[str, Any]:
    """Return the current checking/operating cash balance."""
    connection = _connect()
    try:
        _row, _key, _balance_column, balance = _cash_account(connection)
        return {"account_name": "Checking", "balance": balance}
    finally:
        connection.close()


@app.post("/api/database/reset")
def reset_working_database() -> dict[str, str]:
    """Restore the working database from the untouched original database."""
    if not ORIGINAL_DB.exists():
        raise HTTPException(status_code=500, detail="The original database file is missing.")
    try:
        WORKING_DB.parent.mkdir(parents=True, exist_ok=True)
        # Copying is intentionally limited to this explicit reset route.
        import shutil
        shutil.copyfile(ORIGINAL_DB, WORKING_DB)
    except OSError as exc:
        raise HTTPException(status_code=500, detail="Database reset failed.") from exc
    return {"status": "success", "message": "Database reset to pristine state."}
