"""Purchase-order proposal and approval lifecycle against isolated database copies."""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import main
from mcp_server import server


class PurchaseOrderApprovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "working.db"
        shutil.copyfile(server.PROJECT_ROOT / "data" / "campus_customs.db", self.db_path)
        self.audit_path = self.root / "audit_trail.json"

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    def _patches(self):
        return (
            patch.object(server, "WORKING_DB_PATH", self.db_path),
            patch.object(main, "_connect", self.connect),
            patch.object(main, "AUDIT_FILE", self.audit_path),
        )

    def test_invoice_clearance_then_purchase_order_requires_human_approval(self) -> None:
        patches = self._patches()
        with patches[0], patches[1], patches[2]:
            with self.assertRaisesRegex(ValueError, r"invoice\(s\) 501 remain unpaid"):
                server.propose_purchase_order(101, "CC-TEE-WHITE", "S", 1, 1, "Restock one tee")

            invoice_proposal = server.propose_payment(
                840.0, "Bulldog Print Co", "Clear invoice 501", "invoice:501"
            )
            main.approve_payment(main.PaymentApprovalRequest(
                payment_id=invoice_proposal.proposal_id,
                amount=840.0,
                recipient="Bulldog Print Co",
                reference_type="invoice",
                reference_id=501,
            ))

            proposal = server.propose_purchase_order(
                101, "CC-TEE-WHITE", "S", 1, 1, "Replace the one-unit tee shortfall"
            )
            self.assertEqual(proposal.status, "PENDING_APPROVAL")
            self.assertEqual(proposal.amount, 8.0)
            self.assertFalse(proposal.cash_changed)

            client = TestClient(main.app)
            approval_response = client.get("/api/approvals?limit=100")
            self.assertEqual(approval_response.status_code, 200)
            rows = approval_response.json()
            purchase = next(row for row in rows if row["kind"] == "purchase_order")
            self.assertEqual(purchase["approval_id"], proposal.proposal_id)
            self.assertEqual(purchase["status"], "PENDING_APPROVAL")

            result_response = client.post("/api/purchase-orders/approve", json={
                "approval_id": proposal.proposal_id,
                "approved_by": "Desk Operator",
            })
            self.assertEqual(result_response.status_code, 200)
            result = result_response.json()
            self.assertEqual(result["status"], "APPROVED")
            self.assertFalse(result["cash_changed"])
            self.assertEqual(result["balance"], 2560.0)

            with self.assertRaises(HTTPException) as raised:
                main.approve_purchase_order(main.PurchaseOrderApprovalRequest(
                    approval_id=proposal.proposal_id,
                    approved_by="Desk Operator",
                ))
            self.assertEqual(raised.exception.status_code, 409)

        with closing(self.connect()) as connection:
            self.assertEqual(connection.execute("SELECT balance FROM cash_accounts").fetchone()[0], 2560.0)
            self.assertEqual(connection.execute("SELECT status FROM invoices WHERE id=501").fetchone()[0], "PAID")
            approval = connection.execute(
                "SELECT kind,status FROM approvals WHERE id=?", (proposal.proposal_id,)
            ).fetchone()
            self.assertEqual(tuple(approval), ("purchase_order", "approved"))
            order = connection.execute(
                "SELECT ticket_id,vendor_id,sku,size,qty,total,status FROM purchase_orders"
            ).fetchone()
            self.assertEqual(tuple(order), (101, 1, "CC-TEE-WHITE", "S", 1, 8.0, "approved_for_staff"))
            self.assertEqual(connection.execute("SELECT status FROM tickets WHERE id=101").fetchone()[0], "resolved")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM payments").fetchone()[0], 1)

        audit = self.audit_path.read_text(encoding="utf-8")
        self.assertIn("purchase_order_approved_for_staff", audit)
        self.assertIn("no external order was sent", audit)

    def test_purchase_approval_revalidates_cash_commitments_without_debit(self) -> None:
        patches = self._patches()
        with patches[0], patches[1], patches[2]:
            with closing(self.connect()) as connection:
                connection.execute("UPDATE invoices SET status='PAID' WHERE id=501")
                connection.commit()
            proposal = server.propose_purchase_order(
                101, "CC-TEE-WHITE", "S", 1, 1, "Restock one tee"
            )
            with closing(self.connect()) as connection:
                connection.execute("UPDATE cash_accounts SET balance=0")
                connection.commit()
            with self.assertRaises(HTTPException) as raised:
                main.approve_purchase_order(main.PurchaseOrderApprovalRequest(
                    approval_id=proposal.proposal_id,
                    approved_by="Desk Operator",
                ))
            self.assertEqual(raised.exception.status_code, 400)

        with closing(self.connect()) as connection:
            self.assertEqual(connection.execute("SELECT balance FROM cash_accounts").fetchone()[0], 0.0)
            self.assertEqual(connection.execute("SELECT status FROM approvals WHERE id=?", (proposal.proposal_id,)).fetchone()[0], "pending")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0], 0)

    def test_concurrent_approvals_create_one_staff_handoff(self) -> None:
        patches = self._patches()
        with patches[0], patches[1], patches[2]:
            with closing(self.connect()) as connection:
                connection.execute("UPDATE invoices SET status='PAID' WHERE id=501")
                connection.commit()
            proposal = server.propose_purchase_order(
                101, "CC-TEE-WHITE", "S", 1, 1, "Restock one tee"
            )
            request = main.PurchaseOrderApprovalRequest(
                approval_id=proposal.proposal_id,
                approved_by="Concurrent Test Operator",
            )

            def approve_once():
                try:
                    return main.approve_purchase_order(request)["status"]
                except HTTPException as exc:
                    return exc.status_code

            with ThreadPoolExecutor(max_workers=8) as pool:
                outcomes = list(pool.map(lambda _: approve_once(), range(8)))
        self.assertEqual(outcomes.count("APPROVED"), 1)
        self.assertEqual(outcomes.count(409), 7)
        with closing(self.connect()) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM purchase_orders").fetchone()[0], 1)
            self.assertEqual(connection.execute("SELECT balance FROM cash_accounts").fetchone()[0], 3400.0)


if __name__ == "__main__":
    unittest.main()
