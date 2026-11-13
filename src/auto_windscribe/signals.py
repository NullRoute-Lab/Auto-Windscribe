"""Cancellation-only signal policy. The system-wide tunnel outlives this process."""

import asyncio
import signal
from collections.abc import Callable, Iterator
from contextlib import contextmanager


@contextmanager
def cancellation_signals(stop: Callable[[], object]) -> Iterator[None]:
    loop = asyncio.get_running_loop()
    previous = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    for sig in previous:
        loop.add_signal_handler(sig, stop)
    try:
        yield
    finally:
        for sig, handler in previous.items():
            loop.remove_signal_handler(sig)
            signal.signal(sig, handler)
