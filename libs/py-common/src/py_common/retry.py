"""Small retry helper shared by Python services."""

from __future__ import annotations

import functools
import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def retry(
    attempts: int = 3,
    delay: float = 0.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
    sleep: Callable[[float], None] = time.sleep,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Retry the wrapped function up to `attempts` times on `exceptions`."""
    if attempts < 1:
        raise ValueError("attempts must be >= 1")

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> T:
            for attempt in range(1, attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions:
                    if attempt == attempts:
                        raise
                    sleep(delay)
            raise AssertionError("unreachable")

        return wrapper

    return decorator
