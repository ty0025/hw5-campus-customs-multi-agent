"""Optional FastAPI entry point for running an open ticket through the agent team."""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from backend.agent_team import run_ticket
from backend.app.settings import FRONTEND_ORIGINS
from backend.models import TicketRunResponse


app = FastAPI(title="Campus Customs Agent Operations", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


@app.post("/api/tickets/{ticket_id}/run", response_model=TicketRunResponse)
async def execute_ticket(ticket_id: int) -> TicketRunResponse:
    """Run the ticket workflow; approval and draft safety rules stay enforced."""
    if ticket_id <= 0:
        raise HTTPException(status_code=400, detail="ticket_id must be a positive integer.")
    try:
        return await run_ticket(ticket_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        # Never send provider/client exception details back through the API.
        raise HTTPException(status_code=500, detail="Ticket run failed; review the local audit trail.") from exc
