import asyncio
import random
import time
import logging
from trading.execution_engine import TradingEngine

logger = logging.getLogger("MarketSimulator")

class MarketSimulator:
    """
    Simulates NIFTY 50 price feeds asynchronously when the live market is closed.
    Pushes simulated ticks to the TradingEngine at regular intervals.
    """
    def __init__(self, engine: TradingEngine):
        self.engine = engine
        self.is_running = False
        self.task = None
        self.current_price = 22400.0

    def start(self) -> None:
        """
        Launches the simulation background task on the active event loop.
        """
        if self.is_running:
            return
        self.is_running = True
        self.task = asyncio.create_task(self._run_simulation())
        logger.info("⚡ Market Simulator started. Streaming simulated NIFTY 50 ticks...")

    def stop(self) -> None:
        """
        Cancels the simulation background task.
        """
        if not self.is_running:
            return
        self.is_running = False
        if self.task:
            self.task.cancel()
            self.task = None
        logger.info("🛑 Market Simulator stopped.")

    async def _run_simulation(self) -> None:
        """
        Background loop simulating realistic NIFTY 50 price updates.
        """
        while self.is_running:
            try:
                # Random walk: Add normal-variate noise + minor positive drift
                drift = 0.05
                noise = random.normalvariate(0, 1.2)
                change = drift + noise
                self.current_price = round(self.current_price + change, 2)
                
                # Push the tick to the trading engine for spike evaluation
                logger.info(f"🔮 Simulator Tick: NIFTY Spot = {self.current_price:.2f}")
                await self.engine.process_tick(self.current_price, time.time())
                
                # Generate a tick every 1 second
                await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in market simulator loop: {e}")
                await asyncio.sleep(1.0)
