"""
Payments router
- POST /pay          → initiate STK push
- POST /mpesa/callback → M-Pesa webhook (called by Safaricom)
- GET  /pay/status/{phone} → poll payment status (for frontend polling)
"""
import uuid
import logging
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update

from database import Voter, Payment, PaymentStatus, TxStatus, get_db
from mpesa_service import initiate_stk_push
from voters import normalize_phone
from config import settings

logger = logging.getLogger(__name__)
router = APIRouter()


class PayRequest(BaseModel):
    phone_number: str


@router.post("/pay")
async def pay(req: PayRequest, db: AsyncSession = Depends(get_db)):
    """
    Initiate M-Pesa STK push for voting fee (KES 5).
    Checks voter exists, not already paid, not already voted.
    """
    phone = normalize_phone(req.phone_number)

    # Guard: voter must exist
    result = await db.execute(select(Voter).where(Voter.phone_number == phone))
    voter = result.scalar_one_or_none()
    if not voter:
        raise HTTPException(status_code=404, detail="Voter not registered. Call /start first.")

    if voter.has_voted:
        raise HTTPException(status_code=403, detail="This number has already voted.")

    if voter.payment_status == PaymentStatus.PAID:
        return {"message": "Already paid. Proceed to vote.", "already_paid": True}

    # Create pending payment record
    payment = Payment(
        id=uuid.uuid4(),
        phone_number=phone,
        amount=settings.VOTE_FEE_KES,
        status=TxStatus.PENDING
    )
    db.add(payment)
    await db.flush()

    # Update voter status to PENDING
    await db.execute(
        update(Voter)
        .where(Voter.phone_number == phone)
        .values(payment_status=PaymentStatus.PENDING)
    )

    # Send STK push
    try:
        stk_response = await initiate_stk_push(phone, settings.VOTE_FEE_KES)
        checkout_id = stk_response.get("CheckoutRequestID")
        merchant_id = stk_response.get("MerchantRequestID")

        # Save checkout ID for callback matching
        await db.execute(
            update(Payment)
            .where(Payment.id == payment.id)
            .values(
                checkout_request_id=checkout_id,
                merchant_request_id=merchant_id
            )
        )

        return {
            "message": "STK push sent. Check your phone and enter M-Pesa PIN.",
            "checkout_request_id": checkout_id,
            "phone_number": phone
        }

    except Exception as e:
        logger.error(f"STK push failed for {phone}: {e}")
        await db.execute(
            update(Payment)
            .where(Payment.id == payment.id)
            .values(status=TxStatus.FAILED)
        )
        await db.execute(
            update(Voter)
            .where(Voter.phone_number == phone)
            .values(payment_status=PaymentStatus.UNPAID)
        )
        raise HTTPException(status_code=502, detail=f"M-Pesa request failed: {str(e)}")


@router.post("/mpesa/callback")
async def mpesa_callback(request: Request, db: AsyncSession = Depends(get_db)):
    """
    M-Pesa Daraja callback endpoint.
    Safaricom POSTs here after the voter enters their PIN.
    ResultCode 0 = success.
    """
    try:
        body = await request.json()
        logger.info(f"M-Pesa callback received: {body}")

        stk_callback = body.get("Body", {}).get("stkCallback", {})
        result_code = stk_callback.get("ResultCode")
        result_desc = stk_callback.get("ResultDesc", "")
        checkout_request_id = stk_callback.get("CheckoutRequestID")
        merchant_request_id = stk_callback.get("MerchantRequestID")

        # Find the payment by CheckoutRequestID
        result = await db.execute(
            select(Payment).where(Payment.checkout_request_id == checkout_request_id)
        )
        payment = result.scalar_one_or_none()

        if not payment:
            logger.warning(f"No payment found for CheckoutRequestID: {checkout_request_id}")
            return {"ResultCode": 0, "ResultDesc": "Accepted"}

        if result_code == 0:
            # ✅ Payment successful
            metadata = stk_callback.get("CallbackMetadata", {}).get("Item", [])
            mpesa_receipt = None
            for item in metadata:
                if item.get("Name") == "MpesaReceiptNumber":
                    mpesa_receipt = item.get("Value")
                    break

            # Update payment record
            await db.execute(
                update(Payment)
                .where(Payment.id == payment.id)
                .values(
                    status=TxStatus.PAID,
                    mpesa_receipt=mpesa_receipt,
                    result_code=result_code,
                    result_desc=result_desc
                )
            )

            # Unlock voter for voting
            await db.execute(
                update(Voter)
                .where(Voter.phone_number == payment.phone_number)
                .values(payment_status=PaymentStatus.PAID)
            )

            logger.info(f"✅ Payment confirmed for {payment.phone_number}, receipt: {mpesa_receipt}")

        else:
            # ❌ Payment failed or cancelled
            await db.execute(
                update(Payment)
                .where(Payment.id == payment.id)
                .values(
                    status=TxStatus.FAILED,
                    result_code=result_code,
                    result_desc=result_desc
                )
            )
            await db.execute(
                update(Voter)
                .where(Voter.phone_number == payment.phone_number)
                .values(payment_status=PaymentStatus.UNPAID)
            )
            logger.warning(f"❌ Payment failed for {payment.phone_number}: {result_desc}")

        await db.commit()

    except Exception as e:
        logger.error(f"Callback processing error: {e}")

    # Always return 200 to Safaricom
    return {"ResultCode": 0, "ResultDesc": "Accepted"}


@router.get("/pay/status/{phone_number}")
async def payment_status(phone_number: str, db: AsyncSession = Depends(get_db)):
    """
    Frontend polls this after STK push to check if payment went through.
    """
    phone = normalize_phone(phone_number)

    result = await db.execute(select(Voter).where(Voter.phone_number == phone))
    voter = result.scalar_one_or_none()

    if not voter:
        raise HTTPException(status_code=404, detail="Voter not found.")

    # Also get latest payment receipt
    pay_result = await db.execute(
        select(Payment)
        .where(Payment.phone_number == phone, Payment.status == TxStatus.PAID)
        .order_by(Payment.updated_at.desc())
        .limit(1)
    )
    payment = pay_result.scalar_one_or_none()

    return {
        "phone_number": phone,
        "payment_status": voter.payment_status,
        "paid": voter.payment_status == PaymentStatus.PAID,
        "has_voted": voter.has_voted,
        "mpesa_receipt": payment.mpesa_receipt if payment else None
    }