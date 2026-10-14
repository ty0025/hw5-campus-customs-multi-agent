# Campus Customs MCP Server

`mcp_server/server.py` is a FastMCP server (`mcp.server.fastmcp.FastMCP`, exported as `mcp_server.server.mcp`). It is the only way the five agents reach shop data. Every fact an agent uses about stock, vendors, invoices, rent, cash or prices comes from one of its 11 tools.

## Database

- The server only opens `data/campus_customs_new.db`. The path is fixed relative to the package (`PROJECT_ROOT / "data" / "campus_customs_new.db"`), so it doesn't depend on the current folder, and no environment variable can point it at another file.
- If the working copy is missing, tools raise an error. The server never creates a blank database. See the main README for how to copy `data/campus_customs.db` over it.
- Read tools open SQLite read-only (`mode=ro` plus `PRAGMA query_only = ON`). The four write tools use bound parameters inside one `BEGIN IMMEDIATE` transaction each.
- "Today" is always `desk.date_today` (2026-08-31 in this database), never the computer's clock.

## How it is started

- **From `.mcp.json`** (Claude Code or another MCP client opened in the repo root):
  ```json
  {"mcpServers": {"campus_customs_mcp": {"command": "py", "args": ["-m", "mcp_server.server"], "env": {"PYTHONPATH": "."}}}}
  ```
  `py` is the Windows Python launcher. I used it because on my PC plain `python` points at the Microsoft Store stub. On macOS/Linux, change it to `python3`. `PYTHONPATH` is `.`, so the client has to start it from the repo root.
- **By the backend.** For each ticket run, `backend/agent_team.py` starts `python -m mcp_server.server` as a stdio subprocess with the backend's own Python, opens a `ClientSession`, and checks that all 11 tools are there before any agent starts. The subprocess gets a minimal environment with no `PORTKEY_API_KEY`.
- **By hand**, for testing: `py -m mcp_server.server` (Windows) or `python3 -m mcp_server.server`. It talks over stdin/stdout, opens no port, and prints nothing while it waits for a client.

When run with `-m`, `server.py` re-launches itself with `.venv`'s Python if that folder exists, so the launcher's own Python doesn't need the packages. Without a `.venv`, install `mcp_server/requirements.txt` (`mcp==1.30.0`, `pydantic`) into whatever Python starts it.

## The three Problem 3 tools

These were the first three tools. The examples are what they return on a freshly reset database.

| Tool | Tables | Ticket and why |
| --- | --- | --- |
| `check_inventory_stock(sku, size, order_quantity)` | `inventory` | Ticket 101: `CC-TEE-WHITE` size S has 0 on hand for an order of 1. Ticket 103: `CC-HOOD-NAVY` size M has 8 of the 20 requested, so 12 short. Without it nobody knows whether the order can ship. |
| `check_vendor_restock_eligibility(vendor_id)` | `vendors`, `invoices`, `desk` | Ticket 101: Bulldog Print Co (vendor 1, apparel reprint, 5-day lead) has invoice 501 for $840, due 2026-08-28 and 3 days overdue, so it won't ship until that is paid. |
| `get_lease_due_status(lease_id)` | `leases`, `payments`, `desk` | Ticket 102: lease 1 (Chapel Street shop, Elm City Properties) owes $2,400 on 2026-09-02, 2 days after the desk date, with no payment recorded. |

After the final run these answers change: invoice 501 is paid and the lease's next due date is 2026-10-02.

## All 11 tools

| Tool | Tables | What it does |
| --- | --- | --- |
| `get_ticket_details(ticket_id)` | `tickets`, `desk`, `invoices`, `vendors`, `approvals`, `customer_drafts` | Reads the ticket, the desk date, the vendor behind a linked invoice, any approvals already staged with their status, and how many drafts exist. Boss uses the approvals list to tell a first run from a follow-up run. |
| `check_inventory_stock(sku, size, order_quantity)` | `inventory` | On-hand quantity, shortfall and location for one SKU and size. An unknown SKU is an error, not zero stock. |
| `find_restock_vendors(sku)` | `inventory`, `desk`, `vendors`, `invoices`, `approvals` | Lists every vendor with specialty, lead days, open invoices, whether those block shipping, which already have a pending payment, and the arrival date if ordered today. The database has no SKU-to-vendor table, so the agent picks by specialty. |
| `check_vendor_restock_eligibility(vendor_id)` | `vendors`, `invoices`, `desk` | One vendor's open invoices with days overdue, and whether they block new shipments. |
| `get_lease_due_status(lease_id)` | `leases`, `payments`, `desk` | Rent amount, next due date, days until due (`overdue`, `due_today` or `upcoming`) and any rent payments already recorded. |
| `get_cash_balance(account_name="Operating Cash")` | `cash_accounts` | The checking balance. The default label maps to the database's single `checking` row. |
| `get_pricing_and_margin(sku, quantity=1, discount_percent=0)` | `pricing` | Cost, list price and margin, plus the discounted unit price, margin percent and order revenue, cost and gross profit for a quantity and discount. It only calculates. |
| `propose_payment(amount, recipient, reason, ref_id)` | writes `approvals`; reads `desk`, `cash_accounts`, `invoices` or `leases`, `tickets` | Stages a pending payment for `invoice:<id>` or `lease:<id>`. The amount must match the invoice or the monthly rent, and it refuses if the amount is more than checking minus other pending requests. It never touches `payments` or the balance. |
| `propose_purchase_order(ticket_id, sku, size, quantity, vendor_id, reason)` | writes `approvals`; reads `tickets`, `inventory`, `vendors`, `invoices`, `pricing`, `cash_accounts`, `purchase_orders` | Stages a pending restock order at unit cost. Quantity must equal the ticket's shortfall, the vendor must be an apparel vendor (and the invoice's vendor if the ticket links one), and every open invoice at that vendor must already have a pending payment. It refuses if the cost is more than checking minus pending requests. |
| `save_customer_draft(ticket_id, draft_body)` | reads `tickets`; writes `customer_drafts` | Saves a draft addressed to the ticket's requester, with `sent=false`. Nothing is sent. |
| `update_ticket_status(ticket_id, status, resolution)` | `tickets` | Sets the status (`open`, `in_progress`, `waiting_approval`, `waiting_customer`, `blocked`, `ready_for_staff`, `resolved`) and appends a `Resolution:` line to the ticket notes. |

## Which tools write

Four tools write, and all four stay inside the working database:

- `update_ticket_status`: ticket status and notes
- `propose_payment`: one pending row in `approvals`
- `propose_purchase_order`: one pending row in `approvals`
- `save_customer_draft`: one row in `customer_drafts`

No tool moves cash, marks an invoice paid, changes a lease, places an order or sends a message. `approvals`, `customer_drafts` and `purchase_orders` don't exist in the original database. The tools create them the first time they're needed. Money only moves in the backend route `POST /api/approvals/{approval_id}/approve`, after a person clicks Approve on the board. That route writes `payments`, `purchase_orders`, `invoices`, `leases` and `cash_accounts`, and refuses any amount larger than checking.

The server itself doesn't check who is calling. The rule that only Accounting may call `propose_payment` and `propose_purchase_order` is enforced in `backend/agent_team.py`, which also caps each ticket at 40 MCP calls.

## Tests

From the repo root:

```bash
PYTHONPATH=. python -m unittest tests.test_mcp_server
```

The tests check the tool list and parameters, bad inputs, the SQL-injection-style SKU case, the read-only connection, and that neither database file changes. Several read the working copy and expect the reset state, so `test_ticket_102_lease_is_due_in_two_shop_days` fails after a full run (the lease is then due 2026-10-02).

## Running the backend

The agents reach this server through the backend. From the repo root:

```bash
cd backend && uvicorn main:app --reload --port 8000
```

`POST /api/tickets/{ticket_id}/run` starts this server as a subprocess, runs the team, and closes it again when the run ends.
