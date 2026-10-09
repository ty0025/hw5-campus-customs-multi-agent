# Campus Customs MCP Server

This module gives Boss, Inventory, Accounting, Facilities, and Customer Service nine shared, typed FastMCP tools for reading shop facts and performing the limited local board writes needed for a decision.
It uses `FastMCP` from `mcp.server.fastmcp` and exports the registered server object as `mcp_server.server.mcp`.
Problem 3 defined the tools, and Problem 4 adds a local stdio entry point for MCP clients; importing the module still starts no transport.
No HTTP endpoint or network listener is configured.

## Database and boundaries

- The only tool database is `data/campus_customs_new.db`, resolved from the Homework 5 project root using the module's location, independently of the current working directory.
- The path has no environment override or fallback to another database, and a missing working copy produces an error instead of creating a blank file.
- Read tools open SQLite with `mode=ro` and `PRAGMA query_only = ON`; the three explicitly scoped board/proposal writes use bound SQL parameters and an atomic transaction against the same fixed working file.
- Business-date comparisons use the single `desk.date_today` value; an absent or invalid value produces an error instead of using the computer's date.
- These tools never execute payments, place orders, reserve stock, reset the database, send messages, or make AI calls. They may update ticket status/notes, stage a pending approval, and save a local customer draft.
- Subsequent agent AI calls must use only `gpt-6-luna` through Portkey; this MCP module does not load API credentials.

## Ticket numbering correction

The supplied Q3 labels its vendor tool as Ticket 102 and its lease tool as Ticket 103, but the actual database contains Ticket 101 (customer T-shirt order), Ticket 102 (rent notice), and Ticket 103 (bulk hoodie discount).
Following the user's clarification, documentation uses the actual database relationships and records that discrepancy explicitly.
The vendor tool supports Ticket 101 through its linked invoice and Ticket 103 when evaluating a candidate supplier; the lease tool supports Ticket 102.
The hoodie request still needs a proposed discount and a margin decision in later problems; these three tools provide the requested facts without claiming to resolve the entire ticket.

## Tool contracts

All nine tools include typed input and return contracts; read tools are annotated read-only, while the three local board writes are explicitly annotated as non-destructive local operations without external interactions.
Python return values are Pydantic models; MCP serializes them to structured JSON, with dates represented as ISO `YYYY-MM-DD` strings.
Positive integer inputs reject zero, negatives, booleans, fractions, and numeric strings.
Invalid input or a missing requested record raises `ValueError` in direct Python calls and is surfaced as an MCP tool error through FastMCP.

### `check_inventory_stock(sku: str, size: str, order_quantity: int) -> InventoryStockResult`

Compares the inventory row for a SKU and size with the requested order quantity; this checks Ticket 101's T-shirt availability and Ticket 103's bulk hoodie shortfall.

| Parameter | Type | Meaning |
| --- | --- | --- |
| `sku` | nonempty string | Exact product SKU after trimming and uppercasing. |
| `size` | nonempty string | Exact size after trimming and uppercasing. |
| `order_quantity` | positive integer | Number of units requested. |

Returns `sku`, `size`, `product_name`, and `location` as strings; `order_quantity`, `on_hand`, and `shortfall` as integers; and `can_fulfill` as a boolean.
`shortfall` is the uncovered quantity, never below zero, while an unknown SKU/size produces an error rather than fabricated zero stock.
This reads `inventory` without allocating or decrementing stock.

### `check_vendor_restock_eligibility(vendor_id: int) -> VendorRestockResult`

Checks `vendors` and its related `invoices`, using `desk.date_today` for overdue information, to establish whether unpaid invoices block that vendor's new shipments.

| Parameter | Type | Meaning |
| --- | --- | --- |
| `vendor_id` | positive integer | An existing `vendors.id`, obtained from an invoice or a selected candidate. |

Returns `vendor_id` and `lead_days` as integers; `vendor_name` and `specialty` as strings; `shop_date` as a date; `total_unpaid` as a number; `blocked_by_unpaid_invoices` as a boolean; and `unpaid_invoices` as a list of `UnpaidInvoice` objects.
Each invoice contains `invoice_id: int`, `amount: float`, `due_date: date`, `status: str`, `description: str | None`, `overdue: bool`, and `days_overdue: int`.
An empty invoice list and zero total mean the existing vendor has no blocking invoices; an unknown vendor instead produces an error.

Status handling is explicit: `open`, `unpaid`, `overdue`, and `pending` block shipment even before their due date; `paid`, `settled`, `closed`, `void`, `cancelled`, and `canceled` do not block; any other status produces an error requiring clarification.
The supplied initial database uses `open`.
Vendor specialty and lead time are database facts, but invoice clearance does not establish that the vendor supplies a particular SKU or guarantee arrival timing.

### `get_lease_due_status(lease_id: int) -> LeaseDueResult`

Reads `leases` and `desk` to classify Ticket 102's next recorded rent obligation using the shop's business date.

| Parameter | Type | Meaning |
| --- | --- | --- |
| `lease_id` | positive integer | An existing `leases.id`, normally taken from the rent ticket. |

Returns `lease_id: int`, `space_name: str`, `landlord: str`, `monthly_rent: float`, `next_due: date`, `shop_date: date`, `days_until_due: int`, `notes: str | None`, and `status: Literal["overdue", "due_today", "upcoming"]`.
`days_until_due = next_due - shop_date`: a negative value means overdue, zero means due today, and a positive value means upcoming.
This describes the next recorded liability and does not approve, execute, or assert a payment.

## Local dependency and verification commands

Use Python 3.10 or later and install the dependencies from the Homework 5 root:

```powershell
python -m pip install -r mcp_server/requirements.txt
```

An isolated environment is available at `hw5/.venv`; run the acceptance checks from `hw5` with:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_mcp_server.py -v
```

The suite checks the submitted working copy and uses an isolated copy of the pristine database when it needs the original unpaid-invoice state. It verifies tool schemas and error handling, hashes both submitted databases before and after, and does not invent shop records or start a transport.

## MCP client registration (Problem 4)

The Homework 5 root contains `.mcp.json` with the requested `campus_customs_mcp` registration, command `python`, arguments `["-m", "mcp_server.server"]`, and `PYTHONPATH` set to `.`.
Open or configure the MCP client with `hw5` as its project/working directory because the supplied `PYTHONPATH` is relative to that root.
A client supporting `.mcp.json` can then start the module over stdio; the MCP client owns the server process lifetime.
Creating this file does not establish that a particular editor has already imported or enabled the registration.

When executed as a module, the server first uses `hw5/.venv` if available so plain `python` can find the tested project dependencies without a global package installation.
If there is no project virtual environment, the invoking Python must have `mcp_server/requirements.txt` installed.
The stdio entry point does not print non-protocol text to stdout, load credentials, or make AI requests.

Run the real MCP protocol verification from `hw5`:

```powershell
.\.venv\Scripts\python.exe -B tests/verify_mcp_stdio.py
```

This loads the exact `.mcp.json` registration, starts its command in a child process, initializes an MCP session, discovers all nine tools, and verifies returned JSON against independent read-only SQL queries.
It checks rejected inputs, verifies both database hashes, closes the child session, and writes `output/mcp_tool_test_results.json` plus the live-query snapshot `output/mcp_smoke.json`.

## Problem 5 extension: complete tool registry

The initial three tool descriptions above remain applicable. The complete server now exposes nine tools; this registry supersedes the earlier statements that the server is read-only or registers only three tools. All tools reach only `data/campus_customs_new.db`, resolved from the Homework 5 project root.

| Tool and parameters | Return type | SQLite tables accessed | Effect |
| --- | --- | --- | --- |
| `check_inventory_stock(sku: str, size: str, order_quantity: int)` | `InventoryStockResult` | `inventory` | Read only; returns product/location, on-hand quantity, shortfall, and fulfillment flag. |
| `check_vendor_restock_eligibility(vendor_id: int)` | `VendorRestockResult` | `vendors`, `invoices`, `desk` | Read only; returns vendor specialty/lead time and unpaid invoice blockers dated against `desk.date_today`. |
| `get_lease_due_status(lease_id: int)` | `LeaseDueResult` | `leases`, `desk` | Read only; returns the next rent liability and due classification against the shop date. |
| `get_ticket_details(ticket_id: int)` | `TicketDetailsResult` | `tickets`, `desk`; when `invoice_id` is present, also `invoices`, `vendors` | Read only; maps stored requester/notes into agent-facing fields, includes the shop date, and returns the vendor linked to the ticket's actual invoice reference. |
| `update_ticket_status(ticket_id: int, status: str, resolution: str)` | `TicketStatusResult` | `tickets` | Writes only the ticket status and appends a resolution note to `tickets.notes`; it never changes payment records. |
| `get_cash_balance(account_name: str = "Operating Cash")` | `CashBalanceResult` | `cash_accounts` | Read only; returns the recorded account name, balance, and account date. |
| `propose_payment(amount: float, recipient: str, reason: str, ref_id: str | None)` | `PaymentProposalResult` | `cash_accounts`; for a reference, `invoices` or `leases`, `tickets`, and `approvals` | Checks available cash and obligation identity, then stages a referenced request in `approvals` with database state `pending` and returned state `PENDING_APPROVAL`; it never inserts into `payments`. Without `ref_id`, it returns an unstaged proposal payload. |
| `save_customer_draft(ticket_id: int, draft_body: str)` | `CustomerDraftResult` | `tickets`, `customer_drafts` | Reads recipient/subject and saves a local draft with `sent=false`; no outbound service is called. |
| `get_pricing_and_margin(sku: str)` | `PricingAndMarginResult` | `pricing` | Read only; returns recorded cost/list price and calculated unit gross margin. |

All tool inputs are typed and validated. The three local writes are `update_ticket_status`, `propose_payment`, and `save_customer_draft`; read operations use SQLite query-only mode. The proposal tool checks cash after other pending proposals before staging a new reference and refuses amounts that would take available cash below zero. Only the human approval flow may post an approved disbursement to `payments`.

The five PydanticAI agents are implemented in `backend/agent_team.py` and use an MCP stdio subprocess for all shop facts. The local server process receives no Portkey credential. Only the API process reads `PORTKEY_API_KEY` from the configured environment and makes agent requests through the Portkey gateway.

## Local agent API

From the Homework 5 root, install `backend/requirements.txt` and start `uvicorn backend.agent_api:app --reload`. The `POST /api/tickets/{ticket_id}/run` route runs the Boss-led workflow, opens the local MCP stdio server, and returns a `TicketRunResponse`. The agent route does not send customer or vendor communications and does not execute payments.
