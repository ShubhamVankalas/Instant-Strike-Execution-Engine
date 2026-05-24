import os
import time
import pandas as pd
import plotly.graph_objects as go
from sqlmodel import select
from config.settings import settings
from database.connection import AsyncSessionLocal
from database.models import Trade
from cache.redis_service import RedisService

# Setup static directory for serving charts
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
CHARTS_DIR = os.path.join(STATIC_DIR, "charts")
os.makedirs(CHARTS_DIR, exist_ok=True)

async def generate_nifty_trade_chart() -> str:
    """
    Generates an interactive Plotly HTML chart showing the NIFTY 50 Spot price timeline
    with trade entry points overlaid (Green markers for CE, Red markers for PE).
    Saves the file to the local static charts directory and returns the absolute file path.
    """
    # 1. Fetch spot ticks history from Redis
    ticks = []
    try:
        redis_service = RedisService()
        ticks = redis_service.get_historical_ticks()
    except Exception as e:
        # Gracefully handle Redis offline
        import logging
        logging.getLogger("Charts").warning(f"Failed to fetch tick history from Redis: {e}")
    
    # 2. Fetch trade executions from PostgreSQL
    async with AsyncSessionLocal() as session:
        statement = select(Trade).order_by(Trade.created_at.asc())
        result = await session.execute(statement)
        trades = result.scalars().all()

    # 3. Build Plotly chart
    fig = go.Figure()

    # Add Spot price line
    if ticks:
        df_ticks = pd.DataFrame(ticks, columns=["price", "timestamp"])
        df_ticks["time"] = pd.to_datetime(df_ticks["timestamp"], unit="s")
        
        fig.add_trace(go.Scatter(
            x=df_ticks["time"],
            y=df_ticks["price"],
            mode="lines",
            name="NIFTY 50 Spot",
            line=dict(color="#1f77b4", width=2),
            opacity=0.8
        ))
    else:
        # Add a dummy trace if no tick data exists yet
        fig.add_trace(go.Scatter(
            x=[time.time()],
            y=[22400],
            mode="text",
            text=["Waiting for live ticks feed..."],
            name="System State"
        ))

    # Add trade entry scatter points
    if trades:
        # Extract trade stats
        ce_trades = [t for t in trades if t.option_type == "CE"]
        pe_trades = [t for t in trades if t.option_type == "PE"]

        # Call trades (CE)
        if ce_trades:
            ce_times = [t.created_at for t in ce_trades]
            ce_spots = [t.entry_spot for t in ce_trades]
            ce_hover = [
                f"ID: {str(t.id)[:8]}<br>Strike: {t.strike} CE<br>Premium: {t.entry_price}<br>PnL: {t.pnl:+.2f}<br>Reason: {t.signal_reason}"
                for t in ce_trades
            ]
            fig.add_trace(go.Scatter(
                x=ce_times,
                y=ce_spots,
                mode="markers",
                name="LONG Buy CE",
                marker=dict(color="#2ca02c", size=12, symbol="triangle-up", line=dict(width=1.5, color="black")),
                text=ce_hover,
                hoverinfo="text"
            ))

        # Put trades (PE)
        if pe_trades:
            pe_times = [t.created_at for t in pe_trades]
            pe_spots = [t.entry_spot for t in pe_trades]
            pe_hover = [
                f"ID: {str(t.id)[:8]}<br>Strike: {t.strike} PE<br>Premium: {t.entry_price}<br>PnL: {t.pnl:+.2f}<br>Reason: {t.signal_reason}"
                for t in pe_trades
            ]
            fig.add_trace(go.Scatter(
                x=pe_times,
                y=pe_spots,
                mode="markers",
                name="SHORT Buy PE",
                marker=dict(color="#d62728", size=12, symbol="triangle-down", line=dict(width=1.5, color="black")),
                text=pe_hover,
                hoverinfo="text"
            ))

    # Styling and layout
    fig.update_layout(
        title=dict(
            text="NIFTY 50 Spot Price & Trading execution timeline",
            font=dict(size=18, family="Outfit, Arial")
        ),
        xaxis=dict(title="Time", showgrid=True, gridcolor="#e9e9e9"),
        yaxis=dict(title="Spot Price (INR)", showgrid=True, gridcolor="#e9e9e9"),
        plot_bgcolor="white",
        paper_bgcolor="white",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=40, r=40, t=60, b=40),
        hovermode="closest"
    )

    # Save to dynamic HTML path
    filename = f"nifty_trades_{int(time.time())}.html"
    filepath = os.path.join(CHARTS_DIR, filename)
    fig.write_html(filepath)
    
    # Also save a standard pointer file index.html representing the latest chart
    latest_path = os.path.join(CHARTS_DIR, "latest_chart.html")
    fig.write_html(latest_path)

    return filepath
