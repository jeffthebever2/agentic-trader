"""Caching utilities for data fetching operations."""

import hashlib
import json
import logging
import time
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from tradingagents.logging_config import get_logger

logger = get_logger(__name__)


class DataCache:
    """Simple file-based cache for data fetching operations."""

    def __init__(self, cache_dir: Path, max_age_hours: int = 24):
        """Initialize the cache.

        Args:
            cache_dir: Directory to store cache files
            max_age_hours: Maximum age of cache entries in hours
        """
        self.cache_dir = cache_dir
        self.max_age_hours = max_age_hours
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _get_cache_key(self, func_name: str, args: Tuple, kwargs: Dict) -> str:
        """Generate a cache key from function name and arguments."""
        # Create a hash of the arguments
        args_str = json.dumps(args, sort_keys=True, default=str)
        kwargs_str = json.dumps(kwargs, sort_keys=True, default=str)
        content = f"{func_name}:{args_str}:{kwargs_str}"
        return hashlib.md5(content.encode()).hexdigest()

    def _get_cache_path(self, key: str) -> Path:
        """Get the file path for a cache key."""
        return self.cache_dir / f"{key}.json"

    def get(self, key: str) -> Optional[Any]:
        """Get a value from cache if it exists and is not expired."""
        cache_path = self._get_cache_path(key)
        if not cache_path.exists():
            return None

        try:
            with open(cache_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # Check if cache is expired
            import time
            if time.time() - data['timestamp'] > self.max_age_hours * 3600:
                cache_path.unlink()  # Remove expired cache
                return None

            return data['value']
        except (json.JSONDecodeError, KeyError, OSError) as e:
            logger.warning("Failed to read cache file %s: %s", cache_path, e)
            return None

    def set(self, key: str, value: Any) -> None:
        """Store a value in cache."""
        cache_path = self._get_cache_path(key)
        try:
            data = {
                'timestamp': time.time(),
                'value': value
            }
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, default=str)
        except OSError as e:
            logger.warning("Failed to write cache file %s: %s", cache_path, e)

    def clear(self) -> None:
        """Clear all cache files."""
        for cache_file in self.cache_dir.glob("*.json"):
            try:
                cache_file.unlink()
            except OSError:
                pass


def cached(cache_instance: DataCache):
    """Decorator to cache function results."""
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Skip caching if explicitly disabled
            if kwargs.get('use_cache', True) is False:
                return func(*args, **kwargs)

            cache_key = cache_instance._get_cache_key(func.__name__, args, kwargs)

            # Try to get from cache first
            cached_result = cache_instance.get(cache_key)
            if cached_result is not None:
                logger.debug("Cache hit for %s", func.__name__)
                return cached_result

            # Execute function and cache result
            logger.debug("Cache miss for %s, executing", func.__name__)
            result = func(*args, **kwargs)
            cache_instance.set(cache_key, result)
            return result

        return wrapper
    return decorator


# Global cache instance
_cache_instance: Optional[DataCache] = None


def get_cache(max_age_hours: int = 24) -> DataCache:
    """Get the global cache instance."""
    global _cache_instance
    if _cache_instance is None:
        from .config import get_config
        config = get_config()
        cache_dir = Path(config.get('data_cache_dir', '~/.tradingagents/cache')).expanduser()
        _cache_instance = DataCache(cache_dir, max_age_hours=max_age_hours)
    return _cache_instance