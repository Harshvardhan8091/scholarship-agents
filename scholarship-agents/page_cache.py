"""
Simple on-disk page cache (Item 6).
Key = URL, value = fetched text, 24-hour TTL.
Stored as JSON files under cache/ keyed by URL hash.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Optional

from config import PROJECT_ROOT

CACHE_DIR = PROJECT_ROOT / "cache"
CACHE_TTL_SECONDS = 24 * 60 * 60  # 24 hours


def _cache_path(url: str) -> Path:
    """Return the cache file path for a URL."""
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:24]
    return CACHE_DIR / f"{url_hash}.json"


def get_cached_page(url: str) -> Optional[str]:
    """Return cached page text if present and within TTL, else None."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(url)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - data.get("ts", 0) > CACHE_TTL_SECONDS:
            path.unlink(missing_ok=True)
            return None
        return data.get("text")
    except Exception:
        return None


def set_cached_page(url: str, text: str) -> None:
    """Store fetched page text in the cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(url)
    try:
        path.write_text(
            json.dumps({"url": url, "ts": time.time(), "text": text}, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception:
        pass
