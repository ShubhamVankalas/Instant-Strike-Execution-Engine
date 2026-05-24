# Instant Strike Execution Engine

A modular, high-performance, low-latency options trading simulation system. It connects to live market tick streams, maintains a 60-second price window in Redis, triggers simulated option order executions on rapid spot index movements, persists transactions to PostgreSQL, sends real-time WhatsApp notifications, and integrates with a local LLM (LM Studio) via a Model Context Protocol (MCP) server.

---

## Technical Architecture

* **High-Frequency Ingestion & Spike Detection**: Tick processing occurs directly within FastAPI's asynchronous event loop to ensure sub-millisecond signal matching.
* **Sliding Window Cache**: Ticks are saved to a Redis Sorted Set (`ZSET`) where the timestamp is the score. Ticks older than 60s are pruned continuously.
* **Quant Strike Selection**: The engine dynamically maps spot index prices to the At-The-Money (ATM) strike price rounded to the nearest standard 50-point NIFTY interval.
* **Option Premium Pricing**: Option premiums are calculated using a dynamic **Option Delta** model ($0.50$ Delta sensitivity for CE/PE contracts) with an initial premium entry cost of $0.75\%$ of NIFTY spot.
* **Asynchronous Task Queue (Celery)**: Delayed order closure rules (after 60 seconds) and Twilio WhatsApp notifications are offloaded to Celery to maintain execution throughput.
* **AI Intelligence (FastMCP)**: A Server-Sent Events (SSE) / stdio MCP server exposes database contexts and trade history tools to local LLMs (LM Studio) for natural language trade analysis and interactive charting (Plotly).

### Detailed System Architecture Diagram

```mermaid
graph TD
    %% Define color palette and styles
    %% Ingestion color: Light blue
    %% Ingestion Analysis color: Medium blue
    %% Trade Generation color: Green
    %% Execution Workflow color: Light orange
    %% Celery Task Offloading/Actions color: Brown
    %% User Interfaces & MCP color: Deep purple
    %% PostgreSQL/Redis colors: Lighter versions of their respective flows
    
    %% Styles for classes
    classDef ingestion fill:#E1F5FE,stroke:#01579B,stroke-width:2px,color:#01579B;
    classDef analysis fill:#B3E5FC,stroke:#0277BD,stroke-width:2px,color:#0277BD;
    classDef tradeGen fill:#C8E6C9,stroke:#1B5E20,stroke-width:2px,color:#1B5E20;
    classDef execution fill:#FFE0B2,stroke:#E65100,stroke-width:2px,color:#E65100;
    classDef offload fill:#D7CCC8,stroke:#4E342E,stroke-width:2px,color:#4E342E;
    classDef celeryAction fill:#D7CCC8,stroke:#4E342E,stroke-width:2px,color:#4E342E;
    classDef ui fill:#E1BEE7,stroke:#4A148C,stroke-width:2px,color:#4A148C;
    classDef db fill:#BBDEFB,stroke:#1565C0,stroke-width:2px,color:#1565C0;
    classDef redis fill:#F8BBD0,stroke:#880E4F,stroke-width:2px,color:#880E4F;
    classDef gateway fill:#DCEDC8,stroke:#33691E,stroke-width:2px,color:#33691E;

    %% Ingestion Pipeline
    Dhan["DhanHQ WebSocket / Simulator"] -->|Streams Ticks 100ms| FastServer[FastAPI App Server]:::ingestion
    FastServer -->|Add tick & Prune old ticks| RedisWindow[Redis Sorted Set nifty_ticks]:::redis
    
    %% Ingestion Analysis
    FastServer -->|Query oldest tick| RedisWindow:::redis
    RedisWindow -->|Oldest tick & Current Spot| SpikeCheck{"Price Spike Detected?<br>|Pt - P_t-60|/P_t-60 >= Threshold"}:::analysis
    
    %% Trade Generation
    SpikeCheck -->|No| Skip[Skip / Wait next tick]:::tradeGen
    SpikeCheck -->|Yes: Spike UP/DOWN| Execution[Trading Execution Engine]:::execution
    
    %% Execution Workflow
    Execution -->|1. Calculate ATM Strike| Rounding["NIFTY Strike Selector<br>round to nearest 50"]:::execution
    Execution -->|2. Mock Entry Premium| Pricing["Delta Greeks Pricing Model<br>0.75% of Spot"]:::execution
    Execution -->|3. Commit OPEN Trade| PostgreSQL[(PostgreSQL DB trades Table)]:::db
    
    %% Celery Task Offloading
    Execution -->|4. Push Celery Tasks| RedisBroker[Redis Task Queue Broker]:::redis
    RedisBroker -->|Worker Consume| CeleryWorker[Celery Background Worker]:::offload
    
    %% Celery Actions
    CeleryWorker -->|WhatsApp Alert| Twilio[Twilio WhatsApp Gateway]:::gateway
    CeleryWorker -->|Delayed 60s Close| CloseCheck["Query Redis Current Spot & Update DB status to CLOSED"]:::celeryAction
    CloseCheck -->|Saves exit premium, spot, PnL| PostgreSQL:::db
    
    %% User Interfaces & MCP
    Streamlit[Streamlit Admin Dashboard]:::ui -->|Query Ticks| RedisWindow:::redis
    Streamlit -->|Query Trades| PostgreSQL:::db
    Streamlit -->|Direct API Call| LMStudio[LM Studio Gemma Local LLM]:::ui
    Streamlit -->|POST Manual Spike / Close Position| FastServer:::ingestion
    
    Streamlit -->|Invokes MCP Tools| MCP[MCP Server Tools]:::ui
    MCP -->|Query DB Context| PostgreSQL:::db
    MCP -->|Query Ticks Context| RedisWindow:::redis
    Streamlit -->|Prompt + Tool Outputs| LMStudio:::ui
```

---

## Project Layout

```
Instant Strike Execution Engine/
│
├── config/
│   └── settings.py          # Environment settings loader
├── database/
│   ├── connection.py        # Database sessions & migrations
│   └── models.py            # Trade relational schema
├── cache/
│   └── redis_service.py     # Sliding window sorted set worker
├── data_ingestion/
│   ├── dhan_client.py       # Live DhanHQ WebSocket consumer
│   └── simulator.py         # Synthetic NIFTY index generator
├── trading/
│   ├── strike_selector.py   # Option chain strike calculations
│   ├── pricing_model.py     # Greeks Delta premium pricing
│   └── execution_engine.py  # Spot monitoring & trade lifecycle
├── notifications/
│   └── twilio_client.py     # Twilio WhatsApp dispatcher
├── tasks/
│   ├── celery_app.py        # Celery application configuration
│   └── celery_tasks.py      # Celery task definitions (WhatsApp, auto-exits)
├── ai_analytics/
│   ├── charts.py            # Plotly timeline chart visualizer
│   └── mcp_server.py        # FastMCP tools definitions
├── api/
│   ├── routes.py            # API routing (health, exits, spike injection)
│   └── server.py            # FastAPI service bootstrap
├── frontend/
│   ├── app.py               # Streamlit main dashboard app
│   └── styles.css           # Premium dashboard styling
│
├── main.py                  # Single CLI launch point
├── run.py                   # Process-managing unified application launcher
├── docker-compose.yml       # Docker environment compose definitions
├── Dockerfile               # Production Docker container manifest
├── requirements.txt         # Dependency manifest
├── .env.example             # Local environment settings template
└── .gitignore               # Merged git ignore rules
```


---

## Local Setup & Installation

### 1. Prerequisites
Ensure you have the following installed on your machine:
* Python 3.11+
* PostgreSQL
* Redis
* LM Studio (running a local LLM, e.g., `google/gemma-4-e4b`)


### 2. Configure Virtual Environment & Dependencies
The project uses the `strikeEngine` virtual environment:
```powershell
# Create venv (if not already created)
python -m venv strikeEngine

# Activate the virtual environment
.\strikeEngine\Scripts\Activate.ps1

# Install requirements
pip install -r requirements.txt
```

### 3. Initialize PostgreSQL Database
Create a database named `strike_db` in your PostgreSQL instance. The database models and tables will be created automatically by SQLModel/SQLAlchemy on application startup.


### 4. Configure Environment Variables
Create a `.env` file in the root directory (based on `.env.example`).
```ini
# Database Connection URL (Async dialect)
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/strike_db

# Redis Connection URL
REDIS_URL=redis://localhost:6379/0

# DhanHQ Client Credentials (Optional - Falls back to simulator if empty)
DHAN_CLIENT_ID=
DHAN_ACCESS_TOKEN=

# Twilio WhatsApp Credentials (Optional - Sandbox numbers)
TWILIO_ACCOUNT_SID=your_twilio_sid
TWILIO_AUTH_TOKEN=your_twilio_token
TWILIO_FROM_NUMBER=whatsapp:+14155238886
TWILIO_TO_NUMBER=whatsapp:+your_registered_number

# LM Studio Configuration
LM_STUDIO_BASE_URL=http://localhost:1234/v1
LM_STUDIO_MODEL=google/gemma-4-e4b


# Quant Configuration
SPIKE_THRESHOLD=0.0005  # Set to 0.05 (5%) in production. Use 0.0005 (0.05%) for testing.
SIMULATION_MODE=true    # Set to true to run NIFTY 50 price feed simulator
```

---

## Running the Application

Ensure your PostgreSQL and Redis services are running locally, and your virtual environment is active.

### Recommended: Run all services with a single command
You can launch the Celery worker, FastAPI backend, FastMCP server, and Streamlit dashboard concurrently using the single unified launcher script:
```powershell
python run.py
```
This script runs all services, aggregates their logs with distinct colored prefixes in one terminal window, and handles graceful cleanup of all child processes when you press `Ctrl+C`.

#### Optional Launcher Arguments
If you only want to start a subset of services, you can use the following flags:
* `--no-backend`: Do not start the FastAPI backend server.
* `--no-worker`: Do not start the Celery background worker.
* `--no-frontend`: Do not start the Streamlit frontend dashboard.
* `--no-mcp`: Do not start the FastMCP Trade Intelligence server.

For example, to run only the backend and worker (no frontend and no MCP):
```powershell
python run.py --no-frontend --no-mcp
```

---

### Alternative: Run services in separate terminals
If you prefer running each service manually in separate windows, use these commands:

#### 1. Start Celery Worker
Run the Celery task worker in your terminal:
```powershell
# Start worker (solo pool is required for Windows systems)
celery -A tasks.celery_app worker --loglevel=info -P solo
```

#### 2. Start FastAPI Server
In a separate terminal, launch the FastAPI server:
```powershell
python main.py
```
The server will start on `http://0.0.0.0:8000`. You can access the interactive API docs (Swagger UI) at `http://localhost:8000/docs`.

#### 3. Start Streamlit Admin Dashboard
In a third terminal, run the Streamlit frontend:
```powershell
streamlit run frontend/app.py
```
The dashboard will open automatically in your browser at `http://localhost:8501`.

---

## Testing & Verifying the Ingestion Flow

1. Start the application with `python run.py`.
2. The Market Simulator will begin streaming simulated NIFTY 50 price ticks automatically (when `SIMULATION_MODE=True`).
3. Open the Streamlit dashboard at `http://localhost:8501` to observe live tick updates.
4. **Expected Behavior**:
   * The simulator produces random-walk price ticks every second.
   * When a price spike exceeding `SPIKE_THRESHOLD` occurs within a 60-second window, `TradingEngine` detects the spike and creates an `OPEN` position.
   * A Celery background worker dispatches a Twilio WhatsApp alert for the entry.
   * A delayed Celery task is scheduled. After 60 seconds, the trade auto-settles, the final PnL is written to PostgreSQL, and the status changes to `CLOSED`.
   * A closure WhatsApp message is dispatched.
5. You can also toggle between simulated and live DhanHQ feed from the sidebar in the Streamlit dashboard.


---

## AI Intelligence Layer (LM Studio + MCP)

The Model Context Protocol (MCP) server exposes database statistics, real-time positions, and charting tools directly to LLMs.

### Running the FastMCP Server
Run the MCP server in a separate terminal:
```powershell
python main.py mcp
```

### Supported MCP Tools
* `get_last_trade()`: Inspects the latest trade row.
* `get_open_positions()`: Inspects active open trades with dynamic current PnL.
* `get_pnl_summary()`: Realized metrics summary (win rate, total PnL, profit factor).
* `get_spike_events()`: Retrieves tick spike records.
* `get_best_strike_accuracy()`: Analyzes NIFTY option strikes profitability.
* `generate_trade_chart()`: Renders an interactive Plotly chart containing spot price movements and overlays trade entry markings.

---

## Running with Docker Compose

To build and run all services (FastAPI, Postgres, Redis, Celery Worker, Streamlit Dashboard) containerized in a single command:
```bash
docker-compose up --build
```
* The FastAPI web server is accessible at `http://localhost:8000`.
* The Streamlit Admin Dashboard is accessible at `http://localhost:8501`.
* The database tables are created automatically by the FastAPI service on container startup.

