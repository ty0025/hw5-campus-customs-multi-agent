"""Build output/resolved_tickets.json from the last board run. Read-only.

Uses the audit trail from the most recent "Reset desk" onward plus the working
database. Every agent sentence in the file is the agent's own report from the
audit trail; nothing is written by hand.

    python scripts/export_run_evidence.py
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "campus_customs_new.db"
AUDIT = ROOT / "output" / "audit_trail.json"
OUT = ROOT / "output" / "resolved_tickets.json"


def summary_only(text: str) -> str:
    return text.split("\nNext:")[0].strip()


def main() -> None:
    records = json.loads(AUDIT.read_text(encoding="utf-8"))
    start = max(i for i, row in enumerate(records) if row.get("action") == "database_reset")
    reset, events = records[start], records[start:]
    connection = sqlite3.connect(f"file:{DB.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row

    tickets = []
    for ticket in connection.execute("SELECT * FROM tickets ORDER BY id"):
        tid = ticket["id"]
        mine = [row for row in events if row.get("ticket_id") == tid]
        runs = []
        for row in mine:
            if row["action"] == "ticket_run_completed":
                runs.append({"run_id": row["run_id"], "finished": row["timestamp"],
                             "result": row["message"], "usage": row.get("usage")})
        reports: dict[str, list[str]] = {}
        tools: dict[str, Counter] = {}
        for row in mine:
            agent = row["agent_name"]
            if row["action"] == "response_synthesis":
                reports.setdefault(agent, []).append(summary_only(row["message"]))
            if row["action"] == "tool_call_completed":
                tools.setdefault(agent, Counter())[row["tool_called"]] += 1
        delegations = [f"{row['agent_name']} → {row['delegation_target']}" for row in mine if row["action"] == "delegation"]
        approvals = [
            {"approval_id": row["id"], "kind": row["kind"], "amount": row["amount"],
             "payee": json.loads(row["payload_json"]).get("payee") or json.loads(row["payload_json"]).get("vendor_name"),
             "status": row["status"], "approved_by": row["decided_by"], "decided_at": row["decided_at"],
             "effect": row["decision_note"]}
            for row in connection.execute("SELECT * FROM approvals WHERE ticket_id = ? ORDER BY id", (tid,))
        ]
        drafts = [dict(row) for row in connection.execute(
            "SELECT id, body, status FROM customer_drafts WHERE ticket_id = ? ORDER BY id", (tid,))]
        tickets.append({
            "id": tid,
            "subject": f"{ticket['subject']} ({ticket['requester']})",
            "final_status": ticket["status"],
            "outcome": summary_only(reports["Boss"][-1]) if "Boss" in reports else None,
            "agent_contributions": {agent: " / ".join(texts) for agent, texts in reports.items()},
            "delegations": delegations,
            "mcp_tools_by_agent": {agent: dict(counter) for agent, counter in tools.items()},
            "human_approvals": approvals,
            "customer_drafts": drafts,
            "agent_runs": runs,
        })

    balance = connection.execute("SELECT balance FROM cash_accounts WHERE name = 'checking'").fetchone()[0]
    payments = [dict(row) for row in connection.execute("SELECT * FROM payments ORDER BY id")]
    total_tokens = sum((run["usage"] or {}).get("total_tokens", 0) for t in tickets for run in t["agent_runs"])
    report = {
        "run": {
            "reset_at": reset["timestamp"],
            "reset_note": reset["message"],
            "model": "gpt-6-luna via Portkey",
            "ending_checking_balance": balance,
            "payments_ledger": payments,
            "total_agent_tokens": total_tokens,
        },
        "tickets": tickets,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}: ending balance {balance}, {total_tokens} agent tokens")


if __name__ == "__main__":
    main()
