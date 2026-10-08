# Homework 5: Campus Customs Multi-Agent Operations - AI Prompts Log

## Problem 1: Vibe coder prompts

### Initial Prompt
"Set up the baseline project repository for Homework 5 and create an `AI_prompts.md` file to record prompt iterations across all tasks. Ensure the template captures the problem title, initial custom prompt, follow-up prompt, and an evaluation of what was missing in the initial run."

### Follow-up Prompt
"Make sure each problem section has a dedicated heading and an explicit one-sentence analysis of why the first response was incomplete, especially ensuring we don't dump raw assignment text into the log."

### What was lacking after the first prompt
The initial output created a generic markdown skeleton without enforcing the specific three-part schema (initial prompt, follow-up prompt, and single-sentence post-run deficiency diagnosis) required by the grading rubric.

## Problem 2: Study the Campus Customs database

### Initial Prompt
"Inspect the Campus Customs database, preserve it by working from a copy, list every table's exact SQLite fields and types, and explain how the three open tickets connect to stock and business records."

### Follow-up Prompt
"Resolve each open ticket's invoice and lease references, SKU/size stock, pricing, vendor lead time, and unpaid invoice status against desk.date_today, and note any missing request detail."

### What was lacking after the first prompt
The first pass captured ticket rows and schemas but did not yet resolve their invoice, lease, stock, vendor, pricing, and business-date context.

## Problem 3: Build the MCP server

### Initial Prompt
> 接下来我们来做Q3：
>
> Implement Problem 3 ("Build the MCP server") for Homework 5.

These are the user's exact opening instructions; the accompanying full assignment specification is omitted from this log.

### Follow-up Prompt
"按数据库真实关联标注，并注明题面编号冲突（推荐）"

### What was lacking after the first prompt
The initial pass identified the ticket-number conflict but had not yet settled how the tool documentation should map to the actual database.

## Problem 4: Add the MCP server to vibe coder and test each tool

### Initial Prompt
> Implement Problem 4 ("Add the MCP server to vibe coder and test each tool") for Homework 5.
>
> Execute the following steps programmatically and verify all outputs against the database:

These are the user's exact opening instructions; the supplied configuration specification is omitted from this log.

### Follow-up Prompt
No separate follow-up prompt was issued; the configuration, startup fixes, and verification were completed from the initial request.

### What was lacking after the first prompt
The first inspection found no stdio entry point and no MCP dependency in the default interpreter, so startup and project-environment handling were needed before the supplied configuration could work.

## Problem 5: Build the agent team and grow the MCP tools

### Initial Prompt
"Implement a five-agent Campus Customs team that uses the local MCP server for shop facts, routes through Portkey with the approved model, respects human approval and draft-only rules, and keeps an append-only execution history."

### Follow-up Prompt
"Resume without replacing the existing model and server files: validate them, finish the role prompts and bounded cross-agent delegation, then document the full tool suite and safety rules."

### What was lacking after the first prompt
The first pass added typed schemas and MCP operations but did not yet supply the five role prompts, connected agent orchestration, persistent audit logger, or complete team documentation.

## Problem 6: Plan the three tickets

### Initial Prompt
"Create an offline-ready operations desk for tickets 101–103 that shows each ticket's expected agent handoffs and MCP actions, while leaving the Cash and Reflection tabs for later problems."

### Follow-up Prompt
"Make the five tabs keyboard-accessible, keep each ticket's delegation path limited to relevant specialists, and clearly flag any mismatch between the requested ticket pathways and the database mapping."

### What was lacking after the first prompt
The initial prompt did not resolve the mismatch between the requested ticket pathways and the database's ticket-number mapping, so the dashboard needed an explicit reconciliation note before backend wiring.
*** End of File

## Problem 7: Backend routes

### Initial Prompt
"Implement the FastAPI layer in `backend/main.py` to serve the ticket board, launch the existing agent team, expose recent audit events, report checking cash, approve staged payments safely, and restore the working database from its pristine copy. Keep agent runs locked to `gpt-6-luna` through Portkey and make the app start with `uvicorn main:app` from `backend/`."

### Follow-up Prompt
"Harden payment approval with a transactional cash-floor check and matching staged-payment/liability validation, preserve the audit trail across runs, and make the database and agent imports resolve correctly when Uvicorn starts from the backend directory."

### What was lacking after the first prompt
The initial implementation still needed explicit transactional safeguards for payment finalization and reliable project-root resolution for the requested startup command.
## Problem 8: Agent dashboard

### Initial Prompt
"Build a polished Campus Customs operator dashboard in `frontend/` with React, Vite and TypeScript, connected to the FastAPI service for tickets, agent activity, cash balance, approvals, and reset. Give the workspace a clear physical-desk layout that makes the five specialists, payment decisions, and ticket outcomes easy to follow."

### Follow-up Prompt
"Refine the dashboard with distinct colors and identities for each specialist, live event polling while a ticket runs, a human approval card tied to staged-payment details, responsive three-pane ergonomics, and immediate balance and resolution feedback after actions."

### What was lacking after the first prompt
The initial dashboard direction did not yet define how live traces, agent-specific identity, and payment approval feedback should fit into one operator workflow.

## Problem 9: Resolve the tickets

### Initial Prompt
"Build a foreground-only Q9 runner that resets the working Campus Customs database, runs the multi-agent team on tickets 101–103 in order, handles approved payments, reconciles cash, and publishes the resolved ticket and board artifacts."

### Follow-up Prompt
"Reconcile each ticket against its actual invoice and lease references before approving any payment; keep the original database untouched, preserve audit history and the dashboard's Expected sections, and report any prompt-to-database numbering conflict instead of inventing a relationship."

### What was lacking after the first prompt
The initial run plan needed a ticket-to-liability check because the prompt's payment ticket numbers conflict with the foreign-key relationships stored in SQLite.
## Problem 10: Reflection

### Initial Prompt
"Use the completed run record, actual database ticket identities, and reconciled cash ledger to write a candid, first-person engineering post-mortem with per-ticket grades, plan-versus-run findings, delegation tradeoffs, and examples of work the current tools can and cannot support."

### Follow-up Prompt
"Keep the tone direct and conversational, but verify every ticket and payment claim against the evidence, remove unmeasured token or speed claims, and distinguish an approved payment from actual fulfillment and a resolved status from a resolved customer request."

### What was lacking after the first prompt
The initial draft repeated the planning document's mistaken ticket mapping and treated administrative status changes as proof of completed customer outcomes.
## Problem 11: Submit to GitHub

### Initial Prompt
"Prepare Homework 5 for its confirmed GitHub destination, check the required prompt names, database tracking, secret exclusions, and setup files, then record the URL and publish the finished submission."

### Follow-up Prompt
"Verify both database files are staged, confirm the README documents the reset and three-ticket workflow, then commit the prepared tree on main and push it to the requested origin."

### What was lacking after the first prompt
The first preparation pass left a placeholder URL and no remote, commit, or published branch, so the submission was staged but not yet available at its intended GitHub destination.
