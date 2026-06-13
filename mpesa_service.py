"""
M-Pesa Daraja API Service
Handles: OAuth token, STK Push initiation
Payment goes to: +254142809568
"""
import base64
import httpx
import logging
from datetime import datetime

from config import settings, MPESA_AUTH_URL, MPESA_STK_URL

logger = logging.getLogger(__name__)

# ─── Token Cache ─────────────────────────────────────────────────────────────
_token_cache = {"token": None, "expires_at": None}


async def get_access_token() -> str:
    """Fetch or return cached M-Pesa OAuth token."""
    now = datetime.utcnow().timestamp()
    if _token_cache["token"] and _token_cache["expires_at"] > now:
        return _token_cache["token"]

    credentials = f"{settings.MPESA_CONSUMER_KEY}:{settings.MPESA_CONSUMER_SECRET}"
    encoded = base64.b64encode(credentials.encode()).decode()

    url = MPESA_AUTH_URL[settings.MPESA_ENV]

    async with httpx.AsyncClient() as client:
        resp = await client.get(
            url,
            headers={"Authorization": f"Basic {encoded}"}
        )
        resp.raise_for_status()
        data = resp.json()
        token = data["access_token"]
        expires_in = int(data.get("expires_in", 3600))

        _token_cache["token"] = token
        _token_cache["expires_at"] = now + expires_in - 60  # refresh 60s early

        logger.info("✅ M-Pesa token refreshed")
        return token


def generate_password() -> tuple[str, str]:
    """
    Generate M-Pesa STK push password.
    Returns (password_base64, timestamp)
    """
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    raw = f"{settings.MPESA_SHORTCODE}{settings.MPESA_PASSKEY}{timestamp}"
    password = base64.b64encode(raw.encode()).decode()
    return password, timestamp


async def initiate_stk_push(phone_number: str, amount: int = 5) -> dict:
    """
    Send STK push to voter's phone.
    Payment goes TO: +254142809568 (as configured paybill).

    Args:
        phone_number: Voter's phone in format 2547XXXXXXXX (no +)
        amount: Amount in KES (default 5)

    Returns:
        dict with CheckoutRequestID and MerchantRequestID
    """
    # Normalize phone — remove + prefix if present
    phone = phone_number.lstrip("+")
    if phone.startswith("0"):
        phone = "254" + phone[1:]
    if not phone.startswith("254"):
        phone = "254" + phone

    token = await get_access_token()
    password, timestamp = generate_password()

    url = MPESA_STK_URL[settings.MPESA_ENV]

    payload = {
        "BusinessShortCode": settings.MPESA_SHORTCODE,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": "CustomerPayBillOnline",   # use "CustomerBuyGoodsOnline" for till
        "Amount": str(amount),
        "PartyA": phone,                              # voter's phone
        "PartyB": settings.MPESA_RECIPIENT_SHORTCODE, # paybill receiving payment
        "PhoneNumber": phone,                          # phone to prompt
        "CallBackURL": settings.MPESA_CALLBACK_URL,
        "AccountReference": "VOTE2027",
        "TransactionDesc": f"Kenya 2027 Election Vote Fee - KES {amount}"
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            url,
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            }
        )
        resp.raise_for_status()
        data = resp.json()
        logger.info(f"STK push sent to {phone}: {data}")
        return data