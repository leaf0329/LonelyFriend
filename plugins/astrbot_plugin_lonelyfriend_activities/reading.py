from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from bs4 import BeautifulSoup


class PublicFeedReader:
    def __init__(self, feeds: list[dict], cache_path: Path, fetcher):
        self.feeds = feeds
        self.cache_path = cache_path
        self.fetcher = fetcher
        self.lock = asyncio.Lock()

    async def read(self) -> dict:
        async with self.lock:
            if self.cache_path.is_file():
                cached = json.loads(self.cache_path.read_text(encoding="utf-8"))
                if cached["feeds"] == self.feeds and time.time() - cached["checked_at"] < 600:
                    return cached
            items, failures = [], []
            # The configured four public sources keep network work bounded.
            for feed in self.feeds:
                result = await self.fetcher.fetch(feed["url"], timeout=15, verbose=False)
                if result.error or not result.rss_d:
                    failures.append({"source": feed["name"], "error": str(result.error)[:200]})
                    continue
                for entry in result.rss_d.get("entries", [])[:3]:
                    link = str(entry.get("link", ""))
                    if not link.startswith(("https://", "http://")):
                        continue
                    items.append({
                        "source": feed["name"], "topics": feed["topics"],
                        "title": str(entry.get("title", ""))[:200], "url": link,
                        "published": str(entry.get("published", entry.get("updated", ""))),
                        "summary": BeautifulSoup(str(entry.get("summary", "")), "html.parser").get_text(" ", strip=True)[:800],
                    })
            value = {"checked_at": time.time(), "feeds": self.feeds, "items": items, "failures": failures}
            if not items:
                return value
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.cache_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.cache_path)
            return value
