"""Small thread-safe LRU caches with fixed expiry and bounded payload weights."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Hashable
from threading import Lock
from time import monotonic
from typing import Any


class MemoryCache:
    """Keep payloads in process memory only; reads do not extend their lifetime.

    Weight units are supplied by callers (bytes or UID count), not a claim about
    total Python heap size. Entry count is independently bounded.
    """

    def __init__(
        self,
        ttl: float,
        max_weight: int,
        max_entries: int = 32,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        """Set fixed expiry, aggregate payload limit and a clock injectable in tests."""
        self.ttl, self.max_weight, self.max_entries = ttl, max_weight, max_entries
        self.clock = clock
        self._entries: OrderedDict[Hashable, tuple[float, int, Any]] = OrderedDict()
        self._lock = Lock()

    def _expire(self) -> None:
        """Discard expired entries while holding the cache lock."""
        now = self.clock()
        for key in list(self._entries):
            if self._entries[key][0] <= now:
                del self._entries[key]

    def get(self, key: Hashable) -> Any | None:
        """Return an unexpired payload or None; callers must treat payloads as immutable."""
        with self._lock:
            self._expire()
            entry = self._entries.get(key)
            if entry is None:
                return None
            self._entries.move_to_end(key)
            return entry[2]

    def put(self, key: Hashable, value: Any, weight: int) -> None:
        """Replace a payload and evict LRU entries; oversized values are not retained."""
        with self._lock:
            self._expire()
            self._entries.pop(key, None)
            if weight > self.max_weight:
                return
            self._entries[key] = (self.clock() + self.ttl, weight, value)
            while (
                len(self._entries) > self.max_entries
                or sum(entry[1] for entry in self._entries.values()) > self.max_weight
            ):
                self._entries.popitem(last=False)
