import time
from collections.abc import Hashable


class SimpleCache[K: Hashable, V]:
    def __init__(self) -> None:
        self._data: dict[K, tuple[V, float]] = {}

    def get(self, key: K) -> V | None:
        self.clean()
        pair = self._data.get(key)
        if pair is None:
            return None
        val, _ = pair
        return val

    def set(self, key: K, val: V, ttl: float) -> None:
        if ttl < 0.0:
            raise ValueError("ttl must not be negative")
        self.clean()
        self._data[key] = (val, self._now() + ttl)

    def clean(self) -> None:
        now = self._now()
        expired_keys = [key for key, (_, expire_at) in self._data.items() if expire_at < now]
        for key in expired_keys:
            del self._data[key]

    def _now(self) -> float:
        return time.monotonic()
