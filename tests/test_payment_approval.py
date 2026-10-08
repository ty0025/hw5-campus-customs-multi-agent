"""Payment HITL tests against temporary copies of the supplied database."""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from unittest.mock import patch

from fastapi import HTTPException

from backend import main
from mcp_server import server


class PaymentApprovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "working.db"
        shutil.copyfile(server.PROJECT_ROOT / "data" / "campus_customs.db", self.db_path)
        self.audit_path = self.root / "audit_trail.json"

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=15)
        connection.row_factory = sqlite3.Row
        return connection

    @contextmanager
    def _database(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _patch_paths(self):
        return (
            patch.object(server, "WORKING_DB_PATH", self.db_path),
            patch.object(main, "_connect", self._connect),
            patch.object(main, "AUDIT_FILE", self.audit_path),
        )

    def test_mcp_proposals_finalize_only_through_hitl_approval(self) -> None:
        patches = self._patch_paths()
        with patches[0], patches[1], patches[2]:
            invoice = server.propose_payment(
                840.0, "Bulldog Print Co", "Clear overdue invoice 501", "invoice:501"
            )
            self.assertEqual(invoice.status, "PENDING_APPROVAL")
            self.assertEqual(invoice.ticket_id, 101)

            invoice_result = main.approve_payment(main.PaymentApprovalRequest(
                payment_id=invoice.proposal_id,
                amount=840.0,
                recipient="Bulldog Print Co",
                reference_type="invoice",
                reference_id=501,
            ))
            self.assertEqual(invoice_result["balance"], 2560.0)

            lease = server.propose_payment(
                2400.0, "Elm City Properties", "Pay lease 1", "lease:1"
            )
            self.assertEqual(lease.status, "PENDING_APPROVAL")
            self.assertEqual(lease.ticket_id, 102)
            lease_result = main.approve_payment(main.PaymentApprovalRequest(
                payment_id=lease.proposal_id,
                amount=2400.0,
                recipient="Elm City Properties",
                reference_type="lease",
                reference_id=1,
            ))
            self.assertEqual(lease_result["balance"], 160.0)

        with self._database() as connection:
            self.assertEqual(connection.execute("SELECT balance FROM cash_accounts").fetchone()[0], 160.0)
            self.assertEqual(connection.execute("SELECT status FROM invoices WHERE id=501").fetchone()[0], "PAID")
            self.assertEqual(connection.execute("SELECT payment_status FROM leases WHERE id=1").fetchone()[0], "PAID")
            self.assertEqual(
                [row[0] for row in connection.execute("SELECT status FROM approvals ORDER BY id")],
                ["approved", "approved"],
            )
            payments = connection.execute("SELECT kind,ref_id,amount,status FROM payments ORDER BY id").fetchall()
            self.assertEqual([tuple(row) for row in payments], [
                ("invoice", 501, 840.0, "PAID"),
                ("lease", 1, 2400.0, "PAID"),
            ])

    def test_cash_floor_rejects_without_mutating_proposal_or_liability(self) -> None:
        patches = self._patch_paths()
        with patches[0], patches[1], patches[2]:
            invoice = server.propose_payment(
                840.0, "Bulldog Print Co", "Clear overdue invoice 501", "invoice:501"
            )
            with self._database() as connection:
                connection.execute("UPDATE cash_accounts SET balance=500.0")

            with self.assertRaises(HTTPException) as raised:
                main.approve_payment(main.PaymentApprovalRequest(
                    payment_id=invoice.proposal_id,
                    amount=840.0,
                    recipient="Bulldog Print Co",
                    reference_type="invoice",
                    reference_id=501,
                ))
            self.assertEqual(raised.exception.status_code, 400)
            self.assertEqual(raised.exception.detail, "Insufficient cash balance: negative balance strictly forbidden")

        with self._database() as connection:
            self.assertEqual(connection.execute("SELECT balance FROM cash_accounts").fetchone()[0], 500.0)
            self.assertEqual(connection.execute("SELECT status FROM invoices WHERE id=501").fetchone()[0], "open")
            self.assertEqual(connection.execute("SELECT status FROM approvals WHERE id=?", (invoice.proposal_id,)).fetchone()[0], "pending")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM payments").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
