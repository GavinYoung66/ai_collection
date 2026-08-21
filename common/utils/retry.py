"""指数退避重试装饰器。"""
import asyncio
import functools
from typing import Callable, Type, TypeVar

T = TypeVar("T")


def retry(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple[Type[Exception], ...] = (Exception,),
) -> Callable:
    """异步重试装饰器，指数退避：delay, delay*backoff, delay*backoff^2..."""

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            last_exc = None
            current_delay = delay
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(current_delay)
                        current_delay *= backoff
            raise last_exc

        return wrapper

    return decorator
