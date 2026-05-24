import os
import sys
import time
import pandas as pd
import httpx
import openai
import redis
import streamlit as st
import plotly.graph_objects as go
from datetime import datetime, timedelta
from sqlmodel import Session, create_engine, select
from streamlit_autorefresh import st_autorefresh

# Ensure parent directory is in sys.path to import local modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import settings
from database.models import Trade
from cache.redis_service import RedisService
from trading.pricing_model import calculate_current_premium
from ai_analytics.mcp_server import (
    get_last_trade,
    get_open_positions,
    get_pnl_summary,
    get_spike_events,
    get_best_strike_accuracy,
    generate_trade_chart
)

# Streamlit Page Setup
st.set_page_config(
    page_title="Instant Strike Engine - Dashboard",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Load CSS Styles
css_path = os.path.join(os.path.dirname(__file__), "styles.css")
if os.path.exists(css_path):
    with open(css_path) as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

# 1. Initialize DB Synchronous Connection
# We strip "+asyncpg" from DATABASE_URL to connect synchronously in Streamlit
sync_db_url = settings.DATABASE_URL.replace("+asyncpg", "")
engine = create_engine(sync_db_url)

# 2. Initialize Redis Connection
redis_client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)

# Helper: Fetch trades from PostgreSQL
def fetch_all_trades_sync() -> list[Trade]:
    with Session(engine) as session:
        statement = select(Trade).order_by(Trade.created_at.desc())
        return list(session.exec(statement).all())

# Helper: Fetch NIFTY tick history synchronously from Redis
def fetch_historical_ticks_sync() -> list[tuple[float, float]]:
    history_key = "nifty_history"
    try:
        members = redis_client.lrange(history_key, 0, -1)
        ticks = []
        for m in reversed(members):
            try:
                parts = m.split(":")
                ticks.append((float(parts[0]), float(parts[1])))
            except (ValueError, IndexError):
                continue
        return ticks
    except Exception:
        return []

# Helper: Build a real stock-market-style Plotly figure for the NIFTY Spot Price timeline
def generate_nifty_trade_chart_fig_sync(trades: list[Trade], ticks: list[tuple[float, float]]) -> go.Figure:
    fig = go.Figure()

    # Stock market dark theme colors
    BG_COLOR = "#0d1117"
    GRID_COLOR = "#1c2333"
    TEXT_COLOR = "#8b949e"
    BORDER_COLOR = "#30363d"

    if ticks and len(ticks) >= 2:
        df_ticks = pd.DataFrame(ticks, columns=["price", "timestamp"])
        df_ticks["time"] = pd.to_datetime(df_ticks["timestamp"], unit="s")
        df_ticks = df_ticks.sort_values("time").reset_index(drop=True)

        # Determine overall trend for color scheme
        first_price = df_ticks["price"].iloc[0]
        last_price = df_ticks["price"].iloc[-1]
        is_bullish = last_price >= first_price
        trend_color = "#00c853" if is_bullish else "#ff1744"  # Vivid green / red
        trend_fill = "rgba(0, 200, 83, 0.06)" if is_bullish else "rgba(255, 23, 68, 0.06)"
        trend_glow = "rgba(0, 200, 83, 0.3)" if is_bullish else "rgba(255, 23, 68, 0.3)"

        # --- Main price line ---
        fig.add_trace(go.Scatter(
            x=df_ticks["time"],
            y=df_ticks["price"],
            mode="lines",
            name="NIFTY 50",
            line=dict(color=trend_color, width=2, shape="spline", smoothing=0.8),
            fill="tozeroy",
            fillcolor=trend_fill,
            hovertemplate=(
                "<b>NIFTY 50</b><br>"
                "Price: ₹%{y:,.2f}<br>"
                "Time: %{x|%H:%M:%S}<br>"
                "<extra></extra>"
            ),
        ))

        # --- 20-period Simple Moving Average overlay ---
        if len(df_ticks) >= 20:
            df_ticks["sma20"] = df_ticks["price"].rolling(window=20, min_periods=1).mean()
            fig.add_trace(go.Scatter(
                x=df_ticks["time"],
                y=df_ticks["sma20"],
                mode="lines",
                name="SMA(20)",
                line=dict(color="#ffa726", width=1.2, dash="dot"),
                opacity=0.7,
                hovertemplate="SMA(20): ₹%{y:,.2f}<extra></extra>"
            ))

        # --- Pulsing current price dot (last data point) ---
        fig.add_trace(go.Scatter(
            x=[df_ticks["time"].iloc[-1]],
            y=[last_price],
            mode="markers",
            name="Live Price",
            marker=dict(
                color=trend_color,
                size=10,
                line=dict(width=3, color=trend_glow),
                symbol="circle"
            ),
            showlegend=False,
            hoverinfo="skip"
        ))

        # --- Horizontal current price line ---
        fig.add_hline(
            y=last_price,
            line_dash="dash",
            line_color=trend_color,
            line_width=1,
            opacity=0.5
        )

        # --- Current price annotation on the right ---
        price_change = last_price - first_price
        price_change_pct = (price_change / first_price) * 100 if first_price != 0 else 0
        change_symbol = "+" if price_change >= 0 else ""
        fig.add_annotation(
            x=df_ticks["time"].iloc[-1],
            y=last_price,
            xanchor="left",
            text=(
                f"  ₹{last_price:,.2f}  "
                f"<span style='font-size:11px;color:{trend_color}'>"
                f"{change_symbol}{price_change:,.2f} ({change_symbol}{price_change_pct:.2f}%)</span>"
            ),
            showarrow=False,
            font=dict(size=14, color=trend_color, family="monospace"),
            bgcolor="rgba(13,17,23,0.85)",
            bordercolor=trend_color,
            borderwidth=1,
            borderpad=6,
        )

        # --- Y-axis range with padding ---
        y_min = df_ticks["price"].min()
        y_max = df_ticks["price"].max()
        y_pad = max((y_max - y_min) * 0.15, 2.0)
    else:
        # No ticks yet — show waiting state
        fig.add_trace(go.Scatter(
            x=[datetime.utcnow()],
            y=[22400.0],
            mode="markers+text",
            text=["  Awaiting live NIFTY ticks..."],
            textposition="middle right",
            textfont=dict(size=14, color="#8b949e"),
            marker=dict(color="#ffa726", size=12, symbol="circle"),
            name="Feed Status",
            showlegend=False
        ))
        y_min, y_max, y_pad = 22380, 22420, 10
        trend_color = "#ffa726"

    # --- Trade entry scatter markers ---
    if trades:
        ce_trades = [t for t in trades if t.option_type == "CE"]
        pe_trades = [t for t in trades if t.option_type == "PE"]

        if ce_trades:
            ce_times = [t.created_at for t in ce_trades]
            ce_spots = [t.entry_spot for t in ce_trades]
            ce_hover = [
                f"<b>📈 LONG CE</b><br>"
                f"Trade: {str(t.id)[:8]}<br>"
                f"Strike: {t.strike}<br>"
                f"Entry: ₹{t.entry_price:.2f}<br>"
                f"PnL: {t.pnl:+.2f}<br>"
                f"Reason: {t.signal_reason}"
                for t in ce_trades
            ]
            fig.add_trace(go.Scatter(
                x=ce_times, y=ce_spots,
                mode="markers",
                name="▲ LONG CE",
                marker=dict(
                    color="#00e676", size=14, symbol="triangle-up",
                    line=dict(width=2, color="#00c853")
                ),
                text=ce_hover, hoverinfo="text"
            ))

        if pe_trades:
            pe_times = [t.created_at for t in pe_trades]
            pe_spots = [t.entry_spot for t in pe_trades]
            pe_hover = [
                f"<b>📉 SHORT PE</b><br>"
                f"Trade: {str(t.id)[:8]}<br>"
                f"Strike: {t.strike}<br>"
                f"Entry: ₹{t.entry_price:.2f}<br>"
                f"PnL: {t.pnl:+.2f}<br>"
                f"Reason: {t.signal_reason}"
                for t in pe_trades
            ]
            fig.add_trace(go.Scatter(
                x=pe_times, y=pe_spots,
                mode="markers",
                name="▼ SHORT PE",
                marker=dict(
                    color="#ff5252", size=14, symbol="triangle-down",
                    line=dict(width=2, color="#ff1744")
                ),
                text=pe_hover, hoverinfo="text"
            ))

    # --- Real stock-market dark layout ---
    fig.update_layout(
        xaxis=dict(
            title=None,
            showgrid=True,
            gridcolor=GRID_COLOR,
            gridwidth=1,
            tickformat="%H:%M:%S",
            tickfont=dict(size=11, color=TEXT_COLOR, family="monospace"),
            linecolor=BORDER_COLOR,
            zeroline=False,
            showline=True,
            rangeslider=dict(visible=False),
            spikemode="across",
            spikethickness=1,
            spikecolor="#58a6ff",
            spikedash="solid",
        ),
        yaxis=dict(
            title=None,
            showgrid=True,
            gridcolor=GRID_COLOR,
            gridwidth=1,
            tickformat=",.2f",
            tickprefix="₹",
            tickfont=dict(size=11, color=TEXT_COLOR, family="monospace"),
            linecolor=BORDER_COLOR,
            zeroline=False,
            showline=True,
            side="right",
            range=[y_min - y_pad, y_max + y_pad],
            spikemode="across",
            spikethickness=1,
            spikecolor="#58a6ff",
            spikedash="solid",
        ),
        plot_bgcolor=BG_COLOR,
        paper_bgcolor=BG_COLOR,
        font=dict(family="'SF Mono', 'Fira Code', monospace", color=TEXT_COLOR),
        legend=dict(
            orientation="h",
            yanchor="bottom", y=1.02,
            xanchor="right", x=1,
            font=dict(size=11, color=TEXT_COLOR),
            bgcolor="rgba(0,0,0,0)",
        ),
        margin=dict(l=10, r=80, t=30, b=40),
        hovermode="x unified",
        hoverlabel=dict(
            bgcolor="#161b22",
            bordercolor=BORDER_COLOR,
            font=dict(size=12, color="#c9d1d9", family="monospace")
        ),
        spikedistance=-1,
        height=420,
    )
    return fig


# Auto-refresh the dashboard every 3 seconds to get live price updates and trigger toasts
if "chat_active" not in st.session_state:
    st.session_state["chat_active"] = False

refresh_interval = 3000
if st.session_state["chat_active"]:
    refresh_interval = 600000  # Set to 10 minutes to disable refresh during LLM generation

st_autorefresh(interval=refresh_interval, key="dashboard_refresh")

# Title Header
st.markdown("<h1 style='margin-bottom: 5px;'>⚡ Instant Strike Execution Engine</h1>", unsafe_allow_html=True)
st.markdown("<p style='color: #8b949e; margin-bottom: 25px;'>Real-time NIFTY options simulation & trade intelligence admin console</p>", unsafe_allow_html=True)

# --- REAL-TIME ALERTS DISPATCH & TOAST LOGIC ---
# We retrieve trades and check if a new one was added to trigger a toast
trades_list = fetch_all_trades_sync()
if trades_list:
    latest_trade = trades_list[0]
    # Initialize session state for seen trades
    if "last_seen_trade_id" not in st.session_state:
        st.session_state["last_seen_trade_id"] = latest_trade.id
    elif st.session_state["last_seen_trade_id"] != latest_trade.id:
        # Trigger dynamic toast alert representing WhatsApp message delivery
        type_emoji = "🚀" if latest_trade.option_type == "CE" else "📉"
        st.toast(
            f"🚨 **WhatsApp Alert Sent!**  \n"
            f"{type_emoji} Long NIFTY {latest_trade.strike} {latest_trade.option_type} entered at {latest_trade.entry_price}. "
            f"Reason: {latest_trade.signal_reason}",
            icon="💬"
        )
        st.session_state["last_seen_trade_id"] = latest_trade.id

# Fetch current Spot Price from Redis
latest_tick = None
current_spot = 22400.0
try:
    latest_tick = redis_client.zrange("nifty_ticks", -1, -1, withscores=True)
    if latest_tick:
        current_spot = float(latest_tick[0][0].split(":")[0])
except Exception as e:
    # Do not raise exception, just show warning in sidebar and default to 22400.0
    st.sidebar.warning(f"⚠️ Redis is offline. Using default NIFTY price (22400.0).")


# --- SIDEBAR - DATA INGESTION TOGGLE ---
st.sidebar.title("🔌 Data Feed Configuration")

# Initialize state from API
simulation_mode = True
active_feed = "SIMULATED"
try:
    res = httpx.get("http://127.0.0.1:8000/api/v1/simulation/status", timeout=2.0)
    if res.status_code == 200:
        status_data = res.json()
        simulation_mode = status_data.get("simulation_mode", True)
        active_feed = status_data.get("active_feed", "SIMULATED")
except Exception:
    st.sidebar.warning("⚠️ Cannot connect to backend server to check feed status.")

# Toggle Switch
new_mode = st.sidebar.toggle(
    "Simulate NIFTY 50 Ticks",
    value=simulation_mode,
    help="Toggles between DhanHQ Live WebSocket feed and local market price simulator."
)

if new_mode != simulation_mode:
    try:
        # Call API to toggle mode on the fly
        res = httpx.post(f"http://127.0.0.1:8000/api/v1/simulation/toggle?enable={str(new_mode).lower()}", timeout=3.0)
        if res.status_code == 200:
            st.toast(f"🔄 Ingestion feed switched to {'SIMULATED' if new_mode else 'LIVE'} mode!")
            st.rerun()
        else:
            st.sidebar.error("Failed to switch feed mode in backend.")
    except Exception as e:
        st.sidebar.error(f"Error toggling feed: {e}")

st.sidebar.markdown(f"**Active Data Feed:** `{active_feed}`")
st.sidebar.markdown("---")


# Navigation Tabs
tab_live, tab_db, tab_ai = st.tabs([
    "📈 Live Dashboard & Alerts", 
    "🗄️ PostgreSQL Trades Explorer", 
    "🤖 AI Trade Assistant"
])

# ==========================================
# TAB 1: LIVE DASHBOARD & ALERTS
# ==========================================
with tab_live:
    # Top Row Metrics
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("NIFTY 50 Spot", f"{current_spot:.2f} INR")
    with col2:
        open_count = sum(1 for t in trades_list if t.status == "OPEN")
        st.metric("Open Simulated Trades", str(open_count))
    with col3:
        closed_count = sum(1 for t in trades_list if t.status == "CLOSED")
        st.metric("Closed Positions", str(closed_count))
    with col4:
        # Show how many users (alerts) have been notified
        # Every entry has 1 alert at buy, closed entries have another alert at sell. Total alerts = entry alerts + exit alerts
        notified_alerts_count = len(trades_list) + closed_count
        st.metric("WhatsApp Alerts Dispatched", str(notified_alerts_count), delta="Live")

    # Live NIFTY Spot Price & Entry Markers Timeline
    st.markdown(
        "<div style='display:flex;align-items:center;gap:10px;margin-bottom:8px;'>"
        "<h4 style='margin:0;'>Live NIFTY Spot Price & Entry Markers</h4>"
        "<span style='display:inline-block;width:8px;height:8px;background:#00c853;border-radius:50%;"
        "box-shadow:0 0 6px #00c853,0 0 12px #00c85380;animation:pulse 1.5s infinite;'></span>"
        "<span style='font-size:0.8rem;color:#8b949e;'>LIVE</span>"
        "</div>"
        "<style>@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.3}}</style>",
        unsafe_allow_html=True
    )
    ticks_history = fetch_historical_ticks_sync()
    fig = generate_nifty_trade_chart_fig_sync(trades_list, ticks_history)
    st.plotly_chart(fig, width='stretch', config={
        "displayModeBar": True,
        "modeBarButtonsToRemove": ["toImage", "sendDataToCloud", "lasso2d", "select2d"],
        "displaylogo": False
    })

    st.markdown("---")

    # Main Body: Layout into Simulation controls and Live Positions
    col_left, col_right = st.columns([2, 1])

    with col_left:
        st.subheader("Active Option Positions")
        open_trades = [t for t in trades_list if t.status == "OPEN"]
        
        if open_trades:
            # Construct display dataframe with live premium and PnL updates
            positions_data = []
            for t in open_trades:
                current_premium = calculate_current_premium(
                    entry_price=t.entry_price,
                    entry_spot=t.entry_spot,
                    current_spot=current_spot,
                    option_type=t.option_type
                )
                live_pnl = round(current_premium - t.entry_price, 2)
                duration = int((datetime.utcnow() - t.created_at.replace(tzinfo=None)).total_seconds())
                
                positions_data.append({
                    "Trade ID": str(t.id)[:8],
                    "Strike": f"{t.strike} {t.option_type}",
                    "Entry Spot": f"{t.entry_spot:.2f}",
                    "Entry Premium": f"{t.entry_price:.2f}",
                    "Live Premium": f"{current_premium:.2f}",
                    "Live PnL (INR)": f"{live_pnl:+.2f}",
                    "Duration": f"{duration}s",
                    "ActionKey": t.id  # helper for manual close
                })

            df_pos = pd.DataFrame(positions_data)
            
            # Display positions
            st.dataframe(
                df_pos.drop(columns=["ActionKey"]),
                width='stretch',
                hide_index=True
            )

            # Manual Close Trigger Buttons
            st.markdown("**Manual Position Close Operations:**")
            close_cols = st.columns(min(len(open_trades), 4))
            for i, t in enumerate(open_trades[:4]):
                with close_cols[i]:
                    if st.button(f"Close {t.strike} {t.option_type}", key=f"close_{t.id}"):
                        # Call API to manually exit
                        try:
                            res = httpx.post(f"http://127.0.0.1:8000/api/v1/trades/{t.id}/exit", timeout=5.0)
                            if res.status_code == 200:
                                st.success("Trade closed!")
                                st.rerun()
                            else:
                                st.error("Failed to close trade.")
                        except Exception as e:
                            st.error(f"Error: {e}")
        else:
            st.info("No active open positions. Wait for price spikes in the Dhan feed to enter a simulated trade.")

    with col_right:
        st.subheader("Dispatched Alerts Feed")
        st.markdown("Most recent WhatsApp notifications dispatched by the background worker:")
        
        # Display alerts list in beautiful styled boxes
        st.markdown("<div class='alert-feed-container'>", unsafe_allow_html=True)
        for t in trades_list[:5]:
            time_str = t.created_at.strftime("%H:%M:%S")
            card_class = "alert-card-long" if t.option_type == "CE" else "alert-card-short"
            st.markdown(
                f"<div class='alert-card {card_class}'>"
                f"  <div class='alert-header'>"
                f"    <span>🚨 NIFTY {t.strike} {t.option_type} Entered</span>"
                f"    <span class='alert-time'>{time_str}</span>"
                f"  </div>"
                f"  <div class='alert-body'>"
                f"    WhatsApp alert sent to customer containing entry premium <b>{t.entry_price:.2f}</b>. "
                f"    Reason: {t.signal_reason}"
                f"  </div>"
                f"</div>",
                unsafe_allow_html=True
            )
            if t.status == "CLOSED":
                exit_time_str = t.updated_at.strftime("%H:%M:%S")
                st.markdown(
                    f"<div class='alert-card alert-card-info'>"
                    f"  <div class='alert-header'>"
                    f"    <span>🔔 Position Closed</span>"
                    f"    <span class='alert-time'>{exit_time_str}</span>"
                    f"  </div>"
                    f"  <div class='alert-body'>"
                    f"    Alert dispatched. Closed {t.strike} {t.option_type} at exit premium <b>{t.exit_price:.2f}</b>. "
                    f"    PnL: <b>{t.pnl:+.2f} INR</b>."
                    f"  </div>"
                    f"</div>",
                    unsafe_allow_html=True
                )
        st.markdown("</div>", unsafe_allow_html=True)

# ==========================================
# TAB 2: POSTGRESQL TRADES EXPLORER
# ==========================================
with tab_db:
    st.subheader("PostgreSQL Trade Database Logs")
    
    # Filter Controls
    col_f1, col_f2, col_f3 = st.columns(3)
    with col_f1:
        filter_status = st.multiselect("Status", ["OPEN", "CLOSED"], default=["OPEN", "CLOSED"])
    with col_f2:
        filter_type = st.multiselect("Option Type", ["CE", "PE"], default=["CE", "PE"])
    with col_f3:
        filter_strike = st.text_input("Strike Price (Empty for all)")

    col_f4, col_f5 = st.columns(2)
    with col_f4:
        min_pnl, max_pnl = st.slider("PnL Range (INR)", -300.0, 300.0, (-300.0, 300.0))
    with col_f5:
        # Date range picker
        start_date = st.date_input("Start Date", datetime.utcnow().date())
        end_date = st.date_input("End Date", datetime.utcnow().date())

    # Build DB Query with Filters
    filtered_trades = []
    for t in trades_list:
        # 1. Filter Status
        if t.status not in filter_status:
            continue
        # 2. Filter Type
        if t.option_type not in filter_type:
            continue
        # 3. Filter Strike
        if filter_strike and str(t.strike) != filter_strike.strip():
            continue
        # 4. Filter PnL
        if not (min_pnl <= t.pnl <= max_pnl):
            continue
        # 5. Filter Date
        trade_date = t.created_at.date()
        if not (start_date <= trade_date <= end_date):
            continue
            
        filtered_trades.append(t)

    # Convert to Dataframe for display
    if filtered_trades:
        df_db = pd.DataFrame([{
            "ID": str(t.id),
            "Instrument": t.instrument,
            "Strike": t.strike,
            "Type": t.option_type,
            "Side": t.side,
            "Entry Spot": round(t.entry_spot, 2),
            "Entry Price": round(t.entry_price, 2),
            "Exit Spot": round(t.exit_spot, 2) if t.exit_spot else "-",
            "Exit Price": round(t.exit_price, 2) if t.exit_price else "-",
            "PnL": round(t.pnl, 2),
            "Status": t.status,
            "Signal Reason": t.signal_reason,
            "Timestamp": t.created_at.strftime("%Y-%m-%d %H:%M:%S")
        } for t in filtered_trades])

        st.dataframe(df_db, width='stretch', hide_index=True)

        # Download CSV
        csv_data = df_db.to_csv(index=False).encode('utf-8')
        st.download_button(
            "📥 Download Filtered Log as CSV",
            data=csv_data,
            file_name=f"trade_logs_{int(time.time())}.csv",
            mime="text/csv"
        )

        # Dynamic Breakdown Charts (Plotly Bar charts based on filtered view)
        closed_trades = [t for t in filtered_trades if t.status == "CLOSED"]
        if closed_trades:
            st.markdown("---")
            st.markdown("### 📊 Filtered Trades Performance Breakdown")
            
            c_win, c_acc = st.columns(2)
            with c_win:
                df_closed = pd.DataFrame([{
                    "Type": t.option_type,
                    "PnL": t.pnl
                } for t in closed_trades])
                df_grouped = df_closed.groupby("Type").sum().reset_index()
                
                import plotly.express as px
                fig_bar = px.bar(
                    df_grouped, 
                    x="Type", 
                    y="PnL", 
                    title="Net Realized Profit/Loss by Contract Type (CE vs PE)",
                    color="Type",
                    color_discrete_map={"CE": "#10B981", "PE": "#EF4444"}
                )
                fig_bar.update_layout(plot_bgcolor="white", paper_bgcolor="white")
                st.plotly_chart(fig_bar, width='stretch')

            with c_acc:
                df_strike = pd.DataFrame([{
                    "Strike": t.strike,
                    "Outcome": "Win" if t.pnl > 0 else "Loss"
                } for t in closed_trades])
                df_strike_grouped = df_strike.groupby(["Strike", "Outcome"]).size().unstack(fill_value=0).reset_index()
                
                if "Win" not in df_strike_grouped:
                    df_strike_grouped["Win"] = 0
                if "Loss" not in df_strike_grouped:
                    df_strike_grouped["Loss"] = 0
                    
                fig_strike = px.bar(
                    df_strike_grouped,
                    x="Strike",
                    y=["Win", "Loss"],
                    title="Win/Loss Count by Strike Price",
                    color_discrete_map={"Win": "#10B981", "Loss": "#EF4444"}
                )
                fig_strike.update_layout(barmode="group", plot_bgcolor="white", paper_bgcolor="white")
                st.plotly_chart(fig_strike, width='stretch')
        else:
            st.info("Additional statistics will become available once trading positions are closed.")
    else:
        st.info("No trades match the selected filter criteria.")

# ==========================================
# TAB 3: AI TRADE ASSISTANT
# ==========================================
with tab_ai:
    st.subheader("AI Assistant")
    st.markdown("Query trade analytics, compare CE vs PE profitability, or inspect anomalies in real-time.")

    # 1. Define MCP Tool schemas for OpenAI compatibility
    openai_tools = [
        {
            "type": "function",
            "function": {
                "name": "get_last_trade",
                "description": "Exposes the last executed trade."
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_open_positions",
                "description": "Retrieves all current active open positions and calculates their real-time live PnL."
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_pnl_summary",
                "description": "Aggregates overall performance metrics: Win Rate, Profit Factor, realized PnL, CE vs PE profitability."
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_spike_events",
                "description": "Returns history of spike signals that triggered trades, including timestamps."
            }
        },
        {
            "type": "function",
            "function": {
                "name": "get_best_strike_accuracy",
                "description": "Queries database metrics to find which strike interval had the highest profit percentage (accuracy)."
            }
        },
        {
            "type": "function",
            "function": {
                "name": "generate_trade_chart",
                "description": "Generates a Plotly NIFTY 50 trading timeline chart."
            }
        }
    ]

    def execute_mcp_tool_sync(tool_name: str) -> str:
        """
        Executes an async MCP tool function in the synchronous Streamlit context.
        """
        tool_map = {
            "get_last_trade": get_last_trade,
            "get_open_positions": get_open_positions,
            "get_pnl_summary": get_pnl_summary,
            "get_spike_events": get_spike_events,
            "get_best_strike_accuracy": get_best_strike_accuracy,
            "generate_trade_chart": generate_trade_chart
        }
        func = tool_map.get(tool_name)
        if not func:
            return f"Error: Tool '{tool_name}' not found."
        
        try:
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(func())
            loop.close()
            return result
        except Exception as e:
            return f"Error executing tool '{tool_name}': {e}"

    # Initialize chat history
    if "messages" not in st.session_state:
        st.session_state.messages = []

    # Create container for all chat messages so they render above the input box
    chat_container = st.container()

    # Accept user input (always rendered at the bottom)
    user_prompt = st.chat_input("Ask a trade query (e.g. 'What was my win rate today?', 'CE vs PE profitability')")
    if user_prompt:
        st.session_state["pending_prompt"] = user_prompt
        st.session_state["chat_active"] = True
        st.rerun()

    # Render history and active generation inside the container
    with chat_container:
        # Display chat messages from history
        for message in st.session_state.messages:
            # Ignore tool/assistant intermediate calls in UI rendering
            if isinstance(message, dict) and message.get("role") in ["user", "assistant"] and message.get("content"):
                with st.chat_message(message["role"]):
                    st.markdown(message["content"])

        # Process pending prompt if active
        if st.session_state.get("pending_prompt"):
            pending_prompt = st.session_state["pending_prompt"]
            
            # Display user message in chat
            with st.chat_message("user"):
                st.markdown(pending_prompt)
            st.session_state.messages.append({"role": "user", "content": pending_prompt})

            # Call LM Studio API with tools enabled
            with st.chat_message("assistant"):
                message_placeholder = st.empty()
                
                with st.spinner("AI is analyzing trade data and thinking..."):
                    try:
                        # Setup OpenAI SDK pointing to LM Studio
                        client = openai.OpenAI(base_url=settings.LM_STUDIO_BASE_URL, api_key="lm-studio-not-required")
                        
                        # Format messages for OpenAI function calling flow
                        api_messages = [
                            {
                                "role": "system", 
                                "content": "You are the StrikeIntelligence AI Assistant. You have access to real-time options engine tools. Use them to answer the user's queries. IMPORTANT RULES: 1. If you cannot find the information or do not have the data, explicitly state 'I could not find any information regarding that.' Do not hallucinate or provide random information. 2. Never expose, mention, or explain the internal tools, function names, or system architecture to the user. Provide the final answer directly and naturally."
                            }
                        ]
                        for m in st.session_state.messages:
                            if isinstance(m, dict):
                                if m.get("role") in ["user", "assistant"]:
                                    api_messages.append({"role": m["role"], "content": m["content"]})
                                elif m.get("role") == "tool":
                                    api_messages.append(m)
                            else:
                                # Message is a parsed OpenAI structure (ChatCompletionMessage)
                                api_messages.append(m)

                        # First pass: Ask the model to generate responses or invoke tools
                        response = client.chat.completions.create(
                            model=settings.LM_STUDIO_MODEL,
                            messages=api_messages,
                            tools=openai_tools,
                            tool_choice="auto"
                        )
                        
                        response_message = response.choices[0].message
                        
                        # Check if model wants to run tool calls
                        if response_message.tool_calls:
                            # Append assistant's request to tool calls
                            assistant_msg = {
                                "role": "assistant",
                                "content": response_message.content,
                                "tool_calls": [
                                    {
                                        "id": tc.id,
                                        "type": "function",
                                        "function": {
                                            "name": tc.function.name,
                                            "arguments": tc.function.arguments
                                        }
                                    } for tc in response_message.tool_calls
                                ]
                            }
                            st.session_state.messages.append(assistant_msg)
                            api_messages.append(assistant_msg)
                            
                            # Run each requested tool dynamically
                            for tool_call in response_message.tool_calls:
                                func_name = tool_call.function.name
                                st.info(f"🤖 AI called MCP Tool: `{func_name}()`...")
                                
                                tool_output = execute_mcp_tool_sync(func_name)
                                
                                tool_msg = {
                                    "role": "tool",
                                    "tool_call_id": tool_call.id,
                                    "name": func_name,
                                    "content": tool_output
                                }
                                st.session_state.messages.append(tool_msg)
                                api_messages.append(tool_msg)
                                
                            # Second pass: Feed tool output results back to LLM for final generation
                            second_response = client.chat.completions.create(
                                model=settings.LM_STUDIO_MODEL,
                                messages=api_messages,
                                stream=True
                            )
                            
                            full_response = ""
                            for chunk in second_response:
                                content = chunk.choices[0].delta.content
                                if content:
                                    full_response += content
                                    message_placeholder.markdown(full_response + "▌")
                            message_placeholder.markdown(full_response)
                        else:
                            # Model answered directly without tool calling
                            full_response = response_message.content or "No response received."
                            message_placeholder.markdown(full_response)
                            
                        st.session_state.messages.append({"role": "assistant", "content": full_response})

                    except Exception as e:
                        full_response = (
                            f"⚠️ **Could not connect to LM Studio Local LLM Server or model failed to execute tool.**  \n"
                            f"Error details: {e}  \n"
                            f"*Please verify that LM Studio is running on `{settings.LM_STUDIO_BASE_URL}` with your model loaded, and that it supports function calling.*"
                        )
                        message_placeholder.markdown(full_response)
                        st.session_state.messages.append({"role": "assistant", "content": full_response})
            
            # Clean up state and trigger rerun to restore auto-refresh
            st.session_state["pending_prompt"] = None
            st.session_state["chat_active"] = False
            st.rerun()


