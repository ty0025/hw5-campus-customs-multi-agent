"""Verify the real .mcp.json stdio registration against the working database."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from jsonschema import validate
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


ROOT = Path(__file__).resolve().parents[1]
DATABASE = ROOT / "data" / "campus_customs_new.db"
REPORT = ROOT / "output" / "mcp_tool_test_results.json"
EXPECTED_TOOLS = {
    "check_inventory_stock", "check_vendor_restock_eligibility", "get_lease_due_status",
    "get_ticket_details", "update_ticket_status", "get_cash_balance", "propose_payment",
    "save_customer_draft", "get_pricing_and_margin",
}
READ_ONLY_TOOLS = {
    "check_inventory_stock", "check_vendor_restock_eligibility", "get_lease_due_status",
    "get_ticket_details", "get_cash_balance", "get_pricing_and_margin",
}
SMOKE_REPORT = ROOT / "output" / "mcp_smoke.json"


def database_hashes() -> dict[str, str]:
    return {
        name: hashlib.sha256((ROOT / "data" / name).read_bytes()).hexdigest()
        for name in ["campus_customs.db", "campus_customs_new.db"]
    }


def database_cases() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Compute expected outputs from SQL without importing any server functions."""
    cases = []
    with closing(sqlite3.connect(DATABASE.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        stock_requests = conn.execute(
            "SELECT t.id, t.sku, t.size, t.qty FROM tickets t "
            "WHERE t.sku IS NOT NULL ORDER BY t.id"
        ).fetchall()
        for ticket in stock_requests:
            arguments = {"sku": ticket["sku"], "size": ticket["size"], "order_quantity": ticket["qty"]}
            expected = dict(conn.execute(
                "SELECT sku, size, name AS product_name, location, ? AS order_quantity, "
                "qty AS on_hand, MAX(? - qty, 0) AS shortfall, qty >= ? AS can_fulfill "
                "FROM inventory WHERE sku = ? AND size = ?",
                (ticket["qty"], ticket["qty"], ticket["qty"], ticket["sku"], ticket["size"]),
            ).fetchone())
            expected["can_fulfill"] = bool(expected["can_fulfill"])
            cases.append({"name": f"ticket_{ticket['id']}_inventory", "tool": "check_inventory_stock",
                          "arguments": arguments, "expected": expected})

        ticket_details = conn.execute(
            "SELECT t.id, t.requester, t.type, t.subject, t.notes, t.sku, t.size, t.qty, "
            "t.lease_id, t.invoice_id, i.vendor_id AS invoice_vendor_id, v.name AS invoice_vendor_name, "
            "t.status, t.created_at, d.date_today AS shop_date "
            "FROM tickets t CROSS JOIN desk d LEFT JOIN invoices i ON i.id = t.invoice_id "
            "LEFT JOIN vendors v ON v.id = i.vendor_id ORDER BY t.id"
        ).fetchall()
        for row in ticket_details:
            expected = {
                "ticket_id": row["id"], "customer_id": row["requester"], "type": row["type"],
                "subject": row["subject"], "description": row["notes"], "sku": row["sku"],
                "size": row["size"], "quantity": row["qty"], "lease_id": row["lease_id"],
                "invoice_id": row["invoice_id"], "invoice_vendor_id": row["invoice_vendor_id"],
                "invoice_vendor_name": row["invoice_vendor_name"], "status": row["status"],
                "created_at": row["created_at"], "shop_date": row["shop_date"],
            }
            cases.append({"name": f"ticket_{row['id']}_details", "tool": "get_ticket_details",
                          "arguments": {"ticket_id": row["id"]}, "expected": expected})

        for vendor in conn.execute("SELECT * FROM vendors ORDER BY id").fetchall():
            expected = dict(conn.execute(
                "SELECT v.id AS vendor_id, v.name AS vendor_name, v.specialty, v.lead_days, "
                "d.date_today AS shop_date FROM vendors v CROSS JOIN desk d WHERE v.id = ?",
                (vendor["id"],),
            ).fetchone())
            invoices = [dict(row) for row in conn.execute(
                "SELECT i.id AS invoice_id, i.amount, i.due_date, i.status, i.description, "
                "i.due_date < d.date_today AS overdue, "
                "MAX(CAST(julianday(d.date_today) - julianday(i.due_date) AS INTEGER), 0) AS days_overdue "
                "FROM invoices i CROSS JOIN desk d WHERE i.vendor_id = ? "
                "AND LOWER(TRIM(i.status)) IN ('open','unpaid','overdue','pending') ORDER BY i.due_date, i.id",
                (vendor["id"],),
            )]
            for invoice in invoices:
                invoice["overdue"] = bool(invoice["overdue"])
            expected.update(unpaid_invoices=invoices,
                            total_unpaid=round(sum(row["amount"] for row in invoices), 2),
                            blocked_by_unpaid_invoices=bool(invoices))
            cases.append({"name": f"vendor_{vendor['id']}_clearance", "tool": "check_vendor_restock_eligibility",
                          "arguments": {"vendor_id": vendor["id"]}, "expected": expected})

        for row in conn.execute(
            "SELECT l.id AS lease_id, l.space_name, l.landlord, l.monthly_rent, l.next_due, "
            "d.date_today AS shop_date, CAST(julianday(l.next_due) - julianday(d.date_today) AS INTEGER) "
            "AS days_until_due, CASE WHEN l.next_due < d.date_today THEN 'overdue' "
            "WHEN l.next_due = d.date_today THEN 'due_today' ELSE 'upcoming' END AS status, l.notes "
            "FROM leases l CROSS JOIN desk d ORDER BY l.id"
        ):
            expected = dict(row)
            cases.append({"name": f"lease_{row['lease_id']}_due", "tool": "get_lease_due_status",
                          "arguments": {"lease_id": row["lease_id"]}, "expected": expected})

        cash = conn.execute("SELECT name, balance, date FROM cash_accounts ORDER BY name LIMIT 1").fetchone()
        cases.append({"name": "cash_balance_checking", "tool": "get_cash_balance",
                      "arguments": {"account_name": "Operating Cash"},
                      "expected": {"account_name": cash["name"], "balance": cash["balance"],
                                   "as_of_date": cash["date"]}})

        for row in conn.execute(
            "SELECT DISTINCT p.sku, p.unit_cost, p.list_price FROM pricing p "
            "JOIN tickets t ON t.sku = p.sku ORDER BY p.sku"
        ):
            margin = round(float(row["list_price"]) - float(row["unit_cost"]), 2)
            expected = {
                "sku": row["sku"], "unit_cost": row["unit_cost"], "list_price": row["list_price"],
                "gross_margin_per_unit": margin,
                "gross_margin_percent": round(margin / float(row["list_price"]) * 100, 2),
            }
            cases.append({"name": f"pricing_{row['sku']}", "tool": "get_pricing_and_margin",
                          "arguments": {"sku": row["sku"]}, "expected": expected})

        missing_vendor = conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM vendors").fetchone()[0]
        missing_lease = conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM leases").fetchone()[0]
        ticket = stock_requests[0]
        error_cases = [
            {"name": "reject_zero_order_quantity", "tool": "check_inventory_stock",
             "arguments": {"sku": ticket["sku"], "size": ticket["size"], "order_quantity": 0}},
            {"name": "reject_numeric_string", "tool": "check_inventory_stock",
             "arguments": {"sku": ticket["sku"], "size": ticket["size"], "order_quantity": "1"}},
            {"name": "reject_unknown_vendor", "tool": "check_vendor_restock_eligibility",
             "arguments": {"vendor_id": missing_vendor}},
            {"name": "reject_unknown_lease", "tool": "get_lease_due_status",
             "arguments": {"lease_id": missing_lease}},
        ]
    return cases, error_cases


async def verify_protocol() -> dict[str, Any]:
    configuration = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
    registration = configuration["mcpServers"]["campus_customs_mcp"]
    assert registration == {
        "command": "python", "args": ["-m", "mcp_server.server"], "env": {"PYTHONPATH": "."}
    }, "The MCP registration differs from the supplied configuration."
    parameters = StdioServerParameters(**registration, cwd=ROOT)
    before = database_hashes()
    cases, error_cases = database_cases()
    result: dict[str, Any] = {
        "checked_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
        "server_registration": "campus_customs_mcp",
        "configuration": registration,
        "configured_python_resolution": shutil.which(registration["command"]),
        "working_directory": str(ROOT),
        "transport": "stdio",
        "verification_method": "Independent read-only SQL compared with real MCP subprocess responses",
        "cases": [],
        "error_cases": [],
    }
    try:
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as server_stderr:
            async with stdio_client(parameters, errlog=server_stderr) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=20)) as session:
                    initialized = await session.initialize()
                    result["protocol_version"] = initialized.protocolVersion
                    result["server_info"] = initialized.serverInfo.model_dump(mode="json")
                    listed = await session.list_tools()
                    tools = {tool.name: tool for tool in listed.tools}
                    assert len(listed.tools) == 9 and set(tools) == EXPECTED_TOOLS
                    result["registered_tools"] = sorted(tools)
                    for tool in tools.values():
                        if tool.name in READ_ONLY_TOOLS:
                            assert tool.annotations and tool.annotations.readOnlyHint
                        assert tool.outputSchema is not None
                    for case in cases:
                        response = await session.call_tool(case["tool"], case["arguments"])
                        assert not response.isError, f"{case['name']} returned an MCP error."
                        actual = response.structuredContent
                        assert actual is not None, f"{case['name']} is missing structured output."
                        validate(instance=actual, schema=tools[case["tool"]].outputSchema)
                        text_results = [json.loads(block.text) for block in response.content if block.type == "text"]
                        assert text_results == [actual], f"{case['name']} has inconsistent text and JSON output."
                        assert actual == case["expected"], f"{case['name']} differs from the database."
                        result["cases"].append({**case, "actual": actual, "matches_database": True})
                    for case in error_cases:
                        response = await session.call_tool(case["tool"], case["arguments"])
                        assert response.isError, f"{case['name']} was incorrectly accepted."
                        result["error_cases"].append({**case, "is_error": True})
    finally:
        after = database_hashes()
        assert before == after, "A database changed during MCP protocol verification."
    result.update(status="PASS", database_hashes_before=before, database_hashes_after=after,
                  databases_unchanged=True, session_closed=True)
    return result


async def bounded_verification() -> dict[str, Any]:
    return await asyncio.wait_for(verify_protocol(), timeout=60)


def main() -> None:
    result = asyncio.run(bounded_verification())
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    smoke_names = {
        "ticket_101_details", "ticket_102_details", "ticket_103_details",
        "ticket_101_inventory", "ticket_103_inventory", "vendor_1_clearance",
        "lease_1_due", "cash_balance_checking", "pricing_CC-TEE-WHITE",
        "pricing_CC-HOOD-NAVY",
    }
    smoke_cases = [case for case in result["cases"] if case["name"] in smoke_names]
    if {case["name"] for case in smoke_cases} != smoke_names:
        raise AssertionError("The live MCP smoke snapshot is missing one or more expected shop queries.")
    smoke = {
        "status": result["status"], "checked_at": result["checked_at"],
        "server_registration": result["server_registration"],
        "database": "data/campus_customs_new.db", "transport": result["transport"],
        "registered_tools": result["registered_tools"], "live_queries": smoke_cases,
        "databases_unchanged": result["databases_unchanged"],
    }
    SMOKE_REPORT.write_text(json.dumps(smoke, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"PASS: {len(result['registered_tools'])} MCP tools discovered through the configured stdio command.")
    print(f"PASS: {len(result['cases'])} complete outputs match SQL; {len(result['error_cases'])} invalid calls rejected.")
    print("PASS: both database hashes unchanged; MCP session closed.")
    print(f"Evidence: {REPORT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
