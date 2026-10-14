"""Acceptance checks use the working database and isolated pristine copies, without a transport."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from mcp.server.fastmcp.exceptions import ToolError

from mcp_server import server


class DatabaseGuard:
    """Check that every acceptance case preserves both database files."""

    def setUp(self) -> None:
        self.paths = [server.PROJECT_ROOT / "data" / name for name in
                      ("campus_customs.db", "campus_customs_new.db")]
        self.before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.paths}
        self.fixture_dir = tempfile.TemporaryDirectory(prefix="hw5-mcp-tests-")
        self.addCleanup(self.fixture_dir.cleanup)
        self.pristine_fixture_path = Path(self.fixture_dir.name) / "campus_customs_new.db"
        shutil.copyfile(self.paths[0], self.pristine_fixture_path)
        self.addCleanup(self.assert_databases_unchanged)

    def assert_databases_unchanged(self) -> None:
        for path in self.paths:
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), self.before[path])


class ShopToolTests(DatabaseGuard, unittest.TestCase):
    def test_ticket_101_shirt_is_out_of_stock(self) -> None:
        result = server.check_inventory_stock("CC-TEE-WHITE", "S", 1)
        self.assertEqual((result.on_hand, result.shortfall, result.can_fulfill), (0, 1, False))
        self.assertEqual(result.product_name, "Classic Bulldog Tee")

    def test_ticket_103_hoodie_order_has_twelve_unit_shortfall(self) -> None:
        result = server.check_inventory_stock("CC-HOOD-NAVY", "M", 20)
        self.assertEqual((result.on_hand, result.shortfall, result.can_fulfill), (8, 12, False))

    def test_available_stock_and_identifier_normalization(self) -> None:
        result = server.check_inventory_stock(" cc-hood-navy ", " m ", 8)
        self.assertEqual((result.sku, result.size, result.shortfall, result.can_fulfill),
                         ("CC-HOOD-NAVY", "M", 0, True))

    def test_unknown_stock_and_sql_text_are_not_zero_stock_or_matches(self) -> None:
        for sku, size in [("UNKNOWN-SKU", "M"), ("CC-HOOD-NAVY", "UNKNOWN"),
                          ("' OR 1=1 --", "M")]:
            with self.subTest(sku=sku, size=size), self.assertRaisesRegex(ValueError, "No inventory record"):
                server.check_inventory_stock(sku, size, 1)

    def test_invalid_quantities_are_rejected_in_direct_calls(self) -> None:
        for quantity in [0, -1, 1.5, True, "1"]:
            with self.subTest(quantity=quantity), self.assertRaises(ValueError):
                server.check_inventory_stock("CC-HOOD-NAVY", "M", quantity)
        for sku, size in [(" ", "M"), ("CC-HOOD-NAVY", " ")]:
            with self.subTest(sku=sku, size=size), self.assertRaises(ValueError):
                server.check_inventory_stock(sku, size, 1)

    def test_unpaid_invoice_blocks_vendor_with_actual_lead_time(self) -> None:
        with patch.object(server, "WORKING_DB_PATH", self.pristine_fixture_path):
            result = server.check_vendor_restock_eligibility(1)
        self.assertTrue(result.blocked_by_unpaid_invoices)
        self.assertEqual((result.vendor_name, result.lead_days, result.total_unpaid),
                         ("Bulldog Print Co", 5, 840.0))
        self.assertEqual(result.shop_date, date(2026, 8, 31))
        self.assertEqual(len(result.unpaid_invoices), 1)
        invoice = result.unpaid_invoices[0]
        self.assertEqual((invoice.invoice_id, invoice.overdue, invoice.days_overdue), (501, True, 3))

    def test_other_real_vendors_have_no_invoice_block(self) -> None:
        for vendor_id, lead_days in [(2, 3), (3, 1)]:
            with self.subTest(vendor_id=vendor_id):
                result = server.check_vendor_restock_eligibility(vendor_id)
                self.assertFalse(result.blocked_by_unpaid_invoices)
                self.assertEqual(result.unpaid_invoices, [])
                self.assertEqual((result.total_unpaid, result.lead_days), (0, lead_days))

    def test_ticket_102_lease_is_due_in_two_shop_days(self) -> None:
        result = server.get_lease_due_status(1)
        self.assertEqual((result.space_name, result.landlord), ("Chapel Street shop", "Elm City Properties"))
        self.assertEqual((result.monthly_rent, result.next_due), (2400, date(2026, 9, 2)))
        self.assertEqual((result.shop_date, result.days_until_due, result.status),
                         (date(2026, 8, 31), 2, "upcoming"))

    def test_unknown_and_invalid_vendor_and_lease_ids_are_errors(self) -> None:
        for tool in [server.check_vendor_restock_eligibility, server.get_lease_due_status]:
            for record_id in [999999, 0, -1, 1.5, True, "1"]:
                with self.subTest(tool=tool.__name__, record_id=record_id), self.assertRaises(ValueError):
                    tool(record_id)

    def test_database_path_does_not_depend_on_cwd_or_environment(self) -> None:
        previous = Path.cwd()
        try:
            os.chdir(server.PROJECT_ROOT.parent)
            with patch.dict(os.environ, {"CAMPUS_CUSTOMS_DB": "nonexistent.db"}):
                self.assertEqual(server.WORKING_DB_PATH, server.PROJECT_ROOT / "data/campus_customs_new.db")
                self.assertEqual(server.check_inventory_stock("CC-TEE-WHITE", "S", 1).shortfall, 1)
        finally:
            os.chdir(previous)

    def test_connection_rejects_a_zero_row_update(self) -> None:
        with server._connection() as conn:
            self.assertEqual(conn.execute("PRAGMA query_only").fetchone()[0], 1)
            # Even if read-only enforcement regressed, this probe changes no rows.
            try:
                with self.assertRaisesRegex(sqlite3.OperationalError, "readonly"):
                    conn.execute("UPDATE inventory SET qty = qty WHERE 0")
            finally:
                conn.rollback()

    def test_missing_working_file_is_not_created(self) -> None:
        missing = server.PROJECT_ROOT / "data" / "__q3_missing_working_copy__.db"
        self.assertFalse(missing.exists())
        with patch.object(server, "WORKING_DB_PATH", missing), self.assertRaises(FileNotFoundError):
            server.check_inventory_stock("CC-TEE-WHITE", "S", 1)
        self.assertFalse(missing.exists())


class McpContractTests(DatabaseGuard, unittest.IsolatedAsyncioTestCase):
    async def test_complete_typed_tool_registry_is_registered(self) -> None:
        tools = await server.mcp.list_tools()
        expected_names = {
            "check_inventory_stock", "check_vendor_restock_eligibility", "get_lease_due_status",
            "get_ticket_details", "update_ticket_status", "get_cash_balance", "propose_payment",
            "propose_purchase_order", "find_restock_vendors",
            "save_customer_draft", "get_pricing_and_margin",
        }
        self.assertEqual({tool.name for tool in tools}, expected_names)
        self.assertEqual(len(tools), len(expected_names))
        expected_parameters = {
            "check_inventory_stock": {"sku", "size", "order_quantity"},
            "check_vendor_restock_eligibility": {"vendor_id"},
            "get_lease_due_status": {"lease_id"},
            "get_ticket_details": {"ticket_id"},
            "update_ticket_status": {"ticket_id", "status", "resolution"},
            "get_cash_balance": set(),
            "propose_payment": {"amount", "recipient", "reason"},
            "propose_purchase_order": {"ticket_id", "sku", "size", "quantity", "vendor_id", "reason"},
            "save_customer_draft": {"ticket_id", "draft_body"},
            "get_pricing_and_margin": {"sku"},
            "find_restock_vendors": {"sku"},
        }
        read_only_tools = {
            "check_inventory_stock", "check_vendor_restock_eligibility", "get_lease_due_status",
            "get_ticket_details", "get_cash_balance", "get_pricing_and_margin", "find_restock_vendors",
        }
        for tool in tools:
            self.assertEqual(set(tool.inputSchema.get("required", [])), expected_parameters[tool.name])
            self.assertTrue(tool.outputSchema["properties"])
            self.assertIn("Returns:", tool.description)
            self.assertEqual(tool.annotations.readOnlyHint, tool.name in read_only_tools)
            self.assertFalse(tool.annotations.destructiveHint)
            self.assertFalse(tool.annotations.openWorldHint)

    async def test_registered_calls_return_valid_structured_results(self) -> None:
        cases = [
            ("check_inventory_stock", {"sku": "CC-TEE-WHITE", "size": "S", "order_quantity": 1},
             server.InventoryStockResult, "shortfall", 1),
            ("get_ticket_details", {"ticket_id": 101},
             server.TicketDetailsResult, "invoice_vendor_id", 1),
            ("check_vendor_restock_eligibility", {"vendor_id": 1},
             server.VendorRestockResult, "blocked_by_unpaid_invoices", True),
            ("get_lease_due_status", {"lease_id": 1}, server.LeaseDueResult, "days_until_due", 2),
        ]
        with patch.object(server, "WORKING_DB_PATH", self.pristine_fixture_path):
            for name, arguments, result_type, key, expected in cases:
                with self.subTest(tool=name):
                    content, structured = await server.mcp.call_tool(name, arguments)
                    self.assertEqual(structured[key], expected)
                    self.assertEqual(json.loads(content[0].text), structured)
                    self.assertEqual(result_type.model_validate(structured).model_dump(mode="json"), structured)

            ticket = server.get_ticket_details(101)
            self.assertEqual((ticket.invoice_id, ticket.invoice_vendor_id, ticket.invoice_vendor_name),
                             (501, 1, "Bulldog Print Co"))

    async def test_mcp_validation_rejects_coercion_and_missing_records(self) -> None:
        for quantity in [0, -1, 1.5, True, "1"]:
            with self.subTest(quantity=quantity), self.assertRaises(ToolError):
                await server.mcp.call_tool("check_inventory_stock", {
                    "sku": "CC-HOOD-NAVY", "size": "M", "order_quantity": quantity
                })
        for name, arguments in [
            ("check_vendor_restock_eligibility", {"vendor_id": 999999}),
            ("get_lease_due_status", {"lease_id": 999999}),
            ("get_lease_due_status", {}),
        ]:
            with self.subTest(tool=name, arguments=arguments), self.assertRaises(ToolError):
                await server.mcp.call_tool(name, arguments)
