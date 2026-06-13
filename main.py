"""
Kenya Election Voting System - FastAPI Backend
M-Pesa Paybill: +254142809568
"""

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from typing import Optional
import uvicorn
import os
import logging

from database import init_db, get_db
import voters
import payments
import votes
import admin
from config import settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Kenya Election Voting System",
    description="Secure phone-based voting with M-Pesa payment verification",
    version="1.0.0"
)

# CORS — allow all origins for web + future mobile apps
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files (frontend)
#app.mount("/static", StaticFiles(directory="../frontend/static"), name="static")

# Include routers
app.include_router(voters.router, prefix="/api", tags=["Voters"])
app.include_router(payments.router, prefix="/api", tags=["Payments"])
app.include_router(votes.router, prefix="/api", tags=["Votes"])
app.include_router(admin.router, prefix="/api/admin", tags=["Admin"])

@app.on_event("startup")
async def startup():
    await init_db()
    logger.info("✅ Database initialized")

@app.get("/")
async def root():
    return FileResponse("../index.html")

@app.get("/health")
async def health():
    return {"status": "ok", "system": "Kenya Election Voting System"}

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", 8000)),
        reload=True
    )
