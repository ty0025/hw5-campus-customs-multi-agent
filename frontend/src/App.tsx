import { useCallback, useEffect, useMemo, useState, type CSSProperties } from "react";
import {
  Activity,
  ArrowDownRight,
  ArrowRight,
  BadgeCheck,
  Banknote,
  Box,
  Check,
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  Clock3,
  FileText,
  Loader2,
  MessageCircle,
  Play,
  RotateCcw,
  Send,
  ShieldCheck,
  Sparkles,
  Store,
  Users,
  Wallet
} from "lucide-react";

const API = "http://localhost:8000";

type AgentKey = "boss" | "inventory" | "accounting" | "facilities" | "customer_service";
type TicketStatus = "open" | "resolved";

interface Ticket {
  ticket_id: number;
  description: string;
  type?: string | null;
  status: TicketStatus;
  assigned_agent?: string | null;
}

interface ToolCall {
  name?: string;
  arguments?: unknown;
}

interface AgentEvent {
  timestamp?: string | null;
  ticket_id?: number | null;
  active_agent?: string | null;
  thoughts?: string | null;
  speech?: string | null;
  action?: string | null;
  tool_calls?: ToolCall[];
  delegation_target?: string | null;
}

interface PaymentProposal {
  identity: string;
  payment_id: number | null;
  amount: number;
  recipient: string;
  reason: string;
  reference_type: string;
  reference_id: number | null;
  ticket_id: number;
  status: "PENDING_APPROVAL" | "PAID";
}

interface ApprovalResult {
  status: string;
  payment_id: number;
  payment_status: string;
  balance: number;
}

const AGENTS: Record<AgentKey, { label: string; role: string; color: string; initials: string }> = {
  boss: { label: "Boss", role: "Executive commander", color: "#1E293B", initials: "B" },
  inventory: { label: "Inventory", role: "Stockroom logistics", color: "#D97706", initials: "I" },
  accounting: { label: "Accounting", role: "Financial controller", color: "#059669", initials: "A" },
  facilities: { label: "Facilities", role: "Physical infrastructure", color: "#475569", initials: "F" },
  customer_service: { label: "Customer Service", role: "Communications lead", color: "#0284C7", initials: "CS" }
};

function normalizeAgent(value?: string | null): AgentKey {
  const name = (value ?? "").toLowerCase().replace(/[^a-z]/g, "");
  if (name.includes("inventory")) return "inventory";
  if (name.includes("account")) return "accounting";
  if (name.includes("facilit")) return "facilities";
  if (name.includes("customer") || name.includes("service") || name === "cs") return "customer_service";
  return "boss";
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function textValue(value: unknown): string {
  return typeof value === "string" ? value : value == null ? "" : String(value);
}

function displaySummary(value: unknown): string {
  if (typeof value === "string") return value;
  const record = asRecord(value);
  if (record) {
    const sentence = record.summary ?? record.resolution ?? record.message ?? record.final_decision;
    if (typeof sentence === "string") return sentence;
    return JSON.stringify(record) ?? "Run finished.";
  }
  return textValue(value);
}

function parseId(value: unknown): number | null {
  if (typeof value === "number" && Number.isInteger(value)) return value;
  if (typeof value === "string") {
    const match = value.match(/\d+/);
    if (match) return Number(match[0]);
  }
  return null;
}

function collectPaymentProposals(value: unknown, ticketId: number): PaymentProposal[] {
  const discovered: PaymentProposal[] = [];
  const visit = (node: unknown, inheritedTool = "") => {
    if (Array.isArray(node)) {
      node.forEach((item) => visit(item, inheritedTool));
      return;
    }
    const record = asRecord(node);
    if (!record) return;

    const toolName = textValue(record.name ?? record.tool_called ?? record.tool_name ?? inheritedTool);
    const argumentRecord = asRecord(record.arguments) ?? asRecord(record.args) ?? {};
    const merged = { ...record, ...argumentRecord };
    const status = textValue(merged.status ?? merged.payment_status).toUpperCase();
    const isProposal = toolName.toLowerCase().includes("propose_payment")
      || status === "PENDING_APPROVAL"
      || (merged.amount !== undefined && (merged.recipient !== undefined || merged.payee !== undefined)
        && (merged.reason !== undefined || merged.reference_id !== undefined || merged.ref_id !== undefined));
    if (isProposal && status !== "PAID" && status !== "APPROVED") {
      const amount = Number(merged.amount ?? merged.payment_amount);
      const recipient = textValue(merged.recipient ?? merged.payee ?? merged.vendor_name);
      const reason = textValue(merged.reason ?? merged.purpose ?? merged.description ?? "Payment proposal");
      const rawReferenceType = textValue(merged.reference_type ?? merged.ref_type ?? merged.reference_kind);
      const invoiceReference = merged.invoice_id ?? merged.invoice_reference;
      const leaseReference = merged.lease_id ?? merged.lease_reference;
      const rawReferenceId = merged.reference_id ?? merged.ref_id ?? invoiceReference ?? leaseReference ?? merged.reference;
      const referenceId = parseId(rawReferenceId);
      const inferredType = leaseReference !== undefined
        ? "lease"
        : invoiceReference !== undefined
          ? "invoice"
          : /rent|lease/i.test(reason)
            ? "lease"
            : /invoice|vendor/i.test(reason)
              ? "invoice"
              : "";
      const referenceType = rawReferenceType || inferredType;
      const paymentId = parseId(merged.payment_id ?? merged.paymentId ?? merged.proposal_id ?? merged.id);
      if (Number.isFinite(amount) && amount > 0 && recipient) {
        const identity = [
          ticketId,
          referenceType || "reference",
          referenceId ?? "unknown",
          recipient.toLowerCase(),
          amount.toFixed(2)
        ].join(":");
        discovered.push({
          identity,
          payment_id: paymentId,
          amount,
          recipient,
          reason,
          reference_type: referenceType,
          reference_id: referenceId,
          ticket_id: ticketId,
          status: "PENDING_APPROVAL"
        });
      }
    }

    for (const [key, child] of Object.entries(record)) {
      if (key === "arguments" || key === "args") continue;
      visit(child, toolName || inheritedTool);
    }
    if (record.arguments || record.args) visit(record.arguments ?? record.args, toolName || inheritedTool);
  };
  visit(value);
  return [...new Map(discovered.map((proposal) => [proposal.identity, proposal])).values()];
}

function mergeProposals(current: PaymentProposal[], incoming: PaymentProposal[]): PaymentProposal[] {
  const merged = new Map(current.map((proposal) => [proposal.identity, proposal]));
  incoming.forEach((proposal) => {
    const existing = merged.get(proposal.identity);
    merged.set(proposal.identity, existing
      ? { ...existing, ...proposal, payment_id: proposal.payment_id ?? existing.payment_id }
      : proposal);
  });
  return [...merged.values()].sort((left, right) => right.ticket_id - left.ticket_id);
}

function loadPendingPayments(): PaymentProposal[] {
  try {
    const stored = window.localStorage.getItem("campus-customs-pending-payments");
    const parsed: unknown = stored ? JSON.parse(stored) : [];
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((item): item is PaymentProposal => {
      const row = asRecord(item);
      return Boolean(row)
        && typeof row?.identity === "string"
        && typeof row?.amount === "number"
        && typeof row?.recipient === "string"
        && typeof row?.ticket_id === "number";
    });
  } catch {
    return [];
  }
}

function formatCurrency(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(value);
}

function formatTime(value?: string | null): string {
  if (!value) return "just now";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", second: "2-digit" }).format(date);
}

async function apiRequest<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...options?.headers
    }
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = asRecord(body)?.detail;
    throw new Error(textValue(detail) || `Request failed (${response.status})`);
  }
  return body as T;
}

function App() {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [eventFloor, setEventFloor] = useState<number | null>(null);
  const [selectedTicketId, setSelectedTicketId] = useState(101);
  const [completedTicketIds, setCompletedTicketIds] = useState<Set<number>>(new Set());
  const [approvalNotes, setApprovalNotes] = useState<Record<number, string>>({});
  const [balance, setBalance] = useState<number | null>(null);
  const [pendingPayments, setPendingPayments] = useState<PaymentProposal[]>(loadPendingPayments);
  const [approvedPayments, setApprovedPayments] = useState<PaymentProposal[]>([]);
  const [activeRunTicket, setActiveRunTicket] = useState<number | null>(null);
  const [approvingIdentity, setApprovingIdentity] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [resetting, setResetting] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const refreshTickets = useCallback(async () => {
    const rows = await apiRequest<Ticket[]>("/api/tickets");
    setTickets(rows);
    return rows;
  }, []);

  const refreshBalance = useCallback(async () => {
    const result = await apiRequest<{ account_name: string; balance: number }>("/api/cash/balance");
    setBalance(result.balance);
  }, []);

  const refreshEvents = useCallback(async () => {
    const rows = await apiRequest<AgentEvent[]>("/api/events?limit=200");
    const visibleRows = eventFloor === null ? rows : rows.filter((event) => {
      const timestamp = event.timestamp ? new Date(event.timestamp).getTime() : 0;
      return Number.isFinite(timestamp) && timestamp >= eventFloor;
    });
    setEvents(visibleRows);
    return visibleRows;
  }, [eventFloor]);

  useEffect(() => {
    window.localStorage.setItem("campus-customs-pending-payments", JSON.stringify(pendingPayments));
  }, [pendingPayments]);

  useEffect(() => {
    let mounted = true;
    Promise.all([refreshTickets(), refreshBalance(), refreshEvents()])
      .catch((reason: unknown) => {
        if (mounted) setError(reason instanceof Error ? reason.message : "Could not connect to the operations API.");
      })
      .finally(() => {
        if (mounted) setLoading(false);
      });
    return () => { mounted = false; };
  }, [refreshBalance, refreshEvents, refreshTickets]);

  useEffect(() => {
    if (activeRunTicket === null) return;
    const timer = window.setInterval(() => {
      void refreshEvents().catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : "Could not refresh agent activity.");
      });
    }, 900);
    return () => window.clearInterval(timer);
  }, [activeRunTicket, refreshEvents]);

  const selectedTicket = tickets.find((ticket) => ticket.ticket_id === selectedTicketId);
  const ticketEvents = useMemo(
    () => events.filter((event) => event.ticket_id === selectedTicketId),
    [events, selectedTicketId]
  );
  const ticketPayments = pendingPayments.filter(
    (payment) => payment.ticket_id === selectedTicketId && payment.status === "PENDING_APPROVAL"
  );
  const involvedAgents = [...new Set(ticketEvents.map((event) => normalizeAgent(event.active_agent)))];

  const visibleStatus: TicketStatus = selectedTicket && completedTicketIds.has(selectedTicket.ticket_id)
    ? "resolved"
    : selectedTicket?.status ?? "open";

  async function handleRun(ticketId: number) {
    setError("");
    setNotice("");
    setActiveRunTicket(ticketId);
    setSelectedTicketId(ticketId);
    const runStartedAt = Date.now() - 1000;
    try {
      const result = await apiRequest<Record<string, unknown>>(`/api/tickets/${ticketId}/run`, {
        method: "POST"
      });
      const outputProposals = collectPaymentProposals(result, ticketId);
      setPendingPayments((current) => mergeProposals(current, outputProposals));
      const summary = displaySummary(result.resolution ?? result.summary);
      setNotice(summary || `Ticket ${ticketId} has completed its agent run.`);
      const [latestEvents] = await Promise.all([refreshEvents(), refreshTickets()]);
      const traceProposals = latestEvents.flatMap((event) =>
        event.ticket_id == null || !event.timestamp || new Date(event.timestamp).getTime() < runStartedAt
          ? []
          : collectPaymentProposals(event, event.ticket_id)
      );
      const hasPendingApproval = [...outputProposals, ...traceProposals, ...pendingPayments]
        .some((proposal) => proposal.ticket_id === ticketId && proposal.status === "PENDING_APPROVAL");
      if (!hasPendingApproval) {
        setCompletedTicketIds((current) => new Set(current).add(ticketId));
        setTickets((current) => current.map((ticket) =>
          ticket.ticket_id === ticketId ? { ...ticket, status: "resolved" as const } : ticket
        ));
      }
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The ticket run failed.");
    } finally {
      setActiveRunTicket(null);
    }
  }

  async function handleApprove(proposal: PaymentProposal) {
    if (proposal.payment_id == null || proposal.reference_id == null || !proposal.reference_type) return;
    setError("");
    setNotice("");
    setApprovingIdentity(proposal.identity);
    try {
      const result = await apiRequest<ApprovalResult>("/api/payments/approve", {
        method: "POST",
        body: JSON.stringify({
          payment_id: proposal.payment_id,
          amount: proposal.amount,
          recipient: proposal.recipient,
          reference_type: proposal.reference_type,
          reference_id: proposal.reference_id
        })
      });
      setPendingPayments((current) => current.map((item) =>
        item.identity === proposal.identity ? { ...item, status: "PAID" } : item
      ));
      setApprovedPayments((current) => [{ ...proposal, status: "PAID" }, ...current]);
      setApprovalNotes((current) => ({
        ...current,
        [proposal.ticket_id]: `Payment approved · ${formatCurrency(proposal.amount)} recorded; follow-up may continue.`
      }));
      setNotice(`Disbursement recorded. New checking balance: ${formatCurrency(result.balance)}.`);
      await Promise.all([refreshBalance(), refreshTickets(), refreshEvents()]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Payment approval failed.");
    } finally {
      setApprovingIdentity(null);
    }
  }

  async function handleReset() {
    if (!window.confirm("Reset the working database to its pristine state? This clears current ticket and payment changes.")) return;
    setResetting(true);
    setError("");
    setNotice("");
    try {
      const result = await apiRequest<{ status: string; message: string }>("/api/database/reset", { method: "POST" });
      setPendingPayments([]);
      setApprovedPayments([]);
      setCompletedTicketIds(new Set<number>());
      setApprovalNotes({});
      setEventFloor(Date.now());
      setEvents([]);
      await Promise.all([refreshTickets(), refreshBalance()]);
      setNotice(result.message);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Database reset failed.");
    } finally {
      setResetting(false);
    }
  }

  const agentSummaries = involvedAgents.map((key) => {
    const entries = ticketEvents.filter((event) => normalizeAgent(event.active_agent) === key);
    const latestSpeech = [...entries].reverse().find((event) => event.speech || event.thoughts);
    const tools = [...new Set(entries.flatMap((event) => (event.tool_calls ?? []).map((call) => textValue(call.name)).filter(Boolean)))];
    const fallback = tools.length
      ? `Used ${tools.join(", ")} to move this ticket forward.`
      : entries.at(-1)?.action?.replaceAll("_", " ") || "Contributed to triage and resolution.";
    return { key, entries, summary: latestSpeech?.speech || latestSpeech?.thoughts || fallback };
  });

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="#" aria-label="Campus Customs Desk home">
          <span className="brand-mark"><Store size={19} strokeWidth={2.2} /></span>
          <span className="brand-copy">
            <strong>Campus Customs</strong>
                  <span>OPERATIONS DESK <i>•</i> CAMPUS SHOP</span>
          </span>
        </a>
        <div className="topbar-center">
          <span className="live-dot" />
          <span>Local operations environment</span>
          <span className="divider-dot">/</span>
          <span className="desk-open">Desk open</span>
        </div>
        <div className="topbar-actions">
          <div className="balance-pill" aria-live="polite">
            <span className="balance-icon"><Wallet size={16} /></span>
            <span className="balance-copy"><small>CHECKING BALANCE</small><strong>{formatCurrency(balance)}</strong></span>
          </div>
          <button className="reset-button" onClick={() => void handleReset()} disabled={resetting}>
            {resetting ? <Loader2 className="spin" size={15} /> : <RotateCcw size={15} />}
            <span>{resetting ? "Resetting" : "Reset desk"}</span>
          </button>
        </div>
      </header>

      <main className="desk-page">
        <section className="desk-heading">
          <div>
            <div className="eyebrow"><span className="eyebrow-line" /> STORE MANAGER WORKSPACE</div>
            <h1>Campus Customs <em>Operations Desk</em></h1>
            <p className="heading-subtitle">Three open matters, one coordinated team. Review the work as it happens.</p>
          </div>
          <div className="header-stamp">
            <span className="stamp-icon"><ShieldCheck size={18} /></span>
            <span><strong>Human-led decisions</strong><small>Payments wait for your approval</small></span>
          </div>
        </section>

        {error && <div className="banner banner-error" role="alert"><CircleAlert size={17} /><span>{error}</span><button onClick={() => setError("")} aria-label="Dismiss error">×</button></div>}
        {notice && <div className="banner banner-success" role="status"><CheckCircle2 size={17} /><span>{notice}</span><button onClick={() => setNotice("")} aria-label="Dismiss message">×</button></div>}

        <div className="desk-grid">
          <aside className="ticket-rail panel">
            <div className="panel-heading rail-heading">
              <div><span className="section-kicker">INBOUND</span><h2>Ticket stack</h2></div>
              <span className="count-badge">{tickets.length || 3}</span>
            </div>
            <p className="panel-intro">Choose a ticket to inspect its workstream.</p>
            <div className="ticket-list">
              {[101, 102, 103].map((ticketId, index) => {
                const ticket = tickets.find((row) => row.ticket_id === ticketId);
                const status: TicketStatus = completedTicketIds.has(ticketId) ? "resolved" : ticket?.status ?? "open";
                const isSelected = selectedTicketId === ticketId;
                return (
                  <button
                    className={`ticket-card ${isSelected ? "selected" : ""}`}
                    key={ticketId}
                    onClick={() => { setSelectedTicketId(ticketId); setError(""); setNotice(""); }}
                    aria-pressed={isSelected}
                  >
                    <div className="ticket-card-top">
                      <span className="ticket-number">CC–{ticketId}</span>
                      <span className={`status-badge status-${status}`}><i />{status === "open" ? "OPEN" : "RESOLVED"}</span>
                    </div>
                    <strong className="ticket-title">{ticket?.description || (loading ? "Loading ticket details…" : fallbackTicketTitle(ticketId))}</strong>
                    <div className="ticket-card-bottom">
                      <span className="ticket-type">{ticket?.type || ["Customer order", "Ticket review", "Store request"][index]}</span>
                      <ChevronRight className={isSelected ? "chevron-active" : ""} size={16} />
                    </div>
                    {activeRunTicket === ticketId && <div className="ticket-running"><span className="mini-pulse" /> Team working</div>}
                  </button>
                );
              })}
            </div>

            <div className="rail-note">
              <div className="note-icon"><Sparkles size={15} /></div>
              <div><strong>Full team access</strong><span>Specialists can collaborate across the desk when the ticket needs them.</span></div>
            </div>

            <div className="rail-footer">
              <span className="tiny-ledger"><FileText size={14} /> AUDIT TRAIL</span>
              <span>{events.length} steps logged</span>
            </div>
          </aside>

          <section className="activity-panel panel">
            <div className="panel-heading activity-heading">
              <div><span className="section-kicker">COLLABORATION</span><h2>Agent live stream</h2></div>
              <div className={activeRunTicket === selectedTicketId ? "stream-state is-live" : "stream-state"}>
                <span className="state-dot" />{activeRunTicket === selectedTicketId ? "LIVE" : "READY"}
              </div>
            </div>
            <div className="agent-roster" aria-label="Agent team">
              {(Object.keys(AGENTS) as AgentKey[]).map((key) => {
                const agent = AGENTS[key];
                const isInvolved = involvedAgents.includes(key);
                const isActive = ticketEvents.some((event) => normalizeAgent(event.active_agent) === key && activeRunTicket === selectedTicketId);
                return (
                  <div className={`roster-agent ${isInvolved ? "roster-involved" : ""} ${isActive ? "roster-active" : ""}`} key={key}>
                    <span className="avatar-small" style={{ "--agent-color": agent.color } as CSSProperties}>{agent.initials}</span>
                    <span>{agent.label}</span>
                  </div>
                );
              })}
            </div>
            <div className="stream-context">
              <div className="context-title">
                <span className="context-ticket">CC–{selectedTicketId}</span>
                <span className="context-description">{selectedTicket?.description || fallbackTicketTitle(selectedTicketId)}</span>
              </div>
              <button className="run-button" onClick={() => void handleRun(selectedTicketId)} disabled={activeRunTicket !== null || loading}>
                {activeRunTicket === selectedTicketId ? <Loader2 className="spin" size={16} /> : <Send size={15} />}
                {activeRunTicket === selectedTicketId ? "Agents at work" : "Dispatch agents"}
              </button>
            </div>

            <div className="activity-feed" aria-live="polite">
              {ticketEvents.length === 0 && activeRunTicket !== selectedTicketId && (
                <div className="feed-empty">
                  <div className="empty-orbit"><Activity size={23} /></div>
                  <strong>The desk is waiting</strong>
                  <span>Dispatch the team to see delegation, decisions and tool calls appear here.</span>
                  <button onClick={() => void handleRun(selectedTicketId)} disabled={activeRunTicket !== null || loading}>
                    <Play size={14} fill="currentColor" /> Run this ticket
                  </button>
                </div>
              )}

              {activeRunTicket === selectedTicketId && ticketEvents.length === 0 && (
                <div className="feed-loading"><span className="loader-ring" /><div><strong>Opening the workstream…</strong><span>Listening for the first agent event</span></div></div>
              )}

              {ticketEvents.map((event, index) => {
                const key = normalizeAgent(event.active_agent);
                const agent = AGENTS[key];
                const tools = event.tool_calls ?? [];
                return (
                  <article className="event-row" key={`${event.timestamp ?? "event"}-${index}`}>
                    <div className="event-timeline">
                      <span className="avatar-event" style={{ "--agent-color": agent.color } as CSSProperties}>{agent.initials}</span>
                      {index < ticketEvents.length - 1 && <span className="timeline-stem" />}
                    </div>
                    <div className="event-content">
                      <div className="event-meta">
                        <strong style={{ color: agent.color }}>{agent.label}</strong>
                        <span className="event-action">{(event.action || "working").replaceAll("_", " ")}</span>
                        <time>{formatTime(event.timestamp)}</time>
                      </div>
                      {event.delegation_target && (
                        <div className="delegation-line"><ArrowRight size={13} /> Handed to <strong>{AGENTS[normalizeAgent(event.delegation_target)].label}</strong></div>
                      )}
                      {(event.thoughts || event.speech) && (
                        <div className={event.speech ? "speech-bubble" : "thought-bubble"}>
                          {event.speech ? <MessageCircle size={14} /> : <Sparkles size={13} />}
                          <span>{event.speech || event.thoughts}</span>
                        </div>
                      )}
                      {tools.map((tool, toolIndex) => (
                        <div className="tool-chip" key={`${tool.name ?? "tool"}-${toolIndex}`}>
                          <span className="tool-glyph"><Box size={13} /></span>
                          <strong>{tool.name || "Tool call"}</strong>
                          {tool.arguments != null && <code>{typeof tool.arguments === "string" ? tool.arguments : JSON.stringify(tool.arguments)}</code>}
                        </div>
                      ))}
                    </div>
                  </article>
                );
              })}
            </div>
            <div className="activity-footer"><span><span className="live-dot" /> Events refresh every 0.9 seconds while the team is running</span><span>{ticketEvents.length} events</span></div>
          </section>

          <aside className="decision-panel">
            <section className="decision-card panel">
              <div className="panel-heading decision-heading">
                <div><span className="section-kicker">YOUR DESK</span><h2>Human decisions</h2></div>
                <span className="lock-badge"><ShieldCheck size={15} /> HITL</span>
              </div>

              {ticketPayments.length > 0 ? (
                <div className="approval-stack">
                  {ticketPayments.map((proposal) => {
                    const canApprove = proposal.payment_id != null && proposal.reference_id != null && Boolean(proposal.reference_type);
                    return (
                      <div className="approval-card" key={proposal.identity}>
                        <div className="approval-topline"><span className="approval-alert"><Clock3 size={13} /> PENDING YOUR REVIEW</span><span className="pending-dot" /></div>
                        <div className="approval-amount">{formatCurrency(proposal.amount)}</div>
                        <div className="approval-payee"><span className="payee-icon"><Banknote size={15} /></span><span><small>PAY TO</small><strong>{proposal.recipient}</strong></span></div>
                        <div className="approval-reason"><span>Purpose</span><strong>{proposal.reason}</strong></div>
                        <div className="approval-reason"><span>{proposal.reference_type ? `${proposal.reference_type} reference` : "Reference"}</span><strong>{proposal.reference_id ?? "Awaiting reference ID"}</strong></div>
                        <div className="balance-impact"><span><ArrowDownRight size={15} /> Balance after approval</span><strong>{balance == null ? "—" : formatCurrency(balance - proposal.amount)}</strong></div>
                        <button
                          className="approve-button"
                          onClick={() => void handleApprove(proposal)}
                          disabled={!canApprove || approvingIdentity !== null || activeRunTicket !== null}
                          title={!canApprove ? "The agent trace did not include the staged payment ID and reference required by the approval API." : "Approve and record this disbursement"}
                        >
                          {approvingIdentity === proposal.identity ? <Loader2 className="spin" size={16} /> : <Check size={16} />}
                          Approve disbursement
                        </button>
                        {!canApprove && <p className="approval-missing">The proposal is visible, but approval details are incomplete in the agent trace.</p>}
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="decision-empty">
                  <span className="decision-empty-icon"><ShieldCheck size={20} /></span>
                  <strong>No approval waiting</strong>
                  <span>Any proposed payment will pause here until you review it.</span>
                </div>
              )}

              {approvedPayments.length > 0 && (
                <div className="approved-list">
                  <div className="ledger-title"><span><CheckCircle2 size={14} /> RECENTLY APPROVED</span></div>
                  {approvedPayments.slice(0, 3).map((payment) => (
                    <div className="approved-row" key={payment.identity}>
                      <span className="approved-check"><Check size={12} /></span>
                      <span><strong>{payment.recipient}</strong><small>Ticket {payment.ticket_id} · {payment.reason}</small></span>
                      <b>−{formatCurrency(payment.amount)}</b>
                    </div>
                  ))}
                </div>
              )}
            </section>

            <section className="specialist-card panel">
              <div className="panel-heading specialist-heading">
                <div><span className="section-kicker">AFTER THE HUDDLE</span><h2>Specialist contribution</h2></div>
                <Users size={17} />
              </div>
              {agentSummaries.length === 0 ? (
                <div className="specialist-empty">
                  <span className="small-icon"><Sparkles size={14} /></span>
                  <span>Agent contributions will be summarized here after the run.</span>
                </div>
              ) : (
                <div className="contribution-list">
                  {agentSummaries.map(({ key, summary, entries }) => {
                    const agent = AGENTS[key];
                    return (
                      <div className="contribution-row" key={key}>
                        <span className="contribution-avatar" style={{ "--agent-color": agent.color } as CSSProperties}>{agent.initials}</span>
                        <div><strong>{agent.label}<small>{agent.role}</small></strong><p>{summary}</p></div>
                        <span className="contribution-count">{entries.length}</span>
                      </div>
                    );
                  })}
                </div>
              )}
              <div className="ticket-outcome">
                <span className={`outcome-mark ${visibleStatus === "resolved" ? "outcome-done" : ""}`}>{visibleStatus === "resolved" ? <BadgeCheck size={17} /> : <CircleAlert size={16} />}</span>
                <span><small>TICKET OUTCOME</small><strong>{approvalNotes[selectedTicketId] || (visibleStatus === "resolved" ? "Resolution recorded" : "Awaiting agent run")}</strong></span>
                <span className={`status-badge status-${visibleStatus}`}><i />{visibleStatus === "resolved" ? "RESOLVED" : "OPEN"}</span>
              </div>
            </section>
          </aside>
        </div>

        <footer className="desk-footer">
          <span><span className="footer-mark"><Store size={13} /></span> Campus Customs <i>·</i> Operator console</span>
          <span><ShieldCheck size={13} /> Drafts stay local <i>·</i> Payments require approval</span>
        </footer>
      </main>
    </div>
  );
}

function fallbackTicketTitle(ticketId: number): string {
  const titles: Record<number, string> = {
    101: "Customer order needs a stock check",
    102: "Store operations needs a specialist review",
    103: "A customer request is waiting on the desk"
  };
  return titles[ticketId] ?? "Ticket details unavailable";
}

export default App;
