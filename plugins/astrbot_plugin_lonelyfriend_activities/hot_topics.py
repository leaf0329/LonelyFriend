from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from urllib.parse import quote

import httpx

SNAPSHOT_KIND = "platform_hot_topics_v1"
MAX_RANK = 20
MAX_AGE = 1800
BOARDS = {
    "百度热搜": "https://top.baidu.com/board?tab=realtime",
    "B站热搜": "https://s.search.bilibili.com/main/hotword?limit=30",
}
PROMOTIONAL_TITLE = re.compile(r"^(?:招聘启事|加入我们|限时秒杀|领券|今日特价)|机核招聘|【一周趣图】|录音笔\s*VOL", re.I)


def eligible_topics(snapshot: dict, now: float) -> list[dict]:
    if snapshot.get("kind") != SNAPSHOT_KIND or not 0 <= now - snapshot["checked_at"] <= MAX_AGE:
        return []
    return [item for item in snapshot["items"]
            if item["source"] in BOARDS and 1 <= item["rank"] <= MAX_RANK and item["heat"] > 0
            and 0 <= now - item["board_updated_at"] <= MAX_AGE
            and item["title"].strip() and not PROMOTIONAL_TITLE.search(item["title"])]


def baidu_topics(response: httpx.Response) -> list[dict]:
    match = re.search(r"<!--s-data:(.*?)-->", response.text, re.S)
    if match is None:
        raise ValueError("百度热榜没有返回榜单数据")
    cards = json.loads(match.group(1))["data"]["cards"]
    if not cards:
        raise ValueError("百度返回空榜单")
    board = cards[0]
    updated = float(board["updateTime"])
    result = []
    for item in board["content"]:
        if item.get("isTop"):
            continue
        rank, heat = int(item["index"]) + 1, int(item["hotScore"])
        title = item["word"].strip()
        result.append({"source": "百度热搜", "rank": rank, "heat": heat, "board_updated_at": updated,
                       "title": title[:200], "summary": item.get("desc", "")[:600],
                       "url": "https://www.baidu.com/s?wd=" + quote(item["query"], safe="")})
    return result


def bilibili_topics(response: httpx.Response) -> list[dict]:
    board = response.json()
    if board["code"] != 0:
        raise ValueError("B站热榜返回错误状态")
    updated = float(board["timestamp"])
    result = []
    for item in board["list"]:
        if str(item["stat_datas"]["is_commercial"]) != "0":
            continue
        result.append({"source": "B站热搜", "rank": int(item["pos"]), "heat": int(item["heat_score"]),
                       "board_updated_at": updated, "title": item["show_name"][:200], "summary": "",
                       "url": "https://search.bilibili.com/all?keyword=" + quote(item["keyword"], safe="")})
    return result


class PublicHotReader:
    def __init__(self, cache_path: Path, client: httpx.AsyncClient):
        self.cache_path = cache_path
        self.client = client
        self.lock = asyncio.Lock()

    async def read(self) -> dict:
        async with self.lock:
            now = time.time()
            if self.cache_path.is_file():
                cached = json.loads(self.cache_path.read_text(encoding="utf-8"))
                if cached.get("kind") == SNAPSHOT_KIND and 0 <= now - cached["checked_at"] < 600:
                    return {**cached, "items": eligible_topics(cached, now)}

            async def fetch(source, parser):
                try:
                    response = await self.client.get(BOARDS[source])
                    response.raise_for_status()
                    items = parser(response)
                    snapshot = {"kind": SNAPSHOT_KIND, "checked_at": time.time(), "items": items}
                    valid = eligible_topics(snapshot, time.time())
                    return valid, None if valid else {"source": source, "error": "没有通过热度及时效筛选的候选"}
                except (httpx.HTTPError, ValueError, KeyError, TypeError) as error:
                    return [], {"source": source, "error": str(error)[:200]}

            results = await asyncio.gather(fetch("百度热搜", baidu_topics), fetch("B站热搜", bilibili_topics))
            value = {"kind": SNAPSHOT_KIND, "checked_at": time.time(),
                     "items": [item for items, _ in results for item in items],
                     "failures": [failure for _, failure in results if failure is not None]}
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.cache_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.cache_path)
            return value
