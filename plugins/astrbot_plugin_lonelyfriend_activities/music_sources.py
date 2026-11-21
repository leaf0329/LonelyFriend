"""Platform source acquisition with a same-track GD Music fallback."""

from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx

from .singing_protocol import MAX_SOURCE_BYTES, validate_source

GD_MUSIC_API = "https://music-api.gdstudio.xyz/api.php"


def preferred_titles(songs: list[dict], keyword: str) -> list[dict]:
    def title(value):
        return "".join(value.casefold().split()).strip("《》")

    exact = [song for song in songs if title(song["title"]) == title(keyword)]
    return exact or songs


class MusicSources:
    def __init__(self, library: dict):
        self.library = library
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(30, connect=8),
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://music.163.com"})

    async def close(self):
        await self.http.aclose()

    async def netease_info(self, identifier: str) -> dict:
        response = await self.http.get("https://music.163.com/api/song/detail/",
            params={"id": identifier, "ids": "[" + identifier + "]"})
        response.raise_for_status()
        songs = response.json().get("songs", [])
        if not songs:
            raise ValueError("网易云未找到这首歌。")
        song = songs[0]
        return {"title": song["name"], "duration": song["duration"] / 1000}

    async def resolve_gd(self, source: dict) -> tuple[dict, str, dict]:
        source = validate_source(source)
        if source["source"] != "netease":
            raise ValueError("GD补源当前仅支持网易云歌曲。")
        info = await self.netease_info(source["id"])
        response = await self.http.get(GD_MUSIC_API, params={"types": "url", "source": "netease",
            "id": source["id"], "br": 320}, timeout=15)
        response.raise_for_status()
        data = response.json()
        url = data.get("url") if isinstance(data, dict) else None
        if not isinstance(url, str) or not url:
            raise ValueError("GD音乐台未提供此曲的音频地址。")
        if not self.allowed_download(url, "netease"):
            raise ValueError("GD音乐台返回的地址不属于网易云支持的音频节点。")
        return info, url, {}

    async def search(self, keyword: str, source: str) -> list[dict]:
        if not keyword.strip() or len(keyword) > 100:
            raise ValueError("搜索词须为1至100字。")
        if source == "local":
            return [{"source": "local", "id": key, "title": item["title"], "artist": item.get("artist", ""),
                     "album": "本地已提供音源", "duration": item["duration"]}
                    for key, item in self.library.items()
                    if all(word in " ".join([key, item["title"], item.get("artist", ""), Path(item["path"]).stem]).lower()
                           for word in keyword.lower().split())][:5]
        if source == "netease":
            response = await self.http.post("https://music.163.com/api/search/get/web",
                data={"s": keyword, "limit": 10, "type": 1, "offset": 0})
            response.raise_for_status()
            data = response.json()
            if data.get("code") != 200:
                raise ValueError("网易云搜索暂不可用。")
            songs = [{"source": source, "id": str(song["id"]), "title": song["name"],
                     "artist": "、".join(a["name"] for a in song["artists"]),
                     "album": song["album"]["name"], "duration": song["duration"] / 1000,
                     "restricted": bool(song.get("fee"))}
                    for song in data.get("result", {}).get("songs", [])[:10]]
            return preferred_titles(songs, keyword)
        if source == "tencent":
            from qqmusic_api import Client
            from qqmusic_api.modules.search import SearchType
            async with asyncio.timeout(25), Client() as client:
                result = await client.search.search_by_type(keyword, search_type=SearchType.SONG, num=10)
                songs = [{"source": source, "id": song.mid, "title": song.name,
                         "artist": "、".join(a.name for a in song.singer),
                         "album": song.album.name, "duration": song.interval,
                         "restricted": bool(song.pay.pay_play)} for song in result.song[:10]]
                return preferred_titles(songs, keyword)
        raise ValueError("搜歌来源可选网易、QQ或本地。")

    async def resolve(self, source: dict) -> tuple[dict, str | Path, dict]:
        source = validate_source(source)
        platform, identifier = source["source"], source["id"]
        if platform == "local":
            if identifier not in self.library:
                raise ValueError("本地曲库没有这个编号。")
            item = self.library[identifier]
            return {"title": item["title"], "duration": item["duration"]}, Path(item["path"]), {}
        if platform == "netease":
            info = await self.netease_info(identifier)
            response = await self.http.get("https://music.163.com/api/song/enhance/player/url",
                params={"ids": "[" + identifier + "]", "br": 320000})
            response.raise_for_status()
            entries = response.json().get("data", [])
            if not entries or not entries[0].get("url") or entries[0].get("freeTrialInfo"):
                raise ValueError("网易云未提供完整音频，可能需要账号播放权限。请改用你提供的歌曲文件或可访问的B站链接。")
            return info, entries[0]["url"], {}
        if platform == "tencent":
            from qqmusic_api import Client
            from qqmusic_api.modules.song import SongFileInfo
            async with asyncio.timeout(25), Client() as client:
                detail = (await client.song.get_detail(identifier)).track
                result = await client.song.get_song_urls([SongFileInfo(identifier)])
                if not result.data or not result.data[0].purl or result.data[0].result != 0:
                    raise ValueError("QQ音乐未提供可下载音频，请改用文件或其他来源。")
                dispatch = await client.song.get_cdn_dispatch()
                return {"title": detail.name, "duration": detail.interval}, dispatch.sip[0] + result.data[0].purl, {}
        if platform == "bilibili":
            from bilibili_api.video import Video, VideoDownloadURLDataDetecter, AudioStreamDownloadURL
            async with asyncio.timeout(25):
                video = Video(bvid=identifier)
                info = await video.get_info()
                page = source["page"] - 1
                if page >= len(info["pages"]):
                    raise ValueError("B站视频没有这个分P。")
                data = await video.get_download_url(page_index=page)
                streams = VideoDownloadURLDataDetecter(data).detect_best_streams()
                audio = next((s for s in streams if isinstance(s, AudioStreamDownloadURL)), None)
                if audio is None:
                    raise ValueError("此B站视频未提供独立音轨。")
                urls = [audio.url, *audio.backup_url]
                url = next((u for u in urls if self.allowed_download(u, platform)), None)
                if url is None:
                    raise ValueError("B站没有返回受支持的音频下载节点。")
                return {"title": info["title"], "duration": info["pages"][page]["duration"]}, url, {"Referer": "https://www.bilibili.com/"}
        raise ValueError("上传文件应通过本地上传入口读取。")

    @staticmethod
    def allowed_download(url: str, source: str) -> bool:
        parts = urlsplit(url)
        suffixes = {"netease": ("music.126.net", "music.163.com"),
                    "tencent": ("qq.com",), "bilibili": ("bilivideo.com", "bilivideo.cn", "bilivideo.net")}
        host = (parts.hostname or "").lower()
        return (parts.scheme in {"http", "https"} and not parts.username and not parts.password
                and parts.port in {None, 80, 443}
                and any(host == s or host.endswith("." + s) for s in suffixes[source]))

    async def download(self, url: str, source: str, path: Path, headers: dict):
        total = 0
        async with asyncio.timeout(120):
            for _ in range(5):
                if not self.allowed_download(url, source):
                    raise ValueError("音频地址不属于对应平台的受支持下载域名。")
                parts = urlsplit(url)
                url = urlunsplit(parts._replace(scheme="https"))
                async with self.http.stream("GET", url, headers=headers) as response:
                    if response.is_redirect:
                        url = str(response.url.join(response.headers["location"]))
                        continue
                    response.raise_for_status()
                    with path.open("wb") as output:
                        async for chunk in response.aiter_bytes():
                            total += len(chunk)
                            if total > MAX_SOURCE_BYTES:
                                raise ValueError("原曲超过200MB上限。")
                            output.write(chunk)
                    if total == 0:
                        raise ValueError("平台返回了空音频。")
                    return
            raise ValueError("音频下载重定向次数过多。")
