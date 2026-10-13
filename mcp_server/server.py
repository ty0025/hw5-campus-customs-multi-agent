"""FastMCP tool server for Campus Customs' local shop database and board actions."""

from __future__ import annotations

import os
import json
import math
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Annotated, Literal


PROJECT_ROOT = Path(__file__).resolve().parents[1]

# The supplied MCP config invokes plain "python"; use this project's installed
# dependencies when launched as a module, without changing the global environment.
# Normal imports do not switch interpreters or start a transport.
if __name__ == "__main__":
    _local_python = PROJECT_ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if _local_python.is_file() and Path(sys.executable).absolute() != _local_python.absolute():
        os.execv(str(_local_python), [str(_local_python), "-B", "-m", "mcp_server.server", *sys.argv[1:]])

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field


WORKING_DB_PATH = PROJECT_ROOT / "data" / "campus_customs_new.db"
PositiveInt = Annotated[int, Field(strict=True, gt=0)]
NonemptyString = Annotated[str, Field(strict=True, min_length=1)]


class InventoryStockResult(BaseModel):
    """Observed stock and whether it covers one requested order quantity."""

    sku: str
    size: str
    product_name: str
    location: str
    order_quantity: int
    on_hand: int
    shortfall: int
    can_fulfill: bool


class UnpaidInvoice(BaseModel):
    """An unresolved vendor invoice with timing relative to the shop date."""

    invoice_id: int
    amount: float
    due_date: date
    status: str
    description: str | None
    overdue: bool
    days_overdue: int


class VendorRestockResult(BaseModel):
    """Invoice clearance only; this does not establish SKU supply capability."""

    vendor_id: int
    vendor_name: str
    specialty: str
    lead_days: int
    shop_date: date
    unpaid_invoices: list[UnpaidInvoice]
    total_unpaid: float
    blocked_by_unpaid_invoices: bool


class LeaseDueResult(BaseModel):
    """The next recorded rent obligation, without executing a payment."""

    lease_id: int
    space_name: str
    landlord: str
    monthly_rent: float
    next_due: date
    shop_date: date
    days_until_due: int
    status: Literal["overdue", "due_today", "upcoming"]
    notes: str | None


class TicketDetailsResult(BaseModel):
    """Ticket row translated to the agent-facing ticket contract."""

    ticket_id: int
    customer_id: str
    type: str
    subject: str
    description: str | None
    sku: str | None
    size: str | None
    quantity: int | None
    lease_id: int | None
    invoice_id: int | None
    invoice_vendor_id: int | None = None
    invoice_vendor_name: str | None = None
    status: str
    created_at: str
    shop_date: date


class TicketStatusResult(BaseModel):
    """Updated ticket status and its appended resolution note."""

    ticket_id: int
    status: str
    description: str | None
    updated: bool


class CashBalanceResult(BaseModel):
    """A balance read from the named shop cash account."""

    account_name: str
    balance: float
    as_of_date: date


class PaymentProposalResult(BaseModel):
    """Payment payload; persisted requests remain in approvals, never payments."""

    proposal_id: int | None
    ticket_id: int | None
    amount: float
    recipient: str
    reason: str
    reference_type: Literal["invoice", "lease"] | None
    ref_id: str | None
    status: Literal["PENDING_APPROVAL"]
    available_cash: float
    projected_cash_after_approval: float
    persisted_for_human_review: bool


class PurchaseOrderProposalResult(BaseModel):
    """A local purchase-order request that still requires a human decision."""

    proposal_id: int
    ticket_id: int
    vendor_id: int
    vendor_name: str
    sku: str
    size: str
    quantity: int
    unit_cost: float
    amount: float
    lead_days: int
    reason: str
    status: Literal["PENDING_APPROVAL"]
    cash_changed: Literal[False]


class CustomerDraftResult(BaseModel):
    """Saved local draft; ``sent`` is always false."""

    draft_id: int
    ticket_id: int
    recipient: str
    subject: str
    body: str
    status: Literal["draft"]
    sent: Literal[False]


class PricingAndMarginResult(BaseModel):
    """Recorded SKU unit cost, list price, gross margin, and markup rate."""

    sku: str
    unit_cost: float
    list_price: float
    gross_margin_per_unit: float
    gross_margin_percent: float


mcp = FastMCP(
    "Campus Customs Operations",
    instructions=(
        "Use the fixed working shop database for ticket, inventory, vendor, lease, "
        "cash, margin, and local draft operations. Payment requests are staged for "
        "human approval; no tool sends external messages or directly executes payment."
    ),
)

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

WRITE_LOCAL = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    """Open only the fixed working file, in SQLite's enforced read-only mode."""
    if not WORKING_DB_PATH.is_file():
        raise FileNotFoundError("Missing data/campus_customs_new.db; prepare the working copy with Problem 2 first.")
    if WORKING_DB_PATH.is_symlink():
        raise ValueError("The working database must be a regular file, not a symbolic link.")
    conn = sqlite3.connect(WORKING_DB_PATH.as_uri() + "?mode=ro", uri=True)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        yield conn
    finally:
        conn.close()


@contextmanager
def _write_connection() -> Iterator[sqlite3.Connection]:
    """Open the same working database for one explicit, atomic MCP write."""
    if not WORKING_DB_PATH.is_file():
        raise FileNotFoundError("Missing data/campus_customs_new.db; prepare the working copy with Problem 2 first.")
    if WORKING_DB_PATH.is_symlink():
        raise ValueError("The working database must be a regular file, not a symbolic link.")
    conn = sqlite3.connect(WORKING_DB_PATH, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _positive_integer(value: int, label: str) -> None:
    """Also enforce integer inputs when a tool is called directly in Python."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label} must be a positive integer.")


def _identifier(value: str, label: str) -> str:
    """Normalize a nonempty SKU or size without interpolating it into SQL."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string.")
    return value.strip().upper()


def _shop_date(conn: sqlite3.Connection) -> date:
    """Require one valid desk date instead of falling back to the system clock."""
    rows = conn.execute("SELECT date_today FROM desk").fetchall()
    if len(rows) != 1:
        raise ValueError("desk must contain exactly one business date.")
    try:
        return date.fromisoformat(rows[0]["date_today"])
    except (TypeError, ValueError) as exc:
        raise ValueError("desk.date_today must be a valid YYYY-MM-DD date.") from exc


@mcp.tool(annotations=READ_ONLY)
def check_inventory_stock(
    sku: NonemptyString,
    size: NonemptyString,
    order_quantity: PositiveInt,
) -> InventoryStockResult:
    """Check stock for a SKU and size against a positive requested quantity.

    Args:
        sku: Product SKU, normalized by trimming whitespace and uppercasing.
        size: Product size, normalized in the same way as the SKU.
        order_quantity: Positive whole-number quantity requested by the customer.

    Returns:
        InventoryStockResult with the recorded product, location, on_hand quantity,
        shortfall=max(order_quantity-on_hand, 0), and can_fulfill flag.

    Use the SKU, size, and quantity from Ticket 101, or from Ticket 103 when
    checking its bulk order; this does not reserve stock or approve a discount.
    Raises ValueError for invalid input or a missing SKU/size record, rather than
    treating an unknown product as zero stock; a missing working file is an error.
    """
    _positive_integer(order_quantity, "order_quantity")
    sku = _identifier(sku, "sku")
    size = _identifier(size, "size")
    with _connection() as conn:
        row = conn.execute(
            "SELECT sku, size, name, qty, location FROM inventory WHERE sku = ? AND size = ?",
            (sku, size),
        ).fetchone()
    if row is None:
        raise ValueError(f"No inventory record for SKU {sku!r} and size {size!r}.")
    return InventoryStockResult(
        sku=row["sku"],
        size=row["size"],
        product_name=row["name"],
        location=row["location"],
        order_quantity=order_quantity,
        on_hand=row["qty"],
        shortfall=max(order_quantity - row["qty"], 0),
        can_fulfill=row["qty"] >= order_quantity,
    )


@mcp.tool(annotations=READ_ONLY)
def check_vendor_restock_eligibility(vendor_id: PositiveInt) -> VendorRestockResult:
    """Check whether unpaid invoices prevent a specified vendor's new shipments.

    Args:
        vendor_id: Positive vendors.id, obtained from a linked invoice or an
            independently selected candidate vendor; no SKU mapping is assumed.

    Returns:
        VendorRestockResult containing the vendor's recorded name, specialty,
        lead_days, desk-based shop_date, itemized unpaid_invoices, total_unpaid,
        and blocked_by_unpaid_invoices flag.

    An invoice blocks shipment while its normalized status is open, unpaid,
    overdue, or pending, even if its due date is in the future; unexpected
    statuses raise an error rather than silently clearing a vendor.
    Paid, settled, closed, void, cancelled, and canceled invoices do not block.
    The initial data uses open; other explicit status spellings are supported.
    A clear invoice check is not confirmation of SKU capability or delivery.
    Supports Ticket 101's invoice-linked restock and Ticket 103's candidate check.
    Raises ValueError for invalid IDs, unknown vendors, or invalid desk data.
    """
    _positive_integer(vendor_id, "vendor_id")
    with _connection() as conn:
        vendor = conn.execute(
            "SELECT id, name, specialty, lead_days FROM vendors WHERE id = ?", (vendor_id,)
        ).fetchone()
        if vendor is None:
            raise ValueError(f"No vendor record for vendor_id={vendor_id}.")
        today = _shop_date(conn)
        rows = conn.execute(
            "SELECT id, amount, due_date, status, description FROM invoices "
            "WHERE vendor_id = ? ORDER BY due_date, id", (vendor_id,)
        ).fetchall()
    unpaid = []
    for row in rows:
        status = row["status"].strip().lower()
        if status in {"paid", "settled", "closed", "void", "cancelled", "canceled"}:
            continue
        if status not in {"open", "unpaid", "overdue", "pending"}:
            raise ValueError(f"Invoice {row['id']} has an unrecognized status; vendor clearance is undetermined.")
        due = date.fromisoformat(row["due_date"])
        unpaid.append(UnpaidInvoice(
            invoice_id=row["id"],
            amount=row["amount"],
            due_date=due,
            status=row["status"],
            description=row["description"],
            overdue=due < today,
            days_overdue=max((today - due).days, 0),
        ))
    return VendorRestockResult(
        vendor_id=vendor["id"],
        vendor_name=vendor["name"],
        specialty=vendor["specialty"],
        lead_days=vendor["lead_days"],
        shop_date=today,
        unpaid_invoices=unpaid,
        total_unpaid=round(sum(invoice.amount for invoice in unpaid), 2),
        blocked_by_unpaid_invoices=bool(unpaid),
    )


@mcp.tool(annotations=READ_ONLY)
def get_lease_due_status(lease_id: PositiveInt) -> LeaseDueResult:
    """Read a lease's next rent obligation relative to desk.date_today.

    Args:
        lease_id: Positive leases.id, such as the lease_id recorded on Ticket 102.

    Returns:
        LeaseDueResult with the recorded space, landlord, monthly_rent, next_due,
        notes, and shop_date; days_until_due is negative for overdue rent, zero
        for rent due today, and positive for upcoming rent, with matching status
        values overdue, due_today, or upcoming.

    Uses the database's business date, never the machine's current date.
    This reads the next recorded liability without claiming payment was made,
    scheduling a transfer, or updating the lease; payments need human approval.
    Raises ValueError for invalid IDs, unknown leases, or invalid desk data.
    """
    _positive_integer(lease_id, "lease_id")
    with _connection() as conn:
        lease = conn.execute(
            "SELECT id, space_name, landlord, monthly_rent, next_due, notes "
            "FROM leases WHERE id = ?", (lease_id,)
        ).fetchone()
        if lease is None:
            raise ValueError(f"No lease record for lease_id={lease_id}.")
        today = _shop_date(conn)
    due = date.fromisoformat(lease["next_due"])
    days_until_due = (due - today).days
    status = "overdue" if days_until_due < 0 else "due_today" if days_until_due == 0 else "upcoming"
    return LeaseDueResult(
        lease_id=lease["id"],
        space_name=lease["space_name"],
        landlord=lease["landlord"],
        monthly_rent=lease["monthly_rent"],
        next_due=due,
        shop_date=today,
        days_until_due=days_until_due,
        status=status,
        notes=lease["notes"],
    )


@mcp.tool(annotations=READ_ONLY)
def get_ticket_details(ticket_id: PositiveInt) -> TicketDetailsResult:
    """Read one ticket and its actual shop-date context from the tickets table.

    Args:
        ticket_id: Positive tickets.id for the board item to inspect.

    Returns:
    TicketDetailsResult with requester exposed as customer_id and notes as
    description, together with subject, request fields, status, and the
    date_today read from desk. If invoice_id is present, invoice_vendor_id and
    invoice_vendor_name resolve that actual invoices-to-vendors relationship.

    The source table has no separate customer_id or description columns; these
    returned names keep a clear agent-facing schema without modifying SQLite.
    Raises ValueError when the ticket is absent or desk.date_today is invalid.
    """
    if isinstance(ticket_id, bool) or not isinstance(ticket_id, int) or ticket_id <= 0:
        raise ValueError("ticket_id must be a positive integer.")
    with _connection() as conn:
        row = conn.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if row is None:
            raise ValueError(f"Ticket {ticket_id} does not exist.")
        today = _shop_date(conn)
    invoice_vendor = None
    if row["invoice_id"] is not None:
        with _connection() as conn:
            invoice_vendor = conn.execute(
                "SELECT i.vendor_id, v.name AS vendor_name FROM invoices i "
                "JOIN vendors v ON v.id = i.vendor_id WHERE i.id = ?",
                (row["invoice_id"],),
            ).fetchone()
    return TicketDetailsResult(
        ticket_id=row["id"], customer_id=row["requester"], type=row["type"], subject=row["subject"],
        description=row["notes"], sku=row["sku"], size=row["size"], quantity=row["qty"],
        lease_id=row["lease_id"], invoice_id=row["invoice_id"],
        invoice_vendor_id=invoice_vendor["vendor_id"] if invoice_vendor else None,
        invoice_vendor_name=invoice_vendor["vendor_name"] if invoice_vendor else None,
        status=row["status"],
        created_at=row["created_at"], shop_date=today,
    )


@mcp.tool(annotations=WRITE_LOCAL)
def update_ticket_status(
    ticket_id: PositiveInt,
    status: Literal["open", "in_progress", "waiting_approval", "waiting_customer", "blocked", "resolved", "ready_for_staff"],
    resolution: Annotated[str, Field(min_length=1, max_length=800)],
) -> TicketStatusResult:
    """Set a ticket's allowed status and append its resolution to tickets.notes.

    Args:
        ticket_id: Positive tickets.id for the board item.
        status: One supported workflow state from the Literal input schema.
        resolution: Concise final decision or handoff note, 1 to 800 characters.

    Returns:
        TicketStatusResult containing the resulting status, combined notes text,
        and whether the ticket row was updated.

    ``tickets`` has no resolution or assigned_agent column; the decision is
    recorded in its existing notes field without adding database columns.
    This tool changes only the named ticket and never executes a payment.
    Raises ValueError when the ticket is missing or inputs are invalid.
    """
    if isinstance(ticket_id, bool) or not isinstance(ticket_id, int) or ticket_id <= 0:
        raise ValueError("ticket_id must be a positive integer.")
    resolution = resolution.strip()
    if not resolution:
        raise ValueError("resolution must not be empty.")
    with _write_connection() as conn:
        row = conn.execute("SELECT notes FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if row is None:
            raise ValueError(f"Ticket {ticket_id} does not exist.")
        old_notes = row["notes"] or ""
        marker = f"Resolution: {resolution}"
        notes = old_notes if marker in old_notes.splitlines() else "\n".join(part for part in (old_notes, marker) if part)
        conn.execute("UPDATE tickets SET status = ?, notes = ? WHERE id = ?", (status, notes, ticket_id))
    return TicketStatusResult(ticket_id=ticket_id, status=status, description=notes, updated=True)


def _cash_account(conn: sqlite3.Connection, account_name: str) -> sqlite3.Row:
    """Resolve the assignment's default label to the database's sole account."""
    rows = conn.execute("SELECT name, balance, date FROM cash_accounts ORDER BY name").fetchall()
    if account_name.strip().casefold() == "operating cash" and len(rows) == 1:
        return rows[0]
    row = next((item for item in rows if item["name"].casefold() == account_name.strip().casefold()), None)
    if row is None:
        raise ValueError(f"No cash account matches {account_name!r}.")
    return row


@mcp.tool(annotations=READ_ONLY)
def get_cash_balance(
    account_name: Annotated[str, Field(min_length=1, max_length=120)] = "Operating Cash",
) -> CashBalanceResult:
    """Read cash_accounts without holding, withdrawing, or transferring funds.

    Args:
        account_name: Exact account name; the default label resolves to the
            sole account when the database contains exactly one account.

    Returns:
        CashBalanceResult with the stored account name, balance, and account date.

    The initial database names its single account ``checking``; this tool maps
    the default label ``Operating Cash`` to that actual row, not a fabricated
    balance. Raises ValueError when resolution is ambiguous or no row matches.
    """
    if not isinstance(account_name, str) or not account_name.strip():
        raise ValueError("account_name must be a nonempty string.")
    with _connection() as conn:
        row = _cash_account(conn, account_name)
    return CashBalanceResult(account_name=row["name"], balance=row["balance"],
                             as_of_date=date.fromisoformat(row["date"]))


def _ensure_approval_table(conn: sqlite3.Connection) -> None:
    """Create the app's existing pending-approval shape through the MCP boundary."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS approvals ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id INTEGER NOT NULL, "
        "kind TEXT NOT NULL CHECK(kind IN ('payment','purchase_order')), ref_type TEXT, ref_id INTEGER, "
        "amount REAL NOT NULL, payload_json TEXT NOT NULL, "
        "status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected')), "
        "created_at TEXT NOT NULL, decided_at TEXT, decided_by TEXT, decision_note TEXT, "
        "FOREIGN KEY(ticket_id) REFERENCES tickets(id))"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status, id)")


def _ensure_purchase_order_table(conn: sqlite3.Connection) -> None:
    """Create the app-owned record for an approved staff handoff, never a sent order."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS purchase_orders ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, approval_id INTEGER NOT NULL UNIQUE, "
        "ticket_id INTEGER NOT NULL, vendor_id INTEGER NOT NULL, sku TEXT NOT NULL, "
        "size TEXT NOT NULL, qty INTEGER NOT NULL, unit_cost REAL NOT NULL, total REAL NOT NULL, "
        "lead_days INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, approved_by TEXT)"
    )


@mcp.tool(annotations=WRITE_LOCAL)
def propose_payment(
    amount: Annotated[float, Field(gt=0, allow_inf_nan=False)],
    recipient: Annotated[str, Field(min_length=1, max_length=200)],
    reason: Annotated[str, Field(min_length=1, max_length=800)],
    ref_id: Annotated[str | None, Field(max_length=100)] = None,
) -> PaymentProposalResult:
    """Check available cash and stage a referenced invoice or lease payment.

    Args:
        amount: Positive USD amount, with no more than two decimal places.
        recipient: Payee to show the human approver.
        reason: Business explanation to retain in the approval payload.
        ref_id: Optional obligation as ``invoice:<id>`` or ``lease:<id>``.

    Returns:
        PaymentProposalResult with ``PENDING_APPROVAL`` status, available and
        projected cash, and an approval_id when the referenced request is staged.
        An omitted ref_id returns a validated proposal payload but cannot persist
        it to the human review board because no ticket can be identified.

    Checks the referenced open invoice amount or recorded monthly lease rent,
    links it to its actual ticket, and checks checking-account cash less other
    pending proposals. It writes a pending row to ``approvals`` only; it never
    inserts into ``payments``, updates an invoice/lease/account, or executes a
    payment. The human approval endpoint records an eventual approved payment.
    Insufficient cash, an unsupported/missing reference, and invalid inputs fail.
    """
    if isinstance(amount, bool) or not isinstance(amount, (float, int)) or not math.isfinite(amount) or amount <= 0:
        raise ValueError("amount must be a finite positive number.")
    amount_cents = round(float(amount) * 100)
    if not math.isclose(float(amount), amount_cents / 100, rel_tol=0, abs_tol=1e-8):
        raise ValueError("amount must have at most two decimal places.")
    amount = amount_cents / 100
    recipient = recipient.strip()
    reason = reason.strip()
    if not recipient or not reason:
        raise ValueError("recipient and reason must not be empty.")

    reference_type: Literal["invoice", "lease"] | None = None
    reference_number: int | None = None
    if ref_id is not None:
        prefix, separator, identifier = ref_id.strip().partition(":")
        if not separator or prefix.casefold() not in {"invoice", "lease"} or not identifier.isdecimal():
            raise ValueError("ref_id must be formatted as invoice:<id> or lease:<id>.")
        reference_type = prefix.casefold()  # type: ignore[assignment]
        reference_number = int(identifier)
        if reference_number <= 0:
            raise ValueError("ref_id must contain a positive obligation ID.")

    if reference_type is None:
        with _connection() as conn:
            today = _shop_date(conn)
            cash = _cash_account(conn, "Operating Cash")
            balance = round(float(cash["balance"]), 2)
        if amount > balance:
            raise ValueError(f"Payment proposal refused: {amount:.2f} exceeds available cash {balance:.2f}.")
        return PaymentProposalResult(
            proposal_id=None, ticket_id=None, amount=amount, recipient=recipient, reason=reason,
            reference_type=None, ref_id=None, status="PENDING_APPROVAL", available_cash=balance,
            projected_cash_after_approval=round(balance - amount, 2), persisted_for_human_review=False,
        )

    with _write_connection() as conn:
        today = _shop_date(conn)
        cash = _cash_account(conn, "Operating Cash")
        balance = round(float(cash["balance"]), 2)
        if reference_type == "invoice":
            obligation = conn.execute(
                "SELECT id, amount, status FROM invoices WHERE id = ?", (reference_number,)
            ).fetchone()
            if obligation is None:
                raise ValueError(f"Invoice {reference_number} does not exist.")
            if obligation["status"].strip().casefold() in {"paid", "settled", "closed", "void", "cancelled", "canceled"}:
                raise ValueError(f"Invoice {reference_number} is no longer payable.")
            expected_amount = round(float(obligation["amount"]), 2)
            ticket = conn.execute("SELECT id FROM tickets WHERE invoice_id = ? ORDER BY id LIMIT 1",
                                  (reference_number,)).fetchone()
        else:
            obligation = conn.execute(
                "SELECT id, monthly_rent AS amount FROM leases WHERE id = ?", (reference_number,)
            ).fetchone()
            if obligation is None:
                raise ValueError(f"Lease {reference_number} does not exist.")
            expected_amount = round(float(obligation["amount"]), 2)
            ticket = conn.execute("SELECT id FROM tickets WHERE lease_id = ? ORDER BY id LIMIT 1",
                                  (reference_number,)).fetchone()
        if abs(expected_amount - amount) > 0.009:
            raise ValueError(f"Proposal amount must match the recorded obligation ({expected_amount:.2f}).")
        if ticket is None:
            raise ValueError("The reference is not linked to a ticket and cannot be staged for board review.")
        ticket_id = int(ticket["id"])
        _ensure_approval_table(conn)
        pending = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM approvals WHERE kind = 'payment' AND status = 'pending' "
            "AND NOT (ref_type = ? AND ref_id = ?)",
            (reference_type, reference_number),
        ).fetchone()[0]
        available_for_new = round(balance - float(pending or 0), 2)
        if amount > available_for_new:
            raise ValueError(
                f"Payment proposal refused: {amount:.2f} exceeds unreserved available cash {available_for_new:.2f}."
            )
        existing = conn.execute(
            "SELECT id, amount FROM approvals WHERE kind = 'payment' AND status = 'pending' "
            "AND ref_type = ? AND ref_id = ? ORDER BY id LIMIT 1",
            (reference_type, reference_number),
        ).fetchone()
        if existing:
            if abs(float(existing["amount"]) - amount) > 0.009:
                raise ValueError("This obligation already has a different pending human approval request.")
            approval_id = int(existing["id"])
        else:
            payload = json.dumps({"payee": recipient, "reason": reason, "reference_type": reference_type,
                                  "ref_type": reference_type, "ref_id": reference_number,
                                  "requested_by": "Accounting"}, ensure_ascii=False, separators=(",", ":"))
            cursor = conn.execute(
                "INSERT INTO approvals(ticket_id,kind,ref_type,ref_id,amount,payload_json,status,created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (ticket_id, "payment", reference_type, reference_number, amount, payload, "pending",
                 datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )
            approval_id = int(cursor.lastrowid)
    return PaymentProposalResult(
        proposal_id=approval_id, ticket_id=ticket_id, amount=amount, recipient=recipient, reason=reason,
        reference_type=reference_type, ref_id=f"{reference_type}:{reference_number}",
        status="PENDING_APPROVAL", available_cash=balance,
        projected_cash_after_approval=round(balance - amount, 2), persisted_for_human_review=True,
    )


@mcp.tool(annotations=WRITE_LOCAL)
def propose_purchase_order(
    ticket_id: PositiveInt,
    sku: NonemptyString,
    size: NonemptyString,
    quantity: PositiveInt,
    vendor_id: PositiveInt,
    reason: Annotated[str, Field(min_length=1, max_length=800)],
) -> PurchaseOrderProposalResult:
    """Stage a stock-shortfall purchase order for human approval and staff handoff.

    The request must match the ticket's SKU/size and exact uncovered quantity,
    use a recorded vendor candidate with a compatible apparel specialty, and
    pass the vendor's open-invoice shipping block. The MCP tool never emails a
    supplier, decrements stock, debits cash, or marks goods shipped. Approval
    creates a local ``purchase_orders`` handoff; payment remains a separate,
    human-approved cash operation.

    Returns:
        PurchaseOrderProposalResult with the recorded candidate, exact shortfall,
        estimated commitment, and ``PENDING_APPROVAL`` state.
    """
    sku = _identifier(sku, "sku")
    size = _identifier(size, "size")
    reason = reason.strip()
    if not reason:
        raise ValueError("reason must not be empty.")

    with _write_connection() as conn:
        ticket = conn.execute(
            "SELECT id, type, sku, size, qty, invoice_id, status FROM tickets WHERE id = ?",
            (ticket_id,),
        ).fetchone()
        if ticket is None:
            raise ValueError(f"Ticket {ticket_id} does not exist.")
        if str(ticket["type"]).strip().casefold() not in {"customer_order", "price_override"}:
            raise ValueError("A purchase order can only be linked to a product ticket.")
        if str(ticket["sku"] or "").strip().upper() != sku or str(ticket["size"] or "").strip().upper() != size:
            raise ValueError("Purchase-order SKU and size must match the recorded ticket.")

        stock = conn.execute(
            "SELECT qty FROM inventory WHERE sku = ? AND size = ?", (sku, size)
        ).fetchone()
        if stock is None:
            raise ValueError("No inventory row exists for the ticket SKU and size; no quantity was inferred.")
        required = int(ticket["qty"] or 0)
        shortfall = max(0, required - int(stock["qty"]))
        if shortfall <= 0:
            raise ValueError("The ticket quantity is covered by recorded stock; no purchase order is needed.")
        if quantity != shortfall:
            raise ValueError(f"Purchase quantity must equal the current ticket shortfall ({shortfall}).")

        _ensure_approval_table(conn)
        _ensure_purchase_order_table(conn)
        completed_handoff = conn.execute(
            "SELECT id FROM purchase_orders WHERE ticket_id = ? AND status = 'approved_for_staff' LIMIT 1",
            (ticket_id,),
        ).fetchone()
        if completed_handoff is not None:
            raise ValueError(f"Ticket {ticket_id} already has an approved purchase order for staff handoff.")

        vendor = conn.execute(
            "SELECT id, name, specialty, lead_days FROM vendors WHERE id = ?", (vendor_id,)
        ).fetchone()
        if vendor is None:
            raise ValueError(f"Vendor {vendor_id} does not exist.")
        specialty = str(vendor["specialty"] or "").strip().casefold()
        if "apparel" not in specialty and "reprint" not in specialty:
            raise ValueError("The recorded vendor specialty is not compatible with an apparel restock.")

        if ticket["invoice_id"] is not None:
            linked = conn.execute(
                "SELECT vendor_id FROM invoices WHERE id = ?", (ticket["invoice_id"],)
            ).fetchone()
            if linked is None or int(linked["vendor_id"]) != vendor_id:
                raise ValueError("Vendor must match the supplier linked to this ticket's recorded invoice.")

        unpaid = conn.execute(
            "SELECT id, amount, status FROM invoices WHERE vendor_id = ?", (vendor_id,)
        ).fetchall()
        blockers = [row for row in unpaid if str(row["status"] or "").strip().casefold()
                    in {"open", "unpaid", "overdue", "pending"}]
        if blockers:
            identifiers = ", ".join(str(row["id"]) for row in blockers)
            raise ValueError(
                f"Vendor {vendor['name']} cannot ship while invoice(s) {identifiers} remain unpaid; "
                "Accounting must clear them through human approval first."
            )

        price = conn.execute("SELECT unit_cost FROM pricing WHERE sku = ?", (sku,)).fetchone()
        if price is None:
            raise ValueError(f"No unit cost is recorded for {sku}.")
        unit_cost = round(float(price["unit_cost"]), 2)
        amount = round(unit_cost * quantity, 2)
        existing = conn.execute(
            "SELECT id, amount, payload_json FROM approvals "
            "WHERE ticket_id = ? AND kind = 'purchase_order' AND status = 'pending' ORDER BY id DESC LIMIT 1",
            (ticket_id,),
        ).fetchone()
        if existing is not None:
            previous = json.loads(existing["payload_json"] or "{}")
            if (int(previous.get("vendor_id", -1)) != vendor_id
                    or str(previous.get("sku", "")).upper() != sku
                    or str(previous.get("size", "")).upper() != size
                    or int(previous.get("qty", -1)) != quantity
                    or not math.isclose(float(existing["amount"]), amount, rel_tol=0, abs_tol=0.005)):
                raise ValueError("A different purchase order is already awaiting human approval for this ticket.")
            proposal_id = int(existing["id"])
        else:
            cash = _cash_account(conn, "Operating Cash")
            balance = round(float(cash["balance"]), 2)
            pending = float(conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM approvals WHERE status = 'pending'"
            ).fetchone()[0] or 0)
            if amount > round(balance - pending, 2):
                raise ValueError(
                    f"Purchase proposal refused: {amount:.2f} exceeds unreserved available cash "
                    f"{round(balance - pending, 2):.2f}."
                )
            payload = {
                "vendor_id": vendor_id,
                "vendor_name": str(vendor["name"]),
                "sku": sku,
                "size": size,
                "qty": quantity,
                "unit_cost": unit_cost,
                "total": amount,
                "lead_days": int(vendor["lead_days"]),
                "reason": reason,
                "requested_by": "Accounting",
            }
            cursor = conn.execute(
                "INSERT INTO approvals(ticket_id,kind,ref_type,ref_id,amount,payload_json,status,created_at) "
                "VALUES(?, 'purchase_order', 'vendor', ?, ?, ?, 'pending', ?)",
                (ticket_id, vendor_id, amount, json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                 datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )
            proposal_id = int(cursor.lastrowid)

    return PurchaseOrderProposalResult(
        proposal_id=proposal_id,
        ticket_id=ticket_id,
        vendor_id=vendor_id,
        vendor_name=str(vendor["name"]),
        sku=sku,
        size=size,
        quantity=quantity,
        unit_cost=unit_cost,
        amount=amount,
        lead_days=int(vendor["lead_days"]),
        reason=reason,
        status="PENDING_APPROVAL",
        cash_changed=False,
    )


@mcp.tool(annotations=WRITE_LOCAL)
def save_customer_draft(
    ticket_id: PositiveInt,
    draft_body: Annotated[str, Field(min_length=1, max_length=5000)],
) -> CustomerDraftResult:
    """Save a customer-support message as a local board draft without sending it.

    Args:
        ticket_id: Positive tickets.id for the customer request.
        draft_body: Complete draft body, up to 5,000 characters.

    Returns:
        CustomerDraftResult with the stored recipient, ticket subject, body,
        database draft ID, status ``draft``, and the constant ``sent=False``.

    Inserts only into ``customer_drafts`` in the working shop database.
    This tool does not import an email client, send outbound messages, or call
    the customer. The draft appears with its source ticket on the dashboard.
    Raises ValueError if the ticket is missing or the body is blank.
    """
    if isinstance(ticket_id, bool) or not isinstance(ticket_id, int) or ticket_id <= 0:
        raise ValueError("ticket_id must be a positive integer.")
    if not isinstance(draft_body, str) or not draft_body.strip():
        raise ValueError("draft_body must not be empty.")
    body = draft_body.strip()
    with _write_connection() as conn:
        ticket = conn.execute("SELECT requester, subject FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if ticket is None:
            raise ValueError(f"Ticket {ticket_id} does not exist.")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS customer_drafts ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id INTEGER NOT NULL, body TEXT NOT NULL, "
            "status TEXT NOT NULL DEFAULT 'draft', created_at TEXT NOT NULL, "
            "FOREIGN KEY(ticket_id) REFERENCES tickets(id))"
        )
        cursor = conn.execute(
            "INSERT INTO customer_drafts(ticket_id, body, status, created_at) VALUES(?, ?, 'draft', ?)",
            (ticket_id, body, datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        draft_id = int(cursor.lastrowid)
    return CustomerDraftResult(draft_id=draft_id, ticket_id=ticket_id, recipient=ticket["requester"],
                               subject=ticket["subject"], body=body, status="draft", sent=False)


@mcp.tool(annotations=READ_ONLY)
def get_pricing_and_margin(sku: NonemptyString) -> PricingAndMarginResult:
    """Read one product's cost, list price, and unit gross margin from pricing.

    Args:
        sku: Product SKU, normalized by trimming whitespace and uppercasing.

    Returns:
        PricingAndMarginResult with recorded unit_cost, list_price,
        gross_margin_per_unit = list_price - unit_cost, and the margin as a
        percentage of list price. This does not apply an unrequested discount.

    Raises ValueError when the SKU has no recorded pricing or the list price
    cannot be used as a margin denominator.
    """
    sku = _identifier(sku, "sku")
    with _connection() as conn:
        row = conn.execute("SELECT sku, unit_cost, list_price FROM pricing WHERE sku = ?", (sku,)).fetchone()
    if row is None:
        raise ValueError(f"No pricing record for SKU {sku!r}.")
    if float(row["list_price"]) <= 0:
        raise ValueError("Recorded list_price must be positive to calculate a margin percentage.")
    margin = float(row["list_price"]) - float(row["unit_cost"])
    return PricingAndMarginResult(sku=row["sku"], unit_cost=row["unit_cost"], list_price=row["list_price"],
                                  gross_margin_per_unit=round(margin, 2),
                                  gross_margin_percent=round(margin / float(row["list_price"]) * 100, 2))


if __name__ == "__main__":
    mcp.run(transport="stdio")
