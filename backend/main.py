"""
Kenya Election 2027 — FastAPI Backend
Render URL: https://election-system-api.onrender.com
"""

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from typing import Optional
import uvicorn, os, json, logging, random, string, base64, httpx
from datetime import datetime, date
from contextlib import asynccontextmanager
import asyncpg

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── CONFIG ──────────────────────────────────────────────────────────────────
DATABASE_URL        = os.getenv("DATABASE_URL", "")
MPESA_CONSUMER_KEY  = os.getenv("MPESA_CONSUMER_KEY", "")
MPESA_CONSUMER_SECRET = os.getenv("MPESA_CONSUMER_SECRET", "")
MPESA_SHORTCODE     = os.getenv("MPESA_SHORTCODE", "174379")
MPESA_PASSKEY       = os.getenv("MPESA_PASSKEY", "bfb279f9aa9bdbcf158e97dd71a467cd2e0c893059b10f78e6b72ada1ed2c919")
MPESA_CALLBACK_URL  = os.getenv("MPESA_CALLBACK_URL", "https://election-system-api.onrender.com/api/mpesa-callback")
ENV                 = os.getenv("ENV", "development")

# Sandbox vs Production Daraja URLs
IS_SANDBOX = ENV != "production"
DARAJA_BASE = "https://sandbox.safaricom.co.ke" if IS_SANDBOX else "https://api.safaricom.co.ke"

VALID_CANDIDATES = {"ruto", "kalonzo", "mwangi", "matiangi"}

# ─── DATABASE ────────────────────────────────────────────────────────────────
pool: asyncpg.Pool = None

async def get_pool() -> asyncpg.Pool:
    global pool
    if pool is None:
        # Strip SQLAlchemy prefix if present
        url = DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
        pool = await asyncpg.create_pool(url, min_size=2, max_size=10)
    return pool

async def init_db():
    p = await get_pool()
    async with p.acquire() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS voters (
            id            SERIAL PRIMARY KEY,
            national_id   VARCHAR(10) UNIQUE NOT NULL,
            full_name     TEXT NOT NULL,
            phone         VARCHAR(15) NOT NULL,
            county        TEXT NOT NULL,
            subcounty     TEXT NOT NULL,
            dob           DATE NOT NULL,
            registered_at TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS payments (
            id            SERIAL PRIMARY KEY,
            phone         VARCHAR(15) NOT NULL,
            national_id   VARCHAR(10) NOT NULL,
            checkout_id   VARCHAR(100) UNIQUE,
            amount        INTEGER DEFAULT 5,
            status        VARCHAR(20) DEFAULT 'PENDING',
            mpesa_receipt VARCHAR(30),
            created_at    TIMESTAMPTZ DEFAULT NOW(),
            updated_at    TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE TABLE IF NOT EXISTS votes (
            id            SERIAL PRIMARY KEY,
            national_id   VARCHAR(10) UNIQUE NOT NULL,
            candidate     VARCHAR(30) NOT NULL,
            mpesa_receipt VARCHAR(30) UNIQUE NOT NULL,
            vote_id       VARCHAR(30) UNIQUE NOT NULL,
            county        TEXT NOT NULL,
            cast_at       TIMESTAMPTZ DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS idx_payments_checkout ON payments(checkout_id);
        CREATE INDEX IF NOT EXISTS idx_payments_national_id ON payments(national_id);
        CREATE INDEX IF NOT EXISTS idx_votes_candidate ON votes(candidate);
        """)
        logger.info("✅ Database tables ready")

# ─── MPESA HELPERS ───────────────────────────────────────────────────────────
async def get_mpesa_token() -> str:
    """Get OAuth access token from Daraja"""
    credentials = base64.b64encode(
        f"{MPESA_CONSUMER_KEY}:{MPESA_CONSUMER_SECRET}".encode()
    ).decode()

    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{DARAJA_BASE}/oauth/v1/generate?grant_type=client_credentials",
            headers={"Authorization": f"Basic {credentials}"},
            timeout=15.0
        )
        r.raise_for_status()
        data = r.json()
        token = data.get("access_token")
        if not token:
            raise HTTPException(500, detail=f"Daraja token error: {data}")
        logger.info("✅ Daraja token obtained")
        return token

def get_mpesa_password() -> tuple[str, str]:
    """Generate Daraja STK password and timestamp"""
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    raw = f"{MPESA_SHORTCODE}{MPESA_PASSKEY}{timestamp}"
    password = base64.b64encode(raw.encode()).decode()
    return password, timestamp

async def send_stk_push(phone: str, amount: int = 5) -> dict:
    """
    Send STK Push via Safaricom Daraja API.
    Phone must be in format 2547XXXXXXXX (no +)
    """
    token = await get_mpesa_token()
    password, timestamp = get_mpesa_password()

    # Ensure correct phone format: 2547XXXXXXXX
    if phone.startswith("0"):
        phone_fmt = "254" + phone[1:]
    elif phone.startswith("+"):
        phone_fmt = phone[1:]
    elif phone.startswith("254"):
        phone_fmt = phone
    else:
        phone_fmt = "254" + phone  # 9-digit input

    payload = {
        "BusinessShortCode": MPESA_SHORTCODE,
        "Password": password,
        "Timestamp": timestamp,
        "TransactionType": "CustomerPayBillOnline",
        "Amount": amount,
        "PartyA": phone_fmt,
        "PartyB": MPESA_SHORTCODE,
        "PhoneNumber": phone_fmt,
        "CallBackURL": MPESA_CALLBACK_URL,
        "AccountReference": "IEBC2027",
        "TransactionDesc": "Kenya Election 2027 Voting Fee"
    }

    logger.info(f"STK Push → {phone_fmt} | Amount: KES {amount}")

    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{DARAJA_BASE}/mpesa/stkpush/v1/processrequest",
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            },
            timeout=20.0
        )
        data = r.json()
        logger.info(f"STK response: {data}")

        if r.status_code != 200 or data.get("ResponseCode") != "0":
            err = data.get("errorMessage") or data.get("ResponseDescription") or "STK Push failed"
            raise HTTPException(502, detail=f"M-Pesa error: {err}")

        return data

async def query_stk_status(checkout_id: str) -> dict:
    """Query STK Push transaction status from Daraja"""
    token = await get_mpesa_token()
    password, timestamp = get_mpesa_password()

    payload = {
        "BusinessShortCode": MPESA_SHORTCODE,
        "Password": password,
        "Timestamp": timestamp,
        "CheckoutRequestID": checkout_id
    }

    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{DARAJA_BASE}/mpesa/stkpushquery/v1/query",
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json"
            },
            timeout=15.0
        )
        return r.json()

# ─── UTILS ───────────────────────────────────────────────────────────────────
def gen_vote_id() -> str:
    suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    return f"KE27-{suffix}"

# ─── MODELS ──────────────────────────────────────────────────────────────────
class RegisterRequest(BaseModel):
    full_name:   str  = Field(..., min_length=3)
    national_id: str  = Field(..., min_length=7, max_length=8)
    phone:       str  = Field(..., min_length=9, max_length=9)
    county:      str
    subcounty:   str
    dob:         date

class STKRequest(BaseModel):
    phone:       str
    national_id: str

class VoteRequest(BaseModel):
    national_id:   str
    candidate:     str
    mpesa_receipt: str

# ─── LIFESPAN ────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield
    if pool:
        await pool.close()

# ─── APP ─────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Kenya Election 2027 API",
    version="2.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontend if index.html exists
if os.path.exists("index.html"):
    @app.get("/", response_class=FileResponse)
    async def frontend():
        return FileResponse("index.html")

# ─── HEALTH ──────────────────────────────────────────────────────────────────
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "system": "Kenya General Election 2027",
        "env": ENV,
        "daraja": "sandbox" if IS_SANDBOX else "production",
        "callback_url": MPESA_CALLBACK_URL
    }

# ─── STEP 1: REGISTER ────────────────────────────────────────────────────────
@app.post("/api/register")
async def register_voter(req: RegisterRequest):
    # Age validation — must be 18+
    today = date.today()
    age = today.year - req.dob.year - (
        (today.month, today.day) < (req.dob.month, req.dob.day)
    )
    if age < 18:
        raise HTTPException(400, detail="You must be 18 years or older to vote")

    # Safaricom number validation
    if len(req.phone) != 9 or not req.phone[0] in ('7', '1'):
        raise HTTPException(400, detail="Enter a valid 9-digit Safaricom number")

    p = await get_pool()
    async with p.acquire() as conn:
        # Already voted?
        already_voted = await conn.fetchrow(
            "SELECT id FROM votes WHERE national_id=$1", req.national_id
        )
        if already_voted:
            raise HTTPException(409, detail="This National ID has already voted in this election")

        # Upsert voter
        await conn.execute("""
            INSERT INTO voters (national_id, full_name, phone, county, subcounty, dob)
            VALUES ($1,$2,$3,$4,$5,$6)
            ON CONFLICT (national_id) DO UPDATE
            SET full_name=$2, phone=$3, county=$4, subcounty=$5, dob=$6, registered_at=NOW()
        """, req.national_id, req.full_name, req.phone,
             req.county, req.subcounty, req.dob)

    logger.info(f"✅ Voter registered: {req.national_id} | {req.county}")
    return {"success": True, "message": "Voter verified. Proceed to ballot."}

# ─── STEP 2: INITIATE M-PESA STK PUSH ───────────────────────────────────────
@app.post("/api/initiate-payment")
async def initiate_payment(req: STKRequest):
    # Verify voter exists
    p = await get_pool()
    async with p.acquire() as conn:
        voter = await conn.fetchrow(
            "SELECT national_id FROM voters WHERE national_id=$1", req.national_id
        )
        if not voter:
            raise HTTPException(404, detail="Voter not registered. Complete registration first.")

        # Already voted?
        already_voted = await conn.fetchrow(
            "SELECT id FROM votes WHERE national_id=$1", req.national_id
        )
        if already_voted:
            raise HTTPException(409, detail="This voter has already cast their vote")

    # Send real STK Push via Daraja
    stk_data = await send_stk_push(req.phone, amount=5)
    checkout_id = stk_data["CheckoutRequestID"]

    # Save pending payment
    async with p.acquire() as conn:
        await conn.execute("""
            INSERT INTO payments (phone, national_id, checkout_id, status)
            VALUES ($1,$2,$3,'PENDING')
            ON CONFLICT (checkout_id) DO NOTHING
        """, req.phone, req.national_id, checkout_id)

    logger.info(f"💰 STK sent: {req.phone} | checkout: {checkout_id}")

    return {
        "success": True,
        "checkout_id": checkout_id,
        "merchant_request_id": stk_data.get("MerchantRequestID"),
        "message": "M-Pesa prompt sent to your phone. Enter your PIN to confirm KES 5."
    }

# ─── STEP 3: VERIFY PAYMENT STATUS ──────────────────────────────────────────
@app.get("/api/verify-payment/{checkout_id}")
async def verify_payment(checkout_id: str):
    p = await get_pool()
    async with p.acquire() as conn:
        # First check our DB — callback may have already updated it
        payment = await conn.fetchrow(
            "SELECT status, mpesa_receipt FROM payments WHERE checkout_id=$1",
            checkout_id
        )

        if not payment:
            raise HTTPException(404, detail="Payment record not found")

        if payment["status"] == "CONFIRMED":
            return {
                "status": "CONFIRMED",
                "mpesa_receipt": payment["mpesa_receipt"],
                "message": "Payment confirmed. You may now cast your vote."
            }

        if payment["status"] == "FAILED":
            return {"status": "FAILED", "message": "Payment was cancelled or failed. Please try again."}

    # Still pending — query Daraja directly
    try:
        status_data = await query_stk_status(checkout_id)
        result_code = status_data.get("ResultCode")

        if str(result_code) == "0":
            # Confirmed on Daraja but callback not yet received — mark confirmed
            receipt = status_data.get("MpesaReceiptNumber", f"QRY{checkout_id[-6:]}")
            async with p.acquire() as conn:
                await conn.execute("""
                    UPDATE payments SET status='CONFIRMED', mpesa_receipt=$1, updated_at=NOW()
                    WHERE checkout_id=$2
                """, receipt, checkout_id)
            return {
                "status": "CONFIRMED",
                "mpesa_receipt": receipt,
                "message": "Payment confirmed"
            }
        elif result_code is not None:
            async with p.acquire() as conn:
                await conn.execute("""
                    UPDATE payments SET status='FAILED', updated_at=NOW()
                    WHERE checkout_id=$1
                """, checkout_id)
            return {"status": "FAILED", "message": "Payment not completed"}

    except Exception as e:
        logger.warning(f"Daraja query failed: {e}")

    return {"status": "PENDING", "message": "Waiting for payment confirmation"}

# ─── DARAJA CALLBACK (Safaricom pushes here automatically) ───────────────────
@app.post("/api/mpesa-callback")
async def mpesa_callback(request: Request):
    body = await request.json()
    logger.info(f"📲 M-Pesa callback received: {json.dumps(body)}")

    try:
        stk_cb      = body["Body"]["stkCallback"]
        checkout_id = stk_cb["CheckoutRequestID"]
        result_code = stk_cb["ResultCode"]

        p = await get_pool()
        async with p.acquire() as conn:
            if result_code == 0:
                items   = stk_cb["CallbackMetadata"]["Item"]
                receipt = next((i["Value"] for i in items if i["Name"] == "MpesaReceiptNumber"), None)
                amount  = next((i["Value"] for i in items if i["Name"] == "Amount"), 5)

                await conn.execute("""
                    UPDATE payments
                    SET status='CONFIRMED', mpesa_receipt=$1, amount=$2, updated_at=NOW()
                    WHERE checkout_id=$3
                """, str(receipt), int(amount), checkout_id)

                logger.info(f"✅ Payment confirmed: {receipt} | checkout: {checkout_id}")
            else:
                desc = stk_cb.get("ResultDesc", "Payment failed")
                await conn.execute("""
                    UPDATE payments SET status='FAILED', updated_at=NOW()
                    WHERE checkout_id=$1
                """, checkout_id)
                logger.warning(f"❌ Payment failed: {desc} | checkout: {checkout_id}")

    except Exception as e:
        logger.error(f"Callback parse error: {e} | body: {body}")

    # Always return success to Safaricom
    return {"ResultCode": 0, "ResultDesc": "Accepted"}

# ─── STEP 4: CAST VOTE ───────────────────────────────────────────────────────
@app.post("/api/cast-vote")
async def cast_vote(req: VoteRequest):
    if req.candidate not in VALID_CANDIDATES:
        raise HTTPException(400, detail="Invalid candidate selection")

    p = await get_pool()
    async with p.acquire() as conn:
        # Already voted?
        existing = await conn.fetchrow(
            "SELECT vote_id FROM votes WHERE national_id=$1", req.national_id
        )
        if existing:
            raise HTTPException(409, detail=f"Already voted. Vote ID: {existing['vote_id']}")

        # Verify confirmed payment for this voter
        payment = await conn.fetchrow("""
            SELECT mpesa_receipt FROM payments
            WHERE national_id=$1 AND status='CONFIRMED'
            ORDER BY updated_at DESC LIMIT 1
        """, req.national_id)

        if not payment:
            raise HTTPException(402, detail="No confirmed M-Pesa payment found. Complete payment first.")

        # Verify receipt matches
        if req.mpesa_receipt != payment["mpesa_receipt"]:
            raise HTTPException(400, detail="M-Pesa receipt mismatch. Contact IEBC support.")

        # Get voter county
        voter = await conn.fetchrow(
            "SELECT county FROM voters WHERE national_id=$1", req.national_id
        )

        vote_id = gen_vote_id()
        await conn.execute("""
            INSERT INTO votes (national_id, candidate, mpesa_receipt, vote_id, county)
            VALUES ($1,$2,$3,$4,$5)
        """, req.national_id, req.candidate,
             req.mpesa_receipt, vote_id,
             voter["county"] if voter else "Unknown")

    logger.info(f"🗳️ VOTE CAST: {req.national_id} → {req.candidate} | ID: {vote_id}")

    return {
        "success": True,
        "vote_id": vote_id,
        "candidate": req.candidate,
        "timestamp": datetime.utcnow().isoformat(),
        "message": "Kura yako imehifadhiwa. Your vote has been securely recorded. Asante!"
    }

# ─── LIVE RESULTS ────────────────────────────────────────────────────────────
@app.get("/api/results")
async def get_results():
    p = await get_pool()
    async with p.acquire() as conn:
        rows  = await conn.fetch("""
            SELECT candidate, COUNT(*) AS votes
            FROM votes GROUP BY candidate ORDER BY votes DESC
        """)
        total = await conn.fetchval("SELECT COUNT(*) FROM votes") or 0

    tally = {c: 0 for c in VALID_CANDIDATES}
    for row in rows:
        tally[row["candidate"]] = int(row["votes"])

    results = []
    for cand_id, votes in sorted(tally.items(), key=lambda x: -x[1]):
        pct = round((votes / total * 100), 2) if total > 0 else 0.0
        results.append({"candidate_id": cand_id, "votes": votes, "percentage": pct})

    return {
        "total_votes": total,
        "results": results,
        "last_updated": datetime.utcnow().isoformat()
    }

# ─── COUNTY RESULTS ──────────────────────────────────────────────────────────
@app.get("/api/results/county/{county}")
async def county_results(county: str):
    p = await get_pool()
    async with p.acquire() as conn:
        rows  = await conn.fetch("""
            SELECT candidate, COUNT(*) AS votes
            FROM votes WHERE county=$1
            GROUP BY candidate ORDER BY votes DESC
        """, county)
        total = await conn.fetchval(
            "SELECT COUNT(*) FROM votes WHERE county=$1", county
        ) or 0

    return {
        "county": county,
        "total_votes": total,
        "results": [{"candidate_id": r["candidate"], "votes": int(r["votes"])} for r in rows]
    }

# ─── ADMIN STATS ─────────────────────────────────────────────────────────────
@app.get("/api/admin/stats")
async def admin_stats():
    p = await get_pool()
    async with p.acquire() as conn:
        total_voters   = await conn.fetchval("SELECT COUNT(*) FROM voters") or 0
        total_votes    = await conn.fetchval("SELECT COUNT(*) FROM votes") or 0
        total_payments = await conn.fetchval(
            "SELECT COUNT(*) FROM payments WHERE status='CONFIRMED'"
        ) or 0

    return {
        "registered_voters":   total_voters,
        "votes_cast":          total_votes,
        "confirmed_payments":  total_payments,
        "turnout_pct":         round((total_votes / total_voters * 100) if total_voters > 0 else 0, 2),
        "env":                 ENV,
        "daraja_mode":         "sandbox" if IS_SANDBOX else "production"
    }

# ─── RUN ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        reload=(ENV != "production")
    )