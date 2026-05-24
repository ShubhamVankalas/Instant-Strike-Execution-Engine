import uuid
from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field

class Trade(SQLModel, table=True):
    """
    SQLModel representation of the 'trades' table.
    Enforces schemas, default initializers, and search indexes.
    """
    __tablename__ = "trades"

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
        index=True,
        nullable=False
    )
    instrument: str = Field(index=True, max_length=50)
    strike: int = Field(index=True)
    option_type: str = Field(max_length=10)  # 'CE' or 'PE'
    side: str = Field(max_length=10)         # 'BUY' or 'SELL'
    entry_price: float
    entry_spot: float = Field(default=0.0)
    exit_price: Optional[float] = None
    exit_spot: Optional[float] = None
    pnl: float = Field(default=0.0)
    status: str = Field(default="OPEN", index=True, max_length=20)  # 'OPEN' or 'CLOSED'
    signal_reason: Optional[str] = None
    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        index=True,
        nullable=False
    )
    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        nullable=False
    )
