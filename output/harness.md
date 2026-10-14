# Campus Customs Harness

This file describes what my Homework 5 build is made of and how it behaved in the final run. It covers the shop database (Problem 2), the MCP tools (Problems 3 to 5), the agent team (Problem 5), the safety rules, the API routes (Problem 7), the dashboard (Problem 8) and the final run (Problem 9).

In short: 9 tables in the original database plus 3 the app adds, 11 MCP tools, 5 agents, 8 API routes and one React board. Every agent runs on `gpt-6-luna` through Portkey.

## Problem 2: Database tables

I first generated this section with `python study_database.py` from the project root, then rewrote the Why lines and added the runtime-table notes by hand. The script copies `data/campus_customs.db` to `data/campus_customs_new.db` and then reads the working copy through a read-only sqlite3 connection. Table names come from `sqlite_master` and the field names and types come from `PRAGMA table_info`. Row counts are from the original file, before any run.

### `cash_accounts` (1 row)

- `name`: `TEXT`
- `balance`: `REAL`
- `date`: `TEXT`

Why: there is one account, `checking`. Accounting reads it before staging any payment or order, and the approve route debits it.

### `desk` (1 row)

- `date_today`: `TEXT`
- `notes`: `TEXT`

Why: `date_today` (2026-08-31) is "today" for every agent and tool. Overdue invoices, days until rent and restock arrival dates are all counted from it, never from the computer's clock.

### `inventory` (10 rows)

- `sku`: `TEXT`
- `name`: `TEXT`
- `size`: `TEXT`
- `qty`: `INTEGER`
- `location`: `TEXT`

Why: Inventory compares on-hand `qty` for a SKU and size with what the ticket asks for. Tickets 101 and 103 both depend on it.

### `invoices` (1 row)

- `id`: `INTEGER`
- `vendor_id`: `INTEGER`
- `amount`: `REAL`
- `due_date`: `TEXT`
- `status`: `TEXT`
- `description`: `TEXT`

Why: an open invoice is money we owe, and it also means that vendor won't ship. Invoice 501 is what blocks the tee restock on ticket 101.

### `leases` (1 row)

- `id`: `INTEGER`
- `space_name`: `TEXT`
- `landlord`: `TEXT`
- `monthly_rent`: `REAL`
- `next_due`: `TEXT`
- `notes`: `TEXT`

Why: Facilities reads the rent and `next_due` for ticket 102 and compares the date with the desk date.

### `payments` (0 rows)

- `id`: `INTEGER`
- `kind`: `TEXT`
- `ref_id`: `INTEGER`
- `amount`: `REAL`
- `account`: `TEXT`
- `paid_at`: `TEXT`
- `approved_by`: `TEXT`

Why: the ledger of money that actually left checking, with the name of the person who approved it. Only the approve route writes here. `get_lease_due_status` reads it so rent is not paid twice.

### `pricing` (4 rows)

- `sku`: `TEXT`
- `unit_cost`: `REAL`
- `list_price`: `REAL`

Why: Accounting uses `unit_cost` to price a restock order and both columns to test bulk discounts for ticket 103.

### `tickets` (3 rows)

- `id`: `INTEGER`
- `type`: `TEXT`
- `requester`: `TEXT`
- `subject`: `TEXT`
- `sku`: `TEXT`
- `size`: `TEXT`
- `qty`: `INTEGER`
- `lease_id`: `INTEGER`
- `invoice_id`: `INTEGER`
- `status`: `TEXT`
- `notes`: `TEXT`
- `created_at`: `TEXT`

Why: the three board items. Boss reads the type, the SKU/size/qty and any linked lease or invoice to decide who works the ticket. `status` and `notes` record the outcome.

### `vendors` (3 rows)

- `id`: `INTEGER`
- `name`: `TEXT`
- `specialty`: `TEXT`
- `lead_days`: `INTEGER`

Why: Inventory picks a supplier by `specialty` and quotes arrival as desk date + `lead_days`. There is no SKU-to-vendor table, so the pick is by specialty, or by the vendor on the ticket's invoice.

### Tables and columns the app adds at runtime

The original file is never written. During a run the working copy changes like this:

- `approvals` is created the first time Accounting stages a payment or purchase order. Fields: `id`, `ticket_id`, `kind` (`payment` or `purchase_order`), `ref_type`, `ref_id`, `amount`, `payload_json`, `status` (`pending`, `approved`, `rejected`), `created_at`, `decided_at`, `decided_by`, `decision_note`. Why: it is the queue of money requests waiting for a person. Agents can add rows but cannot approve them.
- `customer_drafts` is created by `save_customer_draft`. Fields: `id`, `ticket_id`, `body`, `status` (always `draft`), `created_at`. Why: customer messages are kept here and never sent.
- `purchase_orders` is created when an order is first staged and gets a row when a human approves one. Fields: `id`, `approval_id`, `ticket_id`, `vendor_id`, `sku`, `size`, `qty`, `unit_cost`, `total`, `lead_days`, `expected_arrival`, `status`, `created_at`, `approved_by`. Why: it records what was ordered and when it should arrive (desk date + `lead_days`). Nothing goes to the vendor.
- `payments` gets one row per approval. `paid_at` is `desk.date_today` (2026-08-31), not the real date, and `approved_by` is the name typed on the board. An approved purchase order is logged with `kind = purchase_order`.
- `leases.next_due` moves forward one month when rent is approved (2026-09-02 became 2026-10-02 in the final run).
- `invoices.status` becomes `paid` when an invoice payment is approved.
- `cash_accounts.balance` goes down by each approved amount.
- `tickets.status` changes as Boss works the ticket, and each Boss decision is appended to `tickets.notes` as a `Resolution:` line.

### Open-ticket findings (original database, at Problem 2 time)

`desk.date_today` is `2026-08-31`, and every overdue count below uses that shop date. The database has 3 open tickets.

#### Table linkages

Declared foreign keys from `PRAGMA foreign_key_list`:

- `invoices.vendor_id` references `vendors.id`.
- `tickets.invoice_id` references `invoices.id`.
- `tickets.lease_id` references `leases.id`.

Other operational links:

- `(tickets.sku, tickets.size)` matches `(inventory.sku, inventory.size)`, and `tickets.sku` matches `pricing.sku`. Neither is a declared foreign key.
- `desk.date_today` supplies the date for invoices, leases and ticket handling.
- `payments.account` can match `cash_accounts.name`, and `payments.kind` plus `payments.ref_id` identify the paid obligation. Neither link is a declared foreign key, and the payment ledger starts empty.
- No table maps SKUs to vendors, so a vendor picked from product category and `vendors.specialty` is a candidate, not a confirmed supplier.

#### Ticket 101: Bulldog tee

- Intake: `customer_order` from Tauhid Zaman; status `open`; created `2026-08-31T09:15:00-04:00`.
- Recorded notes: Needs a tee in size S.
- Stock: `CC-TEE-WHITE` size `S` is Classic Bulldog Tee; 0 on hand at Aisle B for a requested 1, a shortfall of 1.
- Pricing: unit cost $8.00, list price $28.00, gross profit $20.00 per unit at list (71.43% margin).
- Invoice and vendor: `invoice_id=501` belongs to Bulldog Print Co through `invoices.vendor_id`; $840.00, status `open`, due `2026-08-28` (3 days overdue), description "Rush reprint CC-TEE-WHITE S".
- Vendor timing: specialty `apparel reprint`, lead time 5 days. The open invoice blocks new shipments, so the lead time only counts once it is paid.

#### Ticket 102: Rent due

- Intake: `rent_notice` from Elm City Properties; status `open`; created `2026-08-31T11:05:00-04:00`.
- Recorded notes: Email: shop rent due in 2 days.
- Lease: `lease_id=1` is the Chapel Street shop, landlord Elm City Properties, monthly rent $2,400.00, next due `2026-09-02` (2 days away).

#### Ticket 103: Bulk hoodie discount

- Intake: `price_override` from Yale AI Club; status `open`; created `2026-08-31T16:55:00-04:00`.
- Recorded notes: Student org wants 20 hoodies, size M, asking for a bulk discount.
- Stock: `CC-HOOD-NAVY` size `M` is Basic Hoodie Big Yale; 8 on hand at Aisle A for a requested 20, a shortfall of 12.
- Pricing: unit cost $22.00, list price $58.00, gross profit $36.00 per unit at list (62.07% margin).
- Discount: the request does not name a discount or target price, so the discounted margin is open until someone picks one.
- Candidate vendor: Bulldog Print Co (apparel reprint, 5-day lead) is the likely supplier by category. Ticket 103 has no invoice or direct vendor link.
- Vendor hold: at this point invoice 501 ($840.00) also blocks any hoodie restock from that vendor.

#### Cash and operating constraints

- `checking` balance: $3,400.00 as of `2026-08-31`.
- The payment ledger has 0 rows. The inspection makes no payments or ticket changes.
- Every payment needs human approval and enough cash. Cash only goes out, and the balance can't go negative.
- Customer messages and vendor requests stay as drafts on the board. No email is sent and no vendor is called.
- Reset the working database from the original before every full ticket run.

### Inspection check (at Problem 2 time)

- Found 9 tables, 48 fields and 3 open tickets.
- `PRAGMA integrity_check`: `ok`; `PRAGMA foreign_key_check`: no violations.
- At Problem 2 time the original file and the fresh working copy had the same SHA-256:
  `23686a90d698f7fa6f901a3f0a9774db9cbb31e13151c9e5717275f3321f360a`
- I checked the original again on Oct 14 after the final run and it still has this hash. The working copy has changed, as it should.

## Problem 3: The first three MCP tools

The server is `mcp_server/server.py` (FastMCP). Problem 3 asked for three tools that each unlock a ticket. These were my three, and they are still in the server.

| Tool | Table(s) read | Ticket | Why |
| --- | --- | --- | --- |
| `check_inventory_stock(sku, size, order_quantity)` | `inventory` | 101 (also 103) | For 101 it shows CC-TEE-WHITE size S has 0 on hand against 1 ordered, so the tee must be restocked. For 103 it shows only 8 of the 20 M hoodies. |
| `check_vendor_restock_eligibility(vendor_id)` | `vendors`, `invoices`, `desk` | 101 | It shows Bulldog Print Co has invoice 501 ($840) open and 3 days overdue, so they won't ship the tee until that bill is paid. |
| `get_lease_due_status(lease_id)` | `leases`, `desk` (later also `payments`) | 102 | It shows lease 1's $2,400 rent is due 2026-09-02, 2 days after the desk date, so the rent has to be staged for approval now. |

`get_lease_due_status` later started reading `payments` too, so Facilities can see whether this month's rent was already paid.

## Problem 4: Connecting the MCP server

`.mcp.json` in the project root:

```json
{
  "mcpServers": {
    "campus_customs_mcp": {
      "command": "py",
      "args": ["-m", "mcp_server.server"],
      "env": {
        "PYTHONPATH": "."
      }
    }
  }
}
```

`py` is the Windows Python launcher. On my PC, `python` points at the Microsoft Store stub, so I used `py`. On macOS or Linux, change the command to `python3`. When `server.py` runs as a module, it looks for the project's `.venv` and, if it finds one, re-launches itself with that interpreter. That way the MCP packages load no matter which Python started it. Because of `PYTHONPATH=.`, the client has to be opened in the `hw5` folder.

I tested the tools from a Claude Code session opened in the project folder, which loaded `.mcp.json` and called the `campus_customs_mcp` tools. The record of that check is in `output/mcp_smoke.json`. I ran it after the final run, so it shows the post-run state: no unpaid invoice at Bulldog Print Co, and lease 1 next due 2026-10-02 (32 days out) with payment 3 recorded.

`tests/test_mcp_server.py` checks the server without any AI client. It confirms all 11 tools are registered with typed inputs and outputs, that the seven read tools are marked read-only, and that the ticket facts come back right (0 tees for 101, a 12-hoodie shortfall for 103, invoice 501 blocking vendor 1, rent due in 2 days).

The backend does not use `.mcp.json`. For each ticket run, `backend/agent_team.py` starts its own stdio subprocess (`<backend python> -m mcp_server.server`, working directory = project root) and checks that all 11 tools are there before any agent starts.

## Problem 5: Agent team

`backend/agent_team.py` builds one PydanticAI `Agent` per role. Each one reads its system prompt from `backend/prompts/`, answers with the `AgentResult` model from `backend/models.py` (summary, findings, next action, ticket status, whether a human approval is needed), and runs on `gpt-6-luna` through the Portkey gateway (`https://api.portkey.ai/v1`, key `PORTKEY_API_KEY` from `.env`).

Every agent has the same two tools:

- `call_shop_mcp_tool(tool_name, arguments)` calls one MCP tool through a stdio `ClientSession`. The name has to be on the allowlist of the 11 server tools.
- `delegate_to_agent(target_agent, task, context)` hands a task to any other agent (never itself) and waits for its answer, so the team is fully connected.

| Agent | Prompt file | Job | Usually hands work to |
| --- | --- | --- | --- |
| Boss | `boss.md` | Reads every ticket first, decides who works on it, makes the final call (for example the discount) and sets the ticket status. On a follow-up run it checks whether every approval is decided and closes the ticket. | Inventory for orders, Facilities for rent, Inventory and Accounting for discounts, Customer Service for anything a customer will read |
| Inventory | `inventory.md` | Checks stock and the shortfall, picks a restock vendor by specialty, reports lead time and any unpaid-invoice hold. | Accounting, when an invoice payment or restock order needs staging |
| Accounting | `accounting.md` | Reads cash, invoices and margins, and is the only agent that can stage payments and purchase orders. Gives Boss discount numbers at 0%, 10% and 15%. | Back to whoever asked; it can ask Inventory or Facilities for missing facts |
| Facilities | `facilities.md` | Reads the lease, compares `next_due` with the desk date and checks the rent wasn't already paid. | Accounting, to stage the rent |
| Customer Service | `customer_service.md` | Writes the customer message and saves it as a draft. Keeps cash, invoices and approvals out of it. | Nobody; it reports back to Boss |

Delegations in the final run: Boss → Inventory → Accounting (101), Boss → Facilities → Accounting (102), Boss → Customer Service (101 resume run), and Boss → Inventory, Boss → Accounting, Boss → Customer Service (103).

### All 11 MCP tools

| Tool | Reads | Writes | Used in the final run by |
| --- | --- | --- | --- |
| `get_ticket_details(ticket_id)` | `tickets`, `desk`, `invoices`, `vendors`, `approvals`, `customer_drafts` | none | Boss (plus the runner's own reads, logged under Boss), Inventory, Accounting, Customer Service |
| `check_inventory_stock(sku, size, order_quantity)` | `inventory` | none | Inventory (101, 103); Accounting re-checked it on 101 |
| `find_restock_vendors(sku)` | `inventory`, `vendors`, `invoices`, `approvals`, `desk` | none | Inventory (101, 103) |
| `check_vendor_restock_eligibility(vendor_id)` | `vendors`, `invoices`, `desk` | none | Accounting (101) |
| `get_lease_due_status(lease_id)` | `leases`, `payments`, `desk` | none | Facilities (102); Accounting re-checked it on 102 |
| `get_cash_balance(account_name)` | `cash_accounts` | none | Accounting (101, 102, 103) |
| `get_pricing_and_margin(sku, quantity, discount_percent)` | `pricing` | none | Accounting (103, at 10% and 15% off) |
| `propose_payment(amount, recipient, reason, ref_id)` | `invoices` or `leases`, `tickets`, `cash_accounts`, `approvals`, `desk` | `approvals` (pending row) | Accounting only (invoice 501 on 101, lease 1 on 102) |
| `propose_purchase_order(ticket_id, sku, size, quantity, vendor_id, reason)` | `tickets`, `inventory`, `vendors`, `invoices`, `pricing`, `cash_accounts`, `approvals`, `purchase_orders` | `approvals` (pending row) | Accounting only (1 tee on 101) |
| `save_customer_draft(ticket_id, draft_body)` | `tickets` | `customer_drafts` | Customer Service (101, 103) |
| `update_ticket_status(ticket_id, status, resolution)` | `tickets` | `tickets.status`, `tickets.notes` | Boss (all three tickets) |

Seven tools only read. They open the database with `mode=ro` and `PRAGMA query_only`. The other four write: `propose_payment`, `propose_purchase_order`, `save_customer_draft` and `update_ticket_status`. Each write runs in one `BEGIN IMMEDIATE` transaction on the working copy. None of the 11 moves cash, sends a message or calls anything outside the machine.

### Audit trail

`backend/audit.py` writes `output/audit_trail.json` as one JSON array. Each record is an `AuditStep` (`backend/models.py`) with these fields:

- `timestamp` (UTC), `run_id`, `sequence`, `ticket_id`
- `agent_name`: one of the five agents, `System` or `Human`
- `action`: `ticket_run_started`, `thinking_started`, `delegation`, `tool_call_started`, `tool_call_completed`, `response_synthesis`, `ticket_run_completed`, `error`, `approval_decided` or `database_reset`
- `delegation_target`, `tool_called`, `tool_arguments`, and `tool_result` (the MCP result, cut to 2,000 characters)
- `message`: the delegated task with its context, an agent's report, or an error (up to 4,000 characters)
- `findings`, `model`, `usage` (requests and tokens), `depth`

The file is append-only. Each write reads the array, adds one record, writes a temp file and swaps it in, so nothing is edited or deleted. Records from the Oct 8 version were JSON Lines with a `reasoning_notes` field. They were converted into the array without loss, and the reader still accepts both formats. `GET /api/events` and `scripts/export_run_evidence.py` only look at records after the last `database_reset`, so older runs stay in the file without showing up on the current board.

## Safety

### Guardrails

I set these up the way I would want them in a real shop. The agents can read anything they need, but money and customer messages always stop at a person.

- Every dollar needs a human. Agents can only add `pending` rows to `approvals`. The only code that moves cash is `POST /api/approvals/{approval_id}/approve`, which runs when someone clicks Approve on the board. It saves the approver's name in `payments.approved_by` and `approvals.decided_by`.
- Only Accounting can stage money. `call_shop_mcp_tool` refuses `propose_payment` and `propose_purchase_order` from any other agent and logs the attempt as an error, so Inventory, Facilities and Boss have to delegate to Accounting.
- Proposals are checked against the records. `propose_payment` needs an `invoice:<id>` or `lease:<id>` reference, and the amount has to match the invoice or rent exactly. Both proposal tools hold back cash already promised to other pending requests and refuse a new request that doesn't fit. `propose_purchase_order` only accepts the exact shortfall and an apparel vendor (for a ticket with an invoice, that invoice's vendor). It refuses a blocked vendor unless the blocking invoice already has a pending payment request.
- The approve route refuses overdrafts and blocked orders. It returns 400 if the amount is more than what's in checking, so the balance can't go negative. It returns 409 for a purchase order while the vendor still has an open invoice, and 409 for a request that was already decided. Each approval is one transaction, so a refusal changes nothing. `tests/test_approval_flow.py` covers both refusals and the full cash path on a temporary copy of the database, with no AI calls.
- Cash only goes out. No tool or route adds money. After the final run I also wrote the no-negative-cash rule into `accounting.md` and `boss.md` in plain words. That changes nothing at runtime, since the tools and the approve route already enforce it.
- Drafts are never sent. There is no email, SMS or vendor API anywhere in the code. `save_customer_draft` stores the message as `draft` and returns `sent=false`, and the board labels each one "DRAFT · NOT SENT".
- No invented facts. Agents reach shop data only through MCP. The agent code has no SQL. Every prompt says to use only numbers from a tool or a teammate's report and to quote an arrival date only as desk date + `lead_days`.
- Tools are allowlisted. Only the 11 server tools can be called; any other name is rejected and logged.
- The MCP subprocess gets no API key. `_child_environment` passes only `PATH`, `SYSTEMROOT`, `WINDIR`, `TEMP`, `TMP`, `LANG`, `LC_ALL`, `PYTHONPATH` and `PYTHONIOENCODING`.
- The model is locked. `backend/app/settings.py` hard-codes `gpt-6-luna` and the Portkey URL, and `AgentTeam` refuses to start if either one changes or the key is missing.
- Status belongs to Boss. The code drops `ticket_status` from every non-Boss answer. `update_ticket_status` itself is not locked to Boss in code. The other prompts tell agents to leave status alone, and in the final run only Boss called it.
- Clean runs. `POST /api/database/reset` copies the original file over the working copy and logs the starting balance. The original is never opened for writing.

### Token and loop limits

Set in `backend/agent_team.py`:

| Limit | Value |
| --- | --- |
| Delegation depth | 6 |
| Agent turns per ticket run | 12 |
| MCP calls per ticket run | 40 |
| Model requests per agent turn | 10 |
| Tool calls per agent turn | 10 |
| Tokens per agent turn | 60,000 |
| Reasoning effort | none |

Delegated tasks are cut to 1,200 characters and their context to 5,000, and an agent can return at most 12 findings. When a depth or turn limit is hit, the delegation comes back as `blocked` with a note to use the facts already gathered. When a call fails or a per-turn limit is hit, the agent that asked gets a `failed` result. Either way the team stops instead of retrying in a loop.

### Measured cost of the final run

From the `usage` field of each `ticket_run_completed` record: five ticket runs used 13 agent turns, 46 model requests, 45 MCP calls and 95,817 tokens. The biggest single agent turn was Boss on ticket 103 at 13,857 tokens over 5 requests, under a quarter of the 60,000 cap. The busiest ticket run was the first run of 101 (3 agent turns, 14 MCP calls, 28,227 tokens). Delegation never went deeper than 2, and no limit was hit.

Some of those calls were repeats. The runner reads the ticket before and after each run and logs those reads under Boss, so `get_ticket_details` is logged under Boss 7 times on 101 and 7 times on 102 (4 from the runner and 3 from Boss each time); on 101, Inventory, Accounting and Customer Service each read it once more, for 10 in total. Accounting also re-ran `check_inventory_stock` on 101 and `get_lease_due_status` on 102, even though the agent that delegated to it had already passed those facts.

## Problem 7: API routes

`backend/main.py`, started with `cd backend && uvicorn main:app --reload --port 8000`. CORS allows `http://localhost:5173` (and `http://127.0.0.1:5173`).

- `GET /api/tickets`: the three tickets with their request fields, status and number of pending approvals.
- `POST /api/tickets/{ticket_id}/run`: runs the agent team on ticket 101, 102 or 103 and returns the status, Boss's summary, agent turns, model requests and tokens.
- `GET /api/events`: audit records shaped for the board (agent, action, text, tool with arguments and result); `?ticket_id=` filters by ticket, and `since_reset=true` (the default) starts at the last reset.
- `GET /api/approvals`: every staged payment and purchase order with amount, payee, reason, reference, any invoice it waits on, and the human decision.
- `POST /api/approvals/{approval_id}/approve`: body `{"approved_by": "<name>"}`; the only route that moves cash. A payment writes a `payments` row dated `desk.date_today`, marks the invoice paid or moves the lease's `next_due` a month, and debits checking. A purchase order is refused while its vendor has an open invoice; otherwise it writes a `purchase_orders` row (arrival = desk date + `lead_days`) and a `payments` row of kind `purchase_order`, and debits checking. Anything larger than the checking balance is refused with 400.
- `GET /api/drafts`: saved customer drafts with requester and subject, always `sent: false`.
- `GET /api/cash/balance`: the checking balance and the desk date.
- `POST /api/database/reset`: copies `data/campus_customs.db` over `data/campus_customs_new.db` and logs a `database_reset` event with the starting balance.

## Problem 8: Dashboard

The board is in `frontend/` (React + Vite + TypeScript) and runs with `npm run dev` on port 5173. It has three columns:

- Left: the ticket stack, with status badges that come from the database (OPEN, NEEDS APPROVAL, RESOLVED).
- Middle: the agent live stream. It polls `/api/events` every second and puts a divider between runs. Each agent has its own colored avatar. Reports show as speech bubbles, delegated tasks as dashed thought bubbles, and tool calls as chips with their arguments and an expandable MCP result. Human approvals appear in a purple "You" lane and system events in grey.
- Right: "Human decisions". Each approval card shows the amount, payee, why, reference, a warning if it waits on an unpaid invoice or if checking can't cover it, and "Checking after approval", with an "Approving as" name field and an Approve button. Below that: the approved list, the customer drafts marked DRAFT · NOT SENT, "Who did what" (each agent's report and tools), and the ticket outcome.

The header shows the desk date, the checking balance and a Reset desk button. The run button changes with the ticket's state: Dispatch agents, Approve requests first, Resume team to close, Run again. Layout and design choices are explained in `output/design.md`.

## Problem 9: Final run

The final run was on Oct 14, 2026. Times below are UTC; the board showed local time (UTC+8), so 03:54 here was 11:54 on screen. I approved every request on the board as Tongyu Chen.

| Time (UTC) | What happened | Checking |
| --- | --- | --- |
| 03:54:18 | Reset desk from the original database. | $3,400.00 |
| 03:55:11 to 03:55:40 | Ticket 101, first run (28,227 tokens). Boss → Inventory: 0 of 1 size S tees on hand; `find_restock_vendors` shows Bulldog Print Co (apparel reprint, 5-day lead) held by invoice 501. Inventory → Accounting: invoice 501 is $840, due 8/28, 3 days overdue; staged payment #1 ($840) and purchase order #2 (1 tee, $8, waiting on invoice 501). Boss set `waiting_approval`. | $3,400.00 |
| 03:56:21 | Approved #1: invoice 501 marked paid. | $2,560.00 |
| 03:56:22 | Approved #2: purchase order 1, 1 × CC-TEE-WHITE S from Bulldog Print Co, expected 2026-09-05. | $2,552.00 |
| 03:56:24 to 03:56:47 | Ticket 102, first run (20,419 tokens). Boss → Facilities: $2,400 due 9/2, 2 days away, no rent payment recorded. Facilities → Accounting: staged payment #3 ($2,400, `lease:1`). Boss set `waiting_approval`. | $2,552.00 |
| 03:56:50 | Approved #3: rent paid, lease 1 `next_due` moved to 2026-10-02. | $152.00 |
| 03:56:53 to 03:57:07 | Ticket 101, resume run (15,006 tokens). Boss saw both approvals were approved. Customer Service saved draft 1 to Tauhid: size S is out of stock and expected around 2026-09-05. Boss set `resolved`. | $152.00 |
| 03:57:09 to 03:57:15 | Ticket 102, resume run (6,598 tokens). Boss alone confirmed the approval and set `resolved`. | $152.00 |
| 03:57:16 to 03:57:42 | Ticket 103 (25,567 tokens). Boss → Inventory: 8 of 20 M hoodies, short 12. Boss → Accounting: 10% off is $52.20 each, 57.85% margin, $1,044 for 20, $604 gross profit; 15% off is $49.30 each, 55.38% margin, $986; checking is $152. Boss told both of them not to stage anything. Boss → Customer Service: draft 2 offers $52.20 each ($1,044 for 20), says 8 are available now and the other 12 depend on a restock order that hasn't been placed, and asks whether the club wants a split delivery or to wait. Boss set `resolved` with the 10% offer. | $152.00 |

End state: checking is $152.00 in `cash_accounts`, matching 3,400 → 2,560 → 2,552 → 152. The `payments` ledger has three rows, all dated 2026-08-31 and approved by Tongyu Chen: invoice 501 ($840), purchase order 1 ($8) and lease 1 ($2,400). All three tickets are resolved. The run logged no errors, and every audit record that names a model says `gpt-6-luna`. Total agent tokens: 95,817.

One gap against my plan: the revised Expected plan for 103 said Accounting would try the 12-hoodie order ($264) and the tool would refuse it because only $152 was left. In the actual run Boss told Inventory and Accounting not to stage anything, so nobody tried and the refusal never showed up on the board. `tests/test_approval_flow.py` covers it: after the same three approvals, `propose_purchase_order` for 12 hoodies is refused.

Evidence files:

- `output/resolved_tickets.json` is built by `scripts/export_run_evidence.py`. It reads the audit trail from the last reset onward and the working database, both read-only, so every agent sentence in it is the agent's own report.
- `output/ticket_101.png`, `output/ticket_102.png` and `output/ticket_103.png` are screenshots of the live React board, taken by `scripts/capture_board.py` (headless Edge or Chrome at `http://localhost:5173/?ticket=<id>` with the backend and Vite running). `output/resolved_board.html` shows the three screenshots.
- `output/audit_trail.json` holds every step of the run with tool arguments and results.

### Earlier runs still in the audit trail

The audit trail is append-only, so it still holds runs from before the final one. Early on Oct 9 local time (Oct 8, 19:05 to 19:13 UTC), the first version (built with Codex) ran the tickets from a script, `scripts/resolve_all.py`, which has since been deleted. The team stopped at `waiting_approval` or `waiting_customer`. The script then approved the two payments in-process as "Human Approval" (logged as `HumanApproval`) and forced all three tickets to resolved. The board was never used for that run, and those records have no tool arguments or results. On Oct 14, after the rebuild, I ran ticket 101 three times as a sanity check (resets at 03:29, 03:31 and 03:33 UTC). The first two stopped with an error because the code called `result.usage()`, but in pydantic-ai 2.x `usage` is a property. One of them had also run on the old process before uvicorn reloaded. The third worked, I clicked its approvals as "Sanity test", and the resume run closed the ticket. Then I reset the desk at 03:54 for the final run above. The board and the export script only read records after the last reset, so none of these older runs mix into the final evidence.
