"""Voters router — register/check voter by phone number."""
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from database import Voter, PaymentStatus, get_db

router = APIRouter()


class StartRequest(BaseModel):
    phone_number: str


class VoterStatusResponse(BaseModel):
    phone_number: str
    payment_status: str
    has_voted: bool
    exists: bool


@router.post("/start")
async def start(req: StartRequest, db: AsyncSession = Depends(get_db)):
    """
    Register voter by phone number (or return existing).
    Creates voter row if not present.
    """
    phone = normalize_phone(req.phone_number)

    result = await db.execute(select(Voter).where(Voter.phone_number == phone))
    voter = result.scalar_one_or_none()

    if not voter:
        voter = Voter(phone_number=phone)
        db.add(voter)
        await db.flush()
        return {"message": "Voter registered. Proceed to payment.", "exists": False, "phone_number": phone}

    if voter.has_voted:
        raise HTTPException(status_code=403, detail="This number has already voted.")

    return {
        "message": "Voter found. Proceed to payment." if voter.payment_status != "PAID" else "Payment confirmed. Proceed to vote.",
        "exists": True,
        "phone_number": phone,
        "payment_status": voter.payment_status,
        "has_voted": voter.has_voted
    }


@router.get("/voter/{phone_number}")
async def get_voter_status(phone_number: str, db: AsyncSession = Depends(get_db)):
    """Check voter status by phone."""
    phone = normalize_phone(phone_number)
    result = await db.execute(select(Voter).where(Voter.phone_number == phone))
    voter = result.scalar_one_or_none()

    if not voter:
        return {"exists": False}

    return VoterStatusResponse(
        phone_number=voter.phone_number,
        payment_status=voter.payment_status,
        has_voted=voter.has_voted,
        exists=True
    )


def normalize_phone(phone: str) -> str:
    """Normalize to +2547XXXXXXXX format."""
    p = phone.strip().replace(" ", "").replace("-", "")
    if p.startswith("+"):
        return p
    if p.startswith("07") or p.startswith("01"):
        return "+254" + p[1:]
    if p.startswith("254"):
        return "+" + p
    if p.startswith("7") or p.startswith("1"):
        return "+254" + p
    return p