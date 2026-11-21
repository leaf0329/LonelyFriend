from __future__ import annotations

import math
import re
from urllib.parse import parse_qs, urlsplit

MAX_AUDIO_SECONDS = 3600
MAX_VOICE_SECONDS = 30
MAX_RESULT_BYTES = 90 * 1024 * 1024
MAX_SOURCE_BYTES = 200 * 1024 * 1024
SOURCES = {"netease", "tencent", "bilibili", "local", "upload"}
AUDIO_SUFFIXES = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus"}


def seconds(value: str) -> float:
    if not re.fullmatch(r"\d+(?::\d{1,2}){0,2}(?:\.\d{1,3})?", value):
        raise ValueError("时间请写成秒数或分:秒，例如3:53。")
    parts = value.split(":")
    if any(float(part) >= 60 for part in parts[1:]):
        raise ValueError("时间中的秒、分钟须小于60。")
    result = 0.0
    for part in parts:
        result = result * 60 + float(part)
    return result


def interval(value: str) -> tuple[float, float]:
    parts = re.split(r"[-~～—至到]", value.replace("：", ":"))
    if len(parts) != 2:
        raise ValueError("时间段请写成3:53-4:15；不填时间段时默认整首。")
    start, end = map(seconds, parts)
    validate_clip(start, end, 0)
    return start, end


def validate_clip(start: float | None, end: float | None, pitch: int):
    if start is not None or end is not None:
        if (type(start) not in (int, float) or type(end) not in (int, float)
                or not math.isfinite(start) or not math.isfinite(end)
                or start < 0 or end > MAX_AUDIO_SECONDS or end - start < 1):
            raise ValueError("起止时间须同时指定，片段至少1秒且须位于音源前60分钟内；不填时默认整首。")
    if type(pitch) is not int or not -12 <= pitch <= 12:
        raise ValueError("升降调须为-12至12的整数半音。")


def processing_timeout(duration: float) -> int:
    return max(600, math.ceil(duration * 4 + 180))


def validate_source(source: dict) -> dict:
    if not isinstance(source, dict):
        raise ValueError("音源须包含平台和编号。")
    platform, identifier = source.get("source"), str(source.get("id", ""))
    if platform not in SOURCES:
        raise ValueError("不支持此音源。")
    patterns = {"netease": r"\d{1,16}", "tencent": r"[A-Za-z0-9]{8,32}",
                "bilibili": r"BV[A-Za-z0-9]{10}", "local": r"[A-Za-z0-9_-]{1,60}",
                "upload": r"[a-f0-9]{32}"}
    if not re.fullmatch(patterns[platform], identifier):
        raise ValueError("音源编号格式不正确，请先搜歌或提供完整歌曲链接、BV号。")
    result = {"source": platform, "id": identifier}
    if platform == "bilibili":
        page = source.get("page", 1)
        if type(page) is not int or not 1 <= page <= 1000:
            raise ValueError("B站分P序号不正确。")
        result["page"] = page
    return result


def source_from_text(value: str) -> dict:
    if value.startswith(("本地:", "local:")):
        return validate_source({"source": "local", "id": value.split(":", 1)[1]})
    if re.fullmatch(r"BV[A-Za-z0-9]{10}", value):
        return validate_source({"source": "bilibili", "id": value})
    url = urlsplit(value)
    host = (url.hostname or "").lower()
    query = parse_qs(url.query or url.fragment.partition("?")[2])
    if host in {"music.163.com", "y.music.163.com"}:
        return validate_source({"source": "netease", "id": query.get("id", [""])[0]})
    if host in {"www.bilibili.com", "m.bilibili.com", "bilibili.com"}:
        match = re.search(r"/(BV[A-Za-z0-9]{10})(?:/|$)", url.path)
        if match:
            page = query.get("p", ["1"])[0]
            return validate_source({"source": "bilibili", "id": match[1], "page": int(page)})
    if host in {"y.qq.com", "i.y.qq.com"}:
        identifier = query.get("songmid", [url.path.rstrip("/").split("/")[-1]])[0]
        return validate_source({"source": "tencent", "id": identifier})
    raise ValueError("请先搜歌并选序号，或提供完整网易云/QQ歌曲链接、B站BV号、本地曲库编号。")
