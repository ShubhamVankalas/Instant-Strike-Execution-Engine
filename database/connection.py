from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel
from config.settings import settings

from sqlalchemy.pool import NullPool

# Create async engine for PostgreSQL connection with NullPool to prevent multi-loop connection sharing issues
engine = create_async_engine(
    settings.DATABASE_URL,
    poolclass=NullPool,
    echo=False,  # Set to True for debugging SQL statements
    future=True
)

# Async session factory
AsyncSessionLocal = sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

async def init_db():
    """
    Initializes PostgreSQL tables according to metadata definitions.
    Creates schema tables asynchronously.
    """
    async with engine.begin() as conn:
        # Runs the SQLModel metadata creations async
        await conn.run_sync(SQLModel.metadata.create_all)

async def get_db():
    """
    Dependency helper providing transactional database sessions.
    Cleans up automatically when the context exits.
    """
    async with AsyncSessionLocal() as session:
        yield session
