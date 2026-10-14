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
  Mail,
  MessageCircle,
  Play,
  RotateCcw,
  Send,
  ShieldCheck,
  Sparkles,
  Store,
  UserCheck,
  Users,
  Wallet
} from "lucide-react";

const API = "http://localhost:8000";

type AgentKey = "boss" | "inventory" | "accounting" | "facilities" | "customer_service" | "human" | "system";

interface Ticket {
  ticket_id: number;
  type: string;
  requester: string;
  subject: string;
  request: string;
  sku: string | null;
  size: string | null;
  qty: number | null;
  lease_id: number | null;
  invoice_id: number | null;
  status: string;
  board_status: "open" | "resolved";
  latest_note: string | null;
  pending_approvals: number;
}

interface ToolUse {
  name: string;
  arguments?: Record<string, unknown> | null;
  result?: string | null;
}

interface AgentEvent {
  timestamp: string | null;
  run_id: string | null;
  ticket_id: number | null;
  agent: string;
  action: string | null;
  kind: "speech" | "note";
  text: string;
  findings: string[];
  tool: ToolUse | null;
  delegation_target: string | null;
}

interface Approval {
  approval_id: number;
  ticket_id: number;
  kind: "payment" | "purchase_order";
  amount: number;
  payee: string | null;
  reason: string | null;
  reference: string;
  waits_for_invoices: number[];
  status: "pending" | "approved" | "rejected";
  decided_by: string | null;
  decision_note: string | null;
}

interface Draft {
  id: number;
  ticket_id: number;
  body: string;
  requester: string;
  subject: string;
  created_at: string;
}

const AGENTS: Record<AgentKey, { label: string; role: string; color: string; initials: string }> = {
  boss: { label: "Boss", role: "Routes the work, makes the call", color: "#1E293B", initials: "B" },
  inventory: { label: "Inventory", role: "Stock and restock", color: "#D97706", initials: "I" },
  accounting: { label: "Accounting", role: "Cash, invoices, margins", color: "#059669", initials: "A" },
  facilities: { label: "Facilities", role: "Lease and rent", color: "#475569", initials: "F" },
  customer_service: { label: "Customer Service", role: "Customer drafts", color: "#0284C7", initials: "CS" },
  human: { label: "You", role: "Human approver", color: "#7C3AED", initials: "You" },
  system: { label: "System", role: "Run bookkeeping", color: "#94A3B8", initials: "S" }
};
const TEAM: AgentKey[] = ["boss", "inventory", "accounting", "facilities", "customer_service"];

const STATUS_LABELS: Record<string, string> = {
  open: "OPEN",
  in_progress: "IN PROGRESS",
  waiting_approval: "NEEDS APPROVAL",
  waiting_customer: "WAITING ON CUSTOMER",
  blocked: "BLOCKED",
  ready_for_staff: "STAFF REVIEW",
  resolved: "RESOLVED"
};

const ACTION_LABELS: Record<string, string> = {
  ticket_run_started: "run started",
  thinking_started: "picked up the task",
  delegation: "delegated",
  tool_call_started: "calling tool",
  tool_call_completed: "used tool",
  response_synthesis: "reported back",
  ticket_run_completed: "closed the run",
  approval_decided: "approved",
  database_reset: "reset",
  error: "problem"
};

function agentKey(value?: string | null): AgentKey {
  const name = (value ?? "").toLowerCase();
  if (name.includes("inventory")) return "inventory";
  if (name.includes("account")) return "accounting";
  if (name.includes("facilit")) return "facilities";
  if (name.includes("customer")) return "customer_service";
  if (name.includes("human")) return "human";
  if (name.includes("boss")) return "boss";
  return "system";
}

function statusClass(status: string): string {
  if (status === "resolved") return "status-resolved";
  if (status === "blocked" || status === "ready_for_staff") return "status-blocked";
  if (status === "open") return "status-open";
  return "status-waiting";
}

function money(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" }).format(value);
}

function clock(value?: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat("en-US", { hour: "numeric", minute: "2-digit", second: "2-digit" }).format(date);
}

function ticketFromUrl(): number {
  const requested = Number(new URLSearchParams(window.location.search).get("ticket"));
  return [101, 102, 103].includes(requested) ? requested : 101;
}

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    ...options,
    headers: { "Content-Type": "application/json", ...options?.headers }
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body && typeof body === "object" && "detail" in body ? String(body.detail) : "";
    throw new Error(detail || `Request failed (${response.status})`);
  }
  return body as T;
}

function App() {
  const [tickets, setTickets] = useState<Ticket[]>([]);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [balance, setBalance] = useState<number | null>(null);
  const [deskDate, setDeskDate] = useState("");
  const [selectedId, setSelectedId] = useState(ticketFromUrl);
  const [runningId, setRunningId] = useState<number | null>(null);
  const [approvingId, setApprovingId] = useState<number | null>(null);
  const [approver, setApprover] = useState(() => window.localStorage.getItem("cc-approver") || "Store manager");
  const [loading, setLoading] = useState(true);
  const [resetting, setResetting] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const refreshDesk = useCallback(async () => {
    const [ticketRows, approvalRows, cash] = await Promise.all([
      api<Ticket[]>("/api/tickets"),
      api<Approval[]>("/api/approvals"),
      api<{ balance: number; desk_date: string }>("/api/cash/balance")
    ]);
    setTickets(ticketRows);
    setApprovals(approvalRows);
    setBalance(cash.balance);
    setDeskDate(cash.desk_date);
  }, []);

  const refreshTicket = useCallback(async (ticketId: number) => {
    const [eventRows, draftRows] = await Promise.all([
      api<AgentEvent[]>(`/api/events?ticket_id=${ticketId}`),
      api<Draft[]>(`/api/drafts?ticket_id=${ticketId}`)
    ]);
    setEvents(eventRows);
    setDrafts(draftRows);
  }, []);

  useEffect(() => {
    refreshDesk()
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Could not reach the backend on port 8000."))
      .finally(() => setLoading(false));
  }, [refreshDesk]);

  useEffect(() => {
    void refreshTicket(selectedId).catch(() => undefined);
  }, [refreshTicket, selectedId]);

  useEffect(() => {
    window.localStorage.setItem("cc-approver", approver);
  }, [approver]);

  // While the team works, poll the audit trail so each agent's step shows up live.
  useEffect(() => {
    if (runningId === null) return;
    const timer = window.setInterval(() => {
      void Promise.all([refreshTicket(runningId), refreshDesk()]).catch(() => undefined);
    }, 1000);
    return () => window.clearInterval(timer);
  }, [runningId, refreshDesk, refreshTicket]);

  const ticket = tickets.find((row) => row.ticket_id === selectedId);
  const ticketApprovals = approvals.filter((row) => row.ticket_id === selectedId);
  const pending = ticketApprovals.filter((row) => row.status === "pending");
  const decided = ticketApprovals.filter((row) => row.status !== "pending");
  const pendingInvoiceIds = new Set(
    approvals.filter((row) => row.kind === "payment" && row.status === "pending" && row.reference.startsWith("invoice"))
      .map((row) => Number(row.reference.replace(/\D/g, "")))
  );

  const visibleEvents = useMemo(() => events.filter((event, index) => {
    if (event.action !== "tool_call_started") return true;
    return runningId === selectedId && index === events.length - 1;
  }), [events, runningId, selectedId]);

  const involved = new Set(events.map((event) => agentKey(event.agent)));
  const contributions = TEAM.filter((key) => involved.has(key)).map((key) => {
    const mine = events.filter((event) => agentKey(event.agent) === key);
    const report = [...mine].reverse().find((event) => event.action === "response_synthesis");
    const tools = [...new Set(mine.map((event) => event.tool?.name).filter(Boolean))] as string[];
    return { key, report: report?.text ?? "Working…", tools, steps: mine.length };
  });

  const status = ticket?.status ?? "open";
  const canResume = status === "waiting_approval" && pending.length === 0 && decided.length > 0;
  const runLabel = runningId === selectedId
    ? "Agents at work"
    : pending.length > 0
      ? "Approve requests first"
      : canResume
        ? "Resume team to close"
        : status === "resolved" ? "Run again" : "Dispatch agents";

  async function handleRun(ticketId: number) {
    setError("");
    setNotice("");
    setRunningId(ticketId);
    setSelectedId(ticketId);
    try {
      const result = await api<{ status: string; summary: string; total_tokens: number }>(`/api/tickets/${ticketId}/run`, { method: "POST" });
      setNotice(`Ticket ${ticketId} → ${STATUS_LABELS[result.status] ?? result.status}. ${result.summary}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "The ticket run failed.");
    } finally {
      setRunningId(null);
      await Promise.all([refreshDesk(), refreshTicket(ticketId)]).catch(() => undefined);
    }
  }

  async function handleApprove(item: Approval) {
    setError("");
    setNotice("");
    setApprovingId(item.approval_id);
    try {
      const result = await api<{ effect: string; balance: number }>(`/api/approvals/${item.approval_id}/approve`, {
        method: "POST",
        body: JSON.stringify({ approved_by: approver.trim() || "Store manager" })
      });
      setNotice(`Approved ${money(item.amount)} · ${result.effect}. Checking is now ${money(result.balance)}.`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Approval failed.");
    } finally {
      setApprovingId(null);
      await Promise.all([refreshDesk(), refreshTicket(item.ticket_id)]).catch(() => undefined);
    }
  }

  async function handleReset() {
    if (!window.confirm("Reset the working database to the original? All approvals, payments and drafts in the working copy are cleared.")) return;
    setResetting(true);
    setError("");
    try {
      const result = await api<{ balance: number }>("/api/database/reset", { method: "POST" });
      setNotice(`Desk reset. Starting checking balance ${money(result.balance)}.`);
      await Promise.all([refreshDesk(), refreshTicket(selectedId)]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Reset failed.");
    } finally {
      setResetting(false);
    }
  }

  let lastRun: string | null = null;

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="#" aria-label="Campus Customs Desk home">
          <span className="brand-mark"><Store size={19} strokeWidth={2.2} /></span>
          <span className="brand-copy">
            <strong>Campus Customs</strong>
            <span>OPERATIONS DESK <i>•</i> CHAPEL STREET</span>
          </span>
        </a>
        <div className="topbar-center">
          <span className="live-dot" />
          <span>Desk date {deskDate || "…"}</span>
          <span className="divider-dot">/</span>
          <span className="desk-open">gpt-6-luna team</span>
        </div>
        <div className="topbar-actions">
          <div className="balance-pill" aria-live="polite">
            <span className="balance-icon"><Wallet size={16} /></span>
            <span className="balance-copy"><small>CHECKING BALANCE</small><strong>{money(balance)}</strong></span>
          </div>
          <button className="reset-button" onClick={() => void handleReset()} disabled={resetting || runningId !== null}>
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
            <p className="heading-subtitle">Three open tickets, five agents. Watch them work, then sign off on the money.</p>
          </div>
          <div className="header-stamp">
            <span className="stamp-icon"><ShieldCheck size={18} /></span>
            <span><strong>Nothing pays without you</strong><small>Agents stage it, you approve it</small></span>
          </div>
        </section>

        {error && <div className="banner banner-error" role="alert"><CircleAlert size={17} /><span>{error}</span><button onClick={() => setError("")} aria-label="Dismiss error">×</button></div>}
        {notice && <div className="banner banner-success" role="status"><CheckCircle2 size={17} /><span>{notice}</span><button onClick={() => setNotice("")} aria-label="Dismiss message">×</button></div>}

        <div className="desk-grid">
          <aside className="ticket-rail panel">
            <div className="panel-heading rail-heading">
              <div><span className="section-kicker">INBOUND</span><h2>Ticket stack</h2></div>
              <span className="count-badge">{tickets.filter((row) => row.board_status === "open").length} open</span>
            </div>
            <p className="panel-intro">Pick a ticket to see its workstream.</p>
            <div className="ticket-list">
              {[101, 102, 103].map((id) => {
                const row = tickets.find((item) => item.ticket_id === id);
                const rowStatus = row?.status ?? "open";
                return (
                  <button
                    className={`ticket-card ${selectedId === id ? "selected" : ""}`}
                    key={id}
                    onClick={() => { setSelectedId(id); setError(""); setNotice(""); }}
                    aria-pressed={selectedId === id}
                  >
                    <div className="ticket-card-top">
                      <span className="ticket-number">CC–{id}</span>
                      <span className={`status-badge ${statusClass(rowStatus)}`}><i />{STATUS_LABELS[rowStatus] ?? rowStatus}</span>
                    </div>
                    <strong className="ticket-title">{row ? `${row.subject} · ${row.requester}` : loading ? "Loading…" : "Unavailable"}</strong>
                    <span className="ticket-request">{row?.request}</span>
                    <div className="ticket-card-bottom">
                      <span className="ticket-type">{row?.type.replace("_", " ")}{row?.pending_approvals ? ` · ${row.pending_approvals} to approve` : ""}</span>
                      <ChevronRight className={selectedId === id ? "chevron-active" : ""} size={16} />
                    </div>
                    {runningId === id && <div className="ticket-running"><span className="mini-pulse" /> Team working</div>}
                  </button>
                );
              })}
            </div>

            <div className="rail-note">
              <div className="note-icon"><Sparkles size={15} /></div>
              <div><strong>Any agent can call any other</strong><span>Boss starts every ticket; specialists hand work to each other when the facts call for it.</span></div>
            </div>

            <div className="rail-footer">
              <span className="tiny-ledger"><FileText size={14} /> AUDIT TRAIL</span>
              <span>{events.length} steps on CC–{selectedId}</span>
            </div>
          </aside>

          <section className="activity-panel panel">
            <div className="panel-heading activity-heading">
              <div><span className="section-kicker">COLLABORATION</span><h2>Agent live stream</h2></div>
              <div className={runningId === selectedId ? "stream-state is-live" : "stream-state"}>
                <span className="state-dot" />{runningId === selectedId ? "LIVE" : "READY"}
              </div>
            </div>
            <div className="agent-roster" aria-label="Agent team">
              {TEAM.map((key) => (
                <div className={`roster-agent ${involved.has(key) ? "roster-involved" : ""}`} key={key} title={AGENTS[key].role}>
                  <span className="avatar-small" style={{ "--agent-color": AGENTS[key].color } as CSSProperties}>{AGENTS[key].initials}</span>
                  <span>{AGENTS[key].label}</span>
                </div>
              ))}
            </div>
            <div className="stream-context">
              <div className="context-title">
                <span className="context-ticket">CC–{selectedId}</span>
                <span className="context-description">{ticket ? `${ticket.subject}: ${ticket.request}` : ""}</span>
              </div>
              <button
                className="run-button"
                onClick={() => void handleRun(selectedId)}
                disabled={runningId !== null || loading || pending.length > 0}
              >
                {runningId === selectedId ? <Loader2 className="spin" size={16} /> : <Send size={15} />}
                {runLabel}
              </button>
            </div>

            <div className="activity-feed" aria-live="polite">
              {visibleEvents.length === 0 && runningId !== selectedId && (
                <div className="feed-empty">
                  <div className="empty-orbit"><Activity size={23} /></div>
                  <strong>The desk is waiting</strong>
                  <span>Dispatch the team to watch delegations, tool calls and decisions appear here.</span>
                  <button onClick={() => void handleRun(selectedId)} disabled={runningId !== null || loading}>
                    <Play size={14} fill="currentColor" /> Run this ticket
                  </button>
                </div>
              )}
              {runningId === selectedId && visibleEvents.length === 0 && (
                <div className="feed-loading"><span className="loader-ring" /><div><strong>Starting the team…</strong><span>Waiting for Boss to read the ticket</span></div></div>
              )}

              {visibleEvents.map((event, index) => {
                const key = agentKey(event.agent);
                const agent = AGENTS[key];
                const newRun = event.run_id !== lastRun && event.action === "ticket_run_started";
                lastRun = event.run_id;
                return (
                  <div key={`${event.run_id}-${event.timestamp}-${index}`}>
                    {newRun && <div className="run-divider"><span>Run started {clock(event.timestamp)}</span></div>}
                    <article className={`event-row event-${key}`}>
                      <div className="event-timeline">
                        <span className="avatar-event" style={{ "--agent-color": agent.color } as CSSProperties}>{agent.initials}</span>
                        {index < visibleEvents.length - 1 && <span className="timeline-stem" />}
                      </div>
                      <div className="event-content">
                        <div className="event-meta">
                          <strong style={{ color: agent.color }}>{agent.label}</strong>
                          <span className="event-action">{ACTION_LABELS[event.action ?? ""] ?? event.action}</span>
                          <time>{clock(event.timestamp)}</time>
                        </div>
                        {event.delegation_target && (
                          <div className="delegation-line"><ArrowRight size={13} /> Handed to <strong>{AGENTS[agentKey(event.delegation_target)].label}</strong></div>
                        )}
                        {event.text && !event.tool && (
                          <div className={event.kind === "speech" ? "speech-bubble" : "thought-bubble"}>
                            {event.kind === "speech" ? <MessageCircle size={14} /> : <Sparkles size={13} />}
                            <span>{event.text}</span>
                          </div>
                        )}
                        {event.findings.length > 0 && (
                          <ul className="finding-list">{event.findings.map((finding, i) => <li key={i}>{finding}</li>)}</ul>
                        )}
                        {event.tool && (
                          <div className="tool-chip">
                            <span className="tool-glyph"><Box size={13} /></span>
                            <strong>{event.tool.name}</strong>
                            {event.tool.arguments && <code>{JSON.stringify(event.tool.arguments)}</code>}
                          </div>
                        )}
                        {event.tool?.result && (
                          <details className="tool-result"><summary>MCP result</summary><pre>{event.tool.result}</pre></details>
                        )}
                        {event.tool && event.action === "error" && <div className="tool-error">{event.text}</div>}
                      </div>
                    </article>
                  </div>
                );
              })}
            </div>
            <div className="activity-footer"><span><span className="live-dot" /> Refreshes every second while the team is running</span><span>{visibleEvents.length} events</span></div>
          </section>

          <aside className="decision-panel">
            <section className="decision-card panel">
              <div className="panel-heading decision-heading">
                <div><span className="section-kicker">YOUR DESK</span><h2>Human decisions</h2></div>
                <span className="lock-badge"><ShieldCheck size={15} /> HITL</span>
              </div>
              <label className="approver-field">
                <UserCheck size={14} />
                <span>Approving as</span>
                <input value={approver} onChange={(event) => setApprover(event.target.value)} maxLength={60} />
              </label>

              {pending.length > 0 ? (
                <div className="approval-stack">
                  {pending.map((item) => {
                    const isOrder = item.kind === "purchase_order";
                    const waiting = item.waits_for_invoices.filter((id) => pendingInvoiceIds.has(id));
                    const short = balance != null && item.amount > balance;
                    return (
                      <div className="approval-card" key={item.approval_id}>
                        <div className="approval-topline"><span className="approval-alert"><Clock3 size={13} /> {isOrder ? "PURCHASE ORDER" : "PAYMENT"} #{item.approval_id}</span><span className="pending-dot" /></div>
                        <div className="approval-amount">{money(item.amount)}</div>
                        <div className="approval-payee"><span className="payee-icon">{isOrder ? <Box size={15} /> : <Banknote size={15} />}</span><span><small>{isOrder ? "ORDER FROM" : "PAY TO"}</small><strong>{item.payee}</strong></span></div>
                        <div className="approval-reason"><span>Why</span><strong>{item.reason}</strong></div>
                        <div className="approval-reason"><span>Reference</span><strong>{item.reference}</strong></div>
                        {waiting.length > 0 && <p className="approval-missing">Waits for invoice {waiting.join(", ")}: approve that payment first.</p>}
                        {short && <p className="approval-missing">Checking can't cover this. The approve route will refuse it.</p>}
                        <div className="balance-impact"><span><ArrowDownRight size={15} /> Checking after approval</span><strong>{balance == null ? "—" : money(balance - item.amount)}</strong></div>
                        <button
                          className="approve-button"
                          onClick={() => void handleApprove(item)}
                          disabled={approvingId !== null || runningId !== null}
                        >
                          {approvingId === item.approval_id ? <Loader2 className="spin" size={16} /> : <Check size={16} />}
                          {isOrder ? "Approve purchase order" : "Approve payment"}
                        </button>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="decision-empty">
                  <span className="decision-empty-icon"><ShieldCheck size={20} /></span>
                  <strong>Nothing waiting on you</strong>
                  <span>When Accounting stages a payment or an order, it stops here until you approve it.</span>
                </div>
              )}

              {decided.length > 0 && (
                <div className="approved-list">
                  <div className="ledger-title"><span><CheckCircle2 size={14} /> APPROVED ON THIS TICKET</span></div>
                  {decided.map((item) => (
                    <div className="approved-row" key={item.approval_id}>
                      <span className="approved-check"><Check size={12} /></span>
                      <span><strong>{item.payee}</strong><small>{item.decided_by} · {item.reference}</small></span>
                      <b>−{money(item.amount)}</b>
                    </div>
                  ))}
                </div>
              )}
            </section>

            {drafts.length > 0 && (
              <section className="draft-card panel">
                <div className="panel-heading">
                  <div><span className="section-kicker">STAYS ON THE BOARD</span><h2>Customer drafts</h2></div>
                  <Mail size={17} />
                </div>
                {drafts.map((draft) => (
                  <div className="draft-body" key={draft.id}>
                    <div className="draft-meta"><span>To {draft.requester}</span><span className="draft-flag">DRAFT · NOT SENT</span></div>
                    <p>{draft.body}</p>
                  </div>
                ))}
              </section>
            )}

            <section className="specialist-card panel">
              <div className="panel-heading specialist-heading">
                <div><span className="section-kicker">AFTER THE HUDDLE</span><h2>Who did what</h2></div>
                <Users size={17} />
              </div>
              {contributions.length === 0 ? (
                <div className="specialist-empty">
                  <span className="small-icon"><Sparkles size={14} /></span>
                  <span>Each agent's report shows up here once the team runs.</span>
                </div>
              ) : (
                <div className="contribution-list">
                  {contributions.map(({ key, report, tools, steps }) => (
                    <div className="contribution-row" key={key}>
                      <span className="contribution-avatar" style={{ "--agent-color": AGENTS[key].color } as CSSProperties}>{AGENTS[key].initials}</span>
                      <div>
                        <strong>{AGENTS[key].label}<small>{AGENTS[key].role}</small></strong>
                        <p>{report}</p>
                        {tools.length > 0 && <p className="contribution-tools">{tools.join(" · ")}</p>}
                      </div>
                      <span className="contribution-count">{steps}</span>
                    </div>
                  ))}
                </div>
              )}
              <div className="ticket-outcome">
                <span className={`outcome-mark ${status === "resolved" ? "outcome-done" : ""}`}>{status === "resolved" ? <BadgeCheck size={17} /> : <CircleAlert size={16} />}</span>
                <span><small>TICKET OUTCOME</small><strong>{ticket?.latest_note ?? "Not run yet"}</strong></span>
                <span className={`status-badge ${statusClass(status)}`}><i />{STATUS_LABELS[status] ?? status}</span>
              </div>
            </section>
          </aside>
        </div>

        <footer className="desk-footer">
          <span><span className="footer-mark"><Store size={13} /></span> Campus Customs <i>·</i> Operator console</span>
          <span><ShieldCheck size={13} /> Drafts stay local <i>·</i> Every payment needs a human</span>
        </footer>
      </main>
    </div>
  );
}

export default App;
