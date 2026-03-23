"""
Trango Tech AI Sales Agent — Application Entry Point
"""
import uvicorn
from app.api.routes import app
from app.core.config import settings

if __name__ == "__main__":
    uvicorn.run(
        "app.api.routes:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower(),
        ws_ping_interval=20,
        ws_ping_timeout=20,
    )
