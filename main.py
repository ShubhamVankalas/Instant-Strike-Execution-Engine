import sys
import uvicorn
from config.settings import settings

# Prevent UnicodeEncodeError on Windows console when printing emojis
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

def main():
    """
    Root execution script.
    By default, boots the FastAPI Web Server and WebSocket engine on port 8000.
    If 'mcp' argument is supplied, boots the FastMCP Trade Intelligence server.
    """
    if len(sys.argv) > 1 and sys.argv[1] == "mcp":
        print("⚡ Starting FastMCP Trade Intelligence Server...")
        from ai_analytics.mcp_server import mcp
        mcp.run()
    else:
        print(f"🚀 Starting FastAPI Server on {settings.HOST}:{settings.PORT}...")
        uvicorn.run(
            "api.server:app",
            host=settings.HOST,
            port=settings.PORT,
            reload=(settings.ENVIRONMENT == "development")
        )

if __name__ == "__main__":
    main()
