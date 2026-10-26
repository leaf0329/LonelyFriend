from __future__ import annotations

import json
import random
import re
from pathlib import Path

from astrbot.api import logger


class StickerLibrary:
    def __init__(self, directory: Path):
        self.entries = []
        catalog = directory / "catalog.json"
        if not catalog.is_file():
            return
        data = json.loads(catalog.read_text(encoding="utf-8-sig"))
        if data["schema"] != 1:
            raise ValueError("Unsupported sticker catalog schema")
        root = (directory / "files").resolve()
        seen = set()
        for item in data["stickers"]:
            if item.get("review", {}).get("status") != "approved":
                continue
            identity, filename = item["id"], item["file"]
            if identity in seen or not re.fullmatch(r"[a-z0-9_-]+\.(gif|png|jpe?g|webp)", filename):
                raise ValueError("Invalid or duplicate sticker entry")
            seen.add(identity)
            path = (root / filename).resolve()
            if not path.is_relative_to(root):
                raise ValueError("Sticker path escapes its image directory")
            if not path.is_file():
                logger.warning("LonelyFriend 表情文件缺失，已停用该图: %s", identity)
                continue
            if path.stat().st_size > 8 * 1024 * 1024:
                logger.warning("LonelyFriend 表情超过单图大小限制，已停用该图: %s", identity)
                continue
            self.entries.append({**item, "path": path})

    def search(self, query: str) -> list[dict]:
        if not query.strip() or len(query) > 100:
            raise ValueError("表情关键词须为 1 至 100 字符")
        terms = [term.casefold() for term in re.split(r"[\s,，、;；]+", query.strip()) if term]
        if terms == ["随机"]:
            return [{**item, "score": 1} for item in self.entries]
        aliases = {"抽象": ["搞怪", "整活"], "阴阳怪气": ["嘲讽", "无语"],
                   "晚安": ["睡觉"], "早安": ["打招呼", "苏醒"], "伤心": ["难过"]}
        terms = list(dict.fromkeys(term for original in terms for term in [original, *aliases.get(original, [])]))
        matches = []
        for item in self.entries:
            score = 0
            for term in terms:
                if term == item["id"]:
                    score += 100
                if term in item["name"].casefold():
                    score += 8
                if any(term in tag.casefold() or tag.casefold() in term
                       for tag in [item["emotion"], *item["tags"]] if len(tag) >= 2):
                    score += 5
                if term in item["description"].casefold():
                    score += 1
            if score:
                matches.append((score, item))
        matches.sort(key=lambda row: row[0], reverse=True)
        return [{**item, "score": score} for score, item in matches[:30]]

    def choose(self, query: str, recent: list[str]) -> dict | None:
        matches = self.search(query)
        if not matches:
            return None
        candidates = [item for item in matches if item["id"] not in recent] or matches
        best = candidates[0]["score"]
        return random.choice([item for item in candidates if item["score"] >= best - 2])
