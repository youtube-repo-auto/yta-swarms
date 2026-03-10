"""
Retry utilities with exponential back-off for transient failures.
"""

import logging
import time
from functools import wraps
from typing import Callable, Type, Tuple

logger = logging.getLogger(__name__)


def with_retry(
    max_attempts: int = 4,
    base_delay: float = 2.0,
    backoff_factor: float = 2.0,
    exceptions: Tuple[Type[BaseException], ...] = (Exception,),
):
    """
    Decorator that retries the decorated function on specified exceptions
    using exponential back-off.

    Args:
        max_attempts:   Total number of attempts (including the first one).
        base_delay:     Initial wait in seconds between retries.
        backoff_factor: Multiplier applied to delay after each failure.
        exceptions:     Tuple of exception types that trigger a retry.

    Example::

        @with_retry(max_attempts=3, base_delay=1.0, exceptions=(IOError,))
        def fetch_data(url: str) -> str:
            ...
    """

    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            delay = base_delay
            last_exc: BaseException | None = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt == max_attempts:
                        logger.error(
                            "%s failed after %d attempts: %s",
                            func.__name__,
                            max_attempts,
                            exc,
                        )
                        raise
                    logger.warning(
                        "%s attempt %d/%d failed (%s). Retrying in %.1fs…",
                        func.__name__,
                        attempt,
                        max_attempts,
                        exc,
                        delay,
                    )
                    time.sleep(delay)
                    delay *= backoff_factor

            # Should never reach here, but satisfies type-checkers.
            raise last_exc  # type: ignore[misc]

        return wrapper

    return decorator


def retry_call(
    func: Callable,
    *args,
    max_attempts: int = 4,
    base_delay: float = 2.0,
    backoff_factor: float = 2.0,
    exceptions: Tuple[Type[BaseException], ...] = (Exception,),
    **kwargs,
):
    """
    Functional alternative to the decorator – useful for one-off calls.

    Example::

        result = retry_call(requests.get, url, timeout=10, max_attempts=3)
    """
    decorated = with_retry(
        max_attempts=max_attempts,
        base_delay=base_delay,
        backoff_factor=backoff_factor,
        exceptions=exceptions,
    )(func)
    return decorated(*args, **kwargs)
