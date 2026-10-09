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
    "check_inventory_stock",
    "check_vendor_restock_eligibility",
    "get_lease_due_status",
    "get_ticket_details",
    "update_ticket_status",
    "get_cash_balance",
    "propose_payment",
    "save_customer_draft",
    "get_pricing_and_margin",
})
AGENTS: tuple[AgentName, ...] = ("Boss", "Inventory", "Accounting", "Facilities", "Customer Service")
MAX_DELEGATION_DEPTH = 6
MAX_AGENT_TURNS_PER_TICKET = 12
MAX_MCP_CALLS_PER_TICKET = 36
REQUEST_LIMIT_PER_AGENT_TURN = 8
TOOL_CALL_LIMIT_PER_AGENT_TURN = 8
TOTAL_TOKEN_LIMIT_PER_AGENT_TURN = 16000
CHAT_MODEL_SETTINGS: dict[str, Any] = {"openai_reasoning_effort": "none"}
MCP_TOOL_REFERENCE = """
FastMCP bridge usage: call call_shop_mcp_tool with `tool_name` and an `arguments` JSON object.
Available contracts:
- check_inventory_stock: {sku, size, order_quantity}
- check_vendor_restock_eligibility: {vendor_id}
- get_lease_due_status: {lease_id}
- get_ticket_details: {ticket_id}
- update_ticket_status: {ticket_id, status, resolution}
- get_cash_balance: {account_name}; omit account_name to use the server default.
- propose_payment: {amount, recipient, reason, ref_id}; ref_id must be invoice:<id> or lease:<id> to stage.
- save_customer_draft: {ticket_id, draft_body}
- get_pricing_and_margin: {sku}
Only these tools are accepted; never access the database outside FastMCP.
"""

AgentKey = Literal["Boss", "Inventory", "Accounting", "Facilities", "Customer Service"]


@dataclass(frozen=True)
class AgentContext:
    """Ticket-scoped dependencies passed to one PydanticAI agent run."""

    team: "AgentTeam"
    ticket_id: int
    active_agent: AgentName
    depth: int


def _child_environment(project_root: Path) -> dict[str, str]:
    """Pass only the runtime variables needed by the local MCP subprocess."""
    names = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "LC_ALL")
    env = {key: os.environ[key] for key in names if key in os.environ}
    env["PYTHONPATH"] = str(project_root)
    return env


@asynccontextmanager
async def open_mcp_session(project_root: Path = HW5_DIR) -> AsyncIterator[ClientSession]:
    """Start the configured local FastMCP module and initialize its stdio session."""
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
            available = {tool.name for tool in discovered.tools}
            missing = sorted(MCP_TOOL_NAMES - available)
            if missing:
                raise RuntimeError(f"FastMCP server is missing required tools: {', '.join(missing)}")
            yield session


class AgentTeam:
    """Five mutually delegating agents with bounded, audited MCP access."""

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
        self.model = OpenAIChatModel(
            MODEL_NAME,
            provider=provider,
            settings=CHAT_MODEL_SETTINGS,
        )
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
                return json.dumps({"ok": False, "error": f"MCP tool failed ({type(exc).__name__})."})

        @agent.tool
        async def delegate_to_agent(
            ctx: RunContext[AgentContext],
            target_agent: AgentKey,
            task: str,
            context: str = "",
        ) -> str:
            """Delegate a ticket-scoped task to any other team member."""
            try:
                if ctx.deps.depth >= MAX_DELEGATION_DEPTH:
                    ctx.deps.team.audit.append(
                        ticket_id=ctx.deps.ticket_id,
                        agent_name=ctx.deps.active_agent,
                        action="delegation",
                        delegation_target=target_agent,
                        reasoning_notes="Delegation blocked: maximum depth of six reached; current findings must be used.",
                        depth=MAX_DELEGATION_DEPTH,
                    )
                    return DelegationResult(
                        ticket_id=ctx.deps.ticket_id,
                        source_agent=ctx.deps.active_agent,
                        target_agent=target_agent,
                        state="blocked",
                        depth=MAX_DELEGATION_DEPTH,
                        summary="Maximum delegation depth of six reached; Boss must use current findings.",
                    ).model_dump_json()
                if ctx.deps.team.agent_turns >= MAX_AGENT_TURNS_PER_TICKET:
                    ctx.deps.team.audit.append(
                        ticket_id=ctx.deps.ticket_id,
                        agent_name=ctx.deps.active_agent,
                        action="delegation",
                        delegation_target=target_agent,
                        reasoning_notes="Delegation blocked: per-ticket agent-turn budget reached.",
                        depth=ctx.deps.depth,
                    )
                    return DelegationResult(
                        ticket_id=ctx.deps.ticket_id,
                        source_agent=ctx.deps.active_agent,
                        target_agent=target_agent,
                        state="blocked",
                        depth=ctx.deps.depth,
                        summary="Per-ticket agent-turn budget reached; Boss must use current findings.",
                    ).model_dump_json()
                request = DelegationRequest(
                    ticket_id=ctx.deps.ticket_id,
                    source_agent=ctx.deps.active_agent,
                    target_agent=target_agent,
                    task=task,
                    context=context,
                    depth=ctx.deps.depth + 1,
                )
                result = await ctx.deps.team.delegate(request)
                return result.model_dump_json()
            except Exception as exc:
                ctx.deps.team.audit.append(
                    ticket_id=ctx.deps.ticket_id,
                    agent_name=ctx.deps.active_agent,
                    action="error",
                    delegation_target=target_agent,
                    reasoning_notes=f"Delegation request was rejected with {type(exc).__name__}.",
                    depth=ctx.deps.depth,
                )
                return json.dumps({"state": "failed", "error": type(exc).__name__})

        return agent

    async def call_shop_tool(
        self,
        context: AgentContext,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> str:
        """Call an allowlisted server tool and audit its observable lifecycle."""
        if tool_name not in MCP_TOOL_NAMES:
            self.audit.append(
                ticket_id=context.ticket_id,
                agent_name=context.active_agent,
                action="error",
                tool_called=str(tool_name)[:100],
                reasoning_notes="Rejected an unregistered tool name before it could reach the MCP server.",
                depth=context.depth,
            )
            raise ValueError(f"Tool {tool_name!r} is not on the fixed FastMCP allowlist.")
        if self.mcp_calls >= MAX_MCP_CALLS_PER_TICKET:
            self.audit.append(
                ticket_id=context.ticket_id,
                agent_name=context.active_agent,
                action="error",
                tool_called=tool_name,
                reasoning_notes="MCP call blocked at the per-ticket safety limit.",
                depth=context.depth,
            )
            raise RuntimeError("MCP tool-call limit reached for this ticket.")
        self.mcp_calls += 1
        self.audit.append(
            ticket_id=context.ticket_id,
            agent_name=context.active_agent,
            action="tool_call_started",
            tool_called=tool_name,
            reasoning_notes="Calling the approved local FastMCP tool; arguments are not copied to the audit log.",
            depth=context.depth,
        )
        try:
            response = await self.session.call_tool(tool_name, arguments)
            if getattr(response, "isError", False):
                message = " ".join(
                    block.text for block in getattr(response, "content", [])
                    if getattr(block, "text", None)
                )
                raise RuntimeError(message[:500] or "FastMCP returned a tool error.")
            structured = getattr(response, "structuredContent", None)
            if structured is None:
                structured = getattr(response, "structured_content", None)
            if structured is not None:
                rendered = json.dumps(structured, ensure_ascii=False, default=str)
            else:
                rendered = "\n".join(
                    block.text for block in getattr(response, "content", [])
                    if getattr(block, "text", None)
                )
            self.audit.append(
                ticket_id=context.ticket_id,
                agent_name=context.active_agent,
                action="tool_call_completed",
                tool_called=tool_name,
                reasoning_notes="FastMCP returned recorded shop data or a local board result.",
                depth=context.depth,
            )
            return rendered
        except Exception as exc:
            self.audit.append(
                ticket_id=context.ticket_id,
                agent_name=context.active_agent,
                action="error",
                tool_called=tool_name,
                reasoning_notes=f"FastMCP call failed with {type(exc).__name__}; no fallback database was used.",
                depth=context.depth,
            )
            raise

    async def delegate(self, request: DelegationRequest) -> DelegationResult:
        """Run a bounded specialist delegation and return a structured result."""
        depth = request.depth
        if depth > MAX_DELEGATION_DEPTH:
            return self._blocked_delegation(request, "Maximum delegation depth of six was reached.")
        if self.agent_turns >= MAX_AGENT_TURNS_PER_TICKET:
            return self._blocked_delegation(request, "Per-ticket agent-turn budget was reached.")
        self.audit.append(
            ticket_id=request.ticket_id,
            agent_name=request.source_agent,
            action="delegation",
            delegation_target=request.target_agent,
            reasoning_notes=f"Delegated a bounded specialist task: {request.task[:300]}",
            depth=depth,
        )
        try:
            result = await self._run_agent(
                request.target_agent,
                task=request.task,
                context=request.context,
                ticket_id=request.ticket_id,
                depth=depth,
            )
            return DelegationResult(
                ticket_id=request.ticket_id,
                source_agent=request.source_agent,
                target_agent=request.target_agent,
                state="completed",
                depth=depth,
                summary=result.summary,
                findings=result.findings,
            )
        except Exception as exc:
            return DelegationResult(
                ticket_id=request.ticket_id,
                source_agent=request.source_agent,
                target_agent=request.target_agent,
                state="failed",
                depth=depth,
                summary="The specialist run failed; keep the ticket unresolved and report the failure to Boss.",
                findings=[type(exc).__name__],
            )

    def _blocked_delegation(self, request: DelegationRequest, reason: str) -> DelegationResult:
        self.audit.append(
            ticket_id=request.ticket_id,
            agent_name=request.source_agent,
            action="delegation",
            delegation_target=request.target_agent,
            reasoning_notes=f"Delegation blocked: {reason}",
            depth=min(request.depth, MAX_DELEGATION_DEPTH),
        )
        return DelegationResult(
            ticket_id=request.ticket_id,
            source_agent=request.source_agent,
            target_agent=request.target_agent,
            state="blocked",
            depth=min(request.depth, MAX_DELEGATION_DEPTH),
            summary=reason,
        )

    async def _run_agent(
        self,
        agent_name: AgentName,
        *,
        task: str,
        context: str,
        ticket_id: int,
        depth: int,
    ) -> AgentResult:
        if depth > MAX_DELEGATION_DEPTH:
            raise RuntimeError("Maximum delegation depth exceeded.")
        if self.agent_turns >= MAX_AGENT_TURNS_PER_TICKET:
            raise RuntimeError("Per-ticket agent-turn budget exceeded.")
        self.agent_turns += 1
        self.audit.append(
            ticket_id=ticket_id,
            agent_name=agent_name,
            action="thinking_started",
            reasoning_notes="Agent analysis started; only concise operational events are logged, not hidden chain-of-thought.",
            depth=depth,
        )
        deps = AgentContext(team=self, ticket_id=ticket_id, active_agent=agent_name, depth=depth)
        prompt = f"Ticket ID: {ticket_id}\nTask: {task}\nRelevant context: {context or 'Use verified ticket and shop facts from MCP.'}"
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
            usage = result.usage() if callable(getattr(result, "usage", None)) else None
            if usage is not None:
                self.model_requests += int(getattr(usage, "requests", 0) or 0)
                self.total_tokens += int(getattr(usage, "total_tokens", 0) or 0)
            output = getattr(result, "output", None)
            if output is None:
                output = result.data
            data = output.model_dump() if isinstance(output, AgentResult) else output
            data.update(ticket_id=ticket_id, agent_name=agent_name)
            if agent_name != "Boss":
                data["ticket_status"] = None
            parsed = AgentResult.model_validate(data)
            self.audit.append(
                ticket_id=ticket_id,
                agent_name=agent_name,
                action="response_synthesis",
                reasoning_notes=f"Structured result prepared: {parsed.summary[:900]}",
                depth=depth,
            )
            return parsed
        except Exception as exc:
            self.audit.append(
                ticket_id=ticket_id,
                agent_name=agent_name,
                action="error",
                reasoning_notes=f"Agent run failed with {type(exc).__name__}; ticket state remains unchanged by this failure.",
                depth=depth,
            )
            raise

    async def run_ticket(self, ticket_id: int) -> TicketRunResponse:
        """Read one ticket through MCP, run Boss and return the auditable result."""
        if ticket_id <= 0:
            raise ValueError("ticket_id must be a positive integer.")
        system_context = AgentContext(self, ticket_id, "Boss", 0)
        ticket_json = await self.call_shop_tool(system_context, "get_ticket_details", {"ticket_id": ticket_id})
        try:
            ticket_data = json.loads(ticket_json)
        except json.JSONDecodeError as exc:
            raise RuntimeError("FastMCP returned ticket details that were not valid JSON.") from exc
        ticket = Ticket.model_validate(ticket_data)
        result = await self._run_agent(
            "Boss",
            task="Triage this real open ticket, delegate only necessary specialist work, synthesize the verified findings, and set a supported status when a clear next step is ready.",
            context=json.dumps(ticket.model_dump(mode="json"), ensure_ascii=False),
            ticket_id=ticket_id,
            depth=0,
        )
        if result.ticket_status and result.ticket_status != ticket.status:
            await self.call_shop_tool(
                system_context,
                "update_ticket_status",
                {
                    "ticket_id": ticket_id,
                    "status": result.ticket_status,
                    "resolution": result.summary,
                },
            )
        final_ticket_json = await self.call_shop_tool(
            system_context,
            "get_ticket_details",
            {"ticket_id": ticket_id},
        )
        try:
            final_ticket = Ticket.model_validate(json.loads(final_ticket_json))
        except (json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError("FastMCP could not confirm the final ticket record.") from exc
        self.audit.append(
            ticket_id=ticket_id,
            agent_name="Boss",
            action="ticket_run_completed",
            reasoning_notes=f"Ticket run completed with status {final_ticket.status}; human approval={result.needs_human_approval}.",
            depth=0,
        )
        return TicketRunResponse(
            run_id=self.run_id,
            ticket=final_ticket,
            result=result,
            agent_turns=max(self.agent_turns, 1),
            model_requests=self.model_requests,
            total_tokens=self.total_tokens,
        )

    async def close(self) -> None:
        """Close the reusable HTTP client without making a request."""
        await self._openai_client.close()


async def run_ticket(ticket_id: int, *, project_root: Path = HW5_DIR) -> TicketRunResponse:
    """Run one ticket with the fixed Portkey model and the local FastMCP server."""
    if ticket_id <= 0:
        raise ValueError("ticket_id must be a positive integer.")
    run_id = str(uuid4())
    audit = AppendOnlyAuditLogger(run_id)
    audit.append(
        ticket_id=ticket_id,
        agent_name="System",
        action="ticket_run_started",
        reasoning_notes="Starting one bounded ticket workflow using the local FastMCP server.",
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
            ticket_id=ticket_id,
            agent_name="System",
            action="error",
            reasoning_notes=f"Ticket run stopped with {type(exc).__name__}; prior audit events were preserved.",
        )
        raise
