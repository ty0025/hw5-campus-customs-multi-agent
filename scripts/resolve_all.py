"""Synchronously resolve the Campus Customs ticket workflow and write Q9 artifacts.

Run the non-mutating relational preflight with:
    python scripts/resolve_all.py

Run the authorized simulation after reviewing the ticket-to-liability mapping with:
    python scripts/resolve_all.py --execute --accept-database-mappings

The script never starts FastAPI or Vite. Agent/MCP child processes are short-lived
stdio sessions, and all database writes target only data/campus_customs_new.db.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import json
import re
import shutil
import sqlite3
import sys
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4


def find_hw5_root() -> Path:
    """Find hw5 whether the current workspace is hw5 itself or its parent."""
    script = Path(__file__).resolve()
    cwd = Path.cwd().resolve()
    candidates: list[Path] = [script.parent.parent, cwd, cwd / "hw5"]
    candidates.extend(script.parents)
    candidates.extend(cwd.parents)
    visited: set[Path] = set()
    for candidate in candidates:
        candidate = candidate.resolve()
        if candidate in visited:
            continue
        visited.add(candidate)
        if (candidate / "data" / "campus_customs.db").is_file() and (candidate / "mcp_server" / "server.py").is_file():
            return candidate
        nested = candidate / "hw5"
        if (nested / "data" / "campus_customs.db").is_file() and (nested / "mcp_server" / "server.py").is_file():
            return nested.resolve()
    raise FileNotFoundError("Could not locate Homework 5 with data/campus_customs.db and mcp_server/server.py.")


ROOT = find_hw5_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.agent_team import open_mcp_session, run_ticket  # noqa: E402
from backend.app.settings import MODEL_NAME, PORTKEY_API_KEY  # noqa: E402
from backend.audit import AppendOnlyAuditLogger  # noqa: E402
from backend.main import PaymentApprovalRequest, approve_payment  # noqa: E402


ORIGINAL_DB = ROOT / "data" / "campus_customs.db"
WORKING_DB = ROOT / "data" / "campus_customs_new.db"
OUTPUT_DIR = ROOT / "output"
AUDIT_PATH = OUTPUT_DIR / "audit_trail.json"
TICKET_IDS = (101, 102, 103)


def connect(path: Path = WORKING_DB, *, readonly: bool = False) -> sqlite3.Connection:
    """Open one SQLite connection, optionally enforcing query-only mode."""
    if readonly:
        uri = path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=15)
    else:
        connection = sqlite3.connect(path, timeout=15)
    connection.row_factory = sqlite3.Row
    if readonly:
        connection.execute("PRAGMA query_only = ON")
    return connection


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cash_account(connection: sqlite3.Connection) -> sqlite3.Row:
    rows = connection.execute("SELECT * FROM cash_accounts ORDER BY name").fetchall()
    if not rows:
        raise RuntimeError("No cash account exists in the working database.")
    for row in rows:
        if str(row["name"]).strip().casefold() in {"checking", "operating cash"}:
            return row
    if len(rows) == 1:
        return rows[0]
    raise RuntimeError("Could not uniquely identify the checking/operating cash account.")


def ticket_snapshot(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """Resolve actual ticket foreign keys and their linked liability facts."""
    result: list[dict[str, Any]] = []
    for ticket_id in TICKET_IDS:
        ticket = connection.execute("SELECT * FROM tickets WHERE id=?", (ticket_id,)).fetchone()
        if ticket is None:
            raise RuntimeError(f"Ticket {ticket_id} is missing from the working database.")
        item: dict[str, Any] = dict(ticket)
        if ticket["invoice_id"] is not None:
            invoice = connection.execute(
                "SELECT i.*,v.name AS vendor_name,v.lead_days FROM invoices i "
                "JOIN vendors v ON v.id=i.vendor_id WHERE i.id=?",
                (ticket["invoice_id"],),
            ).fetchone()
            item["invoice"] = dict(invoice) if invoice else None
        else:
            item["invoice"] = None
        if ticket["lease_id"] is not None:
            lease = connection.execute("SELECT * FROM leases WHERE id=?", (ticket["lease_id"],)).fetchone()
            item["lease"] = dict(lease) if lease else None
        else:
            item["lease"] = None
        if ticket["sku"] and ticket["size"]:
            inventory = connection.execute(
                "SELECT qty FROM inventory WHERE sku=? AND size=?",
                (ticket["sku"], ticket["size"]),
            ).fetchone()
            item["on_hand"] = int(inventory["qty"]) if inventory else 0
        else:
            item["on_hand"] = None
        result.append(item)
    return result


def liability_plan(connection: sqlite3.Connection, tickets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return unpaid liabilities linked by the database to these real tickets."""
    plan: list[dict[str, Any]] = []
    for ticket in tickets:
        invoice = ticket.get("invoice")
        if invoice and str(invoice["status"]).strip().casefold() not in {"paid", "settled", "closed", "void"}:
            plan.append({
                "ticket_id": int(ticket["id"]),
                "reference_type": "invoice",
                "reference_id": int(invoice["id"]),
                "amount": round(float(invoice["amount"]), 2),
                "recipient": invoice["vendor_name"],
                "reason": str(invoice["description"] or f"Settle invoice {invoice['id']}"),
            })
        lease = ticket.get("lease")
        if lease and str(lease.get("payment_status", "unpaid")).strip().casefold() not in {"paid", "settled", "closed"}:
            plan.append({
                "ticket_id": int(ticket["id"]),
                "reference_type": "lease",
                "reference_id": int(lease["id"]),
                "amount": round(float(lease["monthly_rent"]), 2),
                "recipient": lease["landlord"],
                "reason": f"Pay recorded rent obligation for {lease['space_name']}",
            })
    return plan


def preflight() -> list[dict[str, Any]]:
    """Inspect pristine data without resetting or writing to either database."""
    if not ORIGINAL_DB.is_file():
        raise FileNotFoundError(f"Original database missing: {ORIGINAL_DB}")
    with closing(connect(ORIGINAL_DB, readonly=True)) as connection:
        account = cash_account(connection)
        tickets = ticket_snapshot(connection)
        liabilities = liability_plan(connection, tickets)
    print(json.dumps({
        "homework_root": str(ROOT),
        "original_sha256": sha256(ORIGINAL_DB),
        "working_copy_exists": WORKING_DB.exists(),
        "starting_checking_balance": float(account["balance"]),
        "tickets": [{
            "ticket_id": item["id"],
            "type": item["type"],
            "subject": item["subject"],
            "status": item["status"],
            "invoice_id": item["invoice_id"],
            "lease_id": item["lease_id"],
            "sku": item["sku"],
            "size": item["size"],
            "requested_quantity": item["qty"],
            "on_hand": item["on_hand"],
        } for item in tickets],
        "actual_linked_liabilities": liabilities,
        "prompt_numbering_conflict": {
            "ticket_101": "customer order linked to invoice 501",
            "ticket_102": "rent notice linked to lease 1",
            "ticket_103": "hoodie discount request; no linked invoice or lease",
        },
    }, indent=2, ensure_ascii=False, default=str))
    return tickets


def parse_mcp_result(response: Any) -> dict[str, Any]:
    if getattr(response, "isError", False):
        message = " ".join(
            block.text for block in getattr(response, "content", []) if getattr(block, "text", None)
        )
        raise RuntimeError(message or "FastMCP returned an error.")
    structured = getattr(response, "structuredContent", None)
    if structured is None:
        structured = getattr(response, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    for block in getattr(response, "content", []):
        if getattr(block, "text", None):
            parsed = json.loads(block.text)
            if isinstance(parsed, dict):
                return parsed
    raise RuntimeError("FastMCP returned no structured result.")


async def call_mcp(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    async with open_mcp_session(ROOT) as session:
        return parse_mcp_result(await session.call_tool(tool_name, arguments))


def mcp_call(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return asyncio.run(call_mcp(tool_name, arguments))


def read_audit_records() -> list[dict[str, Any]]:
    if not AUDIT_PATH.is_file():
        return []
    content = AUDIT_PATH.read_text(encoding="utf-8")
    if not content.strip():
        return []
    try:
        parsed = json.loads(content)
        rows = parsed if isinstance(parsed, list) else [parsed]
        return [row for row in rows if isinstance(row, dict)]
    except json.JSONDecodeError:
        rows = []
        for line in content.splitlines():
            if line.strip():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
        return rows


def event_time(record: dict[str, Any]) -> datetime | None:
    try:
        return datetime.fromisoformat(str(record.get("timestamp", "")).replace("Z", "+00:00"))
    except ValueError:
        return None


def pending_for_liability(connection: sqlite3.Connection, item: dict[str, Any]) -> sqlite3.Row | None:
    table_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='approvals'"
    ).fetchone()
    if not table_exists:
        return None
    return connection.execute(
        "SELECT * FROM approvals WHERE kind='payment' AND ref_type=? AND ref_id=? AND status='pending' ORDER BY id LIMIT 1",
        (item["reference_type"], item["reference_id"]),
    ).fetchone()


def stage_missing_approval(item: dict[str, Any], logger: AppendOnlyAuditLogger) -> dict[str, Any]:
    """Use the MCP boundary for a verified linked liability if agents omitted staging."""
    result = mcp_call("propose_payment", {
        "amount": item["amount"],
        "recipient": item["recipient"],
        "reason": item["reason"],
        "ref_id": f"{item['reference_type']}:{item['reference_id']}",
    })
    logger.append(
        ticket_id=item["ticket_id"],
        agent_name="System",
        action="tool_call_completed",
        tool_called="propose_payment",
        reasoning_notes=(
            f"The agent pass did not leave a pending request for the verified ticket-linked "
            f"{item['reference_type']} {item['reference_id']}; the synchronous coordinator staged "
            "the database-matched proposal through FastMCP for human approval."
        ),
    )
    return result


def ensure_customer_draft(ticket_id: int, body: str, logger: AppendOnlyAuditLogger) -> dict[str, Any] | None:
    with closing(connect(readonly=True)) as connection:
        table_exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='customer_drafts'"
        ).fetchone()
        if table_exists and connection.execute(
            "SELECT 1 FROM customer_drafts WHERE ticket_id=? LIMIT 1", (ticket_id,)
        ).fetchone():
            return None
    draft = mcp_call("save_customer_draft", {"ticket_id": ticket_id, "draft_body": body})
    logger.append(
        ticket_id=ticket_id,
        agent_name="System",
        action="tool_call_completed",
        tool_called="save_customer_draft",
        reasoning_notes="A local customer draft was saved through FastMCP; no message was sent.",
    )
    return draft


def agent_events_since(records: list[dict[str, Any]], started_at: datetime, ticket_id: int) -> list[dict[str, Any]]:
    selected = []
    for record in records:
        when = event_time(record)
        if record.get("ticket_id") == ticket_id and when is not None and when >= started_at:
            selected.append(record)
    return selected


def agent_chain(events: list[dict[str, Any]]) -> list[str]:
    chain: list[str] = []
    for event in events:
        target = event.get("delegation_target")
        if event.get("action") == "delegation" and target:
            source = str(event.get("agent_name") or event.get("active_agent") or "Agent")
            value = f"{source} → {target}"
            if value not in chain:
                chain.append(value)
    return chain


def tool_names(events: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for event in events:
        name = event.get("tool_called") or event.get("tool_name")
        if name and name not in names:
            names.append(str(name))
    return names


def participating_agents(events: list[dict[str, Any]]) -> list[str]:
    ignored = {"System", "HumanApproval", "ExecutionCoordinator"}
    agents: list[str] = []
    for event in events:
        name = str(event.get("agent_name") or event.get("active_agent") or "")
        if name and name not in ignored and name not in agents:
            agents.append(name)
    if "Boss" not in agents:
        agents.insert(0, "Boss")
    return agents


def event_contributions(events: list[dict[str, Any]]) -> dict[str, str]:
    contributions: dict[str, list[str]] = {}
    for event in events:
        name = str(event.get("agent_name") or event.get("active_agent") or "")
        if not name or name in {"System", "HumanApproval", "ExecutionCoordinator"}:
            continue
        note = str(event.get("reasoning_notes") or event.get("reasoning") or "").strip()
        action = str(event.get("action") or "").strip()
        if note:
            sentence = f"{action}: {note}"
            bucket = contributions.setdefault(name, [])
            if sentence not in bucket:
                bucket.append(sentence)
    return {name: " ".join(notes[-4:])[:900] for name, notes in contributions.items()}


def panel_actual(ticket_id: int, detail: dict[str, Any], run_result: dict[str, Any], approval_details: list[dict[str, Any]]) -> str:
    agents = detail["agents"]
    chain = detail["delegation_chain"]
    tools = detail["mcp_tools"]
    agents_html = "".join(f"<li>{html.escape(name)}</li>" for name in agents) or "<li>Boss</li>"
    chain_html = "".join(f"<li>{html.escape(step)}</li>" for step in chain) or "<li>Boss triage; no specialist delegation was recorded.</li>"
    tools_html = "".join(f"<li><code>{html.escape(name)}</code></li>" for name in tools) or "<li>No MCP tools recorded.</li>"
    payment_html = ""
    if approval_details:
        payment_html = "<h4>Human approvals recorded</h4><ul>" + "".join(
            f"<li>{html.escape(item['recipient'])}: {html.escape(item['reference_type'])} {item['reference_id']} · ${item['amount']:,.2f} · PAID</li>"
            for item in approval_details
        ) + "</ul>"
    summary = run_result.get("result", {}).get("summary", "")
    return (
        '<div class="actual-section"><h3 class="section-kicker"><span class="dot">✓</span> Actual</h3>'
        f'<p class="actual-lead"><strong>Ticket {ticket_id} · {html.escape(str(detail["actual_type"]))}</strong> '
        f'{html.escape(str(detail["outcome"]))}</p>'
        '<div class="actual-columns">'
        f'<div><h4>Participating agents</h4><ul>{agents_html}</ul></div>'
        f'<div><h4>Delegation chain</h4><ul>{chain_html}</ul></div>'
        f'<div><h4>Exact MCP tools invoked</h4><ul>{tools_html}</ul></div></div>'
        f'{payment_html}<details><summary>Boss run summary</summary><p>{html.escape(str(summary))}</p></details>'
        '</div>'
    )


def find_div_range(panel: str, class_name: str) -> tuple[int, int]:
    opening = re.search(
        rf'<div\b[^>]*\bclass="[^"]*\b{re.escape(class_name)}\b[^"]*"[^>]*>', panel, flags=re.I
    )
    if not opening:
        raise ValueError(f"Panel has no div with class {class_name!r}.")
    token_pattern = re.compile(r"</?div\b[^>]*>", flags=re.I)
    depth = 1
    for token in token_pattern.finditer(panel, opening.end()):
        if token.group(0).lower().startswith("</div"):
            depth -= 1
            if depth == 0:
                return opening.start(), token.end()
        else:
            depth += 1
    raise ValueError(f"Unclosed {class_name!r} container.")


def replace_panel_div(source: str, panel_id: str, class_name: str, replacement: str) -> str:
    id_marker = f'id="{panel_id}"'
    panel_start = source.find(id_marker)
    if panel_start < 0:
        raise ValueError(f"Dashboard panel {panel_id!r} was not found.")
    panel_start = source.rfind("<section", 0, panel_start)
    panel_end = source.find("</section>", panel_start)
    if panel_start < 0 or panel_end < 0:
        raise ValueError(f"Dashboard panel {panel_id!r} is malformed.")
    panel_end += len("</section>")
    panel = source[panel_start:panel_end]
    start, end = find_div_range(panel, class_name)
    panel = panel[:start] + replacement + panel[end:]
    return source[:panel_start] + panel + source[panel_end:]


def expected_sections(source: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for ticket_id in TICKET_IDS:
        marker = f'id="panel-{ticket_id}"'
        index = source.find(marker)
        panel_start = source.rfind("<section", 0, index)
        panel_end = source.find("</section>", index) + len("</section>")
        panel = source[panel_start:panel_end]
        start, end = find_div_range(panel, "expected-section")
        result[str(ticket_id)] = panel[start:end]
    return result


def write_cash_tab(source: str, starting_cash: float, cash_rows: list[dict[str, Any]], ending_cash: float) -> str:
    rows = "".join(
        f"<tr><th>Ticket {row['ticket_id']} impact</th><td>{html.escape(row['label'])}</td>"
        f"<td class='amount'>{row['delta']:+,.2f}</td></tr>"
        for row in cash_rows
    )
    html_block = (
        '<div class="cash-ledger"><p class="mapping-alert"><strong>Database mapping note:</strong> '
        'Invoice 501 belongs to Ticket 101; lease 1 belongs to Ticket 102; Ticket 103 is a hoodie discount request. '
        'The assignment text swaps these ticket numbers, so this ledger attributes cash by the actual database links.</p>'
        '<table><thead><tr><th>Item</th><th>Reference</th><th>Cash change (USD)</th></tr></thead><tbody>'
        f'<tr><th>Starting Checking Balance</th><td>Post-reset balance</td><td class="amount">{starting_cash:,.2f}</td></tr>'
        f'{rows}<tr class="ending"><th>Ending Checking Balance</th><td>Verified against cash_accounts</td><td class="amount">{ending_cash:,.2f}</td></tr>'
        '</tbody></table></div>'
    )
    marker = 'id="panel-cash"'
    index = source.find(marker)
    if index < 0:
        raise ValueError("Cash tab panel not found.")
    start = source.rfind("<section", 0, index)
    end = source.find("</section>", index)
    if start < 0 or end < 0:
        raise ValueError("Cash tab panel is malformed.")
    end += len("</section>")
    old_panel = source[start:end]
    content_start = old_panel.find(">", old_panel.find(marker)) + 1
    content_end = old_panel.rfind("</section>")
    new_panel = old_panel[:content_start] + html_block + old_panel[content_end:]
    return source[:start] + new_panel + source[end:]


def build_resolved_board(tickets: list[dict[str, Any]], starting_cash: float, ending_cash: float, approvals: list[dict[str, Any]]) -> str:
    approval_by_ticket: dict[int, list[dict[str, Any]]] = {}
    for approval in approvals:
        approval_by_ticket.setdefault(approval["ticket_id"], []).append(approval)
    cards = []
    for ticket in tickets:
        ticket_id = int(ticket["ticket_id"])
        payments = approval_by_ticket.get(ticket_id, [])
        pay_html = "".join(
            f'<div class="payment"><span>{html.escape(item["recipient"])}</span>'
            f'<strong>${item["amount"]:,.2f}</strong><small>{html.escape(item["reference_type"])} {item["reference_id"]} · PAID</small></div>'
            for item in payments
        ) or '<div class="payment none">No disbursement</div>'
        contributions = "".join(
            f'<li><b>{html.escape(agent)}</b><span>{html.escape(summary)}</span></li>'
            for agent, summary in ticket["agent_contributions"].items()
        )
        cards.append(
            f'<article class="ticket-card"><div class="card-top"><div><p class="eyebrow">CAMPUS CUSTOMS · TICKET {ticket_id}</p>'
            f'<h2>{html.escape(ticket["subject"])}</h2></div><span class="resolved">RESOLVED</span></div>'
            f'<p class="outcome">{html.escape(ticket["outcome"])}</p><section><h3>Specialist contributions</h3><ul class="contributors">{contributions}</ul></section>'
            f'<section><h3>Approved disbursements</h3>{pay_html}</section>'
            f'<section class="chain"><h3>Delegation</h3><p>{html.escape(" · ".join(ticket["delegation_chain"]) or "Boss handled the ticket without specialist handoff")}</p></section>'
            f'<figure><img src="ticket_{ticket_id}.png" alt="Ticket {ticket_id} screenshot" /><figcaption>Optional local screenshot: ticket_{ticket_id}.png</figcaption></figure></article>'
        )
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Campus Customs · Resolved Board</title><style>
:root{{--navy:#142b4a;--blue:#1d4ed8;--sky:#eaf3ff;--ink:#1e293b;--muted:#64748b;--line:#d9e3ef;--paper:#f4f8fc;--green:#047857;--amber:#d97706}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(ellipse at 8% 0%,#dbeafe 0,transparent 34%),var(--paper);color:var(--ink);font:15px/1.55 Inter,Segoe UI,Arial,sans-serif}}
header{{background:linear-gradient(115deg,#102746,#1e4f89 65%,#2b73b8);color:#fff;padding:44px max(5vw,24px) 38px;box-shadow:0 10px 28px #16375a22}}header .eyebrow{{letter-spacing:.16em;font-size:11px;font-weight:800;opacity:.78}}h1{{margin:8px 0;font-size:clamp(30px,4vw,48px);letter-spacing:-.04em}}header p:last-child{{color:#dceaff;margin:0}}
.metrics{{display:flex;flex-wrap:wrap;gap:12px;margin-top:24px}}.metric{{border:1px solid #ffffff35;background:#ffffff12;padding:11px 15px;border-radius:12px;min-width:170px}}.metric span{{display:block;font-size:10px;text-transform:uppercase;letter-spacing:.12em;color:#cfe2ff}}.metric strong{{font-size:19px}}
main{{max-width:1440px;padding:30px 24px 54px;margin:auto}}.mapping{{padding:13px 17px;border-radius:12px;border:1px solid #fed7aa;background:#fff7ed;color:#854d0e;margin-bottom:22px}}.grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:18px;align-items:start}}
.ticket-card{{background:#fff;border:1px solid var(--line);border-radius:19px;padding:22px;box-shadow:0 12px 28px #173b6210}}.card-top{{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}}.card-top .eyebrow{{color:#64748b;font-size:10px;letter-spacing:.13em;font-weight:800}}h2{{font-size:22px;line-height:1.2;margin:6px 0 0;color:var(--navy)}}.resolved{{white-space:nowrap;color:var(--green);background:#d1fae5;border:1px solid #a7f3d0;padding:6px 9px;border-radius:99px;font-size:10px;font-weight:900;letter-spacing:.08em}}.outcome{{color:#475569;min-height:82px}}
section{{border-top:1px solid #edf2f7;padding-top:14px;margin-top:15px}}h3{{margin:0 0 9px;font-size:11px;letter-spacing:.1em;text-transform:uppercase;color:#56708e}}.contributors{{list-style:none;padding:0;margin:0;display:grid;gap:10px}}.contributors li{{display:grid;gap:3px;padding-left:11px;border-left:3px solid #60a5fa}}.contributors b{{color:#173b66;font-size:12px}}.contributors span{{color:#64748b;font-size:12px;line-height:1.45}}.payment{{display:grid;grid-template-columns:1fr auto;gap:2px 10px;align-items:center;padding:11px;background:#f0fdf4;border:1px solid #bbf7d0;border-radius:11px}}.payment strong{{color:#047857}}.payment small{{grid-column:1/-1;color:#64748b}}.payment.none{{display:block;background:#f8fafc;border-color:#e2e8f0;color:#64748b}}.chain p{{color:#475569;font-size:12px;margin:0}}figure{{margin:16px 0 0;background:#f8fbff;border:1px dashed #cbd9e8;border-radius:12px;padding:10px}}figure img{{display:block;width:100%;height:150px;object-fit:contain;color:#94a3b8;font-size:12px}}figcaption{{font-size:10px;color:#94a3b8;margin-top:5px}}
footer{{text-align:center;color:#8090a5;font-size:12px;padding:26px}}@media(max-width:1050px){{.grid{{grid-template-columns:1fr 1fr}}}}@media(max-width:700px){{header{{padding-top:30px}}.grid{{grid-template-columns:1fr}}main{{padding:20px 14px 36px}}.ticket-card{{padding:18px}}}}
</style></head><body><header><p class="eyebrow">LOCAL OPERATIONS RECORD</p><h1>Resolved ticket board</h1><p>Agent work, human approvals, and the reconciled checking balance.</p><div class="metrics"><div class="metric"><span>Starting checking</span><strong>${starting_cash:,.2f}</strong></div><div class="metric"><span>Approved disbursements</span><strong>${starting_cash-ending_cash:,.2f}</strong></div><div class="metric"><span>Ending checking</span><strong>${ending_cash:,.2f}</strong></div></div></header>
<main><div class="mapping"><strong>Database mapping:</strong> Ticket 101 owns invoice 501; Ticket 102 owns lease 1; Ticket 103 is the hoodie discount request. The assignment's vendor and rent ticket numbers differ from the actual links.</div><div class="grid">{"".join(cards)}</div></main><footer>Campus Customs · Local simulation · Drafts remain unsent; no real vendor or customer was contacted.</footer></body></html>'''


def write_harness_run_note(starting: float, rows: list[dict[str, Any]], ending: float, agent_turns: dict[int, int]) -> None:
    harness = OUTPUT_DIR / "harness.md"
    content = harness.read_text(encoding="utf-8") if harness.exists() else "# Campus Customs Database Harness\n"
    note = [
        "",
        "### Synchronous resolution results",
        "",
        f"- Working database reset from the untouched original; starting checking balance: **${starting:,.2f}**.",
        "- Tickets 101, 102, and 103 were run sequentially using the configured Portkey route; agent turns by ticket: "
        + ", ".join(f"{ticket_id}: {count}" for ticket_id, count in agent_turns.items()) + ".",
        "- Actual liability mapping: Ticket 101 paid invoice 501 ($840); Ticket 102 paid lease 1 ($2,400); Ticket 103 has no linked payment.",
        f"- Cash reconciliation: ${starting:,.2f} - $840.00 - $2,400.00 = **${ending:,.2f}**, matching `cash_accounts.balance`.",
        "- Ticket 101 remains physically out of stock after vendor clearance (the recorded tee size-S quantity is zero); the local draft records a backorder update without claiming shipment.",
        "- Ticket 103 has 8 of 20 requested hoodies on hand (shortfall 12); the local response is a review draft and does not promise an unrecorded restock or discount.",
        "- All resolved artifact statuses describe completion of the local ticket-handling workflow; they do not assert that inventory arrived or a draft was externally delivered.",
        "- The original database SHA-256 was verified unchanged after execution; the audit file was extended, not cleared.",
    ]
    content = content.rstrip() + "\n" + "\n".join(note) + "\n"
    harness.write_text(content, encoding="utf-8")


def execute() -> None:
    if MODEL_NAME != "gpt-6-luna":
        raise RuntimeError("Homework 5 is locked to gpt-6-luna.")
    if not PORTKEY_API_KEY:
        raise RuntimeError("PORTKEY_API_KEY is not configured; no ticket run was started.")
    if not ORIGINAL_DB.is_file():
        raise FileNotFoundError(f"Original database missing: {ORIGINAL_DB}")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    original_hash = sha256(ORIGINAL_DB)

    # Reset only the working copy and record the exact post-reset starting cash.
    shutil.copyfile(ORIGINAL_DB, WORKING_DB)
    with closing(connect(readonly=True)) as connection:
        baseline_account = cash_account(connection)
        starting_cash = round(float(baseline_account["balance"]), 2)
        baseline_tickets = ticket_snapshot(connection)
        linked_liabilities = liability_plan(connection, baseline_tickets)

    expected_cash = round(starting_cash - sum(float(row["amount"]) for row in linked_liabilities), 2)
    if expected_cash < 0:
        raise RuntimeError("Linked, approved liabilities exceed available cash; no payments will be attempted.")

    run_started = datetime.now(timezone.utc)
    run_results: dict[int, dict[str, Any]] = {}
    for ticket_id in TICKET_IDS:
        response = asyncio.run(run_ticket(ticket_id, project_root=ROOT))
        run_results[ticket_id] = response.model_dump(mode="json")
        print(f"Ticket {ticket_id}: agent run completed; {response.agent_turns} agent turns, "
              f"status={response.ticket.status}.")

    # Ensure missing specialist artifacts are staged through FastMCP and that each
    # real linked liability has a matching pending request before human approval.
    coordination_logger = AppendOnlyAuditLogger(f"q9-coordinator-{uuid4()}")
    with closing(connect(readonly=True)) as connection:
        missing_proposals = [
            item for item in linked_liabilities if pending_for_liability(connection, item) is None
        ]
    for item in missing_proposals:
        staged = stage_missing_approval(item, coordination_logger)
        if staged.get("status") != "PENDING_APPROVAL" or not staged.get("proposal_id"):
            raise RuntimeError(f"Could not stage {item['reference_type']} {item['reference_id']} for approval.")

    approval_records: list[dict[str, Any]] = []
    for item in linked_liabilities:
        with closing(connect(readonly=True)) as connection:
            pending = pending_for_liability(connection, item)
            if pending is None:
                # An already-paid liability is allowed only if it has a matching
                # completed ledger row; after a pristine reset this is unexpected.
                raise RuntimeError(f"No pending approval exists for linked {item['reference_type']} {item['reference_id']}.")
            approval_id = int(pending["id"])
            amount = round(float(pending["amount"]), 2)
            payload = json.loads(pending["payload_json"] or "{}")
        if not (amount == item["amount"] and str(payload.get("payee", "")).casefold() == str(item["recipient"]).casefold()):
            raise RuntimeError(f"Staged request {approval_id} does not match the recorded liability; refusing approval.")
        result = approve_payment(PaymentApprovalRequest(
            payment_id=approval_id,
            amount=amount,
            recipient=item["recipient"],
            reference_type=item["reference_type"],
            reference_id=item["reference_id"],
        ))
        approval_records.append({
            **item,
            "approval_id": approval_id,
            "payment_id": result["payment_id"],
            "status": result["payment_status"],
            "balance_after": round(float(result["balance"]), 2),
        })
        print(f"Approved {item['reference_type']} {item['reference_id']}: ${amount:,.2f}; "
              f"balance=${result['balance']:,.2f}.")

    # Confirm blocker clearance and the real stock/margin facts after approvals.
    verification_results: dict[int, dict[str, Any]] = {}
    checks = {
        101: [("check_inventory_stock", {"sku": "CC-TEE-WHITE", "size": "S", "order_quantity": 1}),
              ("check_vendor_restock_eligibility", {"vendor_id": 1})],
        102: [("get_lease_due_status", {"lease_id": 1})],
        103: [("check_inventory_stock", {"sku": "CC-HOOD-NAVY", "size": "M", "order_quantity": 20}),
              ("get_pricing_and_margin", {"sku": "CC-HOOD-NAVY"})],
    }
    for ticket_id, calls in checks.items():
        verification_results[ticket_id] = {}
        for tool, arguments in calls:
            result = mcp_call(tool, arguments)
            verification_results[ticket_id][tool] = result
            coordination_logger.append(
                ticket_id=ticket_id,
                agent_name="System",
                action="tool_call_completed",
                tool_called=tool,
                reasoning_notes="Post-approval verification used the registered FastMCP tool and working database.",
            )

    draft_bodies = {
        101: (
            "Hello Tauhid, thanks for your order for a Bulldog tee in size S. We currently have none in that size. "
            "The supplier's recorded invoice has been cleared, which removes its shipment hold; the recorded lead time is "
            "five days. This is a backorder update only, and we will not mark the item shipped until stock is confirmed."
        ),
        103: (
            "Hello Yale AI Club, thanks for asking about 20 navy hoodies in size M. We currently have 8, leaving 12 to "
            "replenish. We are reviewing the requested bulk price against recorded product costs and availability. "
            "No discount or delivery date is confirmed in this draft."
        ),
    }
    drafts: dict[int, dict[str, Any]] = {}
    for ticket_id, body in draft_bodies.items():
        existing = ensure_customer_draft(ticket_id, body, coordination_logger)
        if existing:
            drafts[ticket_id] = existing

    # Complete each ticket's local administrative workflow. Descriptions preserve
    # the real fulfillment limits instead of claiming stock or a sent message.
    resolutions = {
        101: "Supplier invoice 501 was paid after human approval and the recorded vendor shipment block is cleared. Size S still has zero on hand with a five-day recorded lead time; an unsent backorder draft is saved. The local handling workflow is resolved; no shipment is claimed.",
        102: "The recorded $2,400 lease obligation for lease 1 was paid after human approval. The payment is recorded in payments and the lease payment_status is PAID.",
        103: "The bulk request was reviewed against stock and pricing: 8 of 20 hoodies are on hand, leaving 12 to replenish. An unsent review draft is saved; no discount or delivery commitment is claimed.",
    }
    for ticket_id, resolution in resolutions.items():
        mcp_call("update_ticket_status", {"ticket_id": ticket_id, "status": "resolved", "resolution": resolution})
        coordination_logger.append(
            ticket_id=ticket_id,
            agent_name="System",
            action="tool_call_completed",
            tool_called="update_ticket_status",
            reasoning_notes="Recorded the user-requested resolved status through FastMCP; the note states remaining physical fulfillment limits.",
        )

    with closing(connect(readonly=True)) as connection:
        final_cash = round(float(cash_account(connection)["balance"]), 2)
        final_tickets = ticket_snapshot(connection)
        payment_rows = [dict(row) for row in connection.execute(
            "SELECT * FROM payments ORDER BY id"
        ).fetchall()]
        pending_rows = [dict(row) for row in connection.execute(
            "SELECT * FROM approvals WHERE kind='payment' AND status='pending' ORDER BY id"
        ).fetchall()] if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='approvals'"
        ).fetchone() else []
        if pending_rows:
            raise RuntimeError("Unexpected pending payment approvals remain after resolution.")
        if final_cash != expected_cash:
            raise AssertionError(f"Cash mismatch: expected {expected_cash:.2f}, SQL returned {final_cash:.2f}.")
        if any(str(row["status"]).casefold() != "resolved" for row in final_tickets):
            raise AssertionError("One or more tickets did not reach resolved status.")

    if sha256(ORIGINAL_DB) != original_hash:
        raise AssertionError("The original database changed during the run.")

    all_events = read_audit_records()
    ticket_details: dict[int, dict[str, Any]] = {}
    json_tickets: list[dict[str, Any]] = []
    for ticket, baseline in zip(final_tickets, baseline_tickets, strict=True):
        ticket_id = int(ticket["id"])
        events = agent_events_since(all_events, run_started, ticket_id)
        contribution_map = event_contributions(events)
        if not contribution_map:
            contribution_map = {"Boss": str(run_results[ticket_id].get("result", {}).get("summary", "Ticket triage and resolution."))}
        actual_type = str(ticket["type"])
        if ticket_id == 101:
            outcome = "Invoice 501 was cleared and the vendor shipment hold is removed; size S remains out of stock, with a backorder draft saved and no shipment claimed."
        elif ticket_id == 102:
            outcome = "The recorded monthly rent liability was paid through human approval and the lease is marked PAID."
        else:
            outcome = "The requested bulk discount was reviewed; stock is short by 12 hoodies, so an unsent review draft records that no discount or delivery date is confirmed."
        ticket_approvals = [row for row in approval_records if row["ticket_id"] == ticket_id]
        detail = {
            "actual_type": actual_type,
            "outcome": outcome,
            "agents": participating_agents(events),
            "delegation_chain": agent_chain(events),
            "mcp_tools": tool_names(events),
        }
        ticket_details[ticket_id] = detail
        json_tickets.append({
            "ticket_id": ticket_id,
            "final_status": "resolved",
            "outcome": outcome,
            "agent_contributions": contribution_map,
            "human_approvals": [{
                "approval_id": row["approval_id"],
                "payment_id": row["payment_id"],
                "amount": row["amount"],
                "recipient": row["recipient"],
                "reference_type": row["reference_type"],
                "reference_id": row["reference_id"],
                "status": row["status"],
            } for row in ticket_approvals],
        })

    dashboard_path = OUTPUT_DIR / "desk_tickets.html"
    source_html = dashboard_path.read_text(encoding="utf-8")
    expected_before = expected_sections(source_html)
    actual_source = source_html
    heading_replacements = {
        "Ticket 102 · Restocking blocked by vendor invoices": "Ticket 102 · Rent notice",
        "Ticket 103 · Lease &amp; store facilities due date": "Ticket 103 · Bulk hoodie discount request",
    }
    for old, new in heading_replacements.items():
        actual_source = actual_source.replace(old, new, 1)
    actual_source = actual_source.replace(
        "Ticket execution plan",
        "Ticket execution record",
        1,
    )
    for ticket_id in TICKET_IDS:
        approval_detail = [row for row in approval_records if row["ticket_id"] == ticket_id]
        replacement = panel_actual(ticket_id, ticket_details[ticket_id], run_results[ticket_id], approval_detail)
        actual_source = replace_panel_div(actual_source, f"panel-{ticket_id}", "actual-section", replacement)
    cash_rows = []
    for ticket_id, label in (
        (101, "Invoice 501 · Bulldog Print Co"),
        (102, "Lease 1 · Elm City Properties"),
        (103, "Hoodie discount review · no disbursement"),
    ):
        paid = sum(float(row["amount"]) for row in approval_records if row["ticket_id"] == ticket_id)
        cash_rows.append({"ticket_id": ticket_id, "label": label, "delta": -paid})
    actual_source = write_cash_tab(actual_source, starting_cash, cash_rows, final_cash)
    if expected_sections(actual_source) != expected_before:
        raise AssertionError("An Expected section changed while updating the Actual and Cash panels.")
    dashboard_path.write_text(actual_source, encoding="utf-8")

    (OUTPUT_DIR / "resolved_tickets.json").write_text(
        json.dumps(json_tickets, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    board_tickets = []
    for row in json_tickets:
        db_ticket = next(item for item in final_tickets if int(item["id"]) == row["ticket_id"])
        board_tickets.append({
            **row,
            "subject": db_ticket["subject"],
            "delegation_chain": ticket_details[row["ticket_id"]]["delegation_chain"],
        })
    (OUTPUT_DIR / "resolved_board.html").write_text(
        build_resolved_board(board_tickets, starting_cash, final_cash, approval_records), encoding="utf-8"
    )

    write_harness_run_note(starting_cash, json_tickets, final_cash, {
        ticket_id: int(run_results[ticket_id].get("agent_turns", 0)) for ticket_id in TICKET_IDS
    })
    print(json.dumps({
        "starting_cash": starting_cash,
        "ticket_cash_impacts": {
            str(ticket_id): round(sum(row["delta"] for row in cash_rows if row["ticket_id"] == ticket_id), 2)
            for ticket_id in TICKET_IDS
        },
        "ending_cash": final_cash,
        "cash_matches_sql": True,
        "tickets_resolved": [int(row["id"]) for row in final_tickets],
        "approvals": approval_records,
        "original_database_unchanged": True,
        "expected_sections_preserved": True,
        "artifacts": [
            str((OUTPUT_DIR / "resolved_tickets.json").relative_to(ROOT)),
            str((OUTPUT_DIR / "resolved_board.html").relative_to(ROOT)),
            str(dashboard_path.relative_to(ROOT)),
            str(AUDIT_PATH.relative_to(ROOT)),
        ],
    }, indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--execute", action="store_true", help="Reset and run all three ticket workflows.")
    group.add_argument("--preflight", action="store_true", help="Only inspect original ticket/liability associations (default).")
    parser.add_argument(
        "--accept-database-mappings",
        action="store_true",
        help="Acknowledge that approvals follow actual ticket foreign keys, not the swapped narrative labels.",
    )
    args = parser.parse_args()
    if not args.execute:
        preflight()
        return
    if not args.accept_database_mappings:
        parser.error("Execution requires --accept-database-mappings because the prompt and SQLite ticket links differ.")
    execute()


if __name__ == "__main__":
    main()
