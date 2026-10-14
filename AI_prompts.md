# Homework 5: Campus Customs Multi-Agent Operations - AI Prompts Log

I used two vibe coders for this homework. I built Problems 1-11 with Codex in VS Code on Oct 8-9, and just after midnight on Oct 14 I had Codex check the work against every requirement. Later on Oct 14 I used Claude Code to work out why the results looked off, rebuild the agent run and do one final run on the board. Dates are local time (UTC+8).

## Problem 1: Vibe coder prompts

### My prompt (Codex, Oct 8)
> I'm going to send you a lot of questions, one at a time, so don't rush into anything yet.
>
> Let's do Question 1:
>
> Create the file `AI_prompts.md` in the project root to track development interactions throughout Homework 5, and initialize it with the entry for Problem 1.

The rest of the prompt asked for one section per problem with the first prompt, a follow-up and one sentence on what was missing, plus a starting entry for Problem 1.

### Follow-up (Claude Code, Oct 14)
> Before you start changing anything, I need you to help me answer this. Is gpt-6-luna the only model allowed this time, so I'd lose points if you use Claude Code? Or is there a way you can avoid this problem?

It answered that the rule covers the model the agents call, which stays gpt-6-luna through Portkey, so using Claude Code as the vibe coder was fine.

### What was missing after the first try
The first version of this file never said which vibe coder I was using, and once I brought in Claude Code I also had to check that switching tools did not break the gpt-6-luna rule.

## Problem 2: Study the Campus Customs database

### My prompt (Codex, Oct 8)
> This time the homework goes in the hw5 folder. Unzip the data for me first.

Then:

> Implement Problem 2 ("Study the Campus Customs database") for Homework 5.
>
> Execute the following steps programmatically:

The rest of the prompt said to copy `campus_customs.db` to `campus_customs_new.db`, read every table with `PRAGMA table_info`, look at the three open tickets, and write `output/harness.md` with each table's fields and types plus one sentence on why the agents need it.

### Follow-up (Codex, Oct 8)
In a new session, after the first one kept hitting errors:

> This time the homework goes in the hw5 folder, and it's already unzipped. I'll have you help me check the background material and Questions 1 and 2, and once those are fine we'll keep going.

### What was missing after the first try
The first `harness.md` matched the database, but there was no script to re-run the check, and it didn't say that the hoodie vendor is only a guess from `vendors.specialty` (no table links a SKU to a vendor).

## Problem 3: Build the MCP server

### My prompt (Codex, Oct 8)
> Next, let's do Q3:
>
> Implement Problem 3 ("Build the MCP server") for Homework 5.
>
> Follow these exact architectural requirements:

The rest of the prompt asked for a FastMCP server in `mcp_server/` on `data/campus_customs_new.db` with three tools, one per open ticket, and labeled the vendor-invoice tool "for Ticket 102 - Vendor Restock & Invoice Clearance".

### Follow-up (Codex, Oct 8)
Codex pointed out that in the database 102 is the rent notice and 103 is the hoodie discount, and asked how to label the tools in `harness.md`. I picked the option it had marked as recommended:

> Label them by the actual links in the database, and note the numbering conflict with the question (recommended).

### What was missing after the first try
My brief tied the vendor-invoice tool to ticket 102, but in the database 102 is the rent notice and unpaid invoice 501 belongs to ticket 101, so the labels had to follow the database rather than my brief.

## Problem 4: Add the MCP server to vibe coder and test each tool

### My prompt (Codex, Oct 8)
> Implement Problem 4 ("Add the MCP server to vibe coder and test each tool") for Homework 5.
>
> Execute the following steps programmatically and verify all outputs against the database:

The rest of the prompt gave the `.mcp.json` to create (server `campus_customs_mcp`, command `python`, args `-m mcp_server.server`, `PYTHONPATH=.`).

### Follow-up (Claude Code, Oct 14)
On Oct 14 I chose to connect the server from Claude Code ("As you said, using Claude Code doesn't affect anything, so use Claude Code"). When it listed steps for me to do by hand, I replied:

> I don't get what I'm supposed to do here. Can you just open a new conversation and do it for me?

### What was missing after the first try
The server had no stdio entry point and the default `python` had no `mcp` package, so `server.py` was set to relaunch itself inside `.venv`; on Oct 14 `python` on my PC turned out to be the Microsoft Store stub, so `.mcp.json` now uses the `py` launcher.

## Problem 5: Build the agent team and grow the MCP tools

### My prompt (Codex, Oct 8)
> Implement Problem 5 ("Build the agent team and grow the MCP tools", 15 points) for Homework 5.
>
> Construct the complete PydanticAI multi-agent operations team, expand the MCP server tools, wire the execution audit logger, and update all required documentation according to the exact specifications below:

The rest of the prompt covered the Portkey setup with gpt-6-luna, the Pydantic models, the five agents, delegation between them and the audit log.

### Follow-up (Codex, Oct 8)
After the first run was cut off:

> For Q5:
>
> Resume and complete the remaining deliverables for Problem 5 ("Build the agent team and grow the MCP tools") after the previous execution interruption.
>
> Do not wipe or discard the existing edits in `hw5/backend/models.py` and `hw5/mcp_server/server.py`. Inspect and validate them first, then proceed with the pending tasks below:

The rest listed the remaining steps, starting with checking those two files and writing the five agent prompts in `backend/prompts/`.

On Oct 14 the diagnosis in Problem 9 found that the agent prompts hard-coded what each ticket was, the audit trail kept no tool arguments or results, and the pricing tool couldn't take a quantity or a discount, so the team and tools were rebuilt.

### What was missing after the first try
The first pass stopped after `models.py` and the MCP tools, with no agent prompts, no delegation between agents and no audit logger.

## Problem 6: Plan the three tickets

### My prompt (Codex, Oct 8)
> Implement Problem 6 ("Plan the three tickets", 8 points) for Homework 5.
>
> Construct a standalone, interactive HTML dashboard at `output/desk_tickets.html` and document the expected operational pathways for open tickets 101, 102, and 103 before backend wiring.

The rest of the prompt described the five tabs and gave a plan for each ticket, using the same ticket labels as my Q3 brief.

### Follow-up (Codex, Oct 8)
After I went back to finish Q5:

> Finish Q6.

### What was missing after the first try
The Expected plan followed my brief, so it put a vendor-restock plan under ticket 102 and a lease plan under 103; it was rewritten before the final run on Oct 14, and the original is kept in a collapsed block on each tab.

## Problem 7: Backend routes

### My prompt (Codex, Oct 8)
> Implement Problem 7 ("Backend routes", 7 points) for Homework 5.
>
> Build the FastAPI backend in `backend/main.py` to bridge the React dashboard, the multi-agent system, and the database.

The rest of the prompt listed six routes: tickets, run, events, payment approval, cash balance and reset.

### Follow-up (Codex, Oct 14)
Just after midnight on Oct 14 I asked Codex to check whether every requirement from the first question to the last was done. When it reported gaps that would cost points, I wrote:

> If you've already found the grading gaps, then fix them.

### What was missing after the first try
The only approve route handled payments, so purchase orders had no approval path and drafts had no route; Codex added `GET /api/approvals` and a purchase-order route on Oct 14, and the rebuild merged both into `POST /api/approvals/{approval_id}/approve` and added `GET /api/drafts`.

## Problem 8: Agent dashboard

### My prompt (Codex, Oct 8)
> Implement Problem 8 ("Agent dashboard", 12 points) for Homework 5.
>
> Build a polished, highly usable React + Vite + TypeScript dashboard in `frontend/` that connects to the FastAPI backend at `http://localhost:8000`.

The rest of the prompt covered the Vite setup and the parts of the board, starting with a header that shows the live checking balance.

### Follow-up (Claude Code, Oct 14)
After the diagnosis in Problem 9, Claude Code asked how far to go with the fixes. My answer:

> Fix everything and rerun once, but don't use too many tokens. Cut down on repeated testing; it just needs to run smoothly.

### What was missing after the first try
The board had no live agent text, no drafts card and no way to resume a ticket after approval, and it was never used for the Oct 9 run because my Q9 brief ran everything from a script.

## Problem 9: Resolve the tickets

### My prompt (Codex, Oct 9)
> Implement Problem 9 ("Resolve the tickets", 10 points) for Homework 5.
>
> CRITICAL ENVIRONMENT INSTRUCTION:
> Do NOT launch background or persistent server processes (such as `npm run dev` or `uvicorn main:app --reload`) in the terminal. Execute all resolution steps, multi-agent calls, and validations synchronously via a standalone, single-run Python automation script (e.g., `scripts/resolve_all.py`).

The rest of the prompt had the script reset the database, record the starting cash and run the team on tickets 101, 102 and 103 in order, approvals included.

### Follow-up (Claude Code, Oct 14)
> I need you to help me check how this homework turned out. It's finished, but the results seem a bit off. Diagnose it first, and then we'll look at how to fix it.

After the diagnosis I said yes when Claude Code asked whether to place a restock order for the one tee on ticket 101. In the final run on Oct 14 the desk was reset, each ticket was run from the board, and the three requests were approved there under my name ($840 for invoice 501, $8 for one size S tee, $2,400 rent). All three tickets ended resolved and checking went from $3,400.00 to $152.00.

### What was missing after the first try
The script approved the payments itself as "Human Approval" and forced all three tickets to resolved, so the board was never used, and the audit trail kept no tool arguments or results.

## Problem 10: Reflection

### My prompt (Codex, Oct 9)
> Stop running it for now, let's move on to the next question.
>
> Implement Problem 10 ("Reflection", 10 points) for Homework 5.
>
> Populate the `Reflection` tab inside `output/desk_tickets.html` with an exhaustive, highly critical, first-person operational post-mortem. Embed concrete empirical evidence directly referencing Tickets 101, 102, 103, and the itemized Cash ledger. Update `AI_prompts.md` accordingly.

The rest of the prompt was the reflection in HTML, to go into the tab.

### Follow-up (Codex, Oct 9)
> Update `output/desk_tickets.html` to populate the `Reflection` tab with an authentic, grounded post-mortem of our multi-agent deployment, and update `AI_prompts.md` for Problem 10.

The rest of the prompt gave a revised version of the reflection for the tab.

### What was missing after the first try
The first reflection had the business types of tickets 102 and 103 swapped and described the scripted Oct 9 run, so after the Oct 14 rerun it had to be rewritten from the real run data.

## Problem 11: Submit to GitHub

### My prompt (Codex, Oct 9)
> Implement Problem 11 ("Submit to GitHub", 5 points) for Homework 5.
>
> Prepare the repository for public submission, align the project layout, draft the deployment documentation, configure git tracking, and create `output/github_url.txt`.

### Follow-up (Codex, Oct 9)
> Finalize Problem 11 ("Submit to GitHub") for Homework 5: save the URL, prepare required submission files, stage git tracking, push to the remote repository, and update the prompt log.
>
> Write "https://github.com/ty0025/hw5-campus-customs-multi-agent" into `output/github_url.txt`.

On Oct 14 I also asked Codex to confirm that all the latest changes were on GitHub, and to sync them when they weren't.

### What was missing after the first try
The first pass set up a local repo and staged the files but left a placeholder URL, with no remote, no commit and no push; after the follow-up the first commit (4a2d5c1) went up to GitHub.
