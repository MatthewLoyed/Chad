---
name: youtube_music
description: "Play songs and playlists on YouTube Music in the browser. Triggers on play music, put on some X, anime OST, chill playlist, YouTube Music requests. Not for watching YouTube videos in chat (use web_search + show_youtube_video)."
version: 0.1.0
license: MIT
tags: [music, youtube, media, playlist]
---

# YouTube Music (MVP)

Search the public YouTube Music catalog and open a match in the user's default browser.
No account auth yet — results are not personalized, and there is no in-chat player.

## When to use
- ✅ "play me some anime music", "put on lo-fi", "play a chill playlist", "YouTube Music…"
- ❌ "show me a youtube **video** about X" → `web_search` + `show_youtube_video`
- ❌ System volume → `control_volume`

## SOP
1. Call `play_youtube_music` with the user's mood/genre/song/artist as `query`.
2. Default `mode=play` searches and opens the top match on `music.youtube.com`.
3. If they want options first: `mode=search`, then call again with `video_id` or `playlist_id`.
4. Prefer `filter=playlists` when they ask for a playlist/mix/radio-style set; otherwise `songs`.

## Example
User: "play me some anime music"
→ `play_youtube_music(query="anime music", mode="play", filter="songs")`

User: "find anime playlists first"
→ `play_youtube_music(query="anime", mode="search", filter="playlists")`
→ then `play_youtube_music(playlist_id="...")` for their pick

## MVP limits
- Opens the browser only — no library, likes, history, or taste learning yet.
- Unauthenticated search; Google may rate-limit aggressive calls.
- Skip/like learning and account radio come later.
