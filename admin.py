"""Admin router — protected by token header."""
from fastapi import APIRouter, HTTPException, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from database import Voter, Payment, Vote, PaymentStatus, TxStatus, get_db
from config import settings

router = APIRouter()


async def verify_admin(x_admin_token: str = Header(...)):
    if x_admin_token != settings.ADMIN_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.get("/dashboard", dependencies=[Depends(verify_admin)])
async def admin_dashboard(db: AsyncSession = Depends(get_db)):
    """Full admin stats."""
    total_voters = await db.scalar(select(func.count(Voter.phone_number)))
    paid_voters = await db.scalar(select(func.count(Voter.phone_number)).where(Voter.payment_status == PaymentStatus.PAID))
    total_votes = await db.scalar(select(func.count(Vote.id)))
    total_revenue = await db.scalar(select(func.sum(Payment.amount)).where(Payment.status == TxStatus.PAID)) or 0

    vote_breakdown = await db.execute(
        select(Vote.candidate_id, Vote.candidate_name, func.count(Vote.id).label("votes"))
        .group_by(Vote.candidate_id, Vote.candidate_name)
    )

    return {
        "stats": {
            "total_registered": total_voters,
            "total_paid": paid_voters,
            "total_votes": total_votes,
            "total_revenue_kes": total_revenue
        },
        "vote_breakdown": [
            {"candidate_id": r.candidate_id, "name": r.candidate_name, "votes": r.votes}
            for r in vote_breakdown.all()
        ]
    }