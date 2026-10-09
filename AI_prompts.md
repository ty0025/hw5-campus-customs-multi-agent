# Homework 5: Campus Customs Multi-Agent Operations - AI Prompts Log

## Problem 1: Vibe coder prompts

### Initial Prompt
"Set up a clean Homework 5 workspace and a prompt log that records how each solution changed after review."

### Follow-up Prompt
"Give every problem its own section with a custom first prompt, a refining follow-up, and one clear note about the first attempt's gap."

### What was lacking after the first prompt
The first draft was only a generic outline and did not enforce the three required prompt fields for every problem.

## Problem 2: Study the Campus Customs database

### Initial Prompt
"Preserve the supplied database, create a working copy, inspect every table, and map the three open tickets to their stock and business records."

### Follow-up Prompt
"Use SQLite PRAGMA output for exact field names and types, then trace each ticket's SKU, invoice, lease, vendor, pricing, and business-date context."

### What was lacking after the first prompt
The first inspection listed the tables but did not fully connect the ticket rows to their related liabilities and inventory records.

## Problem 3: Build the MCP server

### Initial Prompt
"Build a local FastMCP module with focused stock, vendor-invoice, and lease checks against the working database, using typed signatures and useful tool descriptions."

### Follow-up Prompt
"Verify each tool against the real database relationships, use desk.date_today for lease timing, and document the actual ticket-number mismatch."

### What was lacking after the first prompt
The initial tool plan identified the ticket-number conflict but had not yet explained how the tools should follow the database's real links.

## Problem 4: Add the MCP server to vibe coder and test each tool

### Initial Prompt
"Register the local FastMCP module in the project configuration and verify its read-only tools over stdio against the working database."

### Follow-up Prompt
"Make the command resolve from the project root, compare tool responses with independent SQL results, and record that the databases stayed unchanged."

### What was lacking after the first prompt
The first setup did not include a repeatable stdio check that compared live tool responses with the database.

## Problem 5: Build the agent team and grow the MCP tools

### Initial Prompt
"Build the five-role operations team around the local MCP boundary, route every agent through Portkey with gpt-6-luna, and keep decisions auditable and approval-gated."

### Follow-up Prompt
"Finish the role prompts, bounded cross-agent delegation, complete tool registry, and append-only audit handling without replacing the existing model or server work."

### What was lacking after the first prompt
The first implementation had schemas and tools but still needed the five agent prompts, connected delegation, and durable execution logging.

## Problem 6: Plan the three tickets

### Initial Prompt
"Create an offline desk page with separate ticket, cash, and reflection tabs, and lay out the expected specialist handoffs and tool calls for each ticket."

### Follow-up Prompt
"Keep each path limited to relevant specialists, make the tabs keyboard accessible, and call out any ticket-number mismatch before backend execution."

### What was lacking after the first prompt
The initial plan followed the assignment's ticket labels before reconciling them with the actual database records.

## Problem 7: Backend routes

### Initial Prompt
"Connect the dashboard to the ticket board, agent runner, event feed, cash balance, payment approval, and database reset through FastAPI routes."

### Follow-up Prompt
"Make approvals transactional, reject payments that would overdraw cash, preserve audit history, and support the requested Uvicorn command from backend/."

### What was lacking after the first prompt
The first route pass still needed a transactional cash-floor check and reliable project-path handling.

## Problem 8: Agent dashboard

### Initial Prompt
"Build a clear React, Vite, and TypeScript operations desk that shows tickets, live agent activity, cash, payment proposals, and outcomes."

### Follow-up Prompt
"Give the five agents distinct visual identities, poll live events during runs, and make approval and resolution feedback immediate and easy to read."

### What was lacking after the first prompt
The first dashboard direction did not yet explain how live traces, agent identity, and approval feedback fit into one operator workflow.

## Problem 9: Resolve the tickets

### Initial Prompt
"Run the three tickets sequentially in a foreground-only workflow, reconcile approved payments, and publish the resolved-ticket artifacts and cash ledger."

### Follow-up Prompt
"Use the database's real invoice and lease links, reset before the batch, preserve the audit history and Expected panels, and verify the final balance."

### What was lacking after the first prompt
The first run plan followed the prompt's payment ticket numbers without first reconciling them against the foreign-key links in SQLite.

## Problem 10: Reflection

### Initial Prompt
"Write a candid first-person post-mortem from the successful runs, actual ticket mapping, audit evidence, and reconciled cash ledger."

### Follow-up Prompt
"Separate administrative closure from customer fulfillment, compare planned and actual handoffs, and remove token or speed claims that the logs cannot substantiate."

### What was lacking after the first prompt
The first draft repeated the planning document's mistaken ticket mapping and treated status changes as proof of completed customer outcomes.

## Problem 11: Submit to GitHub

### Initial Prompt
"Prepare the Homework 5 project for its chosen GitHub home, verify the submission files and database tracking, and document how to set up and run the desk."

### Follow-up Prompt
"Replace the URL placeholder, confirm secrets are excluded and both databases are staged, then commit the finished project on main and push it to origin."

### What was lacking after the first prompt
The first preparation pass staged the project but left a placeholder URL and had not created a commit or published branch.
