"""Deterministic shop tools shared by the REST API and MCP server."""

from __future__ import annotations

import calendar
import json
import sqlite3
from datetime import date, datetime
from typing import Any

from .db import add_event, as_dict, connect, json_dump, utc_now


FINAL_STATUSES = {"open", "waiting_approval", "waiting_customer", "blocked", "resolved", "ready_for_staff"}
UNPAID_STATUSES = {"open", "unpaid", "overdue", "pending"}


def _business_today(conn: sqlite3.Connection) -> date:
    row = conn.execute("SELECT date_today FROM desk LIMIT 1").fetchone()
    if not row:
        return date.today()
    return date.fromisoformat(str(row["date_today"])[:10])


def _is_unpaid(status: str) -> bool:
    return status.strip().lower() not in {"paid", "closed", "settled", "void"}


def _unpaid_invoice_count(conn: sqlite3.Connection, vendor_id: int) -> int:
    rows = conn.execute("SELECT status FROM invoices WHERE vendor_id=?", (vendor_id,)).fetchall()
    return sum(1 for row in rows if _is_unpaid(str(row["status"])))


def _agent_event(
    conn: sqlite3.Connection,
    run_id: str | None,
    ticket_id: int | None,
    agent: str,
    kind: str,
    content: str,
    details: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        "INSERT INTO agent_events(run_id,ticket_id,created_at,agent,kind,content,details_json) VALUES(?,?,?,?,?,?,?)",
        (run_id, ticket_id, utc_now(), agent, kind, content, json_dump(details or {})),
    )


def get_tickets() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM tickets ORDER BY id").fetchall()
        today = _business_today(conn).isoformat()
        result = []
        for row in rows:
            item = dict(row)
            item["shop_date"] = today
            item["drafts"] = [
                dict(draft)
                for draft in conn.execute(
                    "SELECT id,body,status,created_at FROM customer_drafts WHERE ticket_id=? ORDER BY id DESC",
                    (item["id"],),
                ).fetchall()
            ]
            item["approvals"] = [
                _approval_dict(approval)
                for approval in conn.execute(
                    "SELECT * FROM approvals WHERE ticket_id=? ORDER BY id DESC",
                    (item["id"],),
                ).fetchall()
            ]
            result.append(item)
    return result


def get_ticket(ticket_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
        if not row:
            return None
        ticket = dict(row)
        ticket["shop_date"] = _business_today(conn).isoformat()
        ticket["drafts"] = [
            dict(draft)
            for draft in conn.execute(
                "SELECT id,body,status,created_at FROM customer_drafts WHERE ticket_id=? ORDER BY id DESC",
                (ticket_id,),
            ).fetchall()
        ]
        ticket["approvals"] = [
            _approval_dict(approval)
            for approval in conn.execute(
                "SELECT * FROM approvals WHERE ticket_id=? ORDER BY id DESC",
                (ticket_id,),
            ).fetchall()
        ]
    return ticket


def _approval_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item["payload"] = json.loads(item.pop("payload_json") or "{}")
    return item


def get_dashboard_state() -> dict[str, Any]:
    with connect() as conn:
        today = _business_today(conn).isoformat()
        tickets = conn.execute("SELECT * FROM tickets ORDER BY id").fetchall()
        accounts = [dict(row) for row in conn.execute("SELECT * FROM cash_accounts ORDER BY name").fetchall()]
        invoices = [dict(row) for row in conn.execute("SELECT * FROM invoices ORDER BY due_date,id").fetchall()]
        leases = [dict(row) for row in conn.execute("SELECT * FROM leases ORDER BY id").fetchall()]
        vendors = [dict(row) for row in conn.execute("SELECT * FROM vendors ORDER BY id").fetchall()]
        inventory = [dict(row) for row in conn.execute("SELECT * FROM inventory ORDER BY sku,size").fetchall()]
        approvals = [
            _approval_dict(row)
            for row in conn.execute(
                "SELECT a.*,t.subject AS ticket_subject FROM approvals a "
                "LEFT JOIN tickets t ON t.id=a.ticket_id ORDER BY a.id DESC"
            ).fetchall()
        ]
        purchase_orders = [
            dict(row)
            for row in conn.execute(
                "SELECT p.*,v.name AS vendor_name FROM purchase_orders p "
                "LEFT JOIN vendors v ON v.id=p.vendor_id ORDER BY p.id DESC"
            ).fetchall()
        ]
        drafts = [
            dict(row)
            for row in conn.execute(
                "SELECT d.*,t.subject AS ticket_subject FROM customer_drafts d "
                "LEFT JOIN tickets t ON t.id=d.ticket_id ORDER BY d.id DESC"
            ).fetchall()
        ]
        table_counts = {
            table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            for table in ("tickets", "inventory", "pricing", "vendors", "leases", "cash_accounts", "payments", "invoices")
        }
    return {
        "shop_date": today,
        "tickets": [dict(row) for row in tickets],
        "cash_accounts": accounts,
        "invoices": invoices,
        "leases": leases,
        "vendors": vendors,
        "inventory": inventory,
        "approvals": approvals,
        "pending_approvals": [item for item in approvals if item["status"] == "pending"],
        "purchase_orders": purchase_orders,
        "customer_drafts": drafts,
        "table_counts": table_counts,
    }


def inspect_inventory(sku: str, size: str, requested_qty: int = 1) -> dict[str, Any]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT sku,name,size,qty,location FROM inventory WHERE sku=? AND size=?",
            (sku.strip().upper(), size.strip().upper()),
        ).fetchall()
    if not rows:
        return {"sku": sku.strip().upper(), "size": size.strip().upper(), "on_hand": 0, "requested_qty": requested_qty, "shortfall": max(0, requested_qty), "known_size": False}
    row = dict(rows[0])
    row["on_hand"] = row.pop("qty")
    row["requested_qty"] = max(0, int(requested_qty))
    row["shortfall"] = max(0, row["requested_qty"] - row["on_hand"])
    row["known_size"] = True
    return row


def _specialty_for_sku(sku: str) -> str | None:
    normalized = sku.strip().upper()
    if normalized.startswith(("CC-HOOD-", "CC-TEE-", "CC-HAT-")):
        return "apparel reprint"
    if normalized.startswith("CC-MUG-"):
        return "mugs and small goods"
    return None


def get_vendor_options(sku: str) -> dict[str, Any]:
    specialty = _specialty_for_sku(sku)
    with connect() as conn:
        today = _business_today(conn)
        rows = conn.execute("SELECT id,name,specialty,lead_days FROM vendors ORDER BY lead_days,id").fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["open_unpaid_invoice_count"] = _unpaid_invoice_count(conn, int(item["id"]))
            item["eligible_for_sku"] = specialty is not None and item["specialty"].strip().lower() == specialty
            item["blocked_by_unpaid_invoice"] = item["open_unpaid_invoice_count"] > 0
            item["can_ship"] = item["eligible_for_sku"] and not item["blocked_by_unpaid_invoice"]
            item["lead_days"] = int(item["lead_days"])
            result.append(item)
        blockers = [
            dict(row)
            for row in conn.execute(
                "SELECT i.id,i.vendor_id,v.name AS vendor_name,i.amount,i.due_date,i.status,i.description "
                "FROM invoices i JOIN vendors v ON v.id=i.vendor_id ORDER BY i.due_date"
            ).fetchall()
            if _is_unpaid(str(row["status"]))
        ]
        for invoice in blockers:
            invoice["overdue"] = date.fromisoformat(str(invoice["due_date"])[:10]) < today
    return {"sku": sku.strip().upper(), "required_specialty": specialty, "vendors": result, "open_invoices": blockers}


def inspect_invoice(invoice_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute(
            "SELECT i.*,v.name AS vendor_name,v.specialty,v.lead_days FROM invoices i "
            "JOIN vendors v ON v.id=i.vendor_id WHERE i.id=?",
            (invoice_id,),
        ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["shop_date"] = _business_today(conn).isoformat()
        item["overdue"] = _is_unpaid(str(item["status"])) and date.fromisoformat(str(item["due_date"])[:10]) < _business_today(conn)
        item["blocks_vendor_shipment"] = _is_unpaid(str(item["status"]))
        item["vendor_open_unpaid_invoice_count"] = _unpaid_invoice_count(conn, int(item["vendor_id"]))
    return item


def inspect_lease(lease_id: int) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM leases WHERE id=?", (lease_id,)).fetchone()
        if not row:
            return None
        item = dict(row)
        today = _business_today(conn)
        due = date.fromisoformat(str(item["next_due"])[:10])
        item["shop_date"] = today.isoformat()
        item["days_until_due"] = (due - today).days
        item["overdue"] = due < today
        item["due_within_seven_days"] = 0 <= (due - today).days <= 7
    return item


def get_cash_status() -> dict[str, Any]:
    with connect() as conn:
        accounts = [dict(row) for row in conn.execute("SELECT * FROM cash_accounts ORDER BY name").fetchall()]
        pending = conn.execute(
            "SELECT COALESCE(SUM(amount),0) FROM approvals WHERE kind='payment' AND status='pending'"
        ).fetchone()[0]
    return {"accounts": accounts, "pending_payment_requests": float(pending)}


def calculate_margin(sku: str, quantity: int, discount_percent: float = 0) -> dict[str, Any] | None:
    if quantity <= 0 or not 0 <= discount_percent <= 100:
        return None
    with connect() as conn:
        row = conn.execute("SELECT unit_cost,list_price FROM pricing WHERE sku=?", (sku.strip().upper(),)).fetchone()
    if not row:
        return None
    unit_cost, list_price = float(row["unit_cost"]), float(row["list_price"])
    unit_price = list_price * (1 - discount_percent / 100)
    unit_margin = unit_price - unit_cost
    revenue = unit_price * quantity
    cost = unit_cost * quantity
    return {
        "sku": sku.strip().upper(),
        "quantity": quantity,
        "list_price": round(list_price, 2),
        "unit_cost": round(unit_cost, 2),
        "discount_percent": discount_percent,
        "discounted_unit_price": round(unit_price, 2),
        "unit_margin": round(unit_margin, 2),
        "gross_margin_percent": round((unit_margin / unit_price * 100) if unit_price else 0, 2),
        "order_margin": round(revenue - cost, 2),
        "order_cost": round(cost, 2),
    }


def prepare_payment_request(
    ticket_id: int,
    reference_type: str,
    ref_id: int,
    reason: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    reference_type = reference_type.strip().lower()
    with connect() as conn:
        ticket = conn.execute("SELECT id,subject FROM tickets WHERE id=?", (ticket_id,)).fetchone()
        if not ticket:
            return {"ok": False, "error": f"Ticket {ticket_id} does not exist."}
        if reference_type == "invoice":
            ref = conn.execute(
                "SELECT i.*,v.name AS payee FROM invoices i JOIN vendors v ON v.id=i.vendor_id WHERE i.id=?",
                (ref_id,),
            ).fetchone()
            if not ref:
                return {"ok": False, "error": f"Invoice {ref_id} does not exist."}
            if not _is_unpaid(str(ref["status"])):
                return {"ok": False, "error": f"Invoice {ref_id} is already {ref['status']}."}
            amount = float(ref["amount"])
            payload = {
                "reference_type": "invoice",
                "reference_id": ref_id,
                "payee": ref["payee"],
                "description": ref["description"],
                "due_date": ref["due_date"],
                "shop_date": _business_today(conn).isoformat(),
                "reason": reason.strip(),
            }
        elif reference_type == "lease":
            ref = conn.execute("SELECT * FROM leases WHERE id=?", (ref_id,)).fetchone()
            if not ref:
                return {"ok": False, "error": f"Lease {ref_id} does not exist."}
            amount = float(ref["monthly_rent"])
            payload = {
                "reference_type": "lease",
                "reference_id": ref_id,
                "payee": ref["landlord"],
                "description": f"Monthly rent: {ref['space_name']}",
                "due_date": ref["next_due"],
                "shop_date": _business_today(conn).isoformat(),
                "reason": reason.strip(),
            }
        else:
            return {"ok": False, "error": "Payment requests can reference only an invoice or lease."}

        existing = conn.execute(
            "SELECT * FROM approvals WHERE kind='payment' AND ref_type=? AND ref_id=? AND status='pending' ORDER BY id LIMIT 1",
            (reference_type, ref_id),
        ).fetchone()
        if existing:
            approval = _approval_dict(existing)
            return {"ok": True, "created": False, "approval": approval, "message": "A human approval request for this obligation is already pending."}
        balance_row = conn.execute("SELECT balance FROM cash_accounts WHERE name='checking'").fetchone()
        balance = float(balance_row["balance"]) if balance_row else 0.0
        if amount > balance:
            return {
                "ok": False,
                "error": "Payment request refused: available checking cash is insufficient.",
                "amount": amount,
                "available_cash": balance,
                "shortfall": round(amount - balance, 2),
            }
        cursor = conn.execute(
            "INSERT INTO approvals(ticket_id,kind,ref_type,ref_id,amount,payload_json,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (ticket_id, "payment", reference_type, ref_id, amount, json_dump(payload), "pending", utc_now()),
        )
        approval_id = int(cursor.lastrowid)
    add_event(run_id, ticket_id, "Accounting", "approval_request", f"Payment request #{approval_id} queued for manager approval: ${amount:,.2f} to {payload['payee']}.", {"approval_id": approval_id, "amount": amount, "kind": "payment"})
    return {"ok": True, "created": True, "approval_id": approval_id, "amount": amount, "payload": payload, "status": "pending", "message": "Prepared for human approval. No payment has been made."}


def prepare_purchase_order_request(
    ticket_id: int,
    sku: str,
    size: str,
    qty: int,
    vendor_id: int,
    reason: str,
    run_id: str | None = None,
) -> dict[str, Any]:
    sku = sku.strip().upper()
    size = size.strip().upper()
    if qty <= 0:
        return {"ok": False, "error": "Purchase quantity must be a positive whole number."}
    with connect() as conn:
        ticket = conn.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
        if not ticket:
            return {"ok": False, "error": f"Ticket {ticket_id} does not exist."}
        if ticket["sku"] and str(ticket["sku"]).upper() != sku:
            return {"ok": False, "error": "Purchase order SKU must match the ticket."}
        if ticket["size"] and str(ticket["size"]).upper() != size:
            return {"ok": False, "error": "Purchase order size must match the ticket."}
        required_qty = int(ticket["qty"] or qty)
        inventory_row = conn.execute("SELECT qty FROM inventory WHERE sku=? AND size=?", (sku, size)).fetchone()
        on_hand = int(inventory_row["qty"]) if inventory_row else 0
        shortfall = max(0, required_qty - on_hand)
        if shortfall == 0:
            return {"ok": False, "error": "The requested quantity is already covered by on-hand stock; no purchase order is needed."}
        if qty != shortfall:
            return {"ok": False, "error": f"Purchase quantity must match the ticket shortfall ({shortfall}).", "shortfall": shortfall, "on_hand": on_hand}
        vendor = conn.execute("SELECT * FROM vendors WHERE id=?", (vendor_id,)).fetchone()
        if not vendor:
            return {"ok": False, "error": f"Vendor {vendor_id} does not exist."}
        required_specialty = _specialty_for_sku(sku)
        if not required_specialty or str(vendor["specialty"]).strip().lower() != required_specialty:
            return {"ok": False, "error": f"Vendor does not match the required specialty ({required_specialty or 'no known category'})."}
        unpaid = _unpaid_invoice_count(conn, vendor_id)
        if unpaid:
            open_rows = [dict(row) for row in conn.execute(
                "SELECT id,amount,due_date,status,description FROM invoices WHERE vendor_id=?",
                (vendor_id,),
            ).fetchall() if _is_unpaid(str(row["status"]))]
            return {
                "ok": False,
                "error": f"Vendor {vendor['name']} cannot ship while it has an open unpaid invoice.",
                "vendor_id": vendor_id,
                "vendor_name": vendor["name"],
                "open_unpaid_invoices": open_rows,
                "lead_days": int(vendor["lead_days"]),
            }
        price = conn.execute("SELECT unit_cost FROM pricing WHERE sku=?", (sku,)).fetchone()
        if not price:
            return {"ok": False, "error": f"No unit cost is recorded for {sku}."}
        unit_cost = float(price["unit_cost"])
        total = round(unit_cost * qty, 2)
        payload = {
            "vendor_id": vendor_id,
            "vendor_name": vendor["name"],
            "sku": sku,
            "size": size,
            "qty": qty,
            "unit_cost": unit_cost,
            "total": total,
            "lead_days": int(vendor["lead_days"]),
            "reason": reason.strip(),
            "on_hand": on_hand,
            "shortfall": shortfall,
        }
        existing = conn.execute(
            "SELECT * FROM approvals WHERE ticket_id=? AND kind='purchase_order' AND status='pending' ORDER BY id DESC LIMIT 1",
            (ticket_id,),
        ).fetchone()
        if existing:
            return {"ok": True, "created": False, "approval": _approval_dict(existing), "message": "A purchase order request is already awaiting human approval for this ticket."}
        cursor = conn.execute(
            "INSERT INTO approvals(ticket_id,kind,ref_type,ref_id,amount,payload_json,status,created_at) VALUES(?,?,?,?,?,?,?,?)",
            (ticket_id, "purchase_order", "vendor", vendor_id, total, json_dump(payload), "pending", utc_now()),
        )
        approval_id = int(cursor.lastrowid)
    add_event(run_id, ticket_id, "Accounting", "approval_request", f"Purchase order #{approval_id} queued for manager approval: {qty} × {sku} {size} from {payload['vendor_name']} (${total:,.2f}, {payload['lead_days']} day lead).", {"approval_id": approval_id, "kind": "purchase_order", "amount": total})
    return {"ok": True, "created": True, "approval_id": approval_id, "amount": total, "payload": payload, "status": "pending", "message": "Prepared for human approval. No order has been sent to the vendor."}


def save_customer_draft(ticket_id: int, body: str, run_id: str | None = None) -> dict[str, Any]:
    body = body.strip()
    if not body or len(body) > 5000:
        return {"ok": False, "error": "Draft must contain 1 to 5,000 characters."}
    with connect() as conn:
        ticket = conn.execute("SELECT id,requester,subject FROM tickets WHERE id=?", (ticket_id,)).fetchone()
        if not ticket:
            return {"ok": False, "error": f"Ticket {ticket_id} does not exist."}
        cursor = conn.execute(
            "INSERT INTO customer_drafts(ticket_id,body,status,created_at) VALUES(?,?,?,?)",
            (ticket_id, body, "draft", utc_now()),
        )
        draft_id = int(cursor.lastrowid)
    add_event(run_id, ticket_id, "Customer Service", "draft_saved", f"Customer message draft #{draft_id} saved on the ticket. Nothing was sent.", {"draft_id": draft_id})
    return {"ok": True, "draft_id": draft_id, "ticket_id": ticket_id, "recipient": ticket["requester"], "subject": ticket["subject"], "body": body, "status": "draft", "sent": False}


def set_ticket_decision(ticket_id: int, status: str, summary: str, run_id: str | None = None) -> dict[str, Any]:
    if status not in FINAL_STATUSES:
        return {"ok": False, "error": f"Status must be one of: {', '.join(sorted(FINAL_STATUSES))}."}
    summary = summary.strip()
    if not summary:
        return {"ok": False, "error": "A short decision summary is required."}
    with connect() as conn:
        cursor = conn.execute("UPDATE tickets SET status=? WHERE id=?", (status, ticket_id))
        if cursor.rowcount == 0:
            return {"ok": False, "error": f"Ticket {ticket_id} does not exist."}
    add_event(run_id, ticket_id, "Boss", "decision", summary, {"status": status})
    return {"ok": True, "ticket_id": ticket_id, "status": status, "summary": summary}


def mark_ticket_in_progress(ticket_id: int, run_id: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE tickets SET status='in_progress' WHERE id=?", (ticket_id,))
    add_event(run_id, ticket_id, "Boss", "ticket_started", "Boss picked up this ticket and is reviewing the request.")


def fail_ticket_safe(ticket_id: int, run_id: str, message: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE tickets SET status='open' WHERE id=? AND status='in_progress'", (ticket_id,))
    add_event(run_id, ticket_id, "System", "error", message)


def _add_month(iso_date: str) -> str:
    parsed = date.fromisoformat(iso_date[:10])
    month_index = parsed.month
    year = parsed.year + (month_index // 12)
    month = (month_index % 12) + 1
    day = min(parsed.day, calendar.monthrange(year, month)[1])
    return date(year, month, day).isoformat()


def decide_approval(approval_id: int, decision: str, decided_by: str, note: str = "") -> dict[str, Any]:
    if decision not in {"approve", "reject"}:
        return {"ok": False, "error": "Decision must be approve or reject."}
    decided_by = decided_by.strip()
    if not decided_by:
        return {"ok": False, "error": "Approver name is required."}
    now = utc_now()
    payment_result: dict[str, Any] | None = None
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        approval_row = conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        if not approval_row:
            conn.rollback()
            return {"ok": False, "error": f"Approval request {approval_id} does not exist."}
        approval = _approval_dict(approval_row)
        if approval["status"] != "pending":
            conn.rollback()
            return {"ok": False, "error": f"Approval request {approval_id} is already {approval['status']}."}
        if decision == "reject":
            conn.execute(
                "UPDATE approvals SET status='rejected',decided_at=?,decided_by=?,decision_note=? WHERE id=?",
                (now, decided_by, note.strip(), approval_id),
            )
            conn.execute("UPDATE tickets SET status='open' WHERE id=? AND status='waiting_approval'", (approval["ticket_id"],))
            _agent_event(conn, None, int(approval["ticket_id"]), decided_by, "approval_rejected", f"Request #{approval_id} was rejected. No cash or vendor action occurred.", {"approval_id": approval_id, "kind": approval["kind"], "note": note.strip()})
            conn.commit()
            return {"ok": True, "approval_id": approval_id, "status": "rejected", "message": "Request rejected. No payment or vendor order was made."}

        payload = approval["payload"]
        if approval["kind"] == "payment":
            balance_row = conn.execute("SELECT balance FROM cash_accounts WHERE name='checking'").fetchone()
            if not balance_row:
                conn.rollback()
                return {"ok": False, "error": "Checking account is missing; payment was refused."}
            balance = float(balance_row["balance"])
            amount = float(approval["amount"])
            if amount > balance:
                conn.rollback()
                return {"ok": False, "error": "Payment refused: available cash is insufficient. The balance was not changed.", "available_cash": balance, "amount": amount, "shortfall": round(amount - balance, 2)}
            if approval["ref_type"] == "invoice":
                invoice = conn.execute("SELECT * FROM invoices WHERE id=?", (approval["ref_id"],)).fetchone()
                if not invoice or not _is_unpaid(str(invoice["status"])):
                    conn.rollback()
                    return {"ok": False, "error": "Invoice is no longer open and unpaid; no payment was made."}
                if round(float(invoice["amount"]), 2) != round(amount, 2):
                    conn.rollback()
                    return {"ok": False, "error": "Invoice amount changed; prepare a new approval request."}
            elif approval["ref_type"] == "lease":
                lease = conn.execute("SELECT * FROM leases WHERE id=?", (approval["ref_id"],)).fetchone()
                if not lease or round(float(lease["monthly_rent"]), 2) != round(amount, 2):
                    conn.rollback()
                    return {"ok": False, "error": "Lease amount changed; prepare a new approval request."}
            else:
                conn.rollback()
                return {"ok": False, "error": "Unsupported payment reference; payment was refused."}

            payment_cursor = conn.execute(
                "INSERT INTO payments(kind,ref_id,amount,account,paid_at,approved_by) VALUES(?,?,?,?,?,?)",
                (approval["ref_type"], approval["ref_id"], amount, "checking", _business_today(conn).isoformat(), decided_by),
            )
            conn.execute("UPDATE cash_accounts SET balance=balance-?,date=? WHERE name='checking' AND balance>=?", (amount, _business_today(conn).isoformat(), amount))
            if approval["ref_type"] == "invoice":
                conn.execute("UPDATE invoices SET status='paid' WHERE id=?", (approval["ref_id"],))
                conn.execute("UPDATE tickets SET status='open' WHERE status IN ('blocked','waiting_approval') AND id<>?", (approval["ticket_id"],))
                conn.execute("UPDATE tickets SET status='open' WHERE id=? AND status<>'resolved'", (approval["ticket_id"],))
            else:
                conn.execute("UPDATE leases SET next_due=? WHERE id=?", (_add_month(str(lease["next_due"])), approval["ref_id"]))
                conn.execute("UPDATE tickets SET status='resolved' WHERE lease_id=?", (approval["ref_id"],))
            payment_result = {"payment_id": int(payment_cursor.lastrowid), "amount": amount, "balance_after": round(balance - amount, 2)}
            conn.execute(
                "UPDATE approvals SET status='approved',decided_at=?,decided_by=?,decision_note=? WHERE id=?",
                (now, decided_by, note.strip(), approval_id),
            )
            _agent_event(conn, None, int(approval["ticket_id"]), decided_by, "payment_recorded", f"Payment #{payment_result['payment_id']} recorded for ${amount:,.2f}. Checking balance is ${payment_result['balance_after']:,.2f}.", {"approval_id": approval_id, **payment_result})
        elif approval["kind"] == "purchase_order":
            vendor = conn.execute("SELECT * FROM vendors WHERE id=?", (payload["vendor_id"],)).fetchone()
            if not vendor or _unpaid_invoice_count(conn, int(payload["vendor_id"])):
                conn.rollback()
                return {"ok": False, "error": "Purchase order refused: the selected vendor has an open unpaid invoice and cannot ship."}
            cursor = conn.execute(
                "INSERT INTO purchase_orders(approval_id,ticket_id,vendor_id,sku,size,qty,unit_cost,total,lead_days,status,created_at,approved_by) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (approval_id, approval["ticket_id"], payload["vendor_id"], payload["sku"], payload["size"], payload["qty"], payload["unit_cost"], payload["total"], payload["lead_days"], "approved_for_staff", now, decided_by),
            )
            conn.execute(
                "UPDATE approvals SET status='approved',decided_at=?,decided_by=?,decision_note=? WHERE id=?",
                (now, decided_by, note.strip(), approval_id),
            )
            conn.execute("UPDATE tickets SET status='ready_for_staff' WHERE id=?", (approval["ticket_id"],))
            payment_result = {"purchase_order_id": int(cursor.lastrowid), "amount": float(payload["total"]), "vendor_name": payload["vendor_name"], "lead_days": payload["lead_days"], "cash_changed": False}
            _agent_event(conn, None, int(approval["ticket_id"]), decided_by, "purchase_order_approved", f"Purchase order approved for staff handoff: {payload['qty']} × {payload['sku']} {payload['size']} from {payload['vendor_name']}. Nothing was sent to the vendor.", {"approval_id": approval_id, **payment_result})
        else:
            conn.rollback()
            return {"ok": False, "error": "Unsupported approval type."}
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return {"ok": True, "approval_id": approval_id, "status": "approved", **(payment_result or {})}
