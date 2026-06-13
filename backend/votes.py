"""
Votes router
- POST /vote       → cast a vote
- GET  /results    → live tally (public)
"""
import uuid
import logging
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func

from database import Voter, Vote, Payment, PaymentStatus, TxStatus, get_db
from voters import normalize_phone

logger = logging.getLogger(__name__)
router = APIRouter()

# Valid candidates
CANDIDATES = {
    "ruto-kindiki": "William Ruto & Kindiki Kithure",
    "sifuna-babu": "Edwin Sifuna & Babu Owino",
    "gachagua-kalonzo": "Rigathi Gachagua & Kalonzo Musyoka"
}


class VoteRequest(BaseModel):
    phone_number: str
    candidate_id: str


@router.post("/vote")
async def cast_vote(req: VoteRequest, db: AsyncSession = Depends(get_db)):
    """
    Cast a vote. Triple-guarded:
    1. Voter must have paid
    2. Voter must not have voted yet
    3. DB UNIQUE constraint on phone_number in votes table
    """
    phone = normalize_phone(req.phone_number)

    # Validate candidate
    if req.candidate_id not in CANDIDATES:
        raise HTTPException(status_code=400, detail=f"Invalid candidate. Choose: {list(CANDIDATES.keys())}")

    # Fetch voter
    result = await db.execute(select(Voter).where(Voter.phone_number == phone))
    voter = result.scalar_one_or_none()

    if not voter:
        raise HTTPException(status_code=404, detail="Voter not registered.")

    # ⛔ Guard 1: must have paid
    if voter.payment_status != PaymentStatus.PAID:
        raise HTTPException(status_code=403, detail="Payment required before voting.")

    # ⛔ Guard 2: must not have voted
    if voter.has_voted:
        raise HTTPException(status_code=403, detail="This number has already voted.")

    # Get M-Pesa receipt for audit trail
    pay_result = await db.execute(
        select(Payment)
        .where(Payment.phone_number == phone, Payment.status == TxStatus.PAID)
        .order_by(Payment.updated_at.desc())
        .limit(1)
    )
    payment = pay_result.scalar_one_or_none()

    # ✅ Insert vote (DB UNIQUE on phone_number is the final guard)
    try:
        vote = Vote(
            id=uuid.uuid4(),
            phone_number=phone,
            candidate_id=req.candidate_id,
            candidate_name=CANDIDATES[req.candidate_id],
            mpesa_receipt=payment.mpesa_receipt if payment else None
        )
        db.add(vote)
        await db.flush()

        # 🔒 Immediately lock voter account
        await db.execute(
            update(Voter)
            .where(Voter.phone_number == phone)
            .values(has_voted=True)
        )

        logger.info(f"✅ Vote cast: {phone} → {req.candidate_id}")

        return {
            "message": "Vote cast successfully. Thank you for participating!",
            "vote_id": str(vote.id),
            "candidate": CANDIDATES[req.candidate_id],
            "phone_number": phone,
            "mpesa_receipt": vote.mpesa_receipt
        }

    except Exception as e:
        if "uq_vote_phone" in str(e):
            raise HTTPException(status_code=403, detail="This number has already voted (duplicate blocked at DB level).")
        logger.error(f"Vote insert error: {e}")
        raise HTTPException(status_code=500, detail="Vote recording failed. Please try again.")


@router.get("/results")
async def get_results(db: AsyncSession = Depends(get_db)):
    """Live vote tally — public endpoint."""
    rows = await db.execute(
        select(Vote.candidate_id, Vote.candidate_name, func.count(Vote.id).label("votes"))
        .group_by(Vote.candidate_id, Vote.candidate_name)
    )
    results = rows.all()

    total = sum(r.votes for r in results)

    return {
        "total_votes": total,
        "candidates": [
            {
                "candidate_id": r.candidate_id,
                "candidate_name": r.candidate_name,
                "votes": r.votes,
                "percentage": round((r.votes / total * 100), 1) if total > 0 else 0
            }
            for r in sorted(results, key=lambda x: x.votes, reverse=True)
        ]
    }