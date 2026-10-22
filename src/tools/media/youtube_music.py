"""
YouTube Music MVP — search the public catalog and open results in the browser.

Unauthenticated ytmusicapi search only. No library, likes, or taste learning yet.
Playback = open music.youtube.com in the user's default browser.
"""

from __future__ import annotations

import asyncio
import webbrowser
from typing import Any, Dict, List, Optional

from src.core.tools.formatter import tool_display
from src.shared.logger import setup_logger
from src.tools.base_tool import BaseTool

logger = setup_logger("YouTubeMusic")

_VIDEO_ID_RE_LEN = 11


def music_watch_url(video_id: str) -> str:
    return f"https://music.youtube.com/watch?v={video_id}"


def music_playlist_url(playlist_id: str) -> str:
    return f"https://music.youtube.com/playlist?list={playlist_id}"


def normalize_playlist_id(raw: Optional[str]) -> Optional[str]:
    """ytmusicapi sometimes returns browseIds with a VL prefix."""
    if not raw or not isinstance(raw, str):
        return None
    pid = raw.strip()
    if pid.startswith("VL") and len(pid) > 2:
        pid = pid[2:]
    return pid or None


def _artist_names(item: Dict[str, Any]) -> str:
    artists = item.get("artists") or []
    names = [a.get("name") for a in artists if isinstance(a, dict) and a.get("name")]
    return ", ".join(names) if names else ""


def summarize_result(item: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a ytmusicapi search hit into a compact dict for the model."""
    result_type = (item.get("resultType") or item.get("category") or "").lower()
    title = item.get("title") or item.get("name") or "Unknown"
    artists = _artist_names(item)
    video_id = item.get("videoId")
    playlist_id = normalize_playlist_id(item.get("playlistId") or item.get("browseId"))

    kind = "playlist" if "playlist" in result_type or (playlist_id and not video_id) else "song"
    if video_id:
        kind = "song"

    out: Dict[str, Any] = {
        "kind": kind,
        "title": title,
        "artists": artists,
    }
    if video_id:
        out["video_id"] = video_id
        out["url"] = music_watch_url(video_id)
    if playlist_id and kind == "playlist":
        out["playlist_id"] = playlist_id
        out["url"] = music_playlist_url(playlist_id)
    duration = item.get("duration")
    if duration:
        out["duration"] = duration
    return out


@tool_display({
    "play": "Playing YouTube Music: {query}...",
    "search": "Searching YouTube Music for {query}...",
})
class PlayYouTubeMusicTool(BaseTool):
    """Search YouTube Music and open a song/playlist in the default browser."""

    name = "play_youtube_music"
    description = (
        "Search YouTube Music and open a song or playlist in the user's browser. "
        "Use for 'play me some anime music', 'put on lo-fi', 'play a chill playlist'. "
        "Default mode=play searches and opens the top match on music.youtube.com. "
        "Use mode=search to list options, then call again with video_id or playlist_id. "
        "NOT for watching YouTube videos in chat — that is show_youtube_video."
    )
    category = "media"

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "Mood, genre, song, artist, or playlist request "
                            "(e.g. 'anime OSTs', 'lo-fi beats', 'Joe Hisaishi'). "
                            "Optional when video_id or playlist_id is provided."
                        ),
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["play", "search"],
                        "description": (
                            "play (default): search and open the top match. "
                            "search: return candidates without opening a browser."
                        ),
                    },
                    "filter": {
                        "type": "string",
                        "enum": ["songs", "playlists"],
                        "description": "Prefer songs (default) or playlists/mixes.",
                    },
                    "video_id": {
                        "type": "string",
                        "description": "11-char YouTube video ID — open directly, skip search.",
                    },
                    "playlist_id": {
                        "type": "string",
                        "description": "YouTube Music playlist ID — open directly, skip search.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max search results to return in mode=search (default 5, max 10).",
                    },
                },
                "required": [],
            },
        }

    def format_action(self, params: Dict[str, Any]) -> str:
        mode = (params.get("mode") or "play").strip().lower()
        if params.get("video_id") or params.get("playlist_id"):
            target = params.get("video_id") or params.get("playlist_id")
            return f"Opening YouTube Music ({target})"
        query = (params.get("query") or "").strip()
        if query:
            short = query if len(query) <= 40 else query[:37] + "..."
            verb = "Searching" if mode == "search" else "Playing"
            return f'{verb} YouTube Music: "{short}"'
        return "YouTube Music"

    def validate_params(
        self,
        query: Optional[str] = None,
        mode: str = "play",
        filter: str = "songs",
        video_id: Optional[str] = None,
        playlist_id: Optional[str] = None,
        limit: Optional[int] = None,
        **kwargs: Any,
    ) -> tuple[bool, Optional[str]]:
        mode_n = (mode or "play").strip().lower()
        if mode_n not in ("play", "search"):
            return False, "mode must be 'play' or 'search'."
        filt = (filter or "songs").strip().lower()
        if filt not in ("songs", "playlists"):
            return False, "filter must be 'songs' or 'playlists'."
        if video_id and (len(str(video_id).strip()) != _VIDEO_ID_RE_LEN):
            return False, "video_id must be an 11-character YouTube id."
        if not query and not video_id and not playlist_id:
            return False, "Provide query, video_id, or playlist_id."
        if mode_n == "search" and not (query or "").strip():
            return False, "mode=search requires a query."
        if limit is not None and (int(limit) < 1 or int(limit) > 10):
            return False, "limit must be between 1 and 10."
        return True, None

    def is_available(self) -> bool:
        if not super().is_available():
            return False
        try:
            import ytmusicapi  # noqa: F401
            return True
        except ImportError:
            return False

    async def execute(
        self,
        query: Optional[str] = None,
        mode: str = "play",
        filter: str = "songs",
        video_id: Optional[str] = None,
        playlist_id: Optional[str] = None,
        limit: Optional[int] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        mode_n = (mode or "play").strip().lower()
        filt = (filter or "songs").strip().lower()
        q = (query or "").strip()
        vid = (video_id or "").strip() or None
        pid = normalize_playlist_id(playlist_id)
        n = max(1, min(int(limit or 5), 10))

        # Direct open — no search
        if vid or pid:
            url = music_watch_url(vid) if vid else music_playlist_url(pid)  # type: ignore[arg-type]
            opened = await self._open_url(url)
            if not opened:
                return {"success": False, "error": f"Failed to open browser for {url}"}
            label = f"video {vid}" if vid else f"playlist {pid}"
            return {
                "success": True,
                "result": f"Opened YouTube Music ({label}) in your browser.",
                "url": url,
                "video_id": vid,
                "playlist_id": pid,
                "caption": f"YouTube Music: {label}",
            }

        try:
            hits = await asyncio.to_thread(self._search, q, filt, n if mode_n == "search" else max(n, 5))
        except ImportError:
            return {
                "success": False,
                "error": "ytmusicapi is not installed. Run: pip install ytmusicapi",
            }
        except Exception as e:
            logger.error(f"YouTube Music search failed: {e}", exc_info=True)
            return {"success": False, "error": f"YouTube Music search failed: {e}"}

        if not hits:
            return {
                "success": False,
                "error": f"No YouTube Music {filt} found for '{q}'. Try a different query.",
            }

        results = [summarize_result(h) for h in hits]
        # Drop incomplete rows (no openable URL)
        results = [r for r in results if r.get("url")]
        if not results:
            return {
                "success": False,
                "error": f"Search returned hits for '{q}' but none had playable IDs.",
            }

        if mode_n == "search":
            lines = []
            for i, r in enumerate(results[:n], 1):
                who = f" — {r['artists']}" if r.get("artists") else ""
                id_bit = r.get("video_id") or r.get("playlist_id")
                lines.append(f"{i}. [{r['kind']}] {r['title']}{who} ({id_bit})")
            return {
                "success": True,
                "result": f"YouTube Music results for '{q}':\n" + "\n".join(lines),
                "results": results[:n],
                "caption": f"YTM search: {q}",
            }

        pick = results[0]
        url = pick["url"]
        opened = await self._open_url(url)
        if not opened:
            return {"success": False, "error": f"Failed to open browser for {url}", "results": results[:n]}

        who = f" by {pick['artists']}" if pick.get("artists") else ""
        return {
            "success": True,
            "result": (
                f"Playing '{pick['title']}'{who} on YouTube Music in your browser. "
                f"({pick['kind']})"
            ),
            "url": url,
            "played": pick,
            "alternatives": results[1:n],
            "video_id": pick.get("video_id"),
            "playlist_id": pick.get("playlist_id"),
            "caption": f"YouTube Music: {pick['title']}",
        }

    def _search(self, query: str, filt: str, limit: int) -> List[Dict[str, Any]]:
        from ytmusicapi import YTMusic

        yt = YTMusic()
        # filter= songs|playlists matches ytmusicapi's search filter names
        raw = yt.search(query, filter=filt, limit=limit)
        hits = [r for r in (raw or []) if isinstance(r, dict)]
        return hits[:limit]

    async def _open_url(self, url: str) -> bool:
        try:
            await asyncio.to_thread(webbrowser.open, url)
            return True
        except Exception as e:
            logger.error(f"webbrowser.open failed for {url}: {e}")
            return False
