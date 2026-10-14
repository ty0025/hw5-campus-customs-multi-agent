"""PydanticAI operations team using only the local FastMCP shop-data boundary."""

from __future__ import annotations

import json
import os
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Literal
from uuid import uuid4

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import AsyncOpenAI
from pydantic_ai import Agent, RunContext, UsageLimits
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from backend.app.settings import HW5_DIR, MODEL_NAME, PORTKEY_API_KEY, PORTKEY_BASE_URL
from backend.audit import AppendOnlyAuditLogger
from backend.models import AgentName, AgentResult, DelegationRequest, DelegationResult, Ticket, TicketRunResponse


MCP_TOOL_NAMES = frozenset({
    "get_ticket_details",
    "check_inventory_stock",
    "find_restock_vendors",
    "check_vendor_restock_eligibility",
    "get_lease_due_status",
    "get_cash_balance",
    "get_pricing_and_margin",
    "propose_payment",
    "propose_purchase_order",
    "save_customer_draft",
    "update_ticket_status",
})
FINANCE_TOOLS = frozenset({"propose_payment", "propose_purchase_order"})
AGENTS: tuple[AgentName, ...] = ("Boss", "Inventory", "Accounting", "Facilities", "Customer Service")

# Token and loop limits. A ticket normally needs 3-5 agent turns and fewer than 20 tool calls.
MAX_DELEGATION_DEPTH = 6
MAX_AGENT_TURNS_PER_TICKET = 12
MAX_MCP_CALLS_PER_TICKET = 40
REQUEST_LIMIT_PER_AGENT_TURN = 10
TOOL_CALL_LIMIT_PER_AGENT_TURN = 10
TOTAL_TOKEN_LIMIT_PER_AGENT_TURN = 60000
CHAT_MODEL_SETTINGS: dict[str, Any] = {"openai_reasoning_effort": "none"}

MCP_TOOL_REFERENCE = """
How to reach shop data: call `call_shop_mcp_tool` with `tool_name` and an `arguments` object.
- get_ticket_details {ticket_id}: ticket fields, desk date, linked invoice vendor, approvals already staged and their status, drafts saved
- check_inventory_stock {sku, size, order_quantity}
- find_restock_vendors {sku}: every vendor with specialty, lead_days, unpaid-invoice hold, arrival date if ordered today
- check_vendor_restock_eligibility {vendor_id}
- get_lease_due_status {lease_id}
- get_cash_balance {}
- get_pricing_and_margin {sku, quantity, discount_percent}
- propose_payment {amount, recipient, reason, ref_id} with ref_id "invoice:<id>" or "lease:<id>"  (Accounting only)
- propose_purchase_order {ticket_id, sku, size, quantity, vendor_id, reason}  (Accounting only)
- save_customer_draft {ticket_id, draft_body}
- update_ticket_status {ticket_id, status, resolution}
Delegate with `delegate_to_agent` {target_agent, task, context}. Put the facts the other agent needs in `context`.
"""

AgentKey = Literal["Boss", "Inventory", "Accounting", "Facilities", "Customer Service"]


def _clip(text: Any, limit: int) -> str:
    value = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, default=str)
    return value if len(value) <= limit else value[: limit - 3] + "..."


@dataclass(frozen=True)
class AgentContext:
    """Ticket-scoped dependencies passed to one PydanticAI agent run."""

    team: "AgentTeam"
    ticket_id: int
    active_agent: AgentName
    depth: int


def _child_environment(project_root: Path) -> dict[str, str]:
    """Pass only the runtime variables needed by the local MCP subprocess (no API key)."""
    names = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL")
    env = {key: os.environ[key] for key in names if key in os.environ}
    env["PYTHONPATH"] = str(project_root)
    env["PYTHONIOENCODING"] = "utf-8"
    return env


@asynccontextmanager
async def open_mcp_session(project_root: Path = HW5_DIR) -> AsyncIterator[ClientSession]:
    """Start the FastMCP server as a stdio subprocess and check its tool list."""
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_server.server"],
        env=_child_environment(project_root),
        cwd=str(project_root),
    )
    async with stdio_client(server, errlog=sys.stderr) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            discovered = await session.list_tools()
            missing = sorted(MCP_TOOL_NAMES - {tool.name for tool in discovered.tools})
            if missing:
                raise RuntimeError(f"FastMCP server is missing required tools: {', '.join(missing)}")
            yield session


class AgentTeam:
    """Five agents; any of them can delegate to any other. All shop facts come through MCP."""

    def __init__(
        self,
        session: ClientSession,
        *,
        run_id: str,
        audit: AppendOnlyAuditLogger,
        project_root: Path = HW5_DIR,
    ) -> None:
        if MODEL_NAME != "gpt-6-luna":
            raise RuntimeError("Homework 5 agent model is locked to gpt-6-luna.")
        if not PORTKEY_API_KEY:
            raise RuntimeError("PORTKEY_API_KEY is not configured in the process environment or project .env.")
        if PORTKEY_BASE_URL != "https://api.portkey.ai/v1":
            raise RuntimeError("Homework 5 agent gateway is locked to the Portkey OpenAI-compatible endpoint.")

        self.session = session
        self.run_id = run_id
        self.audit = audit
        self.project_root = project_root
        self.agent_turns = 0
        self.model_requests = 0
        self.total_tokens = 0
        self.mcp_calls = 0
        self._openai_client = AsyncOpenAI(
            api_key=PORTKEY_API_KEY,
            base_url=PORTKEY_BASE_URL,
            default_headers={"x-portkey-api-key": PORTKEY_API_KEY},
        )
        provider = OpenAIProvider(openai_client=self._openai_client)
        self.model = OpenAIChatModel(MODEL_NAME, provider=provider, settings=CHAT_MODEL_SETTINGS)
        self.agents = {name: self._build_agent(name) for name in AGENTS}

    def _build_agent(self, agent_name: AgentName) -> Agent[AgentContext, AgentResult]:
        prompt_file = self.project_root / "backend" / "prompts" / f"{agent_name.lower().replace(' ', '_')}.md"
        system_prompt = prompt_file.read_text(encoding="utf-8")
        agent = Agent(
            self.model,
            name=f"{agent_name.lower().replace(' ', '_')}_agent",
            deps_type=AgentContext,
            output_type=AgentResult,
            system_prompt=f"{system_prompt}\n\n{MCP_TOOL_REFERENCE}",
            model_settings=CHAT_MODEL_SETTINGS,
        )

        @agent.tool
        async def call_shop_mcp_tool(
            ctx: RunContext[AgentContext],
            tool_name: str,
            arguments: dict[str, Any],
        ) -> str:
            """Call one registered FastMCP tool; all shop-data access uses this boundary."""
            if ctx.deps.active_agent != agent_name:
                return json.dumps({"ok": False, "error": "Agent context mismatch."})
            try:
                return await ctx.deps.team.call_shop_tool(ctx.deps, tool_name, arguments)
            except Exception as exc:
                # The agent sees the real reason (for example "refused: exceeds available cash").
                return json.dumps({"ok": False, "error": _clip(str(exc) or type(exc).__name__, 500)})

        @agent.tool
        async def delegate_to_agent(
            ctx: RunContext[AgentContext],
            target_agent: AgentKey,
            task: str,
            context: str = "",
        ) -> str:
            """Hand a ticket-scoped task to any other team member and get its result back."""
            try:
                request = DelegationRequest(
                    ticket_id=ctx.deps.ticket_id,
                    source_agent=ctx.deps.active_agent,
                    target_agent=target_agent,
                    task=_clip(task, 1200),
                    context=_clip(context, 5000),
                    depth=min(ctx.deps.depth + 1, MAX_DELEGATION_DEPTH),
                )
            except Exception as exc:
                return json.dumps({"state": "failed", "error": _clip(str(exc), 300)})
            if ctx.deps.depth >= MAX_DELEGATION_DEPTH:
                return self._blocked_delegation(request, "Maximum delegation depth of six reached; use current findings.").model_dump_json()
            return (await ctx.deps.team.delegate(request)).model_dump_json()

        return agent

    async def call_shop_tool(self, context: AgentContext, tool_name: str, arguments: dict[str, Any]) -> str:
        """Call an allowlisted MCP tool and log the arguments and result."""
        def log_error(message: str) -> None:
            self.audit.append(
                ticket_id=context.ticket_id, agent_name=context.active_agent, action="error",
                tool_called=str(tool_name)[:100], tool_arguments=arguments if isinstance(arguments, dict) else None,
                message=_clip(message, 1000), depth=context.depth,
            )

        if tool_name not in MCP_TOOL_NAMES:
            log_error("Rejected a tool name that is not on the MCP allowlist.")
            raise ValueError(f"Tool {tool_name!r} is not on the FastMCP allowlist.")
        if tool_name in FINANCE_TOOLS and context.active_agent != "Accounting":
            log_error("Only Accounting may stage payments or purchase orders.")
            raise PermissionError("Only Accounting may stage payment or purchase-order approvals. Delegate to Accounting.")
        if self.mcp_calls >= MAX_MCP_CALLS_PER_TICKET:
            log_error("Per-ticket MCP call limit reached.")
            raise RuntimeError("MCP tool-call limit reached for this ticket.")
        self.mcp_calls += 1
        self.audit.append(
            ticket_id=context.ticket_id, agent_name=context.active_agent, action="tool_call_started",
            tool_called=tool_name, tool_arguments=arguments, depth=context.depth,
        )
        try:
            response = await self.session.call_tool(tool_name, arguments)
            if getattr(response, "isError", False):
                message = " ".join(block.text for block in getattr(response, "content", []) if getattr(block, "text", None))
                raise RuntimeError(message[:500] or "FastMCP returned a tool error.")
            structured = getattr(response, "structuredContent", None)
            if structured is not None:
                rendered = json.dumps(structured, ensure_ascii=False, default=str)
            else:
                rendered = "\n".join(block.text for block in getattr(response, "content", []) if getattr(block, "text", None))
        except Exception as exc:
            log_error(f"MCP call failed: {exc}")
            raise
        self.audit.append(
            ticket_id=context.ticket_id, agent_name=context.active_agent, action="tool_call_completed",
            tool_called=tool_name, tool_arguments=arguments, tool_result=_clip(rendered, 2000), depth=context.depth,
        )
        return rendered

    async def delegate(self, request: DelegationRequest) -> DelegationResult:
        """Run the target agent on the delegated task and return its structured result."""
        if self.agent_turns >= MAX_AGENT_TURNS_PER_TICKET:
            return self._blocked_delegation(request, "Per-ticket agent-turn budget reached; use current findings.")
        self.audit.append(
            ticket_id=request.ticket_id, agent_name=request.source_agent, action="delegation",
            delegation_target=request.target_agent,
            message=_clip(f"{request.task}\nContext: {request.context}" if request.context else request.task, 4000),
            depth=request.depth,
        )
        try:
            result = await self._run_agent(
                request.target_agent, task=request.task, context=request.context,
                ticket_id=request.ticket_id, depth=request.depth,
            )
        except Exception as exc:
            return DelegationResult(
                ticket_id=request.ticket_id, source_agent=request.source_agent, target_agent=request.target_agent,
                state="failed", depth=request.depth,
                summary=_clip(f"{request.target_agent} could not finish: {exc}", 1600),
            )
        return DelegationResult(
            ticket_id=request.ticket_id, source_agent=request.source_agent, target_agent=request.target_agent,
            state="completed", depth=request.depth,
            summary=_clip(f"{result.summary} Next: {result.next_action}" if result.next_action else result.summary, 1600),
            findings=result.findings,
        )

    def _blocked_delegation(self, request: DelegationRequest, reason: str) -> DelegationResult:
        self.audit.append(
            ticket_id=request.ticket_id, agent_name=request.source_agent, action="delegation",
            delegation_target=request.target_agent, message=f"Delegation blocked: {reason}",
            depth=min(request.depth, MAX_DELEGATION_DEPTH),
        )
        return DelegationResult(
            ticket_id=request.ticket_id, source_agent=request.source_agent, target_agent=request.target_agent,
            state="blocked", depth=min(request.depth, MAX_DELEGATION_DEPTH), summary=reason,
        )

    async def _run_agent(self, agent_name: AgentName, *, task: str, context: str, ticket_id: int, depth: int) -> AgentResult:
        if self.agent_turns >= MAX_AGENT_TURNS_PER_TICKET:
            raise RuntimeError("Per-ticket agent-turn budget exceeded.")
        self.agent_turns += 1
        self.audit.append(
            ticket_id=ticket_id, agent_name=agent_name, action="thinking_started",
            message=_clip(task, 1200), depth=depth,
        )
        deps = AgentContext(team=self, ticket_id=ticket_id, active_agent=agent_name, depth=depth)
        prompt = f"Ticket ID: {ticket_id}\nTask: {task}\nContext: {context or 'Read the facts you need through MCP.'}"
        try:
            result = await self.agents[agent_name].run(
                prompt,
                deps=deps,
                usage_limits=UsageLimits(
                    request_limit=REQUEST_LIMIT_PER_AGENT_TURN,
                    tool_calls_limit=TOOL_CALL_LIMIT_PER_AGENT_TURN,
                    total_tokens_limit=TOTAL_TOKEN_LIMIT_PER_AGENT_TURN,
                ),
            )
            usage = result.usage  # a property in pydantic-ai 2.x
            requests = int(getattr(usage, "requests", 0) or 0)
            input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
            output_tokens = int(getattr(usage, "output_tokens", 0) or 0)
            self.model_requests += requests
            self.total_tokens += input_tokens + output_tokens
            data = result.output.model_dump()
            data.update(ticket_id=ticket_id, agent_name=agent_name, findings=data.get("findings", [])[:12])
            if agent_name != "Boss":
                data["ticket_status"] = None
            parsed = AgentResult.model_validate(data)
        except Exception as exc:
            self.audit.append(
                ticket_id=ticket_id, agent_name=agent_name, action="error",
                message=_clip(f"{agent_name} run failed: {type(exc).__name__}: {exc}", 1000), depth=depth,
            )
            raise
        self.audit.append(
            ticket_id=ticket_id, agent_name=agent_name, action="response_synthesis",
            message=_clip(f"{parsed.summary}\nNext: {parsed.next_action}" if parsed.next_action else parsed.summary, 4000),
            findings=parsed.findings, model=MODEL_NAME,
            usage={"requests": requests, "input_tokens": input_tokens, "output_tokens": output_tokens},
            depth=depth,
        )
        return parsed

    async def _read_ticket(self, context: AgentContext) -> Ticket:
        details = json.loads(await self.call_shop_tool(context, "get_ticket_details", {"ticket_id": context.ticket_id}))
        return Ticket.model_validate({key: details[key] for key in Ticket.model_fields if key in details})

    async def run_ticket(self, ticket_id: int) -> TicketRunResponse:
        """Boss reads the ticket, works it with the team, and records a status."""
        system = AgentContext(self, ticket_id, "Boss", 0)
        details = await self.call_shop_tool(system, "get_ticket_details", {"ticket_id": ticket_id})
        result = await self._run_agent(
            "Boss",
            task=("Work this ticket. Delegate only the specialist work it needs, make the final call, "
                  "and record the ticket status with update_ticket_status before you answer."),
            context=details,
            ticket_id=ticket_id,
            depth=0,
        )
        ticket = await self._read_ticket(system)
        if result.ticket_status and ticket.status != result.ticket_status:
            # Boss named a status in its answer but did not save it; save it once.
            await self.call_shop_tool(system, "update_ticket_status", {
                "ticket_id": ticket_id, "status": result.ticket_status, "resolution": _clip(result.summary, 800),
            })
            ticket = await self._read_ticket(system)
        self.audit.append(
            ticket_id=ticket_id, agent_name="Boss", action="ticket_run_completed",
            message=_clip(f"Status {ticket.status}. {result.summary}", 4000), model=MODEL_NAME,
            usage={"agent_turns": self.agent_turns, "requests": self.model_requests,
                   "total_tokens": self.total_tokens, "mcp_calls": self.mcp_calls},
        )
        return TicketRunResponse(
            run_id=self.run_id, ticket=ticket, result=result, agent_turns=max(self.agent_turns, 1),
            model_requests=self.model_requests, total_tokens=self.total_tokens,
        )

    async def close(self) -> None:
        await self._openai_client.close()


async def run_ticket(ticket_id: int, *, project_root: Path = HW5_DIR) -> TicketRunResponse:
    """Run one ticket with the fixed Portkey model and the local FastMCP server."""
    if ticket_id <= 0:
        raise ValueError("ticket_id must be a positive integer.")
    run_id = str(uuid4())
    audit = AppendOnlyAuditLogger(run_id)
    audit.append(
        ticket_id=ticket_id, agent_name="System", action="ticket_run_started", model=MODEL_NAME,
        message="Dashboard started the agent team on this ticket (FastMCP over data/campus_customs_new.db).",
    )
    try:
        async with open_mcp_session(project_root) as session:
            team = AgentTeam(session, run_id=run_id, audit=audit, project_root=project_root)
            try:
                return await team.run_ticket(ticket_id)
            finally:
                await team.close()
    except Exception as exc:
        audit.append(
            ticket_id=ticket_id, agent_name="System", action="error",
            message=_clip(f"Ticket run stopped: {type(exc).__name__}: {exc}", 1000),
        )
        raise
