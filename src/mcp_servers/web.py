"""
chad-web - Browser and Web MCP Server for CHAD
Provides safe, robust browser navigation and media content retrieval capabilities.
"""

import os
import re
import sys
import logging
import asyncio
import webbrowser
from typing import Dict, Any, Optional, List

from mcp.server.fastmcp import FastMCP

from src.shared.logger import setup_logger

logger = setup_logger("chad-web-server")

# Initialize the FastMCP server
mcp = FastMCP("chad-web")


# ============================================================================
# READ-CLASS WEB TOOLS (AUTO-EXECUTE)
# ============================================================================

@mcp.tool(meta={"hitl_class": "READ"})
async def youtube_transcript(video_url: str) -> Dict[str, Any]:
    """
    Fetch transcripts and metadata for YouTube videos. Use for summarizing or processing video content.
    
    Args:
        video_url: The full URL of the YouTube video.
    """
    def extract_video_id(url: str) -> Optional[str]:
        patterns = [
            r'(?:v=|\/)([0-9A-Za-z_-]{11}).*',
            r'youtu\.be\/([0-9A-Za-z_-]{11})',
            r'embed\/([0-9A-Za-z_-]{11})'
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    video_id = extract_video_id(video_url)
    if not video_id:
        return {"success": False, "error": f"Invalid YouTube URL: {video_url}"}

    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        
        # Run inside a thread to keep it asynchronous
        def fetch_transcript():
            if hasattr(YouTubeTranscriptApi, "get_transcript"):
                return YouTubeTranscriptApi.get_transcript(video_id)
            fetched = YouTubeTranscriptApi().fetch(video_id)
            return [
                {"text": getattr(t, "text", t["text"] if isinstance(t, dict) else str(t))}
                for t in fetched
            ]
            
        transcript_list = await asyncio.to_thread(fetch_transcript)
        full_text = " ".join([t['text'] for t in transcript_list])
        
        return {
            "success": True,
            "result": f"Successfully retrieved transcript for video {video_id}",
            "transcript": full_text[:10000],  # Soft limit to avoid token explosion
            "is_truncated": len(full_text) > 10000
        }
    except Exception as e:
        return {
            "success": False, 
            "error": f"Could not retrieve transcript: {str(e)}. This might happen if captions are disabled."
        }


# ============================================================================
# WRITE-CLASS WEB TOOLS (GATED EXECUTION)
# ============================================================================

@mcp.tool(meta={"hitl_class": "WRITE"})
async def open_browser(query: str) -> Dict[str, Any]:
    """
    Launch the default browser to a specific destination or search query.
    Provide the brand or site name (e.g. 'youtube', 'github', 'stackoverflow').
    Only provide a raw URL if the user explicitly dictated one.
    
    Args:
        query: The destination name, search query, or URL.
    """
    target = query.strip()
    
    async def is_site_reachable(url: str) -> bool:
        import httpx
        try:
            async with httpx.AsyncClient(verify=False) as client:
                resp = await client.head(url, timeout=0.3, follow_redirects=True)
                return resp.status_code != 404 and resp.status_code < 500
        except:
            return False

    async def resolve_lucky_destination(q: str) -> str:
        import httpx
        from urllib.parse import urlparse, parse_qs
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        lucky_url = f"https://www.google.com/search?q={q.replace(' ', '+')}&btnI=I"
        try:
            async with httpx.AsyncClient(headers=headers, follow_redirects=True, verify=False) as client:
                resp = await client.get(lucky_url, timeout=1.0)
                final_url = str(resp.url)
                if "google.com/url?" in final_url:
                    parsed = urlparse(final_url)
                    qs = parse_qs(parsed.query)
                    if 'q' in qs:
                        return qs['q'][0]
                return final_url
        except Exception as e:
            logger.warning(f"Lucky resolution background tunnel failed: {e}. Falling back to lucky URL.")
            return lucky_url

    try:
        # === KNOWN DOMAINS: Skip ALL verification for instant navigation ===
        KNOWN_DOMAINS = {
            'youtube', 'reddit', 'google', 'twitter', 'x', 'facebook', 'instagram',
            'github', 'stackoverflow', 'wikipedia', 'amazon', 'netflix', 'spotify',
            'twitch', 'discord', 'linkedin', 'chatgpt', 'openai', 'gemini', 'claude',
            'aniwatch', 'crunchyroll', 'hulu', 'autotrader', 'zillow', 'indeed'
        }
        target_lower = target.lower().strip()
        if target_lower in KNOWN_DOMAINS:
            domain_map = {
                'x': 'x.com',
                'chatgpt': 'chatgpt.com',
                'gemini': 'gemini.google.com',
                'claude': 'claude.ai',
                'aniwatch': 'aniwatch.to'
            }
            domain = domain_map.get(target_lower, f"{target_lower}.com")
            url = f"https://www.{domain}" if '.' in domain and not domain.startswith('www.') else f"https://{domain}"
            url = url.replace('www.www.', 'www.')
            
            # Execute browser open
            await asyncio.to_thread(webbrowser.open, url)
            return {
                "success": True,
                "result": f"Instantly opened {target.title()}",
                "url": url,
                "caption": f"Opening {target.title()}..."
            }

        # Analyze strictly URL intent
        is_strict_url = bool(re.search(r'^[a-z]+://', target)) or \
                        bool(re.search(r'\.[a-z]{2,}(/|$)', target.lower()))

        # Path A: Heuristic Expansion
        if not is_strict_url and ' ' not in target and target.isalnum():
            heuristic_url = f"https://www.{target.lower()}.com"
            if await is_site_reachable(heuristic_url):
                await asyncio.to_thread(webbrowser.open, heuristic_url)
                return {
                    "success": True, 
                    "result": f"Decisively navigated to {target}.com", 
                    "url": heuristic_url,
                    "caption": f"Navigating to {target.title()}..."
                }

        # Path B: Strict URL Verify
        if is_strict_url:
            target_url = target if '://' in target else f'https://{target}'
            if await is_site_reachable(target_url):
                await asyncio.to_thread(webbrowser.open, target_url)
                return {"success": True, "result": f"Opened {target_url}", "url": target_url}
            else:
                logger.warning(f"URL '{target_url}' unreachable. Escalating to Lucky Resolution.")
                target = target.split('//')[-1].split('/')[0].split('.')[0]

        # Path C: Silent Lucky Resolution
        logger.info(f"[ RESOLVER ] : Resolving '{target}' in background tunnel...")
        resolved_url = await resolve_lucky_destination(target)
        
        await asyncio.to_thread(webbrowser.open, resolved_url)
        return {
            "success": True,
            "result": f"Resolved and navigated to {resolved_url}",
            "url": resolved_url,
            "caption": f"Launching {target.title()}..."
        }
        
    except Exception as e:
        logger.error(f"Browser navigation error: {e}")
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    mcp.run()
