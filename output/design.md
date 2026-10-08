# Campus Customs Operations Desk — Design Notes

## Spatial desk layout

The dashboard uses a three-part working surface that mirrors an operator's physical desk. The left pane is the inbound ticket stack: each ticket is a compact, selectable card with its own status, short description, category, and a clear dispatch state. The center pane is the collaboration surface, where agent identity, delegation, speech, reasoning notes, and MCP tool calls arrive in a timestamped live stream. The right pane keeps decisions close to the work: staged payments sit above the ticket's specialist contribution summary and final outcome.

At desktop widths, the three panes remain visible together so an operator can compare the request, agent progress, and financial action without switching screens. On smaller screens, they fold into a single vertical workspace while preserving the same order and controls. The checking balance and reset action remain in the top bar so the most consequential controls stay easy to find.

## Agent identity hierarchy

Agent identities use the requested color system: Boss in deep navy (#1E293B), Inventory in amber (#D97706), Accounting in emerald (#059669), Facilities in slate (#475569), and Customer Service in sky blue (#0284C7). Each agent has a compact initials avatar, a readable name, and a plain-language role label; color supports recognition but never carries meaning alone.

Delegation appears inline with the agent event that initiated it, using a directional handoff line and a named recipient. Tool calls use a separate pale-blue chip with the tool name and its arguments. Speech is shown in a clean message bubble, while internal reasoning notes use a quieter, dashed treatment. This makes the event type and the responsible agent distinguishable at a glance without turning the activity stream into a dense log table.

## Financial and resolution feedback

The checking balance is visible in the header and refreshes after every approved payment. The approval card previews the projected balance before the operator acts, then records the payee and disbursement in the local ledger after the backend confirms approval. Staged payments stay in a pending state until the operator uses the explicit approval button; incomplete payment references remain visible with approval disabled.

Open tickets use a warm urgent accent and a live dot, while resolved tickets switch to a settled green state with a check mark. Running a ticket changes its stream to a live state, exposes the polling cadence, and marks the completed ticket as resolved after the backend run returns. A concise contribution list collects the agents and tools represented in that ticket's event trace.

## Human-centered details

The visual language takes cues from a well-kept campus shop work surface: soft paper-white cards, a cool blue-gray desk background, restrained navy framing, generous spacing, and small warm accents for attention-worthy tasks. The control density stays deliberate: ticket descriptions truncate cleanly, tool arguments are contained, and the feed scrolls independently so the page frame remains stable during longer runs.

The header pairs the store name with a clear local-environment indicator and a financial summary. Secondary notes explain the approval boundary and local-draft behavior where they matter. Empty states give operators a concrete next action, while keyboard focus, status text, icons, and color work together to keep the interface legible during long monitoring sessions. Decorative elements stay quiet and functional; there are no generic chart grids or broad admin tables competing with the actual decisions.
