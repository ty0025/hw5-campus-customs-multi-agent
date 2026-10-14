"""End-to-end money flow on a temporary copy of the original database (no AI calls).

Walks the three tickets the way the board does: Accounting stages requests
through MCP tools, a human approves them through the FastAPI route, and the
pay path refuses anything checking cannot cover.
"""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

from backend import audit, main
from mcp_server import server


class ApprovalFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db_path = Path(temp.name) / "working.db"
        shutil.copyfile(server.PROJECT_ROOT / "data" / "campus_customs.db", self.db_path)
        for patcher in (
            patch.object(server, "WORKING_DB_PATH", self.db_path),
            patch.object(main, "WORKING_DB", self.db_path),
            patch.object(audit, "AUDIT_PATH", Path(temp.name) / "audit_trail.json"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _one(self, sql: str, *args):
        connection = sqlite3.connect(self.db_path)
        try:
            return connection.execute(sql, args).fetchone()
        finally:
            connection.close()

    def approve(self, approval_id: int) -> dict:
        return main.approve(approval_id, main.ApprovalDecision(approved_by="Test operator"))

    def test_three_tickets_cash_path(self) -> None:
        # Ticket 101: invoice 501 blocks the vendor, so pay it, then order one tee.
        invoice = server.propose_payment(840, "Bulldog Print Co", "Clear overdue invoice 501", "invoice:501")
        order = server.propose_purchase_order(101, "CC-TEE-WHITE", "S", 1, 1, "Restock one tee for the order")
        self.assertEqual(order.waits_for_invoice_payments, [501])
        with self.assertRaises(HTTPException) as blocked:
            self.approve(order.proposal_id)
        self.assertEqual(blocked.exception.status_code, 409)
        self.assertEqual(self.approve(invoice.proposal_id)["balance"], 2560.0)
        result = self.approve(order.proposal_id)
        self.assertEqual(result["balance"], 2552.0)
        self.assertEqual(self._one("SELECT expected_arrival FROM purchase_orders")[0], "2026-09-05")
        self.assertEqual(self._one("SELECT status FROM invoices WHERE id = 501")[0], "paid")
        details = server.get_ticket_details(101)
        self.assertEqual({item.status for item in details.approvals}, {"approved"})

        # Ticket 102: rent is paid once and the lease moves to next month.
        rent = server.propose_payment(2400, "Elm City Properties", "September rent", "lease:1")
        self.assertEqual(self.approve(rent.proposal_id)["balance"], 152.0)
        lease = server.get_lease_due_status(1)
        self.assertEqual((lease.next_due.isoformat(), len(lease.payments_recorded)), ("2026-10-02", 1))
        self.assertEqual(self._one("SELECT paid_at FROM payments WHERE kind = 'lease'")[0], "2026-08-31")

        # Ticket 103: the vendor is clear now, but 12 hoodies cost more than is left.
        vendors = {v.vendor_id: v for v in server.find_restock_vendors("CC-HOOD-NAVY").vendors}
        self.assertFalse(vendors[1].blocked_by_unpaid_invoices)
        with self.assertRaisesRegex(ValueError, "refused"):
            server.propose_purchase_order(103, "CC-HOOD-NAVY", "M", 12, 1, "Cover the 12-hoodie shortfall")
        price = server.get_pricing_and_margin("CC-HOOD-NAVY", 20, 10)
        self.assertEqual((price.unit_price_after_discount, price.order_revenue), (52.2, 1044.0))

        # Cash never goes negative and an approval cannot be used twice.
        with self.assertRaises(HTTPException):
            self.approve(rent.proposal_id)
        self.assertEqual(self._one("SELECT balance FROM cash_accounts")[0], 152.0)

    def test_pay_route_refuses_when_checking_is_short(self) -> None:
        rent = server.propose_payment(2400, "Elm City Properties", "September rent", "lease:1")
        connection = sqlite3.connect(self.db_path)
        connection.execute("UPDATE cash_accounts SET balance = 100")
        connection.commit()
        connection.close()
        with self.assertRaises(HTTPException) as refused:
            self.approve(rent.proposal_id)
        self.assertEqual(refused.exception.status_code, 400)
        self.assertEqual(self._one("SELECT COUNT(*) FROM payments")[0], 0)
        self.assertEqual(self._one("SELECT next_due FROM leases")[0], "2026-09-02")


if __name__ == "__main__":
    unittest.main()
