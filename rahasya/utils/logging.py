import sys
import json
import functools
import time
from loguru import logger
from typing import Callable, Any

def setup_logging(log_level: str = "INFO", log_file: str = "rahasya.log", json_format: bool = False):
    """Configure loguru for structured logging."""
    logger.remove()  # Remove default handler
    
    # Console output
    if json_format:
        logger.add(sys.stdout, level=log_level, serialize=True)
    else:
        logger.add(
            sys.stdout,
            level=log_level,
            format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level> {extra}"
        )

    # File output with rotation
    logger.add(
        log_file,
        rotation="10 MB",
        retention="30 days",
        level=log_level,
        serialize=json_format
    )

def get_scan_logger(scan_id: str):
    """Get a logger bound to a specific scan_id."""
    return logger.bind(scan_id=scan_id)

def time_it(func: Callable) -> Callable:
    """Decorator to measure and log execution time of functions."""
    @functools.wraps(func)
    async def async_wrapper(*args, **kwargs) -> Any:
        start = time.perf_counter()
        result = await func(*args, **kwargs)
        duration = time.perf_counter() - start
        logger.debug(f"{func.__name__} executed in {duration:.4f} seconds")
        return result

    @functools.wraps(func)
    def sync_wrapper(*args, **kwargs) -> Any:
        start = time.perf_counter()
        result = func(*args, **kwargs)
        duration = time.perf_counter() - start
        logger.debug(f"{func.__name__} executed in {duration:.4f} seconds")
        return result

    import asyncio
    if asyncio.iscoroutinefunction(func):
        return async_wrapper
    return sync_wrapper
