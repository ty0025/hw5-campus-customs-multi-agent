# Board design: Campus Customs operations desk

The board is one React page (`frontend/src/App.tsx`, styles in `frontend/src/style.css`). I designed it for one person: the store manager sitting at the desk on 2026-08-31 with three tickets, five agents and $3,400 in checking. That person needs to see what the agents did, check it against the data, and decide whether money goes out. Everything on the page comes from the FastAPI backend on port 8000. The page itself only does three things: dispatch the team on a ticket, approve a staged request, and reset the desk.

## Layout

The top bar has the store name ("Campus Customs, Operations desk · Chapel Street"), the desk date from `desk.date_today`, a "gpt-6-luna team" label, the checking balance and a Reset desk button. Below it is a one-line heading and a small stamp that says "Nothing pays without you." Success and error messages appear as a banner just above the columns.

Then there are three columns, left to right.

1. Ticket stack. Cards for CC–101, CC–102 and CC–103. Each card shows a status badge, the subject and requester ("Bulldog tee · Tauhid Zaman"), the request line from the ticket, the ticket type, and "· 2 to approve" when requests are waiting. While the team works a ticket, its card shows a pulsing "Team working" line. The heading counts open tickets and the footer counts audit steps on the selected ticket.
2. Agent live stream, the widest column. A roster of the five agents (each one lights up once it has done something on this ticket), the selected ticket's request next to the Run button, and then the feed of everything that happened on the ticket.
3. Your desk. "Human decisions" with the approval cards, then "Customer drafts" when there are any, then "Who did what" with each agent's last report and the ticket outcome.

The columns follow the life of a ticket: what came in, what the team did, what I have to decide. All three fit on a normal laptop screen. So when Accounting says in the middle column that invoice 501 is 3 days overdue, the $840 card asking me to pay it sits right next to it. The feed scrolls inside its own panel, so a long run doesn't push the approval cards off the screen. In a narrower window the right column moves under the other two, and on a phone everything stacks into one column with the three ticket cards side by side at the top.

## How the agents read differently

Each agent has one color and one set of initials, and they are the same everywhere it shows up (roster, stream avatar, name, "Who did what"): Boss navy (B), Inventory amber (I), Accounting green (A), Facilities slate (F), Customer Service sky blue (CS). Hovering over a name in the roster shows the agent's job, such as "Cash, invoices, margins".

The human approver is purple and labelled "You". Approvals sit in the same stream as the agent steps, in a purple bubble, for example "Tongyu Chen approved payment #1 for $840.00: invoice 501 marked paid. Checking $3,400.00 -> $2,560.00." Nothing else on the page is purple, so the moments when a person moved money are easy to find. System is grey and faded, and it only marks when a run started.

There are two bubble styles. A solid speech bubble with a chat icon is a report: what an agent found and handed back, with its findings listed underneath. Boss's closing line for each run uses it too. A dashed, italic thought bubble is a task. When an agent delegates, the row says "Handed to Accounting" and shows the task and the context it passed along. The receiving agent's "picked up the task" row shows the same task. That way I can tell what an agent was asked to do from what it actually found.

Every MCP call is a small chip with the tool name and its arguments, like `check_inventory_stock {"sku":"CC-TEE-WHITE","size":"S","order_quantity":1}`. Under the chip, "MCP result" opens the JSON the tool returned. During a live run the call in progress shows as "calling tool". Once it returns, only the finished call and its result stay in the feed, so each call appears once. Each row also has a short action label ("delegated", "picked up the task", "used tool", "reported back", "closed the run", "approved") and a time.

A divider line reading "Run started" plus the time separates runs on the same ticket. Ticket 101 took two runs. The first staged the $840 payment and the $8 order. Next come my two approvals in purple, then the resume run in which Customer Service wrote the draft and Boss closed the ticket. Without the dividers these would run together. While a run is going, the feed asks `/api/events` for new steps every second and the badge in the corner switches from READY to LIVE.

The manager doesn't have to take a summary on trust. If Boss says size S is out, I can open the result of Inventory's `check_inventory_stock` call and see `"on_hand": 0`. The colors tell me who is talking and the bubble style tells me whether it's a request or a finding. In my final run this also showed me waste I would have missed in a summary: on ticket 102, Accounting called `get_lease_due_status` again even though Facilities had just passed it the lease facts.

## How resolved tickets show up

The status badge comes straight from `tickets.status`, which Boss sets with `update_ticket_status`. OPEN is orange, NEEDS APPROVAL (the `waiting_approval` state) is yellow, and RESOLVED is green. The other states the tool allows also have badges (IN PROGRESS, WAITING ON CUSTOMER, BLOCKED, STAFF REVIEW), but none came up in the final run. A finished run does not make a ticket green. It turns green only after Boss has written "resolved" to the database.

At the bottom of "Who did what" is a Ticket outcome line with Boss's latest resolution note, the same badge, and a green check once the ticket is resolved. Above it, each agent that worked the ticket is listed with its last report, the tools it used and how many steps it took.

The Run button changes with the ticket:

- "Dispatch agents" for a new ticket
- "Approve requests first", disabled, while anything is still pending
- "Resume team to close" once every request is approved but the ticket is still waiting
- "Run again" after it is resolved
- "Agents at work" during a run

Tickets 101 and 102 can't be finished in one pass because the team has to stop and wait for me, so the button tells me the next move. Disabling it while money is pending keeps me from re-running a ticket whose requests are still undecided.

## How cash shows up

The balance pill in the top bar shows checking from `cash_accounts`. It refreshes after every approval and every second during a run. In the final run it went $3,400.00, then $2,560.00, $2,552.00 and finally $152.00.

Each staged request gets an approval card with:

- the type and number ("PAYMENT #1", "PURCHASE ORDER #2") and the amount in large type
- who gets the money ("PAY TO Bulldog Print Co", or "ORDER FROM" for an order)
- Accounting's reason, and the reference ("invoice 501", "lease 1", or "1 x CC-TEE-WHITE S, 5-day lead")
- "Checking after approval", which is the current balance minus this amount

Two warnings can appear on a card. "Waits for invoice 501: approve that payment first." shows on an order while the payment for that vendor's invoice is still pending. "Checking can't cover this. The approve route will refuse it." shows when the amount is more than the balance. The backend enforces both rules anyway; the warnings just tell me before I click.

Above the cards is an "Approving as" field. The name typed there is remembered by the browser and saved in `payments.approved_by`. Once approved, a request moves to an "Approved on this ticket" list with the payee, who approved it, the reference and the amount as a negative number (−$840.00). A green banner also says what changed, for example "Approved $840.00 · invoice 501 marked paid. Checking is now $2,560.00."

I wanted the manager to see the effect of a click before making it. On the $2,400 rent card, "Checking after approval" read $152.00. I want that number in front of me before paying rent, because it means the shop will have almost nothing left for the hoodie restock on ticket 103.

## Customer drafts

When Customer Service saves a draft, a "Customer drafts" card appears on the right. It has a dashed border, "To" plus the requester, a "DRAFT · NOT SENT" tag, and the full text. On ticket 101 it held the note to Tauhid Zaman that size S is out of stock and expected around 2026-09-05. On ticket 103 it held the offer to Yale AI Club at $52.20 a hoodie. The manager reads exactly what the customer would get, and the tag makes it plain that the app sent nothing.

## Creative choices: what makes it feel like a real desk

- It runs on the shop's date. The top bar says 2026-08-31 even though I ran it in October, and every "overdue" and "due in 2 days" in the stream uses that date.
- It has a place. The header says "Chapel Street", which is the shop on lease 1.
- Approvals are signed. The three payments in the final run say "Tongyu Chen" in the ledger, not "admin".
- Talk and money are kept apart. Agents talk in the middle column and money waits on the right until I act. The page is navy, white and pale blue, while the approval cards are tinted warm amber, so anything waiting on my money is easy to spot. The footer repeats the rule: "Drafts stay local · Every payment needs a human."
- Reset desk asks for confirmation before it wipes the working copy, and the stream starts empty afterwards, like clearing the desk at the start of a day.
- Empty spots say what to do next: "The desk is waiting" with a "Run this ticket" link in the feed, and "Nothing waiting on you" under Human decisions.
- There are no charts or summary tables. With three tickets, the decisions themselves are the dashboard.
- Adding `?ticket=103` to the URL opens a specific ticket. `scripts/capture_board.py` uses this to take one screenshot per ticket for `resolved_board.html`.
