"""Typed agent, delegation, approval, draft, and audit contracts.

The supplied SQLite ticket schema calls its identifier ``id``, its external
party ``requester``, and its narrative ``notes``. The public Ticket contract
maps those to ``ticket_id``, ``customer_id``, and ``description`` without
adding columns to the source database. ``assigned_agent`` is workflow metadata.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


AgentName = Literal["Boss", "Inventory", "Accounting", "Facilities", "Customer Service"]
TicketStatus = Literal[
    "open", "in_progress", "waiting_approval", "waiting_customer", "blocked", "resolved", "ready_for_staff"
]
DelegationState = Literal["completed", "blocked", "failed"]
ApprovalState = Literal["PENDING_APPROVAL"]
ReferenceType = Literal["invoice", "lease"]


class Ticket(BaseModel):
    """Agent-facing view of one row in ``tickets`` plus assignment metadata."""

    model_config = ConfigDict(extra="forbid")

    ticket_id: int = Field(gt=0, description="The tickets.id value; the SQLite table stores this as id.")
    customer_id: str = Field(description="The requester value; this schema has no separate customer_id column.")
    type: str
    description: str = Field(description="The notes value; the SQLite table has no separate description column.")
    status: TicketStatus
    assigned_agent: AgentName | None = Field(
        default=None, description="Current workflow owner; this is not persisted in the supplied tickets table."
    )
    subject: str
    sku: str | None = None
    size: str | None = None
    quantity: int | None = Field(default=None, ge=0)
    lease_id: int | None = Field(default=None, gt=0)
    invoice_id: int | None = Field(default=None, gt=0)
    invoice_vendor_id: int | None = Field(
        default=None, gt=0, description="Vendor linked to invoice_id through invoices.vendor_id, when present."
    )
    invoice_vendor_name: str | None = Field(
        default=None, description="Vendor name linked to invoice_id through invoices and vendors."
    )
    created_at: str
    shop_date: str


class DelegationRequest(BaseModel):
    """A bounded, ticket-scoped task transfer between two different agents."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    ticket_id: int = Field(gt=0)
    source_agent: AgentName
    target_agent: AgentName
    task: str = Field(min_length=1, max_length=1200)
    context: str = Field(default="", max_length=5000)
    depth: int = Field(ge=0, le=6)

    @field_validator("target_agent")
    @classmethod
    def target_must_be_another_agent(cls, value: AgentName, info: object) -> AgentName:
        source = getattr(info, "data", {}).get("source_agent")
        if source == value:
            raise ValueError("An agent can delegate to any other team member, but not to itself.")
        return value


class DelegationResult(BaseModel):
    """Observable result of a delegation, including an explicit depth block."""

    ticket_id: int
    source_agent: AgentName
    target_agent: AgentName
    state: DelegationState
    depth: int = Field(ge=0, le=6)
    summary: str = Field(max_length=1600)
    findings: list[str] = Field(default_factory=list, max_length=12)


class PaymentProposal(BaseModel):
    """An obligation-specific payment request awaiting a human decision."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    ticket_id: int = Field(gt=0)
    amount: float = Field(gt=0, allow_inf_nan=False)
    recipient: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=800)
    reference_type: ReferenceType | None = None
    ref_id: str | None = Field(default=None, max_length=100)
    status: ApprovalState = "PENDING_APPROVAL"
    approval_id: int | None = Field(default=None, gt=0)
    available_cash: float = Field(ge=0)
    projected_cash_after_approval: float = Field(ge=0)
    persisted_for_human_review: bool = False


class CustomerDraft(BaseModel):
    """A local customer message draft; no outbound delivery status exists."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    ticket_id: int = Field(gt=0)
    recipient: str = Field(min_length=1, max_length=200)
    subject: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=5000)
    status: Literal["draft"] = "draft"
    sent: Literal[False] = False


class AgentResult(BaseModel):
    """Public, structured answer from one specialist or the Boss."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    ticket_id: int = Field(gt=0)
    agent_name: AgentName
    summary: str = Field(min_length=1, max_length=1600)
    findings: list[str] = Field(default_factory=list, max_length=12)
    next_action: str = Field(default="", max_length=600)
    ticket_status: TicketStatus | None = None
    needs_human_approval: bool = False


class TicketRunResponse(BaseModel):
    """Final response and run accounting for a single board ticket."""

    run_id: str
    ticket: Ticket
    result: AgentResult
    agent_turns: int = Field(ge=1)
    model_requests: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class AuditStep(BaseModel):
    """One append-only, sanitized event from an agent's observable workflow."""

    model_config = ConfigDict(extra="forbid")

    timestamp: datetime
    run_id: str = Field(min_length=1, max_length=80)
    sequence: int = Field(ge=1)
    ticket_id: int | None = Field(default=None, gt=0)
    agent_name: AgentName | Literal["System"]
    action: Literal[
        "ticket_run_started", "thinking_started", "delegation", "tool_call_started",
        "tool_call_completed", "response_synthesis", "ticket_run_completed", "error",
    ]
    delegation_target: AgentName | None = None
    tool_called: str | None = Field(default=None, max_length=100)
    reasoning_notes: str = Field(default="", max_length=1600)
    depth: int = Field(default=0, ge=0, le=6)
