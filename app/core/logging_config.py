"""
Structured logging configuration for the Trango AI Sales Agent.
"""
import logging
import sys
from pathlib import Path


def setup_logging(log_level: str = "INFO", log_dir: str = "./logs") -> logging.Logger:
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    log_format = "[%(asctime)s] %(levelname)-8s %(name)-35s %(message)s"
    date_format = "%Y-%m-%d %H:%M:%S"

    handlers: list[logging.Handler] = [
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(f"{log_dir}/agent.log", encoding="utf-8"),
    ]

    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format=log_format,
        datefmt=date_format,
        handlers=handlers,
    )

    # Quiet noisy third-party loggers
    for lib in ("httpx", "httpcore", "openai", "anthropic", "chromadb", "urllib3"):
        logging.getLogger(lib).setLevel(logging.WARNING)

    return logging.getLogger("trango_agent")


logger = setup_logging()
