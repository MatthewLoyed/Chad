"""
Browser Tool - Open URLs in default browser
"""

import webbrowser
import sys
import asyncio
from typing import Dict, Any, Optional

from src.tools.base_tool import BaseTool
from src.core.tools.formatter import tool_display

from src.shared.logger import setup_logger

logger = setup_logger("BrowserTool")

@tool_display("Opening {query} in browser...")
class BrowserTool(BaseTool):

    """Tool for opening URLs in the default browser."""
    
    name = "open_browser"
    description = (
        "COMMAND: Launch the default browser to a named site (e.g. 'open youtube', 'go to "
        "reddit') for a visible window. Do NOT use to research/answer questions — use "
        "web_search instead (returns content, no window)."
    )
    category = "browser"
    
    def __init__(self):
        super().__init__()
    
    def get_schema(self) -> Dict[str, Any]:
        """Return the function schema for this tool."""
        return {
            "name": self.name,
            "description": "Launch the default browser to a specific destination or search query. THE AI SHOULD PROVIDE THE NAME, TOPIC, OR SEARCH TERM, NOT A URL. Example: 'youtube', 'aniwatch', 'youtube music search'. Only provide a raw URL if the user explicitly dictated one.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The destination name or search query. DO NOT GUESS URLs. Provide the brand or site name."
                    }
                },
                "required": ["query"]
            }
        }
    
    async def execute(self, query: str, **kwargs) -> Dict[str, Any]:
        """
        Open destination in browser using Verified Lucky Resolution.
        """
        import re
        target = query.strip()
        
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
                # Map special cases
                domain_map = {
                    'x': 'x.com',
                    'chatgpt': 'chatgpt.com',
                    'gemini': 'gemini.google.com',
                    'claude': 'claude.ai',
                    'aniwatch': 'aniwatch.to'
                }
                domain = domain_map.get(target_lower, f"{target_lower}.com")
                url = f"https://www.{domain}" if '.' in domain and not domain.startswith('www.') else f"https://{domain}"
                # Fix double www
                url = url.replace('www.www.', 'www.')
                webbrowser.open(url)
                return {
                    "success": True,
                    "result": f"Instantly opened {target.title()}",
                    "url": url,
                    "caption": f"Opening {target.title()}..."
                }

            # 1. ANALYZE INTENT
            is_strict_url = bool(re.search(r'^[a-z]+://', target)) or \
                            bool(re.search(r'\.[a-z]{2,}(/|$)', target.lower()))

            # 2. PATH A: Heuristic Expansion (Zero-Latency)
            if not is_strict_url and ' ' not in target and target.isalnum():
                heuristic_url = f"https://www.{target.lower()}.com"
                if await self._is_site_reachable(heuristic_url):
                    webbrowser.open(heuristic_url)
                    return {
                        "success": True, 
                        "result": f"Decisively navigated to {target}.com", 
                        "url": heuristic_url,
                        "caption": f"Navigating to {target.title()}..."
                    }

            # 3. PATH B: Strict URL (Verify and Launch)
            if is_strict_url:
                target_url = target if '://' in target else f'https://{target}'
                if await self._is_site_reachable(target_url):
                    webbrowser.open(target_url)
                    return {"success": True, "result": f"Opened {target_url}", "url": target_url}
                else:
                    logger.warning(f"URL '{target_url}' unreachable. Escalating to Lucky Resolution.")
                    # Fall through to Path C using domain as query
                    target = target.split('//')[-1].split('/')[0].split('.')[0]

            # 4. PATH C: THE PERMANENT FIX - "Silent Resolution Tunnel"
            # We resolve the 'I'm Feeling Lucky' destination in the background
            # to avoid the 'Redirect Notice' page and extra clicks.
            logger.info(f"[ RESOLVER ] : Resolving '{target}' in background tunnel...")
            
            resolved_url = await self._resolve_lucky_destination(target)
            
            # Launch the final destination
            webbrowser.open(resolved_url)
            
            return {
                "success": True,
                "result": f"Resolved and navigated to {resolved_url}",
                "url": resolved_url,
                "caption": f"Launching {target.title()}..."
            }
            
        except Exception as e:
            logger.error(f"Navigation Error: {e}")
            return {"success": False, "error": str(e)}

    async def _resolve_lucky_destination(self, query: str) -> str:
        """
        Follows the 'I'm Feeling Lucky' redirect chain manually in the background
        to extract the final landing page URL.
        """
        import httpx
        from urllib.parse import urlparse, parse_qs
        
        # Professional Chrome User-Agent to avoid 'Redirect Notice' detections
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        
        lucky_url = f"https://www.google.com/search?q={query.replace(' ', '+')}&btnI=I"
        
        try:
            async with httpx.AsyncClient(headers=headers, follow_redirects=True, verify=False) as client:
                # We timeout at 1 second - if it's slower than that, the user is better off seeing the search results
                resp = await client.get(lucky_url, timeout=1.0)
                
                final_url = str(resp.url)
                
                # Check for the dreaded 'Redirect Notice'
                if "google.com/url?" in final_url:
                    parsed = urlparse(final_url)
                    qs = parse_qs(parsed.query)
                    if 'q' in qs:
                        return qs['q'][0]
                
                return final_url
                
        except Exception as e:
            logger.warning(f"Background tunnel failed: {e}. Falling back to direct Lucky URL.")
            return lucky_url

    async def _is_site_reachable(self, url: str) -> bool:
        """
        Professional Aliveness Check.
        Uses a very tight timeout to ensure no perceptible delay.
        """
        import httpx
        try:
            async with httpx.AsyncClient(verify=False) as client:
                # 300ms safety window
                resp = await client.head(url, timeout=0.3, follow_redirects=True)
                # Treat any non-404 as 'alive' (including 403 blocks)
                return resp.status_code != 404 and resp.status_code < 500
        except:
            return False

    def validate_params(self, query: str = None, **kwargs) -> tuple[bool, str | None]:
        """Validate parameters."""
        # Check both 'query' and legacy 'url' field for compatibility during transition
        target = query or kwargs.get('url')
        if not target or not target.strip():
            return False, "Destination query cannot be empty"
        return True, None

