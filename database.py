"""
Database models — PostgreSQL via SQLAlchemy async
Tables: voters, payments, votes
"""
import uuid
import ssl
import re
from datetime import datetime
from sqlalchemy import (
    Column, String, Boolean, Integer, DateTime,
    Enum, UniqueConstraint, text
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import enum

from config import settings

# ─── Engine & Session ────────────────────────────────────────────────────────

def _clean_database_url(url: str) -> str:
    """
    asyncpg does not accept 'sslmode' as a query parameter.
    Strip it from the URL — SSL is handled via connect_args instead.
    Also ensure the scheme is postgresql+asyncpg.
    """
    # Replace common scheme variants with the asyncpg one
    url = re.sub(r"^postgres(ql)?://", "postgresql+asyncpg://", url)
    url = re.sub(r"^postgresql://", "postgresql+asyncpg://", url)

    # Remove ?sslmode=... or &sslmode=... from the URL
    url = re.sub(r"[?&]sslmode=[^&]*", "", url)

    # Clean up any trailing ? or & left behind
    url = re.sub(r"\?$", "", url)
    url = re.sub(r"&$", "", url)

    return url


DATABASE_URL = _clean_database_url(settings.DATABASE_URL)

# Use a proper SSL context so asyncpg can verify the server certificate.
# For local development without SSL, set DATABASE_URL without sslmode
# and comment out the connect_args below.
import ssl

ssl_context = ssl.create_default_context()
ssl_context.check_hostname = False
ssl_context.verify_mode = ssl.CERT_NONE

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    connect_args={"ssl": ssl_context},
)

AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

Base = declarative_base()


# ─── Enums ───────────────────────────────────────────────────────────────────

class PaymentStatus(str, enum.Enum):
    UNPAID = "UNPAID"
    PENDING = "PENDING"
    PAID = "PAID"
    FAILED = "FAILED"


class TxStatus(str, enum.Enum):
    PENDING = "PENDING"
    PAID = "PAID"
    FAILED = "FAILED"


# ─── Models ──────────────────────────────────────────────────────────────────

class Voter(Base):
    """
    One row per unique phone number.
    payment_status tracks M-Pesa payment.
    has_voted is set TRUE immediately after vote is inserted.
    """
    __tablename__ = "voters"

    phone_number = Column(String(20), primary_key=True)
    payment_status = Column(
        Enum(PaymentStatus),
        default=PaymentStatus.UNPAID,
        nullable=False,
    )
    has_voted = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Payment(Base):
    """
    One row per STK push attempt.
    mpesa_receipt is set by the M-Pesa callback on success.
    UNIQUE on mpesa_receipt prevents receipt replay attacks.
    """
    __tablename__ = "payments"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    phone_number = Column(String(20), nullable=False, index=True)
    amount = Column(Integer, default=5)
    status = Column(Enum(TxStatus), default=TxStatus.PENDING, nullable=False)
    checkout_request_id = Column(String(100), unique=True, nullable=True)
    mpesa_receipt = Column(String(50), unique=True, nullable=True)
    merchant_request_id = Column(String(100), nullable=True)
    result_code = Column(Integer, nullable=True)
    result_desc = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Vote(Base):
    """
    One row per cast vote.
    phone_number UNIQUE — DB-level prevention of double voting.
    """
    __tablename__ = "votes"
    __table_args__ = (
        UniqueConstraint("phone_number", name="uq_vote_phone"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    phone_number = Column(String(20), unique=True, nullable=False)
    candidate_id = Column(String(50), nullable=False)
    candidate_name = Column(String(100), nullable=False)
    mpesa_receipt = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ─── Init ────────────────────────────────────────────────────────────────────

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()