import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from config.settings import settings
from database.connection import init_db
from trading.execution_engine import TradingEngine
from data_ingestion.dhan_client import DhanWebSocketClient
from data_ingestion.simulator import MarketSimulator
from api.routes import router

# Setup logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("FastAPIServer")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Life-cycle context manager for FastAPI.
    Initializes PostgreSQL tables, instantiates components, and manages background workers.
    """
    logger.info("Initializing Instant Strike Execution Engine server lifecycle...")
    
    # 1. Initialize PostgreSQL Database Tables
    try:
        await init_db()
        logger.info("PostgreSQL database tables initialized successfully.")
    except Exception as e:
        logger.critical(f"Failed to initialize PostgreSQL database: {e}")
        # Proceeding anyway to support other components
        
    # 2. Instantiate Trading Execution Engine
    trading_engine = TradingEngine()
    app.state.trading_engine = trading_engine

    # 3. Initialize Ingestion feeds
    dhan_client = DhanWebSocketClient(trading_engine)
    app.state.dhan_client = dhan_client

    simulator = MarketSimulator(trading_engine)
    app.state.simulator = simulator

    # 4. Boot active feed based on configuration
    if settings.SIMULATION_MODE:
        logger.info("SIMULATION_MODE is True. Starting Market Simulator...")
        simulator.start()
    else:
        logger.info("SIMULATION_MODE is False. Starting DhanHQ Live WebSocket feed...")
        dhan_client.start()

    yield  # Hand over execution to FastAPI routes

    # 5. Cleanup on Server Shutdown
    logger.info("Stopping background ingestion feeds and shutting down...")
    
    # Stop Dhan Client
    dhan_client = getattr(app.state, "dhan_client", None)
    if dhan_client:
        dhan_client.stop()

    # Stop Simulator
    simulator = getattr(app.state, "simulator", None)
    if simulator:
        simulator.stop()

# OpenAPI tag metadata for Swagger UI grouping
tags_metadata = [
    {
        "name": "System",
        "description": "Server health checks and data feed control operations.",
    },
    {
        "name": "Trading",
        "description": "Trade lifecycle management — view trade history, inspect open positions, and manually close active trades.",
    },
    {
        "name": "Analytics",
        "description": "Interactive chart generation and AI-powered trade performance analytics.",
    },
]

def create_app() -> FastAPI:
    """
    FastAPI Application Factory.
    Wires middlewares, static file servers, lifespans, and APIRouters.
    """
    app = FastAPI(
        title="Instant Strike Execution Engine",
        description=(
            "A modular, high-performance, low-latency options trading simulation system. It connects to live market tick streams, maintains a 60-second price window in Redis, triggers simulated option order executions on rapid spot index movements, persists transactions to PostgreSQL, sends real-time WhatsApp notifications, and integrates with a local LLM (LM Studio) via a Model Context Protocol (MCP) server."
        ),
        version="1.0.0",
        lifespan=lifespan,
        openapi_tags=tags_metadata,
        contact={
            "name": "Shubham",
            "url": "https://github.com/ShubhamVankalas",
        },
        license_info={
            "name": "MIT License",
            "url": "https://opensource.org/licenses/MIT",
        },
    )

    # Enable CORS for frontend flexibility
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Route registrations
    app.include_router(router)

    # Serve static directory (charts/index.html) if it exists
    import os
    static_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
    os.makedirs(static_path, exist_ok=True)
    app.mount("/static", StaticFiles(directory=static_path), name="static")

    return app

# Instantiate FastAPI application
app = create_app()
