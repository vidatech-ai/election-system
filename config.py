"""
Configuration — loads from environment variables / .env file
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore"
    )

    # Database
    DATABASE_URL: str

    # M-Pesa Daraja API
    MPESA_CONSUMER_KEY: str = "your_consumer_key_here"
    MPESA_CONSUMER_SECRET: str = "your_consumer_secret_here"
    MPESA_SHORTCODE: str = "174379"
    MPESA_PASSKEY: str = "your_passkey_here"
    MPESA_CALLBACK_URL: str = "https://yourdomain.com/api/mpesa/callback"

    # Payment destination
    MPESA_RECIPIENT_PHONE: str = "+254142809568"
    MPESA_RECIPIENT_SHORTCODE: str = "174379"

    # Environment
    MPESA_ENV: str = "sandbox"

    # Security
    SECRET_KEY: str = "change-this-to-a-strong-random-secret-key"
    ADMIN_TOKEN: str = "change-this-admin-token"

    # Voting
    VOTE_FEE_KES: int = 5


settings = Settings()

MPESA_AUTH_URL = {
    "sandbox": "https://sandbox.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials",
    "production": "https://api.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials"
}

MPESA_STK_URL = {
    "sandbox": "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest",
    "production": "https://api.safaricom.co.ke/mpesa/stkpush/v1/processrequest"
}