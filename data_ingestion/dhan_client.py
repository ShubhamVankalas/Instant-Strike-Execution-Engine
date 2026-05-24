import logging
import asyncio
import threading
import time
from typing import Optional
from dhanhq import DhanContext, MarketFeed
from config.settings import settings
from trading.execution_engine import TradingEngine

logger = logging.getLogger("DhanWebSocketClient")

class DhanWebSocketClient:
    """
    Consumer client for the live DhanHQ WebSocket market feed.
    Subscribes to live LTP updates for NIFTY 50 (Security ID 13).
    """
    def __init__(self, engine: TradingEngine):
        self.engine = engine
        self.client_id = settings.DHAN_CLIENT_ID
        self.access_token = settings.DHAN_ACCESS_TOKEN
        self.feed: Optional[MarketFeed] = None
        self.thread: Optional[threading.Thread] = None
        self.is_running = False
        self.loop = asyncio.get_event_loop()

    def start(self) -> None:
        """
        Initializes and starts the WebSocket client in a background thread.
        """
        if not self.client_id or not self.access_token:
            logger.warning("DhanHQ credentials not found. WebSocket feed will not start.")
            return

        self.is_running = True
        self.thread = threading.Thread(target=self._run_feed, daemon=True)
        self.thread.start()
        logger.info("DhanHQ WebSocket client thread launched.")

    def stop(self) -> None:
        """
        Disconnects the WebSocket client and joins the worker thread.
        """
        self.is_running = False
        if self.feed:
            try:
                res = self.feed.close_connection()
                # Handle cases where close_connection returns a coroutine (async wrapper)
                import inspect
                if inspect.iscoroutine(res):
                    asyncio.run_coroutine_threadsafe(res, self.loop)
                logger.info("DhanHQ WebSocket connection closed.")
            except Exception as e:
                logger.error(f"Error closing DhanHQ connection: {e}")
        if self.thread:
            self.thread.join(timeout=2.0)

    def _run_feed(self) -> None:
        """
        Connects to DhanHQ WebSocket and subscribes to NIFTY 50 ticks.
        """
        # Configure context
        dhan_context = DhanContext(self.client_id, self.access_token)

        # Instruments: (Exchange segment, Security ID, Subscription Type)
        # NIFTY 50 Security ID: 13, Exchange: NSE_INDICES (ExchangeSegment 2, index id)
        # We subscribe to Ticker data mode
        instruments = [(MarketFeed.NSE, "13", MarketFeed.Ticker)]

        try:
            self.feed = MarketFeed(dhan_context, instruments, version="v2")

            # Setup callbacks
            self.feed.on_message = self._on_message
            self.feed.on_connect = self._on_connect
            self.feed.on_error = self._on_error
            self.feed.on_close = self._on_close

            # Start WebSocket loop (blocks until connection closes)
            self.feed.run_forever()

        except Exception as e:
            logger.error(f"DhanHQ MarketFeed exception: {e}")

    def _on_message(self, *args, **kwargs) -> None:
        """
        Callback triggered on receiving ticks.
        Pushes tick to the trading engine.
        Supports both on_message(ws, data) and on_message(data) signatures.
        """
        # Resolve data packet depending on the number of passed arguments
        data = args[1] if len(args) == 2 else (args[0] if len(args) == 1 else None)
        if data is None:
            logger.warning("📥 Received empty or unresolvable DhanHQ message.")
            return

        logger.info(f"📥 Received raw DhanHQ packet: {data}")
        try:
            # Parse tick price. Data format is usually a dictionary in the latest wrapper versions.
            # Example message format:
            # {'type': 'Ticker', 'security_id': 13, 'ltp': 22432.5}
            if isinstance(data, dict):
                security_id = data.get("security_id") or data.get("securityId")
                # NIFTY 50 ID check
                if str(security_id) == "13":
                    price = data.get("ltp") or data.get("last_traded_price") or data.get("price")
                    if price:
                        logger.info(f"🎯 Parsed NIFTY 50 Spot Tick Price: {price} INR")
                        # Schedule tick processing on the main event loop
                        asyncio.run_coroutine_threadsafe(
                            self.engine.process_tick(float(price), time.time()),
                            self.loop
                        )
                    else:
                        logger.warning(f"⚠️ Could not find LTP in NIFTY 50 data packet: {data}")
                else:
                    logger.info(f"ℹ️ Received tick for unrelated security ID: {security_id}")
            else:
                logger.warning(f"⚠️ DhanHQ packet is not a dictionary: {data} (type: {type(data)})")
        except Exception as e:
            logger.error(f"Error parsing incoming DhanHQ packet: {e}")

    def _on_connect(self, *args, **kwargs) -> None:
        logger.info("Successfully connected to DhanHQ WebSocket market feed.")

    def _on_error(self, *args, **kwargs) -> None:
        error = args[-1] if args else "Unknown error"
        logger.error(f"DhanHQ WebSocket connection encountered an error: {error}")

    def _on_close(self, *args, **kwargs) -> None:
        logger.warning("DhanHQ WebSocket connection closed.")
