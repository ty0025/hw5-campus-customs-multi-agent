"""Render resolved-ticket screenshots from the compiled React board, offline.

This one-shot tool embeds the already-built Vite assets in a temporary local
HTML file, supplies read-only data snapshots from the working database and
audit trail, then runs a headless browser synchronously. It starts no web
server, does not call the agents, and does not alter either database.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output"
WORKING_DB = ROOT / "data" / "campus_customs_new.db"
FRONTEND_INDEX = ROOT / "frontend" / "dist" / "index.html"
RESOLVED_JSON = OUTPUT / "resolved_tickets.json"
AUDIT_JSON = OUTPUT / "audit_trail.json"
TICKET_IDS = (101, 102, 103)


def _records_from_json_or_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    content = path.read_text(encoding="utf-8")
    if not content.strip():
        return []
    try:
        value = json.loads(content)
        rows = value if isinstance(value, list) else [value]
    except json.JSONDecodeError:
        rows = []
        for line in content.splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return [row for row in rows if isinstance(row, dict)]


def _read_app_snapshot() -> dict[str, Any]:
    if not WORKING_DB.is_file():
        raise FileNotFoundError(f"Working database is missing: {WORKING_DB}")
    connection = sqlite3.connect(WORKING_DB.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        ticket_rows = connection.execute(
            "SELECT id,type,requester,subject,notes,status,sku,size,qty FROM tickets ORDER BY id"
        ).fetchall()
        tickets = [
            {
                "ticket_id": int(row["id"]),
                "type": row["type"],
                "requester": row["requester"],
                "subject": row["subject"],
                "description": row["notes"],
                "status": "resolved" if str(row["status"]).strip().casefold() == "resolved" else "open",
                "sku": row["sku"],
                "size": row["size"],
                "quantity": row["qty"],
            }
            for row in ticket_rows
        ]
        account = connection.execute(
            "SELECT name,balance FROM cash_accounts WHERE lower(trim(name))='checking' "
            "ORDER BY name LIMIT 1"
        ).fetchone()
        if account is None:
            rows = connection.execute("SELECT name,balance FROM cash_accounts").fetchall()
            if len(rows) != 1:
                raise ValueError("Could not resolve one checking cash account for the snapshot.")
            account = rows[0]

        approvals: list[dict[str, Any]] = []
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='approvals'"
        ).fetchone()
        if exists:
            for row in connection.execute("SELECT * FROM approvals ORDER BY id"):
                payload = json.loads(row["payload_json"] or "{}")
                kind = str(row["kind"]).strip().casefold()
                stored_status = str(row["status"]).strip().casefold()
                approvals.append({
                    "approval_id": int(row["id"]),
                    "payment_id": int(row["id"]) if kind == "payment" else None,
                    "ticket_id": int(row["ticket_id"]),
                    "kind": kind,
                    "amount": float(row["amount"]),
                    "recipient": payload.get("payee") or payload.get("recipient") or payload.get("vendor_name"),
                    "reason": payload.get("reason") or (
                        f"{payload.get('qty', '')} x {payload.get('sku', '')} {payload.get('size', '')}".strip()
                        if kind == "purchase_order" else "Recorded obligation"
                    ),
                    "reference_type": row["ref_type"],
                    "reference_id": row["ref_id"],
                    "sku": payload.get("sku"),
                    "size": payload.get("size"),
                    "quantity": payload.get("qty"),
                    "lead_days": payload.get("lead_days"),
                    "status": "PENDING_APPROVAL" if stored_status == "pending"
                    else "PAID" if kind == "payment" and stored_status == "approved"
                    else stored_status.upper(),
                    "created_at": row["created_at"],
                })
    finally:
        connection.close()

    events: list[dict[str, Any]] = []
    for row in _records_from_json_or_jsonl(AUDIT_JSON)[-200:]:
        tools = row.get("tool_calls")
        if not isinstance(tools, list):
            tool_name = row.get("tool_called") or row.get("tool_name")
            tools = [{"name": tool_name, "arguments": row.get("tool_arguments", {})}] if tool_name else []
        events.append({
            "timestamp": row.get("timestamp"),
            "ticket_id": row.get("ticket_id"),
            "active_agent": row.get("active_agent") or row.get("agent_name") or row.get("agent"),
            "thoughts": row.get("thoughts") or row.get("reasoning") or row.get("reasoning_notes"),
            "speech": row.get("speech") or row.get("response") or row.get("message"),
            "action": row.get("action"),
            "tool_calls": tools,
            "delegation_target": row.get("delegation_target"),
        })

    resolved = json.loads(RESOLVED_JSON.read_text(encoding="utf-8"))
    if {int(row["ticket_id"]) for row in resolved} != set(TICKET_IDS):
        raise ValueError("resolved_tickets.json must contain only the resolved 101, 102, and 103 records.")
    if {int(row["ticket_id"]) for row in tickets if row["status"] == "resolved"} != set(TICKET_IDS):
        raise ValueError("The working database does not show all three resolved ticket states.")

    return {
        "tickets": tickets,
        "balance": {"account_name": account["name"], "balance": float(account["balance"])},
        "events": events,
        "approvals": approvals,
    }


def _find_browser() -> Path:
    configured = os.environ.get("CAMPUS_CUSTOMS_BROWSER")
    candidates = [Path(configured)] if configured else []
    candidates.extend([
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    ])
    for command in ("msedge", "microsoft-edge", "chrome", "chromium", "chromium-browser"):
        located = shutil.which(command)
        if located:
            candidates.append(Path(located))
    browser = next((candidate for candidate in candidates if candidate.is_file()), None)
    if browser is None:
        raise FileNotFoundError("A Chromium-based browser was not found; set CAMPUS_CUSTOMS_BROWSER to its executable.")
    return browser


def _self_contained_snapshot(template: str, data: dict[str, Any], asset_root: Path) -> str:
    style_match = re.search(r'<link[^>]+href="([^"]+\.css)"[^>]*>', template)
    script_match = re.search(r'<script[^>]+src="([^"]+\.js)"[^>]*></script>', template)
    if style_match is None or script_match is None:
        raise ValueError("The built Vite page does not contain its expected single CSS and JavaScript bundles.")
    css_path = asset_root / Path(style_match.group(1)).name
    js_path = asset_root / Path(script_match.group(1)).name
    if not css_path.is_file() or not js_path.is_file():
        raise FileNotFoundError("The built React/CSS bundle is missing; run npm run build in frontend first.")
    css = css_path.read_text(encoding="utf-8")
    js = js_path.read_text(encoding="utf-8")
    data_json = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    stub = f"""<script>
      const snapshot = {data_json};
      const encode = value => new Response(JSON.stringify(value), {{status: 200, headers: {{'Content-Type':'application/json'}}}});
      window.fetch = async (resource, options = {{}}) => {{
        const method = String(options.method || 'GET').toUpperCase();
        const path = new URL(typeof resource === 'string' ? resource : resource.url, location.href).pathname;
        if (method !== 'GET') return new Response(JSON.stringify({{detail:'Read-only offline board snapshot.'}}), {{status:405, headers:{{'Content-Type':'application/json'}}}});
        if (path === '/api/tickets') return encode(snapshot.tickets);
        if (path === '/api/cash/balance') return encode(snapshot.balance);
        if (path === '/api/events') return encode(snapshot.events);
        if (path === '/api/approvals') return encode(snapshot.approvals);
        return new Response(JSON.stringify({{detail:'No local snapshot for this endpoint.'}}), {{status:404, headers:{{'Content-Type':'application/json'}}}});
      }};
      try {{ window.localStorage.setItem('__snapshot_probe__','1'); window.localStorage.removeItem('__snapshot_probe__'); }}
      catch {{ const storage = new Map(); Object.defineProperty(window, 'localStorage', {{value:{{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,String(v)),removeItem:k=>storage.delete(k)}}}}); }}
    </script>"""
    template = template.replace(style_match.group(0), f"<style>{css}</style>")
    template = template.replace(script_match.group(0), f"<script type=\"module\">{js}</script>")
    template = re.sub(r"\s+crossorigin(?:=\"[^\"]*\")?", "", template)
    return template.replace("</head>", stub + "</head>")


def main() -> None:
    if not FRONTEND_INDEX.is_file():
        raise FileNotFoundError("frontend/dist/index.html is missing; build the Vite app before rendering ticket screenshots.")
    data = _read_app_snapshot()
    browser = _find_browser()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    template = FRONTEND_INDEX.read_text(encoding="utf-8")
    asset_root = FRONTEND_INDEX.parent / "assets"
    with tempfile.TemporaryDirectory(prefix="campus-customs-snapshot-") as folder:
        temp_root = Path(folder)
        snapshot_file = temp_root / "react_board.html"
        snapshot_file.write_text(_self_contained_snapshot(template, data, asset_root), encoding="utf-8")
        user_data_dir = temp_root / "browser-profile"
        for ticket_id in TICKET_IDS:
            screenshot = OUTPUT / f"ticket_{ticket_id}.png"
            command = [
                str(browser), "--headless=new", "--disable-gpu", "--disable-extensions",
                "--no-first-run", "--no-default-browser-check", "--disable-background-networking",
                "--hide-scrollbars", "--allow-file-access-from-files", "--disable-web-security",
                f"--user-data-dir={user_data_dir}", "--window-size=1600,1400",
                "--virtual-time-budget=4000", f"--screenshot={screenshot}",
                f"{snapshot_file.as_uri()}?ticket={ticket_id}",
            ]
            completed = subprocess.run(command, capture_output=True, text=True, timeout=45, check=False)
            if completed.returncode != 0 or not screenshot.is_file():
                details = (completed.stderr or completed.stdout)[-1200:]
                raise RuntimeError(f"Headless browser did not render Ticket {ticket_id}: {details}")
            if screenshot.read_bytes()[:8] != b"\x89PNG\r\n\x1a\n" or screenshot.stat().st_size < 10_000:
                raise RuntimeError(f"Ticket {ticket_id} screenshot is invalid or unexpectedly small.")
            print(f"Rendered {screenshot.relative_to(ROOT)} ({screenshot.stat().st_size:,} bytes).")


if __name__ == "__main__":
    main()
