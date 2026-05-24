import asyncio
import uuid
import logging
from datetime import datetime
from celery.utils.log import get_task_logger
from sqlmodel import select
from tasks.celery_app import celery_app
from database.connection import AsyncSessionLocal
from database.models import Trade
from cache.redis_service import RedisService
from trading.pricing_model import calculate_current_premium
from notifications.twilio_client import send_whatsapp_alert

logger = get_task_logger(__name__)

def run_async(coro):
    """
    Helper function to run async coroutines within synchronous Celery task worker threads.
    """
    return asyncio.run(coro)

@celery_app.task(name="tasks.send_whatsapp_alert_task")
def send_whatsapp_alert_task(message: str) -> bool:
    """
    Celery task wrapper to send a WhatsApp notification via Twilio.
    Offloading this prevents slow Twilio API calls from blocking execution loops.
    """
    logger.info(f"Dispatching WhatsApp alert task: '{message}'")
    success = send_whatsapp_alert(message)
    return success

@celery_app.task(name="tasks.auto_close_trade_task")
def auto_close_trade_task(trade_id_str: str) -> bool:
    """
    Celery task that executes 60 seconds after trade entry to auto-close the trade.
    Queries the latest spot price from Redis and updates the database row.
    """
    logger.info(f"Running auto-close trade task for Trade UUID: {trade_id_str}")
    return run_async(async_close_trade(trade_id_str))

async def async_close_trade(trade_id_str: str) -> bool:
    """
    Asynchronously handles retrieving Redis tickers and updating the PostgreSQL row.
    """
    try:
        trade_uuid = uuid.UUID(trade_id_str)
    except ValueError:
        logger.error(f"Invalid trade UUID string received: '{trade_id_str}'")
        return False

    # 1. Retrieve latest NIFTY spot tick from Redis Sorted Set
    redis_service = RedisService()
    latest_ticks = redis_service.client.zrange(redis_service.key, -1, -1, withscores=True)
    
    if not latest_ticks:
        logger.error("Failed to auto-close trade: No spot price ticks available in Redis.")
        return False

    member, _ = latest_ticks[0]
    try:
        current_spot = float(member.split(":")[0])
    except (ValueError, IndexError):
        logger.error(f"Failed to parse spot price from Redis tick member: '{member}'")
        return False

    # 2. Query and update the trade record in PostgreSQL
    async with AsyncSessionLocal() as session:
        statement = select(Trade).where(Trade.id == trade_uuid)
        result = await session.execute(statement)
        trade = result.scalar_one_or_none()

        if not trade:
            logger.error(f"Trade record {trade_uuid} not found in database.")
            return False

        if trade.status == "CLOSED":
            logger.info(f"Trade {trade_uuid} is already closed. Skipping settlement.")
            return True

        # Calculate current option premium using the Delta Greeks pricing model
        exit_premium = calculate_current_premium(
            entry_price=trade.entry_price,
            entry_spot=trade.entry_spot,
            current_spot=current_spot,
            option_type=trade.option_type
        )

        # Set final execution metrics
        trade.exit_price = exit_premium
        trade.exit_spot = current_spot
        trade.pnl = round(exit_premium - trade.entry_price, 2)
        trade.status = "CLOSED"
        trade.updated_at = datetime.utcnow()

        session.add(trade)
        await session.commit()
        await session.refresh(trade)
        
        logger.info(
            f"Successfully auto-closed trade {trade.id}. "
            f"Exit Spot: {current_spot}, Exit Premium: {exit_premium}, PnL: {trade.pnl}"
        )

        # 3. Queue WhatsApp Alert for Trade Exit
        exit_message = (
            f"🔔 Trade Closed! {trade.instrument} {trade.strike} {trade.option_type} "
            f"exited at premium {exit_premium} (Spot: {current_spot}). PnL: {trade.pnl:+.2f}."
        )
        send_whatsapp_alert_task.delay(exit_message)
        return True
