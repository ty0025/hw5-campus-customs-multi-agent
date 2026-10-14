# Campus Customs Multi-Agent Operations

Campus Customs is a small campus apparel shop. This app is its operations desk, built for Yale MGT 409 Homework 5. It has three parts:

- a FastMCP server (`mcp_server/server.py`) with 11 tools over the shop database `data/campus_customs_new.db`
- a FastAPI backend (`backend/main.py`) that runs a five-agent PydanticAI team (Boss, Inventory, Accounting, Facilities, Customer Service). Every agent uses `gpt-6-luna` through Portkey, and any agent can hand work to any other.
- a React + Vite + TypeScript board (`frontend/`) where you watch the agents work a ticket and approve any money they want to spend

Only Accounting can stage a payment or purchase order, and staging moves no money. Cash moves only when a person clicks Approve on the board, and the approve route refuses anything checking can't cover. Customer messages are saved as drafts. Nothing is emailed or sent to a vendor.

## What you need

- Python 3.12 or newer
- Node.js `^20.19` or `>=22.12`, with npm
- A Portkey API key (only needed to run the agents; the tests and the MCP server don't use it)

## Setup

From the repo root:

```bash
python -m venv .venv
```

Activate it:

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

```bash
# macOS / Linux
source .venv/bin/activate
```

Install the Python packages:

```bash
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and set `PORTKEY_API_KEY` to your key (`Copy-Item .env.example .env` on Windows, `cp .env.example .env` elsewhere). `backend/app/settings.py` reads `.env` from the repo root first and then from the folder one level up, so a shared `.env` there also works. `.env` is git-ignored. The model name (`gpt-6-luna`) and the gateway (`https://api.portkey.ai/v1`) are fixed in `settings.py`.

## Clean working copy

`data/campus_customs.db` is the original and is never changed. Everything reads and writes `data/campus_customs_new.db`. Before a full run, put a fresh copy in place:

```powershell
# Windows PowerShell
Copy-Item data\campus_customs.db data\campus_customs_new.db -Force
```

```bash
# macOS / Linux
cp data/campus_customs.db data/campus_customs_new.db
```

Or, with the backend running, click **Reset desk** on the board (or `POST http://localhost:8000/api/database/reset`). I use the button: besides copying the file, it writes a `database_reset` record to `output/audit_trail.json`. The board's live stream and `scripts/export_run_evidence.py` only look at events after the last reset record, so a manual file copy leaves the previous run's events on screen.

## Start the MCP server (on its own)

You don't need this for the board. The backend starts the MCP server itself as a stdio subprocess for each ticket run, and Claude Code starts it from `.mcp.json` when the project is opened in this folder. To start it by hand from the repo root:

```powershell
# Windows (the "py" launcher, as in .mcp.json)
py -m mcp_server.server
```

```bash
# macOS / Linux
python3 -m mcp_server.server
```

It is a stdio server: it opens no port, prints nothing and waits for an MCP client. Stop it with Ctrl+C. When started this way it re-launches itself with `.venv`'s Python if that folder exists. On macOS/Linux, change `"command": "py"` in `.mcp.json` to `"python3"`. More detail is in `mcp_server/README.md`.

## Start the backend

In a terminal with the venv active:

```bash
cd backend
uvicorn main:app --reload --port 8000
```

The API is at `http://localhost:8000` and only allows the board's origin (`http://localhost:5173`) through CORS.

| Route | What it does |
| --- | --- |
| `GET /api/tickets` | Tickets 101-103 with status and how many approvals are pending |
| `POST /api/tickets/{ticket_id}/run` | Runs the agent team on one ticket |
| `GET /api/events` | Audit events for the board (`?ticket_id=`, `since_reset=true` by default) |
| `GET /api/approvals` | Payment and purchase-order requests and their decisions |
| `POST /api/approvals/{approval_id}/approve` | Body `{"approved_by": "..."}`. The only route that moves cash |
| `GET /api/drafts` | Customer drafts (never sent) |
| `GET /api/cash/balance` | Checking balance and the desk date |
| `POST /api/database/reset` | Copies the original database over the working copy and logs the reset |

## Start the board

In a second terminal:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`. Adding `?ticket=102` to the URL opens that ticket.

## A full three-ticket run

1. Click **Reset desk**. Checking starts at $3,400.00.
2. Select CC-101 (T-shirt order) and click **Dispatch agents**. Accounting stages payment #1 ($840 to Bulldog Print Co for overdue invoice 501) and purchase order #2 ($8 for one size S tee). The ticket shows NEEDS APPROVAL.
3. Type your name in "Approving as". Approve the $840 payment first, then the $8 order. The order card warns that it waits on invoice 501, and the backend refuses it until that invoice is paid.
4. Click **Resume team to close**. Customer Service saves a draft to the customer and Boss resolves the ticket.
5. Select CC-102 (rent), click **Dispatch agents**, approve the $2,400 lease payment, then click **Resume team to close**.
6. Select CC-103 (bulk hoodie discount) and click **Dispatch agents**. The team prices the order and saves a draft offer. No money moves.

Checking should end at $152.00 ($3,400 - $840 - $8 - $2,400). The agents' wording changes from run to run. On ticket 103, if Accounting tries to stage the 12-hoodie restock ($264), `propose_purchase_order` refuses it because only $152 is left.

## Tests

From the repo root (no AI calls, no server needed):

```bash
# macOS / Linux
PYTHONPATH=. python -m unittest discover tests
```

```powershell
# Windows PowerShell
$env:PYTHONPATH = "."; python -m unittest discover tests
```

`tests/test_approval_flow.py` walks the money path for all three tickets on a temporary copy of the original database, including the refusal when checking is too low. `tests/test_mcp_server.py` checks the tools and that neither database file changes. The MCP tests that need the reset-state invoice and rent (vendor 1 held by invoice 501, rent due in 2 days, the structured tool calls) run on a temporary copy of `data/campus_customs.db`. The other tests only read stock and vendors, which the final run did not change. So all 17 tests pass on a fresh reset and on the working copy as submitted, and the suite never writes to either database file.

## Helper scripts

- `python scripts/export_run_evidence.py` rebuilds `output/resolved_tickets.json` from the audit trail (everything after the last reset) and the working database. It only reads.
- `python scripts/capture_board.py` saves `output/ticket_101.png` to `ticket_103.png` from the live board using headless Edge or Chrome. The backend and `npm run dev` must be running.
- `study_database.py` is the Problem 2 script. It resets the working copy and rewrites `output/harness.md` from scratch, so don't run it on the finished repo.

## Project layout

```
.mcp.json                  MCP registration (campus_customs_mcp)
mcp_server/server.py       FastMCP server, 11 tools
backend/main.py            FastAPI routes, approvals, reset
backend/agent_team.py      the five agents, their limits, the MCP client
backend/prompts/           one prompt file per agent
backend/models.py          AgentResult and the other shared models
backend/audit.py           writes output/audit_trail.json
backend/app/settings.py    .env loading, Portkey gateway, model name
frontend/src/App.tsx       the board
frontend/src/style.css     board styles
data/campus_customs.db     original database (reset source)
data/campus_customs_new.db working copy
tests/                     unit tests
scripts/                   helper scripts
AI_prompts.md              prompts I used, problem by problem
```

## Outputs

All in `output/`:

- `harness.md`: tables, MCP tools, agents, routes, board and safety notes
- `design.md`: how the board is laid out and why
- `desk_tickets.html`: tabs for tickets 101, 102 and 103 (Expected and Actual), a Cash tab and a Reflection tab
- `resolved_tickets.json`: the final run, built by `export_run_evidence.py`
- `resolved_board.html`: a screenshot of the board for each resolved ticket (`ticket_101.png` to `ticket_103.png`)
- `audit_trail.json`: every agent step, tool call (arguments and result), approval and reset, as one append-only JSON array. The final run is everything after the last `database_reset` record.
- `mcp_smoke.json`: the Problem 4 check from Claude Code through `.mcp.json` (one call each to the three Problem 3 tools). It was taken after the final run, so it shows invoice 501 paid and the rent next due 2026-10-02.
- `github_url.txt`: the repo link
