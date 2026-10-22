from typing import Dict, Any, List, Optional
from src.tools.base_tool import BaseTool

class ToolSearchTool(BaseTool):
    """
    Tool to search for other available tools in the system.
    Helps models discover capabilities on-demand without bloating the initial prompt.
    """
    
    name: str = "search_for_tool"
    description: str = (
        "Search for an available tool that can help with a task. "
        "Use this when you don't have the right tool exposed to complete a request. "
        "You can optionally specify a category (e.g. 'canvas', 'browser', 'workspace', 'system') "
        "to scope the search."
    )
    category: str = "system"
    is_side_effecting: bool = False

    def format_action(self, params: Dict[str, Any]) -> str:
        q = params.get("query", "")
        cat = params.get("category")
        if cat:
            return f"Searching tools for '{q}' in category '{cat}'"
        return f"Searching tools for '{q}'" if q else "Searching available tools"

    def __init__(self, config: Optional[Dict[str, Any]] = None, registry=None):
        super().__init__(config)
        self.registry = registry

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The capability or task you are searching for (e.g., 'download course files', 'open an application', 'send email')"
                    },
                    "category": {
                        "type": "string",
                        "description": "Optional category or namespace to scope the search (e.g. 'canvas', 'browser', 'workspace', 'system', 'playwright', 'os')"
                    }
                },
                "required": ["query"]
            }
        }

    async def execute(self, query: str = None, category: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        if not query:
            return {"success": False, "error": "Please provide a search query."}
        
        if not self.registry:
            return {"success": False, "error": "Registry not available."}
            
        try:
            # Use semantic search from the registry with optional category filter
            new_schemas = self.registry.search_tools(query, limit=5, category=category)
            
            results = []
            for schema in new_schemas:
                name = schema.get("name", "unknown")
                if name == self.name: continue
                
                results.append({
                    "name": name,
                    "description": schema.get("description", "No description available")
                })
            
            # Create a human-readable summary for the model to see immediately
            found_count = len(results)
            scope_desc = f" in category '{category}'" if category else ""
            if found_count == 0:
                # Capability-gap signal: Chad explicitly went looking for a tool and the
                # registry had nothing. This is his own deliberate admission of a missing
                # capability — log it (fire-and-forget) so recurring misses can be turned
                # into tool/skill suggestions. Never let this break the tool call.
                self._log_capability_gap(query, found_count=0, matched_tools=[])
                return {"success": True, "result": {"message": f"No tools found for that query{scope_desc}.", "tools": []}}

            formatted_list = "\n".join([f"- {r['name']}: {r['description']}" for r in results])
            
            return {
                "success": True,
                "result": {
                    "message": f"Found {found_count} matching tools{scope_desc}:\n{formatted_list}\n\nThese tools have been injected and are now available for you to use.",
                    "tools": results,
                    "_injected_schemas": new_schemas # Metadata for orchestrator to pick up
                }
            }
        except Exception as e:
            from src.shared.logger import setup_logger
            logger = setup_logger("ToolSearch")
            logger.error(f"Search failed: {e}", exc_info=True)
            return {"success": False, "error": f"Search failed: {str(e)}"}

    def _log_capability_gap(self, query: str, *, found_count: int, matched_tools: list) -> None:
        """Fire-and-forget: record a missing-capability event for self-improvement."""
        try:
            import asyncio
            from src.core.intelligence.capability_gaps import log_gap
            from src.core.orchestration.context import current_turn_tool_queries_var

            turn_id = None
            try:
                ctx = current_turn_tool_queries_var.get()
                if ctx:
                    turn_id = ctx.get("__turn_id__")
            except Exception:
                pass

            asyncio.create_task(log_gap(
                query, found_count=found_count, matched_tools=matched_tools,
                turn_id=turn_id, registry=self.registry))
        except Exception as e:
            from src.shared.logger import setup_logger
            setup_logger("ToolSearch").error(f"Failed to log capability gap: {e}")
