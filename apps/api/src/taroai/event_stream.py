"""Event-driven wakeups for server-sent-event thread followers.

The control-plane stores append run events from worker threads (FastAPI
threadpool or background workers).  ``ThreadEventHub`` bridges those writes to
asyncio waiters on the API event loop so SSE followers can push new events
immediately instead of polling the store on an interval.
"""

import asyncio
import threading


class ThreadEventHub:
    """Per-thread-id wakeup hub for SSE followers.

    ``wait`` must be called from a running event loop.  ``notify`` is safe to
    call from any thread: it hands the wakeup to the bound event loop via
    ``call_soon_threadsafe``.  When no loop has been bound (worker processes,
    plain synchronous tests), ``notify`` degrades to a no-op and followers
    fall back to their heartbeat-interval refresh.
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_lock = threading.Lock()
        self._waiters: dict[str, set[asyncio.Event]] = {}

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        with self._loop_lock:
            self._loop = loop

    def bind_running_loop(self) -> None:
        self.bind_loop(asyncio.get_running_loop())

    def unbind_loop(self) -> None:
        with self._loop_lock:
            self._loop = None

    def waiter_count(self, thread_id: str) -> int:
        return len(self._waiters.get(thread_id, ()))

    def has_waiter_entry(self, thread_id: str) -> bool:
        return thread_id in self._waiters

    def notify(self, thread_id: str) -> None:
        with self._loop_lock:
            loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._wake, thread_id)
        except RuntimeError:
            # The loop closed between the check and the call; there is no
            # follower left to wake.
            return

    def _wake(self, thread_id: str) -> None:
        for waiter in tuple(self._waiters.get(thread_id, ())):
            waiter.set()

    async def wait(self, thread_id: str, timeout: float) -> bool:
        """Wait for a notification for ``thread_id``.

        Returns ``True`` when woken by ``notify`` and ``False`` on timeout.
        The waiter registration is always removed on exit (including client
        disconnect cancellations), and empty per-thread entries are dropped
        to bound memory.
        """
        waiter = asyncio.Event()
        self._waiters.setdefault(thread_id, set()).add(waiter)
        try:
            await asyncio.wait_for(waiter.wait(), timeout)
            return True
        except (TimeoutError, asyncio.TimeoutError):
            return False
        finally:
            remaining = self._waiters.get(thread_id)
            if remaining is not None:
                remaining.discard(waiter)
                if not remaining:
                    self._waiters.pop(thread_id, None)
