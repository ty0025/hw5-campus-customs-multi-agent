# Homework 5: Campus Customs Multi-Agent Operations - AI Prompts Log

I built Problems 1-11 with Codex in VS Code on Oct 8-9. Just after midnight on Oct 14 I had Codex check everything against the full assignment, and that check added a purchase-order approval and the board screenshots. Later on Oct 14 I used Claude Code to work out why the results looked wrong, rebuild the agent run, and do one clean final run on the board.

Notes on this log:

- Quotes are what I actually sent, copied from my saved Codex and Claude Code sessions. Chinese quotes have an English translation underneath.
- For most problems my Codex message was a short line plus a longer English brief that I had paraphrased from the problem page. I quote the start of each brief and describe the rest instead of pasting it all. I also pasted assignment text as context a few times (the background section on Oct 8, the full assignment to Codex and to Claude Code on Oct 14), and I say so each time.
- When I gave Codex the full assignment on Oct 14 I told it the briefs were my paraphrases: "这是所有题目，需要你仔细阅读一下然后再理解一下为什么你有些地方和要求做的不一样，只需要阅读并理解，因为我给你的prompt都是我paraphrase过的" (Here are all the questions. Read them carefully and figure out why some of your work differs from the requirements. Just read and understand, because the prompts I gave you were all my paraphrases.)
- Earlier versions of this file (commits 4a2d5c1 and f92bd94) mostly showed polished English prompts that I never typed. This version was rebuilt on Oct 14 from the saved sessions and replaces them.
- Dates are local time (UTC+8).

## Problem 1: Vibe coder prompts

### Prompt I typed (Codex, Oct 8)
> 我会有很多个问题一个个发给你，你先别急着做
>
> 我们来完成Question1：

Translation: I'm going to send you a lot of questions one at a time, so don't start yet. / Let's do Question 1.

After that line I pasted a brief asking Codex to create `AI_prompts.md` with one section per problem (first prompt, follow-up, one sentence on what was missing) and a ready-made Problem 1 entry.

### Follow-up (Codex, Oct 9, after Problem 11)
> Run a comprehensive grading audit across all 11 problems of Homework 5 and perform a complete English translation and human-tone polish of `AI_prompts.md`.

### What was missing after the first try
The log held tidy template prompts instead of what I typed, and my follow-up made it worse: Codex "translated" my Chinese prompts into English ones I never sent.

## Problem 2: Study the Campus Customs database

### Prompt I typed (Codex, Oct 8)
> 这次我们的作业要在hw5的文件夹里做，帮我先解压data

Translation: This homework goes in the hw5 folder. Unzip the data for me first.

> Implement Problem 2 ("Study the Campus Customs database") for Homework 5.
>
> Execute the following steps programmatically:

The rest of the brief said to copy `campus_customs.db` to `campus_customs_new.db`, read every table with `PRAGMA table_info`, look at the three open tickets, and write `output/harness.md` with each table's fields and types plus one sentence on why the agents need it.

### Follow-up (Codex, Oct 8, in a new session after the first one kept hitting errors)
> 这次我们的作业要在hw5的文件夹里做，已经解压了，然后我会让你协助我检查一下背景材料和第一第二题，没问题之后我们继续往下做

Translation: This homework is in the hw5 folder and it's already unzipped. I want you to help me check the background material and questions 1 and 2, and once those are fine we'll keep going.

I then pasted the assignment's background section and my Question 1 brief again.

### What was missing after the first try
The first `harness.md` matched the database, but there was no script to re-run the check, and it didn't say that the hoodie vendor is only a guess from `vendors.specialty` (no table links a SKU to a vendor).

## Problem 3: Build the MCP server

### Prompt I typed (Codex, Oct 8)
> 接下来我们来做Q3：
>
> Implement Problem 3 ("Build the MCP server") for Homework 5.
>
> Follow these exact architectural requirements:

Translation of the first line: Next, let's do Q3.

The brief asked for a FastMCP server in `mcp_server/` on `data/campus_customs_new.db` with three tools, and it labeled the vendor-invoice tool "for Ticket 102 - Vendor Restock & Invoice Clearance".

### Follow-up (Codex, Oct 8)
I had already sent the same brief in my first Codex session, where it failed:

> 为啥会报错
>
> 你能继续工作吗

Translation: Why is it throwing an error? / Can you keep working?

In the new session Codex asked how to label the tools, because in the database 102 is the rent notice and 103 is the hoodie discount. I picked the option it marked as recommended:

> 按数据库真实关联标注，并注明题面编号冲突（推荐）

Translation: Label them by the real links in the database and note the numbering conflict in the question (recommended).

### What was missing after the first try
My brief tied the vendor-invoice tool to ticket 102, but in the database 102 is rent and unpaid invoice 501 belongs to ticket 101, so the "conflict" came from my own paraphrase, not the assignment.

## Problem 4: Add the MCP server to vibe coder and test each tool

### Prompt I typed (Codex, Oct 8)
> Implement Problem 4 ("Add the MCP server to vibe coder and test each tool") for Homework 5.
>
> Execute the following steps programmatically and verify all outputs against the database:

The brief then gave the `.mcp.json` to create (`campus_customs_mcp`, command `python`, args `-m mcp_server.server`, `PYTHONPATH=.`).

### Follow-up (Claude Code, Oct 14)
None with Codex. On Oct 14 Claude Code asked whether to connect the MCP server from Claude Code or Codex. My answer (part of a longer reply quoted in Problem 9):

> 3. P4 用 Claude Code 还是 Codex 接 MCP？，如你所说用claude code也不影响那就用claude

Translation: 3. Connect the MCP server from Claude Code or Codex for P4? Like you said, using Claude Code doesn't change anything, so use Claude.

When it listed steps for me to do by hand, I replied:

> 这个我要干啥没看懂，你能直接新开一个对话帮我做了吗

Translation: I don't get what I'm supposed to do here. Can you just open a new conversation and do it for me?

### What was missing after the first try
The server had no stdio entry point and the default `python` had no `mcp` package, so Codex made `server.py` relaunch itself inside `.venv`; on Oct 14 `python` on my PC turned out to be the Microsoft Store stub, so `.mcp.json` now uses the `py` launcher.

## Problem 5: Build the agent team and grow the MCP tools

### Prompt I typed (Codex, Oct 8)
> Implement Problem 5 ("Build the agent team and grow the MCP tools", 15 points) for Homework 5.
>
> Construct the complete PydanticAI multi-agent operations team, expand the MCP server tools, wire the execution audit logger, and update all required documentation according to the exact specifications below:

The brief then covered the Portkey setup with gpt-6-luna, the Pydantic models, the five agents, delegation and the audit log.

### Follow-up (Codex, Oct 8, after the first run was cut off)
> 针对Q5：
>
> Resume and complete the remaining deliverables for Problem 5 ("Build the agent team and grow the MCP tools") after the previous execution interruption.
>
> Do not wipe or discard the existing edits in `hw5/backend/models.py` and `hw5/mcp_server/server.py`. Inspect and validate them first, then proceed with the pending tasks below:

Translation of the first line: About Q5.

Claude Code, Oct 14, before it changed anything:

> 在你开始修改之前，我需要你协助我回答这个问题。这次模型是不是只能用gpt6 luna，所以如果你用claude code的话会被扣分。还是说你能够规避掉这个问题

Translation: Before you start changing things, help me answer this. Is gpt6 luna the only model allowed this time, so I'd lose points if you use Claude Code? Or is there a way around that?

It answered that the rule covers the model the agents call, which stays gpt-6-luna through Portkey, so using Claude Code as the vibe coder was fine. The diagnosis (Problem 9) then found that the agent prompts hard-coded what each ticket was, the audit trail kept no tool arguments or results, and the pricing tool couldn't take a quantity or a discount, so the team and tools were rebuilt.

### What was missing after the first try
The first pass stopped after `models.py` and the MCP tools, with no agent prompts, no delegation between agents and no audit logger.

## Problem 6: Plan the three tickets

### Prompt I typed (Codex, Oct 8)
> Implement Problem 6 ("Plan the three tickets", 8 points) for Homework 5.
>
> Construct a standalone, interactive HTML dashboard at `output/desk_tickets.html` and document the expected operational pathways for open tickets 101, 102, and 103 before backend wiring.

The brief then gave a plan for each ticket, using the same wrong ticket labels as my Q3 brief.

### Follow-up (Codex, Oct 8, after I went back to finish Q5)
> 完成Q6

Translation: Finish Q6.

### What was missing after the first try
The Expected plan followed my brief, so it put a vendor-restock plan under ticket 102 and a lease plan under 103; it was rewritten before the final run on Oct 14, and the original is kept in a collapsed block on each tab.

## Problem 7: Backend routes

### Prompt I typed (Codex, Oct 8)
> Implement Problem 7 ("Backend routes", 7 points) for Homework 5.
>
> Build the FastAPI backend in `backend/main.py` to bridge the React dashboard, the multi-agent system, and the database.

The brief listed six routes: tickets, run, events, payment approval, cash balance and reset.

### Follow-up
None at the time. The later changes came from the Oct 14 check and rebuild in Problem 9.

### What was missing after the first try
The only approve route handled payments, so purchase orders had no approval path and drafts had no route; Codex added `GET /api/approvals` and a purchase-order route on Oct 14, and the rebuild merged both into `POST /api/approvals/{approval_id}/approve` and added `GET /api/drafts`.

## Problem 8: Agent dashboard

### Prompt I typed (Codex, Oct 8)
> Implement Problem 8 ("Agent dashboard", 12 points) for Homework 5.
>
> Build a polished, highly usable React + Vite + TypeScript dashboard in `frontend/` that connects to the FastAPI backend at `http://localhost:8000`.

### Follow-up
None at the time. The purchase-order card came from the Oct 14 Codex check, and the rest from the Claude Code rebuild (Problem 9).

### What was missing after the first try
The board had no live agent text, no drafts card and no way to resume a ticket after approval, and it was never used for the Oct 9 run because my Q9 brief ran everything from a script.

## Problem 9: Resolve the tickets

### Prompt I typed (Codex, Oct 9)
> Implement Problem 9 ("Resolve the tickets", 10 points) for Homework 5.
>
> CRITICAL ENVIRONMENT INSTRUCTION:
> Do NOT launch background or persistent server processes (such as `npm run dev` or `uvicorn main:app --reload`) in the terminal. Execute all resolution steps, multi-agent calls, and validations synchronously via a standalone, single-run Python automation script (e.g., `scripts/resolve_all.py`).

### Follow-up
Codex, Oct 9, while the script was still running:

> 先别跑了，我们进行下一题吧

Translation: Stop running it, let's move on to the next question.

Codex, Oct 14, after I pasted the full assignment for a check:

> 既然你已经发现评分缺口，你就给我改正啊
>
> 工作区恢复可读写是啥意思，然后html渲染出来是不是没有图片啊

Translation: If you've already found the gaps that would cost points, then fix them. / What does "workspace restored to read-write" mean? And does the rendered html have no pictures?

These led to the purchase-order approval and the three board screenshots (commit cc38a4d).

Claude Code, Oct 14. After this message I pasted the full assignment with my Chinese notes:

> 我需要你协助我检查一下这个作业做的怎么样，目前已经做完了，但是效果好像有点问题。你先帮我诊断，然后我们再看看怎么修改。我会把要求发你，我目前让codex协助我做的过程中遇到了一些问题：我会把作业要求也直接发给你：

Translation: I need you to help me check how this homework turned out. It's done, but the results seem a bit off. Diagnose it first, then we'll see how to fix it. I'll send you the requirements. I ran into some problems while Codex was helping me, so I'll paste the assignment requirements straight in.

Claude Code then asked me three questions (the numbered questions are its wording, the answers are mine):

> 1. 修到什么程度？ 全修加重跑一次，但不要太消耗token，减少反复测试的次数，能顺利运行即可
> 2. 101 要不要下 1 件 T 恤的补货单？ 可以下
> 3. P4 用 Claude Code 还是 Codex 接 MCP？，如你所说用claude code也不影响那就用claude

Translation: 1. How much to fix? Fix all of it and rerun once, but don't burn too many tokens. Cut down on repeated testing; it just has to run cleanly. 2. Place a restock order for the one tee on 101? Yes. 3. Connect the MCP server from Claude Code or Codex for P4? Like you said, Claude Code doesn't change anything, so use Claude.

In the final run on Oct 14 the desk was reset, each ticket was run from the board, and the three requests were approved there under my name ($840 for invoice 501, $8 for one size S tee, $2,400 rent). All three tickets ended resolved and checking went from $3,400.00 to $152.00.

### What was missing after the first try
The script approved the payments itself as "Human Approval" and forced all three tickets to resolved, so the board was never used, and the audit trail kept no tool arguments or results.

## Problem 10: Reflection

### Prompt I typed (Codex, Oct 9)
Sent in the same message as "先别跑了，我们进行下一题吧" (Problem 9):

> Implement Problem 10 ("Reflection", 10 points) for Homework 5.
>
> Populate the `Reflection` tab inside `output/desk_tickets.html` with an exhaustive, highly critical, first-person operational post-mortem. Embed concrete empirical evidence directly referencing Tickets 101, 102, 103, and the itemized Cash ledger. Update `AI_prompts.md` accordingly.

An HTML reflection template followed.

### Follow-up
Codex, Oct 9, with a full reflection text pasted underneath:

> Update `output/desk_tickets.html` to populate the `Reflection` tab with an authentic, grounded post-mortem of our multi-agent deployment, and update `AI_prompts.md` for Problem 10.
>
> Inject the text below into the Reflection tab container of `output/desk_tickets.html`. Keep the phrasing sharp, candid, and conversational—written strictly from a pragmatic developer's perspective rather than generic AI prose.

### What was missing after the first try
The reflection template in my first brief had the business types of tickets 102 and 103 swapped and described the scripted Oct 9 run, so after the Oct 14 rerun it had to be rewritten from the real run data.

## Problem 11: Submit to GitHub

### Prompt I typed (Codex, Oct 9)
> Implement Problem 11 ("Submit to GitHub", 5 points) for Homework 5.
>
> Prepare the repository for public submission, align the project layout, draft the deployment documentation, configure git tracking, and create `output/github_url.txt`.

### Follow-up
Codex, Oct 9:

> Finalize Problem 11 ("Submit to GitHub") for Homework 5: save the URL, prepare required submission files, stage git tracking, push to the remote repository, and update the prompt log.
>
> Write "https://github.com/ty0025/hw5-campus-customs-multi-agent" into `output/github_url.txt`.

Codex, Oct 14:

> 最新的改动是否都update到github里了，你确认一下
>
> 那你同步一下啊，为啥你的git不行？

Translation: Check whether all the latest changes are on GitHub. / Then sync them. Why isn't your git working?

### What was missing after the first try
The first pass set up a local repo and staged the files but left a placeholder URL, with no remote, no commit and no push; after the follow-up the first commit (4a2d5c1) went up to GitHub.
