import asyncio
import logging
from typing import Dict, Any, List, Optional
import re
from src.tools.base_tool import BaseTool

class YouTubeToolkitTool(BaseTool):
    """Tool for fetching YouTube video transcripts and metadata."""
    
    name = "youtube_toolkit"
    description = "API: Fetch transcripts and metadata for YouTube videos. Use for summarizing or processing video content. Do NOT use for searching, recommending, or displaying videos in chat (use web_search + show_youtube_video)."
    category = "web"
    
    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "video_url": {
                        "type": "string",
                        "description": "The full URL of the YouTube video."
                    },
                    "action": {
                        "type": "string",
                        "enum": ["get_transcript", "get_metadata"],
                        "description": "What information to retrieve."
                    }
                },
                "required": ["video_url"]
            }
        }

    def _extract_video_id(self, url: str) -> Optional[str]:
        """Extract the 11-character video ID from various YouTube URL formats."""
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

    async def execute(self, video_url: str, action: str = "get_transcript", **kwargs) -> Dict[str, Any]:
        video_id = self._extract_video_id(video_url)
        if not video_id:
            return {"success": False, "error": f"Invalid YouTube URL: {video_url}"}

        try:
            if action == "get_transcript":
                from youtube_transcript_api import YouTubeTranscriptApi
                
                try:
                    def _fetch():
                        if hasattr(YouTubeTranscriptApi, "get_transcript"):
                            return YouTubeTranscriptApi.get_transcript(video_id)
                        fetched = YouTubeTranscriptApi().fetch(video_id)
                        return [
                            {"text": getattr(t, "text", t["text"] if isinstance(t, dict) else str(t))}
                            for t in fetched
                        ]

                    transcript_list = await asyncio.to_thread(_fetch)
                    # Combine transcript parts into a single text block
                    full_text = " ".join([t['text'] for t in transcript_list])
                    
                    return {
                        "success": True,
                        "result": f"Successfully retrieved transcript for video {video_id}",
                        "transcript": full_text[:10000],  # Soft limit to avoid token explosion
                        "is_truncated": len(full_text) > 10000
                    }
                except Exception as e:
                    return {"success": False, "error": f"Could not retrieve transcript: {str(e)}. This might happen if captions are disabled."}

            elif action == "get_metadata":
                 # Fallback to a basic search or info scraping if needed
                 # For now, let's just return the video ID as we don't have a reliable library for metadata without API key
                 return {
                     "success": True,
                     "result": f"Video ID identified as {video_id}",
                     "video_id": video_id
                 }

            return {"success": False, "error": f"Unknown action: {action}"}
            
        except Exception as e:
            logging.error(f"Error in {self.name}: {e}")
            return {"success": False, "error": str(e)}
