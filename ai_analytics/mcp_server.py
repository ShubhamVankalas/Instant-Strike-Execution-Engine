import os
import logging
import json
from datetime import datetime
from sqlmodel import select
from fastmcp import FastMCP

from database.connection import AsyncSessionLocal
from database.models import Trade
from cache.redis_service import RedisService
from trading.pricing_model import calculate_current_premium
from ai_analytics.charts import generate_nifty_trade_chart

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("MCPServer")

# Initialize FastMCP Server
mcp = FastMCP("StrikeIntelligence")

@mcp.tool()
async def get_last_trade() -> str:
    """
    Exposes the last executed trade.
    Returns:
        str: JSON representation of the latest trade record or error message.
    """
    async with AsyncSessionLocal() as session:
        statement = select(Trade).order_by(Trade.created_at.desc()).limit(1)
        result = await session.execute(statement)
        trade = result.scalar_one_or_none()
        
        if not trade:
            return "No trades recorded in the database yet."
        
        # Serialize fields manually to avoid UUID datetime json issues
        trade_data = {
            "id": str(trade.id),
            "instrument": trade.instrument,
            "strike": trade.strike,
            "option_type": trade.option_type,
            "side": trade.side,
            "entry_price": trade.entry_price,
            "entry_spot": trade.entry_spot,
            "exit_price": trade.exit_price,
            "exit_spot": trade.exit_spot,
            "pnl": trade.pnl,
            "status": trade.status,
            "signal_reason": trade.signal_reason,
            "created_at": trade.created_at.isoformat()
        }
        return json.dumps(trade_data, indent=2)

@mcp.tool()
async def get_open_positions() -> str:
    """
    Retrieves all current active open positions and calculates their real-time PnL
    referencing the latest Spot price from Redis.
    """
    # 1. Fetch latest spot price from Redis
    redis_service = RedisService()
    latest_ticks = redis_service.client.zrange(redis_service.key, -1, -1, withscores=True)
    
    if not latest_ticks:
        return "Cannot calculate live positions: No tick data available in Redis."
    
    member, _ = latest_ticks[0]
    try:
        current_spot = float(member.split(":")[0])
    except (ValueError, IndexError):
        return "Failed to parse spot price from Redis cache."

    # 2. Query open trades from Database
    async with AsyncSessionLocal() as session:
        statement = select(Trade).where(Trade.status == "OPEN")
        result = await session.execute(statement)
        open_trades = result.scalars().all()
        
        if not open_trades:
            return f"No open positions currently active. Latest Spot: {current_spot}"

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
                "duration_seconds": int((datetime.utcnow() - t.created_at.replace(tzinfo=None)).total_seconds()),
                "signal_reason": t.signal_reason
            })
            
        return json.dumps(positions, indent=2)

@mcp.tool()
async def get_pnl_summary() -> str:
    """
    Aggregates overall performance metrics: Win Rate, Profit Factor, CE vs PE profitability.
    """
    async with AsyncSessionLocal() as session:
        statement = select(Trade)
        result = await session.execute(statement)
        trades = result.scalars().all()
        
        if not trades:
            return "No trading history available to generate performance summary."
            
        closed_trades = [t for t in trades if t.status == "CLOSED"]
        open_trades = [t for t in trades if t.status == "OPEN"]
        
        total_trades = len(trades)
        closed_count = len(closed_trades)
        
        # Calculate stats on closed trades
        total_pnl = sum(t.pnl for t in closed_trades)
        wins = [t for t in closed_trades if t.pnl > 0]
        losses = [t for t in closed_trades if t.pnl <= 0]
        
        win_rate = (len(wins) / closed_count * 100) if closed_count > 0 else 0.0
        
        # Win/Loss details
        total_profit = sum(t.pnl for t in wins)
        total_loss = abs(sum(t.pnl for t in losses))
        profit_factor = (total_profit / total_loss) if total_loss > 0 else (total_profit if total_profit > 0 else 1.0)
        
        # CE vs PE profitability
        ce_trades = [t for t in closed_trades if t.option_type == "CE"]
        pe_trades = [t for t in closed_trades if t.option_type == "PE"]
        
        ce_pnl = sum(t.pnl for t in ce_trades)
        pe_pnl = sum(t.pnl for t in pe_trades)
        
        summary = {
            "performance_metrics": {
                "total_trades_initiated": total_trades,
                "active_open_positions": len(open_trades),
                "closed_positions": closed_count,
                "total_realized_pnl": round(total_pnl, 2),
                "win_rate_pct": f"{win_rate:.2f}%",
                "wins": len(wins),
                "losses": len(losses),
                "profit_factor": round(profit_factor, 2)
            },
            "option_type_breakdown": {
                "ce_closed_trades": len(ce_trades),
                "ce_realized_pnl": round(ce_pnl, 2),
                "pe_closed_trades": len(pe_trades),
                "pe_realized_pnl": round(pe_pnl, 2)
            }
        }
        return json.dumps(summary, indent=2)

@mcp.tool()
async def get_spike_events() -> str:
    """
    Returns history of spike signals that triggered trades, including timestamps and changes.
    """
    async with AsyncSessionLocal() as session:
        statement = select(Trade).order_by(Trade.created_at.desc())
        result = await session.execute(statement)
        trades = result.scalars().all()
        
        if not trades:
            return "No spike events recorded."
            
        events = []
        for t in trades:
            events.append({
                "timestamp": t.created_at.isoformat(),
                "instrument": t.instrument,
                "strike": t.strike,
                "option_type": t.option_type,
                "entry_spot": t.entry_spot,
                "signal_reason": t.signal_reason
            })
            
        return json.dumps(events, indent=2)

@mcp.tool()
async def get_best_strike_accuracy() -> str:
    """
    Queries database metrics to find which strike interval had the highest profit percentage (accuracy).
    """
    async with AsyncSessionLocal() as session:
        statement = select(Trade).where(Trade.status == "CLOSED")
        result = await session.execute(statement)
        trades = result.scalars().all()
        
        if not trades:
            return "No closed trades available to analyze strike accuracy."
            
        # Group by strike
        strike_groups = {}
        for t in trades:
            strike_groups.setdefault(t.strike, []).append(t)
            
        stats = []
        for strike, group in strike_groups.items():
            total = len(group)
            wins = sum(1 for t in group if t.pnl > 0)
            avg_pnl = sum(t.pnl for t in group) / total
            win_rate = wins / total
            
            stats.append({
                "strike": strike,
                "total_trades": total,
                "win_rate_pct": round(win_rate * 100, 2),
                "avg_pnl": round(avg_pnl, 2)
            })
            
        # Sort by Win Rate (accuracy) and Avg PnL descending
        stats.sort(key=lambda x: (x["win_rate_pct"], x["avg_pnl"]), reverse=True)
        return json.dumps(stats, indent=2)

@mcp.tool()
async def generate_trade_chart() -> str:
    """
    Generates a Plotly NIFTY 50 trading timeline chart and returns the filepath to the HTML.
    """
    try:
        filepath = await generate_nifty_trade_chart()
        return f"Chart generated successfully. Local interactive file saved at: {filepath}"
    except Exception as e:
        logger.error(f"Error generating trade chart: {e}")
        return f"Failed to generate trade chart: {str(e)}"
