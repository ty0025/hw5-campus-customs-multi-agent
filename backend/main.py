"""FastAPI backend for the Campus Customs operations desk.

Run from the backend directory with:
    uvicorn main:app --reload --port 8000

Agents never move money. They stage requests in the ``approvals`` table through
MCP tools; the approve route below is the only code that changes cash.
"""
from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi import FastAPI, HTTPException, Query  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

from backend.app import settings  # noqa: E402  (loads .env and holds the fixed model name)
from backend.audit import AppendOnlyAuditLogger, load_records  # noqa: E402

DATA_DIR = PROJECT_ROOT / "data"
WORKING_DB = DATA_DIR / "campus_customs_new.db"
ORIGINAL_DB = DATA_DIR / "campus_customs.db"
TICKET_IDS = (101, 102, 103)
OPEN_INVOICE_STATES = ("open", "unpaid", "overdue", "pending")
PURCHASE_ORDERS_DDL = (
    "CREATE TABLE IF NOT EXISTS purchase_orders ("
    "id INTEGER PRIMARY KEY AUTOINCREMENT, approval_id INTEGER NOT NULL UNIQUE, "
    "ticket_id INTEGER NOT NULL, vendor_id INTEGER NOT NULL, sku TEXT NOT NULL, "
    "size TEXT NOT NULL, qty INTEGER NOT NULL, unit_cost REAL NOT NULL, total REAL NOT NULL, "
    "lead_days INTEGER NOT NULL, expected_arrival TEXT, status TEXT NOT NULL, "
    "created_at TEXT NOT NULL, approved_by TEXT)"
)

app = FastAPI(title="Campus Customs Operations API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class ApprovalDecision(BaseModel):
    approved_by: str = Field(default="Store manager", min_length=1, max_length=120)


# ---------------------------------------------------------------- helpers

def _connect() -> sqlite3.Connection:
    if not WORKING_DB.is_file():
        raise HTTPException(status_code=500, detail="data/campus_customs_new.db is missing; call the reset route.")
    connection = sqlite3.connect(WORKING_DB, timeout=15)
    connection.row_factory = sqlite3.Row
    return connection


def _has_table(connection: sqlite3.Connection, table: str) -> bool:
    return connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone() is not None


def _desk_date(connection: sqlite3.Connection) -> date:
    return date.fromisoformat(connection.execute("SELECT date_today FROM desk").fetchone()[0])


def _checking(connection: sqlite3.Connection) -> float:
    row = connection.execute("SELECT balance FROM cash_accounts WHERE name = 'checking'").fetchone()
    if row is None:
        raise HTTPException(status_code=500, detail="cash_accounts has no 'checking' row.")
    return round(float(row["balance"]), 2)


def _audit(ticket_id: int | None, agent_name: str, action: str, message: str) -> None:
    AppendOnlyAuditLogger(f"desk-{uuid4()}").append(
        ticket_id=ticket_id, agent_name=agent_name, action=action, message=message,
    )


def _add_months(day: date, months: int = 1) -> date:
    month_index = day.month - 1 + months
    year, month = day.year + month_index // 12, month_index % 12 + 1
    for candidate in (day.day, 30, 29, 28):
        try:
            return date(year, month, candidate)
        except ValueError:
            continue
    raise ValueError("Could not move the due date forward.")


def _event_view(row: dict[str, Any]) -> dict[str, Any]:
    """Shape one audit record for the board (older records used reasoning_notes)."""
    action = row.get("action")
    text = row.get("message") or row.get("reasoning_notes") or row.get("reasoning") or ""
    tool = None
    if row.get("tool_called"):
        tool = {"name": row["tool_called"], "arguments": row.get("tool_arguments"), "result": row.get("tool_result")}
    return {
        "timestamp": row.get("timestamp"),
        "run_id": row.get("run_id"),
        "ticket_id": row.get("ticket_id"),
        "agent": row.get("agent_name") or row.get("active_agent") or "System",
        "action": action,
        "kind": "speech" if action in {"response_synthesis", "ticket_run_completed"} else "note",
        "text": text,
        "findings": row.get("findings") or [],
        "tool": tool,
        "delegation_target": row.get("delegation_target"),
    }


# ---------------------------------------------------------------- routes

@app.get("/api/tickets")
def get_tickets() -> list[dict[str, Any]]:
    """The three board tickets with their request fields and open/resolved state."""
    connection = _connect()
    try:
        pending: dict[int, int] = {}
        if _has_table(connection, "approvals"):
            for row in connection.execute(
                "SELECT ticket_id, COUNT(*) AS n FROM approvals WHERE status = 'pending' GROUP BY ticket_id"
            ):
                pending[row["ticket_id"]] = row["n"]
        tickets = []
        for row in connection.execute("SELECT * FROM tickets WHERE id IN (101, 102, 103) ORDER BY id"):
            lines = (row["notes"] or "").splitlines()
            notes = [line[len("Resolution: "):] for line in lines if line.startswith("Resolution: ")]
            tickets.append({
                "ticket_id": row["id"],
                "type": row["type"],
                "requester": row["requester"],
                "subject": row["subject"],
                "request": next((line for line in lines if not line.startswith("Resolution: ")), ""),
                "sku": row["sku"], "size": row["size"], "qty": row["qty"],
                "lease_id": row["lease_id"], "invoice_id": row["invoice_id"],
                "status": row["status"],
                "board_status": "resolved" if row["status"] == "resolved" else "open",
                "latest_note": notes[-1] if notes else None,
                "pending_approvals": pending.get(row["id"], 0),
            })
        return tickets
    finally:
        connection.close()


@app.post("/api/tickets/{ticket_id}/run")
async def run_ticket(ticket_id: int) -> dict[str, Any]:
    """Run the five-agent team on one ticket. The board polls /api/events while this runs."""
    if ticket_id not in TICKET_IDS:
        raise HTTPException(status_code=404, detail="Only tickets 101, 102, and 103 are on the board.")
    if not settings.PORTKEY_API_KEY:
        raise HTTPException(status_code=503, detail="PORTKEY_API_KEY is not configured (.env).")
    from backend.agent_team import run_ticket as run_team

    try:
        outcome = await run_team(ticket_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Agent run failed: {type(exc).__name__}: {exc}") from exc
    return {
        "run_id": outcome.run_id,
        "ticket_id": ticket_id,
        "status": outcome.ticket.status,
        "summary": outcome.result.summary,
        "next_action": outcome.result.next_action,
        "agent_turns": outcome.agent_turns,
        "model_requests": outcome.model_requests,
        "total_tokens": outcome.total_tokens,
    }


@app.get("/api/events")
def get_events(
    ticket_id: int | None = None,
    since_reset: bool = True,
    limit: int = Query(default=300, ge=1, le=2000),
) -> list[dict[str, Any]]:
    """Recent agent events: who acted, what they said, which MCP tool they used and with what."""
    records = load_records()
    if since_reset:
        resets = [index for index, row in enumerate(records) if row.get("action") == "database_reset"]
        if resets:
            records = records[resets[-1]:]
    if ticket_id is not None:
        records = [row for row in records if row.get("ticket_id") == ticket_id]
    return [_event_view(row) for row in records[-limit:]]


@app.get("/api/approvals")
def get_approvals(ticket_id: int | None = None) -> list[dict[str, Any]]:
    """Payment and purchase-order requests staged by Accounting, with their human decision."""
    connection = _connect()
    try:
        if not _has_table(connection, "approvals"):
            return []
        rows = connection.execute(
            "SELECT * FROM approvals WHERE (? IS NULL OR ticket_id = ?) ORDER BY id", (ticket_id, ticket_id)
        ).fetchall()
        result = []
        for row in rows:
            payload = json.loads(row["payload_json"] or "{}")
            is_order = row["kind"] == "purchase_order"
            result.append({
                "approval_id": row["id"],
                "ticket_id": row["ticket_id"],
                "kind": row["kind"],
                "amount": row["amount"],
                "payee": payload.get("vendor_name") if is_order else payload.get("payee"),
                "reason": payload.get("reason"),
                "reference": (f"{payload.get('qty')} x {payload.get('sku')} {payload.get('size')}, "
                              f"{payload.get('lead_days')}-day lead") if is_order
                             else f"{row['ref_type']} {row['ref_id']}",
                "waits_for_invoices": payload.get("waits_for_invoice_payments", []),
                "status": row["status"],
                "created_at": row["created_at"],
                "decided_at": row["decided_at"],
                "decided_by": row["decided_by"],
                "decision_note": row["decision_note"],
            })
        return result
    finally:
        connection.close()


@app.post("/api/approvals/{approval_id}/approve")
def approve(approval_id: int, decision: ApprovalDecision) -> dict[str, Any]:
    """A human approves a staged payment or purchase order. This is the only route that moves cash.

    Payment: records a payments row dated desk.date_today, marks invoice paid or
    moves the lease's next_due forward a month, and debits checking.
    Purchase order: refuses while the vendor still has an open invoice, then
    records a purchase_orders row with its expected arrival and debits checking.
    Either way, if checking cannot cover the amount the request is refused and nothing changes.
    """
    approver = decision.approved_by.strip()
    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        if not _has_table(connection, "approvals"):
            raise HTTPException(status_code=404, detail="No approvals have been staged yet.")
        approval = connection.execute("SELECT * FROM approvals WHERE id = ?", (approval_id,)).fetchone()
        if approval is None:
            raise HTTPException(status_code=404, detail=f"Approval {approval_id} was not found.")
        if approval["status"] != "pending":
            raise HTTPException(status_code=409, detail=f"Approval {approval_id} is already {approval['status']}.")
        payload = json.loads(approval["payload_json"] or "{}")
        amount = round(float(approval["amount"]), 2)
        balance = _checking(connection)
        if amount > balance:
            raise HTTPException(
                status_code=400,
                detail=f"Refused: ${amount:,.2f} is more than the ${balance:,.2f} in checking. Balances may not go negative.",
            )
        today = _desk_date(connection)

        if approval["kind"] == "payment":
            ref_type, ref_id = approval["ref_type"], int(approval["ref_id"])
            if ref_type == "invoice":
                invoice = connection.execute("SELECT status FROM invoices WHERE id = ?", (ref_id,)).fetchone()
                if invoice is None or invoice["status"].strip().lower() not in OPEN_INVOICE_STATES:
                    raise HTTPException(status_code=409, detail=f"Invoice {ref_id} is not open.")
                connection.execute("UPDATE invoices SET status = 'paid' WHERE id = ?", (ref_id,))
                effect = f"invoice {ref_id} marked paid"
            elif ref_type == "lease":
                lease = connection.execute("SELECT next_due FROM leases WHERE id = ?", (ref_id,)).fetchone()
                if lease is None:
                    raise HTTPException(status_code=409, detail=f"Lease {ref_id} was not found.")
                new_due = _add_months(date.fromisoformat(lease["next_due"]))
                connection.execute("UPDATE leases SET next_due = ? WHERE id = ?", (new_due.isoformat(), ref_id))
                effect = f"lease {ref_id} rent paid; next_due moved to {new_due.isoformat()}"
            else:
                raise HTTPException(status_code=409, detail="A payment must reference an invoice or a lease.")
            kind, ledger_ref = ref_type, ref_id
        elif approval["kind"] == "purchase_order":
            vendor_id = int(payload["vendor_id"])
            blockers = connection.execute(
                f"SELECT id FROM invoices WHERE vendor_id = ? AND lower(trim(status)) IN ({','.join('?' * len(OPEN_INVOICE_STATES))})",
                (vendor_id, *OPEN_INVOICE_STATES),
            ).fetchall()
            if blockers:
                ids = ", ".join(str(row["id"]) for row in blockers)
                raise HTTPException(
                    status_code=409,
                    detail=f"{payload.get('vendor_name')} will not ship while invoice {ids} is unpaid. Approve that payment first.",
                )
            connection.execute(PURCHASE_ORDERS_DDL)
            arrival = today + timedelta(days=int(payload["lead_days"]))
            cursor = connection.execute(
                "INSERT INTO purchase_orders(approval_id,ticket_id,vendor_id,sku,size,qty,unit_cost,total,lead_days,"
                "expected_arrival,status,created_at,approved_by) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (approval_id, approval["ticket_id"], vendor_id, payload["sku"], payload["size"], int(payload["qty"]),
                 float(payload["unit_cost"]), amount, int(payload["lead_days"]), arrival.isoformat(), "approved",
                 today.isoformat(), approver),
            )
            kind, ledger_ref = "purchase_order", int(cursor.lastrowid)
            effect = (f"purchase order {ledger_ref}: {payload['qty']} x {payload['sku']} {payload['size']} "
                      f"from {payload.get('vendor_name')}, expected {arrival.isoformat()}")
        else:
            raise HTTPException(status_code=409, detail=f"Unknown approval kind {approval['kind']!r}.")

        cursor = connection.execute(
            "INSERT INTO payments(kind, ref_id, amount, account, paid_at, approved_by) VALUES(?,?,?,?,?,?)",
            (kind, ledger_ref, amount, "checking", today.isoformat(), approver),
        )
        payment_id = int(cursor.lastrowid)
        new_balance = round(balance - amount, 2)
        connection.execute("UPDATE cash_accounts SET balance = ? WHERE name = 'checking'", (new_balance,))
        connection.execute(
            "UPDATE approvals SET status = 'approved', decided_at = ?, decided_by = ?, decision_note = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), approver,
             f"Payment {payment_id}: {effect}.", approval_id),
        )
        connection.commit()
    except HTTPException:
        connection.rollback()
        raise
    finally:
        connection.close()

    _audit(approval["ticket_id"], "Human", "approval_decided",
           f"{approver} approved {approval['kind'].replace('_', ' ')} #{approval_id} for ${amount:,.2f}: {effect}. "
           f"Checking ${balance:,.2f} -> ${new_balance:,.2f}.")
    return {"approval_id": approval_id, "payment_id": payment_id, "amount": amount,
            "effect": effect, "balance_before": balance, "balance": new_balance}


@app.get("/api/drafts")
def get_drafts(ticket_id: int | None = None) -> list[dict[str, Any]]:
    """Customer message drafts saved by Customer Service. Nothing is ever sent."""
    connection = _connect()
    try:
        if not _has_table(connection, "customer_drafts"):
            return []
        rows = connection.execute(
            "SELECT d.id, d.ticket_id, d.body, d.status, d.created_at, t.requester, t.subject "
            "FROM customer_drafts d JOIN tickets t ON t.id = d.ticket_id "
            "WHERE (? IS NULL OR d.ticket_id = ?) ORDER BY d.id", (ticket_id, ticket_id),
        ).fetchall()
        return [dict(row) | {"sent": False} for row in rows]
    finally:
        connection.close()


@app.get("/api/cash/balance")
def get_cash_balance() -> dict[str, Any]:
    """Current checking balance from cash_accounts."""
    connection = _connect()
    try:
        return {"account_name": "checking", "balance": _checking(connection), "desk_date": _desk_date(connection).isoformat()}
    finally:
        connection.close()


@app.post("/api/database/reset")
def reset_working_database() -> dict[str, Any]:
    """Copy the untouched original database over the working copy for a clean run."""
    if not ORIGINAL_DB.is_file():
        raise HTTPException(status_code=500, detail="data/campus_customs.db is missing.")
    shutil.copyfile(ORIGINAL_DB, WORKING_DB)
    connection = _connect()
    try:
        balance = _checking(connection)
    finally:
        connection.close()
    _audit(None, "System", "database_reset",
           f"Working database reset from data/campus_customs.db. Starting checking balance ${balance:,.2f}.")
    return {"status": "reset", "balance": balance}
