import os
import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from sqlmodel import select

from database.connection import AsyncSessionLocal
from database.models import Trade
from cache.redis_service import RedisService
from trading.pricing_model import calculate_current_premium
from ai_analytics.charts import generate_nifty_trade_chart
from tasks.celery_tasks import send_whatsapp_alert_task

router = APIRouter(prefix="/api/v1")


# ========================================================================
# Pydantic Response Models for Swagger Documentation
# ========================================================================

class HealthResponse(BaseModel):
    """Health check response indicating server and dependency status."""
    status: str = Field(..., example="healthy", description="Overall system health: 'healthy' or 'unhealthy'.")
    redis_connection: bool = Field(..., example=True, description="Whether the Redis cache is reachable.")
    timestamp: str = Field(..., example="2026-05-24T14:30:00.000000", description="UTC timestamp of the health check.")

    model_config = {"json_schema_extra": {"examples": [
        {"status": "healthy", "redis_connection": True, "timestamp": "2026-05-24T14:30:00.000000"}
    ]}}


class TradeResponse(BaseModel):
    """Serialized trade record from PostgreSQL."""
    id: str = Field(..., example="a1b2c3d4-e5f6-7890-abcd-ef1234567890")
    instrument: str = Field(..., example="NIFTY")
    strike: int = Field(..., example=22450)
    option_type: str = Field(..., example="CE", description="'CE' for Call or 'PE' for Put.")
    side: str = Field(..., example="BUY")
    entry_price: float = Field(..., example=168.30, description="Entry premium paid for the option contract.")
    entry_spot: float = Field(..., example=22440.50, description="NIFTY 50 spot price at trade entry.")
    exit_price: Optional[float] = Field(None, example=173.50, description="Exit premium (null if trade is still open).")
    exit_spot: Optional[float] = Field(None, example=22450.75)
    pnl: float = Field(..., example=5.20, description="Realized profit/loss in INR.")
    status: str = Field(..., example="OPEN", description="'OPEN' or 'CLOSED'.")
    signal_reason: Optional[str] = Field(None, example="🚀 +5.12% Spike in 60s (Spot: 22440.50, 60s ago: 21347.00)")
    created_at: str = Field(..., example="2026-05-24T14:05:00.000000")
    updated_at: str = Field(..., example="2026-05-24T14:06:00.000000")


class OpenPositionDetail(BaseModel):
    """A single open position with dynamically computed live PnL."""
    id: str = Field(..., example="a1b2c3d4-e5f6-7890-abcd-ef1234567890")
    instrument: str = Field(..., example="NIFTY")
    strike: int = Field(..., example=22450)
    option_type: str = Field(..., example="CE")
    entry_price: float = Field(..., example=168.30)
    entry_spot: float = Field(..., example=22440.50)
    current_spot: float = Field(..., example=22455.00, description="Latest NIFTY spot price from Redis cache.")
    current_premium: float = Field(..., example=175.55, description="Dynamically estimated current option premium via Delta Greeks.")
    live_pnl: float = Field(..., example=7.25, description="Unrealized profit/loss = current_premium - entry_price.")
    duration_seconds: int = Field(..., example=42, description="How many seconds since the trade was opened.")


class OpenPositionsResponse(BaseModel):
    """Aggregated view of all currently active open positions."""
    current_nifty_spot: float = Field(..., example=22455.00)
    open_positions: list[OpenPositionDetail] = Field(default_factory=list)


class TradeCloseResponse(BaseModel):
    """Response after manually closing a trade."""
    message: str = Field(..., example="Position closed successfully.")
    trade: TradeResponse


class FeedStatusResponse(BaseModel):
    """Current data ingestion feed status."""
    simulation_mode: bool = Field(..., example=True, description="Whether the market simulator is active.")
    active_feed: str = Field(..., example="SIMULATED", description="'SIMULATED' or 'LIVE'.")


class FeedToggleResponse(BaseModel):
    """Response after toggling the data feed source."""
    status: str = Field(..., example="success")
    simulation_mode: bool = Field(..., example=True)
    active_feed: str = Field(..., example="SIMULATED")


# ========================================================================
# API Endpoints
# ========================================================================

@router.get(
    "/health",
    tags=["System"],
    response_model=HealthResponse,
    summary="System Health Check",
    description=(
        "Returns the overall health status of the Instant Strike Execution Engine, "
        "including the connectivity status of the Redis sliding window cache. "
        "Use this endpoint for monitoring and uptime checks."
    ),
    responses={
        200: {
            "description": "System is operational.",
            "content": {"application/json": {"example": {
                "status": "healthy",
                "redis_connection": True,
                "timestamp": "2026-05-24T14:30:00.000000"
            }}},
        },
    },
)
async def health_check():
    """
    Returns server, database connection, and Redis window cache health.
    """
    redis_service = RedisService()
    try:
        redis_ok = redis_service.client.ping()
    except Exception:
        redis_ok = False
        
    return {
        "status": "healthy" if redis_ok else "unhealthy",
        "redis_connection": redis_ok,
        "timestamp": datetime.utcnow().isoformat()
    }


@router.get(
    "/trades",
    response_model=list[TradeResponse],
    tags=["Trading"],
    summary="Get All Trade History",
    description=(
        "Fetches the complete history of all simulated trades from PostgreSQL, "
        "ordered by most recent first. Each record includes entry/exit prices, "
        "the calculated ATM strike, option type (CE/PE), realized PnL, and the "
        "signal reason that triggered the trade."
    ),
    responses={
        200: {
            "description": "List of trade records.",
            "content": {"application/json": {"example": [
                {
                    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                    "instrument": "NIFTY",
                    "strike": 22450,
                    "option_type": "CE",
                    "side": "BUY",
                    "entry_price": 168.30,
                    "entry_spot": 22440.50,
                    "exit_price": 173.50,
                    "exit_spot": 22450.75,
                    "pnl": 5.20,
                    "status": "CLOSED",
                    "signal_reason": "🚀 +5.12% Spike in 60s (Spot: 22440.50, 60s ago: 21347.00)",
                    "created_at": "2026-05-24T14:05:00",
                    "updated_at": "2026-05-24T14:06:00"
                }
            ]}},
        },
    },
)
async def get_all_trades():
    """
    Fetches the history of all simulated trades from PostgreSQL.
    """
    async with AsyncSessionLocal() as session:
        statement = select(Trade).order_by(Trade.created_at.desc())
        result = await session.execute(statement)
        trades = result.scalars().all()
        return trades


@router.get(
    "/trades/open",
    tags=["Trading"],
    summary="Get Open Positions with Live PnL",
    description=(
        "Retrieves all currently active (OPEN) trade positions and calculates their "
        "real-time unrealized PnL using the latest NIFTY spot price from the Redis "
        "cache and the Delta Greeks pricing model.\n\n"
        "**How Live PnL is calculated:**\n"
        "```\n"
        "current_premium = entry_premium + (delta × (current_spot − entry_spot))\n"
        "live_pnl = current_premium − entry_premium\n"
        "```\n"
        "Where `delta = +0.50` for CE (Call) and `delta = −0.50` for PE (Put)."
    ),
    responses={
        200: {
            "description": "Open positions with live PnL data.",
            "content": {"application/json": {"example": {
                "current_nifty_spot": 22455.00,
                "open_positions": [
                    {
                        "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                        "instrument": "NIFTY",
                        "strike": 22450,
                        "option_type": "CE",
                        "entry_price": 168.30,
                        "entry_spot": 22440.50,
                        "current_spot": 22455.00,
                        "current_premium": 175.55,
                        "live_pnl": 7.25,
                        "duration_seconds": 42
                    }
                ]
            }}},
        },
    },
)
async def get_open_positions(request: Request):
    """
    Retrieves all active open positions and calculates their real-time live PnL.
    """
    redis_service = RedisService()
    latest_ticks = redis_service.client.zrange(redis_service.key, -1, -1, withscores=True)
    
    if not latest_ticks:
        return {"message": "No spot tick data available in Redis cache yet."}
        
    member, _ = latest_ticks[0]
    current_spot = float(member.split(":")[0])

    async with AsyncSessionLocal() as session:
        statement = select(Trade).where(Trade.status == "OPEN")
        result = await session.execute(statement)
        open_trades = result.scalars().all()

        positions = []
        for t in open_trades:
            current_premium = calculate_current_premium(
                entry_price=t.entry_price,
                entry_spot=t.entry_spot,
                current_spot=current_spot,
                option_type=t.option_type
            )
            live_pnl = round(current_premium - t.entry_price, 2)
            
            positions.append({
                "id": str(t.id),
                "instrument": t.instrument,
                "strike": t.strike,
                "option_type": t.option_type,
                "entry_price": t.entry_price,
                "entry_spot": t.entry_spot,
                "current_spot": current_spot,
                "current_premium": current_premium,
                "live_pnl": live_pnl,
                "duration_seconds": int((datetime.utcnow() - t.created_at.replace(tzinfo=None)).total_seconds())
            })
            
        return {
            "current_nifty_spot": current_spot,
            "open_positions": positions
        }


@router.post(
    "/trades/{trade_id}/exit",
    tags=["Trading"],
    summary="Manually Close a Trade",
    description=(
        "Manually closes an active open trade position by fetching the latest NIFTY spot "
        "price from Redis, calculating the exit premium using the Delta Greeks model, "
        "updating the PostgreSQL record with exit price, exit spot, and realized PnL, "
        "and dispatching a WhatsApp notification via the Celery background worker.\n\n"
        "**Example usage:**\n"
        "```\n"
        "POST /api/v1/trades/a1b2c3d4-e5f6-7890-abcd-ef1234567890/exit\n"
        "```"
    ),
    responses={
        200: {
            "description": "Trade closed successfully.",
            "content": {"application/json": {"example": {
                "message": "Position closed successfully.",
                "trade": {
                    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
                    "instrument": "NIFTY",
                    "strike": 22450,
                    "option_type": "CE",
                    "pnl": 5.20,
                    "status": "CLOSED"
                }
            }}},
        },
        400: {"description": "No spot price ticks available in Redis to calculate exit premium."},
        404: {"description": "Trade ID not found in the database."},
    },
)
async def manual_exit_trade(trade_id: uuid.UUID):
    """
    Manually closes an active open trade using the latest index spot price.
    """
    redis_service = RedisService()
    latest_ticks = redis_service.client.zrange(redis_service.key, -1, -1, withscores=True)
    
    if not latest_ticks:
        raise HTTPException(status_code=400, detail="Cannot close trade: No spot prices in Redis.")
        
    member, _ = latest_ticks[0]
    current_spot = float(member.split(":")[0])

    async with AsyncSessionLocal() as session:
        statement = select(Trade).where(Trade.id == trade_id)
        result = await session.execute(statement)
        trade = result.scalar_one_or_none()

        if not trade:
            raise HTTPException(status_code=404, detail="Trade not found.")

        if trade.status == "CLOSED":
            return {"message": "Trade is already closed.", "trade": trade}

        exit_premium = calculate_current_premium(
            entry_price=trade.entry_price,
            entry_spot=trade.entry_spot,
            current_spot=current_spot,
            option_type=trade.option_type
        )

        trade.exit_price = exit_premium
        trade.exit_spot = current_spot
        trade.pnl = round(exit_premium - trade.entry_price, 2)
        trade.status = "CLOSED"
        trade.updated_at = datetime.utcnow()

        session.add(trade)
        await session.commit()
        await session.refresh(trade)

        # Trigger Celery WhatsApp Notification
        exit_message = (
            f"🔔 Manual Exit! {trade.instrument} {trade.strike} {trade.option_type} "
            f"exited at premium {exit_premium} (Spot: {current_spot}). PnL: {trade.pnl:+.2f}."
        )
        send_whatsapp_alert_task.delay(exit_message)

        return {
            "message": "Position closed successfully.",
            "trade": trade
        }


@router.get(
    "/chart",
    tags=["Analytics"],
    summary="Generate Interactive Trade Chart",
    description=(
        "Generates a rich interactive Plotly HTML chart showing the NIFTY 50 spot price "
        "timeline overlaid with trade entry markers (green triangles for CE Long signals, "
        "red triangles for PE Short signals). The chart includes hover tooltips with trade "
        "details, strike prices, premium values, and signal reasons.\n\n"
        "The chart is returned as an `text/html` file response that can be rendered "
        "directly in a browser."
    ),
    responses={
        200: {"description": "Interactive HTML Plotly chart file."},
        404: {"description": "Chart file was not created successfully."},
        500: {"description": "Internal chart generation error."},
    },
)
async def get_interactive_chart():
    """
    Generates and returns the latest HTML trade chart.
    """
    try:
        filepath = await generate_nifty_trade_chart()
        if not os.path.exists(filepath):
            raise HTTPException(status_code=404, detail="Chart file was not created successfully.")
        return FileResponse(filepath, media_type="text/html")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chart generation error: {e}")


@router.get(
    "/simulation/status",
    tags=["System"],
    response_model=FeedStatusResponse,
    summary="Check Data Feed Status",
    description=(
        "Returns the current state of the data ingestion feed — whether the system "
        "is consuming live ticks from the DhanHQ WebSocket or running the built-in "
        "random-walk market simulator."
    ),
    responses={
        200: {
            "description": "Current feed status.",
            "content": {"application/json": {"example": {
                "simulation_mode": True,
                "active_feed": "SIMULATED"
            }}},
        },
    },
)
async def get_feed_status(request: Request):
    """
    Retrieves the current database tick ingestion feed status.
    """
    app = request.app
    simulator = getattr(app.state, "simulator", None)
    is_simulating = simulator.is_running if simulator else False
    return {
        "simulation_mode": is_simulating,
        "active_feed": "SIMULATED" if is_simulating else "LIVE"
    }


@router.post(
    "/simulation/toggle",
    tags=["System"],
    response_model=FeedToggleResponse,
    summary="Toggle Simulation / Live Feed",
    description=(
        "Dynamically switches between the simulated NIFTY 50 market data generator "
        "and the live DhanHQ WebSocket feed **without restarting the server**.\n\n"
        "**Example usage:**\n"
        "```\n"
        "POST /api/v1/simulation/toggle?enable=true   → Switches to SIMULATED mode\n"
        "POST /api/v1/simulation/toggle?enable=false  → Switches to LIVE DhanHQ feed\n"
        "```\n\n"
        "**Note:** Switching to LIVE mode requires valid `DHAN_CLIENT_ID` and "
        "`DHAN_ACCESS_TOKEN` environment variables to be configured."
    ),
    responses={
        200: {
            "description": "Feed mode switched successfully.",
            "content": {"application/json": {"example": {
                "status": "success",
                "simulation_mode": True,
                "active_feed": "SIMULATED"
            }}},
        },
        500: {"description": "Ingestion modules not initialized on server."},
    },
)
async def toggle_simulation_mode(request: Request, enable: bool):
    """
    Toggles dynamically between simulation mode (random walk) and live DhanHQ feed.
    """
    app = request.app
    simulator = getattr(app.state, "simulator", None)
    dhan_client = getattr(app.state, "dhan_client", None)
    
    if not simulator or not dhan_client:
        raise HTTPException(status_code=500, detail="Ingestion modules not initialized on server.")
        
    if enable:
        dhan_client.stop()
        simulator.start()
        mode = "SIMULATED"
    else:
        simulator.stop()
        dhan_client.start()
        mode = "LIVE"
        
    return {
        "status": "success",
        "simulation_mode": enable,
        "active_feed": mode
    }
