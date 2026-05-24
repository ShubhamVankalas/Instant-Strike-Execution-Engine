import logging
from datetime import datetime
from sqlmodel import select
from config.settings import settings
from database.connection import AsyncSessionLocal
from database.models import Trade
from cache.redis_service import RedisService
from trading.strike_selector import calculate_atm_strike
from trading.pricing_model import calculate_entry_premium
from tasks.celery_tasks import send_whatsapp_alert_task, auto_close_trade_task

logger = logging.getLogger("TradingEngine")

class TradingEngine:
    """
    Core trading engine that evaluates sliding window ticks, detects spikes,
    calculates ATM strike pricing, and executes simulated trades.
    """
    def __init__(self):
        self.redis_service = RedisService()
        self.cooldown_key = "trade_cooldown"

    async def process_tick(self, price: float, timestamp: float) -> None:
        """
        Processes a new market price tick.
        1. Appends tick to Redis sliding window.
        2. Prunes ticks older than 60 seconds.
        3. Evaluates if a price spike has occurred.
        """
        # Save current tick
        self.redis_service.add_tick(price, timestamp)
        # Save to historical ticks list for Plotly charting
        self.redis_service.add_historical_tick(price, timestamp)
        # Expire older tick frames
        self.redis_service.prune_old_ticks(timestamp)

        # Retrieve price from 60 seconds ago
        oldest_tick = self.redis_service.get_oldest_tick()
        if not oldest_tick:
            return

        old_price, old_time = oldest_tick
        time_diff = timestamp - old_time

        # Ensure we have at least 45+ seconds of data in the window to perform spike checks
        # (WebSocket startup takes a few seconds to fill the window)
        if time_diff < 45.0:
            return

        # Calculate percentage price change
        price_change = (price - old_price) / old_price

        # Check if the change exceeds the threshold
        if abs(price_change) >= settings.SPIKE_THRESHOLD:
            await self._evaluate_signal(price, old_price, price_change)

    async def _evaluate_signal(self, current_spot: float, base_spot: float, change: float) -> None:
        """
        Checks trade cooldown restrictions, builds the trade order,
        persists to PostgreSQL, and schedules background task notifications.
        """
        # Enforce trade cooldown to avoid multiple execution entries during the same spike wave
        if self.redis_service.client.get(self.cooldown_key):
            return

        # Set 60-second cooldown key in Redis
        self.redis_service.client.set(self.cooldown_key, "active", ex=60)

        # Determine option contract parameters
        if change > 0:
            signal_type = "LONG"
            option_type = "CE"
            reason = f"🚀 +{change*100:.2f}% Spike in 60s (Spot: {current_spot:.2f}, 60s ago: {base_spot:.2f})"
        else:
            signal_type = "SHORT"
            option_type = "PE"
            reason = f"📉 -{abs(change)*100:.2f}% Spike in 60s (Spot: {current_spot:.2f}, 60s ago: {base_spot:.2f})"

        atm_strike = calculate_atm_strike(current_spot)
        entry_premium = calculate_entry_premium(current_spot)

        # Build trade record
        trade = Trade(
            instrument="NIFTY",
            strike=atm_strike,
            option_type=option_type,
            side="BUY",
            entry_price=entry_premium,
            entry_spot=current_spot,
            pnl=0.0,
            status="OPEN",
            signal_reason=reason
        )

        logger.info(
            f"⚡ Spike Signal Detected: {signal_type}! "
            f"Buying NIFTY {atm_strike} {option_type} at Premium: {entry_premium}"
        )

        # Persist trade record
        async with AsyncSessionLocal() as session:
            try:
                session.add(trade)
                await session.commit()
                await session.refresh(trade)
                logger.info(f"Database transaction committed successfully. Trade UUID: {trade.id}")
            except Exception as e:
                logger.error(f"Failed to persist trade execution in PostgreSQL: {e}")
                # Clear cooldown if DB fail to allow retries
                self.redis_service.client.delete(self.cooldown_key)
                return

        # 1. Dispatch Outbound Twilio WhatsApp Entry Notification via Celery
        alert_time = datetime.now().strftime("%H:%M:%S")
        alert_msg = (
            f"🚨 Trade Alert! Long NIFTY {atm_strike} {option_type} entered at {alert_time}. "
            f"Reason: {change*100:+.2f}% Spike."
        )
        send_whatsapp_alert_task.delay(alert_msg)

        # 2. Schedule Auto-Close order settlement via Celery delay (60 seconds)
        auto_close_trade_task.apply_async((str(trade.id),), countdown=60)
        logger.info(f"Auto-close task scheduled in Celery worker queue for Trade UUID: {trade.id}")
