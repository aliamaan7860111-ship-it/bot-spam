"""
Sales Bot — Shared configuration and utilities.
Store config, retry decorator, structured logging.
"""

import os
import asyncio
import logging
import json
import functools
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

# ── Structured Logging ────────────────────────────────────

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
            "module": record.module,
        }
        if record.exc_info and record.exc_info[0]:
            log_data["exception"] = self.formatException(record.exc_info)
        return json.dumps(log_data)


def get_logger(name: str) -> logging.Logger:
    """Get a structured JSON logger."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


# ── Multi-Store Config ────────────────────────────────────
# Maps WhatChimp phone_number_id → store config.
# Add new stores here — no code changes needed elsewhere.

STORE_CONFIG = {
    os.getenv("WHATCHIMP_PHONE_NUMBER_ID", "default"): {
        "store": os.getenv("DEFAULT_STORE", "elara"),
        "currency": os.getenv("STORE_CURRENCY", "AED"),
        "name": os.getenv("STORE_NAME", "Elara"),
        "human_label_id": os.getenv("WHATCHIMP_HUMAN_LABEL_ID", "294168"),
        "phone_number_id": os.getenv("WHATCHIMP_PHONE_NUMBER_ID", ""),
    },
    # Add more stores:
    # "phone_id_2": {
    #     "store": "amarasroom",
    #     "currency": "AED",
    #     "name": "Amara's Room",
    #     "human_label_id": "294169",
    #     "phone_number_id": "phone_id_2",
    # },
}


def get_store_config(phone_number_id: str) -> dict | None:
    """Look up store config by WhatChimp phone_number_id."""
    config = STORE_CONFIG.get(phone_number_id)
    if config:
        return config
    # Fallback: if only one store configured, use it
    if len(STORE_CONFIG) == 1:
        return list(STORE_CONFIG.values())[0]
    return None


# ── Retry Decorator ───────────────────────────────────────

def retry(max_attempts: int = 3, delay: float = 1.0, fallback=None):
    """
    Retry decorator for both sync and async functions.
    On total failure, returns fallback value if provided, else re-raises.
    """
    def decorator(func):
        if asyncio.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args, **kwargs):
                last_error = None
                for attempt in range(1, max_attempts + 1):
                    try:
                        return await func(*args, **kwargs)
                    except Exception as e:
                        last_error = e
                        if attempt < max_attempts:
                            await asyncio.sleep(delay)
                if fallback is not None:
                    return fallback
                raise last_error
            return async_wrapper
        else:
            @functools.wraps(func)
            def sync_wrapper(*args, **kwargs):
                last_error = None
                for attempt in range(1, max_attempts + 1):
                    try:
                        return func(*args, **kwargs)
                    except Exception as e:
                        last_error = e
                        if attempt < max_attempts:
                            import time
                            time.sleep(delay)
                if fallback is not None:
                    return fallback
                raise last_error
            return sync_wrapper
    return decorator


# ── Rate Limiter ──────────────────────────────────────────

class RateLimiter:
    """Simple per-phone rate limiter. Max N messages per minute."""

    def __init__(self, max_per_minute: int = 20):
        self.max_per_minute = max_per_minute
        self._counts: dict[str, list[float]] = {}

    def is_allowed(self, phone: str) -> bool:
        """Check if this phone is under the rate limit."""
        import time
        now = time.time()
        cutoff = now - 60

        if phone not in self._counts:
            self._counts[phone] = []

        # Remove old entries
        self._counts[phone] = [t for t in self._counts[phone] if t > cutoff]

        if len(self._counts[phone]) >= self.max_per_minute:
            return False

        self._counts[phone].append(now)
        return True


rate_limiter = RateLimiter(max_per_minute=20)
