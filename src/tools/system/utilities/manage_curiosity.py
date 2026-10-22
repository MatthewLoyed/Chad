"""
Manage Curiosity Tool - Interface for Curiosity Buffer, Content Discovery, and Interest Tuning.

Allows autonomous routines, heartbeat passes, or interactive chat to retrieve,
add, discover, or calibrate verified media and discoveries (YouTube shorts,
tech articles, trending GitHub repos, anime releases, and user interest tuning).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from src.tools.base_tool import BaseTool
from src.core.tools.formatter import tool_display
from src.core.autonomy.curiosity_service import CuriosityService
from src.core.discovery import ContentDiscoveryPipeline, InterestTuningEngine
from src.shared.logger import setup_logger

logger = setup_logger("ManageCuriosityTool")


@tool_display("Managing discoveries and interests")
class ManageCuriosity(BaseTool):
    """Tool for retrieving verified discoveries, executing discovery pipeline, and tuning interests."""

    name = "manage_curiosity"
    description = (
        "COMMAND: Retrieve, discover, or inspect verified discoveries and tune user interests. "
        "Actions:\n"
        "1. get_next: Pop the top verified item (YouTube short, GitHub repo, anime news, or tech find) ready to share.\n"
        "2. discover: Trigger content discovery pipeline across YouTube shorts, tech articles, GitHub repos, and anime releases.\n"
        "3. tune: Adjust affinity weights for a category or topic (0.1 to 3.0).\n"
        "4. get_interests: Inspect current interest profile, category weights, and topic affinities.\n"
        "5. feedback: Record user feedback (e.g. 'fire', 'sick', 'mid', 'less of this') to automatically tune weights.\n"
        "6. add: Manually add a newly discovered verified link to the stash.\n"
        "7. status: Check how many un-sent verified items are available in the stash."
    )
    category = "custom"

    def __init__(self, config: Optional[Dict[str, Any]] = None, **kwargs):
        super().__init__(config=config)
        self.curiosity_service = CuriosityService()
        self.tuning_engine = InterestTuningEngine()
        self.pipeline = ContentDiscoveryPipeline(tuning_engine=self.tuning_engine)

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": "Fetch verified discoveries to share with the user, discover new content, or tune interests.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["get_next", "discover", "tune", "get_interests", "feedback", "add", "status"],
                        "description": "get_next: pop top item; discover: run pipeline; tune: set weight; get_interests: view profile; feedback: learn from reaction; add: manual link; status: stash count",
                    },
                    "category": {
                        "type": "string",
                        "description": "Category name (e.g. 'youtube_shorts', 'tech', 'github', 'articles', 'anime', 'spikeball', 'meme').",
                    },
                    "topic": {
                        "type": "string",
                        "description": "Fine-grained topic for 'tune' or 'feedback' (e.g. 'local_llm', 'spikeball', 'rust').",
                    },
                    "weight": {
                        "type": "number",
                        "description": "Affinity weight for 'tune' action (0.1 to 3.0, 1.0 is neutral).",
                    },
                    "feedback_text": {
                        "type": "string",
                        "description": "User reaction or sentiment text for 'feedback' action (e.g. '🔥', 'banger', 'mid', 'more of this').",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max items to discover for 'discover' action (default 5).",
                    },
                    "push_to_dashboard": {
                        "type": "boolean",
                        "description": "For 'discover': whether to also publish top discoveries to user's web Dashboard feed.",
                    },
                    "url": {
                        "type": "string",
                        "description": "For 'add': the verified URL of the discovery.",
                    },
                    "title": {
                        "type": "string",
                        "description": "For 'add': title or brief description of the discovery.",
                    },
                    "summary": {
                        "type": "string",
                        "description": "For 'add': what makes this find interesting.",
                    },
                },
                "required": ["action"],
            },
        }

    async def execute(
        self,
        action: str = "get_next",
        category: Optional[str] = None,
        topic: Optional[str] = None,
        weight: Optional[float] = None,
        feedback_text: Optional[str] = None,
        limit: int = 5,
        push_to_dashboard: bool = False,
        url: Optional[str] = None,
        title: Optional[str] = None,
        summary: Optional[str] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        try:
            if action == "get_next":
                item = self.curiosity_service.pop_next_curiosity(category=category)
                if not item:
                    return {
                        "success": False,
                        "message": "No un-sent verified items currently available in the stash.",
                        "available_count": 0,
                    }
                return {
                    "success": True,
                    "item": item,
                    "tip": "Share this link directly in all-lowercase casual text with a 1-sentence take (e.g. 'check this out: <link>'). Do NOT use uppercase letters, corporate greetings, or metaphors.",
                }

            elif action == "discover":
                cats = [category] if category else None
                discovered = await self.pipeline.discover(
                    categories=cats,
                    limit_per_source=max(1, limit // 2),
                    max_total_items=limit,
                    verify=True,
                    push_to_stash=True,
                    push_to_dashboard=push_to_dashboard,
                )
                return {
                    "success": True,
                    "discovered_count": len(discovered),
                    "items": [it.to_dict() for it in discovered],
                }

            elif action == "tune":
                if weight is None:
                    return {"success": False, "error": "Weight is required for 'tune' action."}
                if category:
                    new_val = self.tuning_engine.tune_category(category, weight)
                    return {"success": True, "message": f"Category '{category}' weight set to {new_val}"}
                elif topic:
                    new_val = self.tuning_engine.tune_topic(topic, weight)
                    return {"success": True, "message": f"Topic '{topic}' weight set to {new_val}"}
                else:
                    return {"success": False, "error": "Either 'category' or 'topic' must be specified for 'tune'."}

            elif action == "get_interests":
                profile = self.tuning_engine.get_profile()
                return {
                    "success": True,
                    "categories": profile.categories,
                    "top_topics": sorted(profile.topics.items(), key=lambda x: x[1], reverse=True),
                    "blocked_domains": profile.blocked_domains,
                    "recent_feedback": profile.feedback_history[-5:],
                }

            elif action == "feedback":
                if not feedback_text:
                    return {"success": False, "error": "feedback_text is required for 'feedback' action."}
                res = self.tuning_engine.record_feedback(
                    feedback_text=feedback_text,
                    category=category,
                    topic=topic,
                    url=url,
                )
                return {
                    "success": True,
                    "feedback_result": res,
                }

            elif action == "add":
                if not url:
                    return {"success": False, "error": "URL is required for 'add' action."}
                added = await self.curiosity_service.add_item(
                    url=url,
                    title=title or url,
                    category=category or "tech",
                    summary=summary or "",
                    verify=True,
                )
                return {
                    "success": added,
                    "message": "Item added to curiosity stash." if added else "Item rejected (invalid URL or duplicate).",
                }

            elif action == "status":
                status = self.curiosity_service.get_status()
                pipeline_status = self.pipeline.get_pipeline_status()
                return {
                    "success": True,
                    "status": status,
                    "pipeline_status": pipeline_status,
                }

            else:
                return {"success": False, "error": f"Unknown action: {action}"}

        except Exception as e:
            logger.error(f"[ TOOL ] manage_curiosity failed: {e}", exc_info=True)
            return {"success": False, "error": str(e)}
