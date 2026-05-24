import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    """
    Application configurations mapped to environment variables.
    Supports reading from local .env files.
    """
    # FastAPI Server configuration
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    ENVIRONMENT: str = "development"

    # PostgreSQL Database Connection URL (Async dialect required)
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/strike_db"

    # Redis Connection URL (Used for tick stream storage and Celery backend)
    REDIS_URL: str = "redis://localhost:6379/0"

    # Data Feed Configuration (Set to True to simulate indexes when market is closed)
    SIMULATION_MODE: bool = True

    # DhanHQ Live Market Feed Credentials
    DHAN_CLIENT_ID: Optional[str] = None
    DHAN_ACCESS_TOKEN: Optional[str] = None

    # Twilio WhatsApp Notification API Credentials
    TWILIO_ACCOUNT_SID: Optional[str] = None
    TWILIO_AUTH_TOKEN: Optional[str] = None
    TWILIO_FROM_NUMBER: Optional[str] = "whatsapp:+14155238886"
    TWILIO_TO_NUMBER: Optional[str] = None

    # LM Studio (OpenAI SDK Compatible) Local Service
    LM_STUDIO_BASE_URL: str = "http://localhost:1234/v1"
    LM_STUDIO_MODEL: str = "gemma-2-2b-it"

    # Quantitative Strategy Constants
    # Trigger threshold representing percentage move (e.g. 0.05 = 5%, 0.0005 = 0.05%)
    SPIKE_THRESHOLD: float = 0.05

    # Configure Pydantic to read from environment variables and fallback to .env file
    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

# Instantiate settings globally for export
settings = Settings()
