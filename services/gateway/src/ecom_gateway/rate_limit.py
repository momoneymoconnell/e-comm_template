"""Per-client rate limiting.

A fixed-window counter held in memory. Simple, fast, and adequate for a single
gateway instance.

**Its limits, stated plainly.** The counters live in this process, so running
two gateway replicas doubles the effective limit, and a restart resets every
counter. Neither matters for a locally hosted deployment. The moment you run
more than one replica, move the counter to Redis — the interface below is
deliberately narrow so that is a single-file change.

It is also a fixed window rather than a sliding one, which means a client can
send `limit` requests at the end of one window and `limit` again at the start
of the next. That burst is acceptable here: the goal is to stop scraping and
brute force, not to shape traffic precisely.
"""

from __future__ import annotations

import time
from collections import defaultdict

from ecom_shared.logging import get_logger

log = get_logger(__name__)


class RateLimiter:
    """Counts requests per client per fixed window.

    Attributes:
        limit: Requests allowed per window.
        window_seconds: Window length.
    """

    def __init__(self, *, limit: int, window_seconds: int) -> None:
        """Create the limiter.

        Args:
            limit: Requests allowed per client per window.
            window_seconds: Window length in seconds.
        """
        self.limit = limit
        self.window_seconds = window_seconds
        #: (window index, count) per client key.
        self._counters: dict[str, tuple[int, int]] = defaultdict(lambda: (0, 0))

    def _window(self) -> int:
        """Current window index."""
        return int(time.time()) // self.window_seconds

    def check(self, key: str) -> tuple[bool, int, int]:
        """Record a request and report whether it is allowed.

        Args:
            key: Client identifier, normally the IP address.

        Returns:
            ``(allowed, remaining, seconds until the window resets)``.
        """
        window = self._window()
        stored_window, count = self._counters[key]

        if stored_window != window:
            # New window: the old count is irrelevant, so it is replaced rather
            # than decayed. This is also what bounds memory — a key that stops
            # being used holds one small tuple until `prune` removes it.
            count = 0

        count += 1
        self._counters[key] = (window, count)

        reset_in = self.window_seconds - (int(time.time()) % self.window_seconds)
        allowed = count <= self.limit
        return allowed, max(0, self.limit - count), reset_in

    def prune(self) -> int:
        """Drop counters from previous windows.

        Without this the dict grows by one entry per unique IP, forever, which
        is a slow memory leak that a scraper rotating addresses turns into a
        fast one.

        Returns:
            How many entries were removed.
        """
        window = self._window()
        stale = [key for key, (w, _) in self._counters.items() if w != window]
        for key in stale:
            del self._counters[key]
        return len(stale)
