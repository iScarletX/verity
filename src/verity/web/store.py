"""In-memory bounded report store.

Each successful review produces a randomly-named entry with three
serialisations: JSON, HTML, SARIF. Entries are evicted in LRU order once
the store hits its capacity. Restart loses all reports (documented in
the README).
"""

from __future__ import annotations

import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Optional


@dataclass
class StoredReport:
    review_id: str          # opaque, unguessable id used in the URL
    engine: str
    verdict: dict
    coverage: str
    findings_by_severity: dict
    json_text: str
    html_text: str
    sarif_text: str
    created_at: float


class ReportStore:
    """Bounded, thread-safe LRU. Not intended for multi-process use."""

    def __init__(self, capacity: int = 32, ttl_seconds: int = 24 * 3600) -> None:
        self._capacity = capacity
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()
        self._data: "OrderedDict[str, StoredReport]" = OrderedDict()

    def _new_id(self) -> str:
        # 128 bits of entropy is plenty for a local, per-process store.
        return secrets.token_urlsafe(16)

    def _evict_expired(self, now: float) -> None:
        # called with lock held
        for k in list(self._data.keys()):
            if now - self._data[k].created_at > self._ttl_seconds:
                del self._data[k]

    def put(self, report: StoredReport) -> str:
        with self._lock:
            rid = self._new_id()
            report.review_id = rid
            self._data[rid] = report
            self._data.move_to_end(rid)
            while len(self._data) > self._capacity:
                self._data.popitem(last=False)
            return rid

    def get(self, review_id: str) -> Optional[StoredReport]:
        now = time.time()
        with self._lock:
            self._evict_expired(now)
            entry = self._data.get(review_id)
            if entry is None:
                return None
            # LRU touch
            self._data.move_to_end(review_id)
            return entry

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)
