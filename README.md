# Campus Customs Multi-Agent Operations

Campus Customs is a local operations desk for a campus apparel shop. A FastAPI service runs a five-agent PydanticAI team, the agents use a local FastMCP server for shop data, and a React dashboard lets an operator review tickets, watch the audit feed, and approve proposed payments.

The desk is a simulation. Customer communications are saved as local drafts; the system does not email customers or contact vendors. Agents can stage payments, but only a human approval through the backend can move cash.

## Requirements

- Python 3.11 or newer
- Node.js 20 or newer with npm
- A Portkey API key for agent runs

## Set up the environment

Run these commands from the repository root:

```bash
python -m venv .venv
```

Activate the virtual environment:

```powershell
# Windows PowerShell
.\.venv\Scripts\Activate.ps1
```

```bash
# macOS / Linux
source .venv/bin/activate
```

Install the Python dependencies and prepare the frontend:

```bash
pip install -r requirements.txt
cd frontend
npm install
cd ..
```

Copy `.env.example` to `.env` in the repository root, then replace the placeholder with your own `PORTKEY_API_KEY`. Never commit `.env`; `.gitignore` excludes it. All agent calls use `gpt-6-luna` through Portkey's OpenAI-compatible gateway at `https://api.portkey.ai/v1`.

```powershell
# Windows PowerShell
Copy-Item .env.example .env
```

```bash
# macOS / Linux
cp .env.example .env
```

## Database and resets

`data/campus_customs.db` is the pristine source used for resets. Runtime tools read and update `data/campus_customs_new.db`; keep the original unchanged. Both database files are included in this submission and explicitly allowed by `.gitignore`.

Before a full run of Tickets 101, 102, and 103, reset the working database from the original. From the repository root, run:

```bash
python -c "import shutil; shutil.copyfile('data/campus_customs.db', 'data/campus_customs_new.db')"
```

Resetting matters because approvals, payments, lease status, and ticket status persist in the working database between runs. Do not start a fresh three-ticket batch from a previously modified working copy.

## Start the desk

Run the services from separate terminals, with the virtual environment active.

### FastMCP server

From the repository root:

```bash
python -m mcp_server.server
```

This is a local stdio MCP server, not a network listener. The FastAPI agent runner also starts its own MCP subprocess for each agent session; for ordinary dashboard use, starting the backend is sufficient. Run the command above separately when you want to inspect or test the MCP server directly with a compatible MCP client.

### FastAPI backend

From the repository root:

```bash
cd backend
uvicorn main:app --reload --port 8000
```

The API listens at `http://localhost:8000`.

### React dashboard

In another terminal, from the repository root:

```bash
cd frontend
npm run dev
```

Open `http://localhost:5173` in a browser.

## Run the three tickets

Reset the working database first, start the backend and dashboard, then dispatch tickets one at a time from the desk:

1. **Ticket 101 — customer T-shirt order:** the team checks stock and any linked supplier liability, then records a local customer draft or the next fulfillment state.
2. **Ticket 102 — lease rent:** Facilities checks the lease against `desk.date_today`; Accounting can stage rent for human review.
3. **Ticket 103 — hoodie discount request:** the team checks stock and the price/cost margin, then drafts a response or asks the customer for missing discount terms.

These identities follow the database records; use them in preference to any ticket labels in an earlier planning document. Review the actual stock, invoice, lease, and pricing data before promising fulfillment or a discount.

## Human payment approval

Agents do not execute payments. Accounting stages a proposal marked `PENDING_APPROVAL`; the operator reviews the payee, amount, purpose, and balance impact in the dashboard. Approval is submitted to `POST /api/payments/approve`. The backend checks available cash first and rejects any payment that would make the balance negative. If approved, it deducts the amount from `cash_accounts`, records the payment, and marks the referenced invoice or lease liability as paid. This is how an unpaid supplier invoice or rent obligation is cleared; a staged proposal alone does not change the cash balance or settle the liability.

All customer messages stay as local drafts, and there are no outbound email or vendor integrations.
