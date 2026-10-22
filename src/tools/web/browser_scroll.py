"""
Browser Scroll Tool — dedicated scroll primitive for the DOM-first browser_agent.

The Playwright MCP server (@playwright/mcp) exposes 23 tools but no scroll
primitive (issue #287). Without one, the model has to hand-roll a
`browser_evaluate` scrollBy call itself, which it rarely gets right, and it
has no reliable way to detect whether scrolling actually revealed new content
on infinite-scroll pages (AutoTrader, Zillow, etc.). This tool composes
scroll + settle-wait + re-snapshot into one call via the existing MCPExecutor,
the same pattern call_mcp_tool.py uses to reach into a registered MCP server.
"""

from typing import Any, Dict, Optional

from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger

logger = setup_logger("BrowserScrollTool")


class BrowserScrollTool(BaseTool):
    name = "browser_scroll"
    description = (
        "Scroll the current browser page and return a fresh snapshot. Use this on "
        "infinite-scroll or lazy-loaded pages (e.g. search result listings) to reveal "
        "more content before reading it — do not try to scroll via browser_evaluate."
    )
    category = "playwright"
    is_side_effecting = True

    def __init__(self, registry: Any, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        self.registry = registry
        self._turn_key: str = ""
        self._stationary_counts: Dict[str, int] = {}

    def _reset_turn_if_needed(self) -> None:
        try:
            from src.core.orchestration.context import session_id_var, turn_id_var, browser_run_id_var
            curr_key = f"{session_id_var.get()}:{browser_run_id_var.get() or turn_id_var.get()}"
        except Exception:
            curr_key = ""
        if self._turn_key != curr_key:
            self._turn_key = curr_key
            self._stationary_counts.clear()

    def format_action(self, params: Dict[str, Any]) -> str:
        direction = params.get("direction", "down")
        return f"Scrolling page {direction}..."

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["down", "up"],
                        "description": "Which direction to scroll. Default 'down'.",
                    },
                    "amount": {
                        "type": "number",
                        "description": "Pixels to scroll. Default one viewport height.",
                    },
                    "target": {"type": "string", "description": "Optional current ref of a scrollable panel or an element inside it. Scrolls that container instead of the window."},
                },
                "required": [],
            },
        }

    async def execute(self, direction: str = "down", amount: Optional[float] = None, target: Optional[str] = None, **kwargs) -> Dict[str, Any]:
        self._reset_turn_if_needed()
        from src.core.orchestration.context import browser_run_id_var
        if target or browser_run_id_var.get():
            from src.tools.web.container_scroll import scroll_container
            return await scroll_container(self.registry, direction, amount, target)

        # If this direction has already hit the boundary repeatedly, fail-fast immediately.
        if self._stationary_counts.get(direction, 0) >= 2:
            boundary_name = "bottom" if direction == "down" else "top"
            logger.info(f"[ BrowserScroll ] Fast-failing repetitive scroll: already at {boundary_name}.")
            return {
                "success": True,
                "result": [
                    {
                        "type": "text",
                        "text": (
                            f"[PAGE BOUNDARY REACHED / SCROLL SATIATED]: The page is already at the "
                            f"{boundary_name}. Scrolling {direction} has not revealed new elements. "
                            "Stop scrolling and proceed with your task using the elements already visible."
                        ),
                    }
                ],
            }

        mcp_registry = getattr(self.registry, "mcp_registry", None)
        if not mcp_registry:
            return {"success": False, "error": "MCP registry is not initialized in ToolRegistry."}

        server_entry = mcp_registry.get("playwright")
        if not server_entry:
            return {"success": False, "error": "Playwright MCP server is not connected."}

        from src.infrastructure.tools.execution.executor_strategy import MCPExecutor
        executor = MCPExecutor()

        sign = -1 if direction == "up" else 1
        delta = f"{sign} * ({amount if amount is not None else 'window.innerHeight'})"
        scroll_js = (
            "() => {\n"
            "    const prevY = window.scrollY || window.pageYOffset || 0;\n"
            "    const docHeight = Math.max(\n"
            "        document.body ? document.body.scrollHeight : 0,\n"
            "        document.documentElement ? document.documentElement.scrollHeight : 0,\n"
            "        document.body ? document.body.offsetHeight : 0,\n"
            "        document.documentElement ? document.documentElement.offsetHeight : 0\n"
            "    );\n"
            "    const winHeight = window.innerHeight || (document.documentElement ? document.documentElement.clientHeight : 1);\n"
            "    const maxY = Math.max(0, docHeight - winHeight);\n"
            f"    const delta = {delta};\n"
            "    const url = window.location.href || '';\n"
            "    const isBlank = !url || url === 'about:blank';\n"
            "    window.scrollBy(0, delta);\n"
            "    const currY = window.scrollY || window.pageYOffset || 0;\n"
            "    const moved = Math.abs(currY - prevY) > 5;\n"
            "    const atBottom = (currY >= maxY - 15) && (delta > 0);\n"
            "    const atTop = (currY <= 15) && (delta < 0);\n"
            "    return JSON.stringify({\n"
            "        url: url,\n"
            "        isBlank: isBlank,\n"
            "        prevY: Math.round(prevY),\n"
            "        currY: Math.round(currY),\n"
            "        maxY: Math.round(maxY),\n"
            "        moved: moved,\n"
            "        atBottom: atBottom,\n"
            "        atTop: atTop\n"
            "    });\n"
            "}"
        )

        scroll_result = await executor.execute(
            {"name": "browser_evaluate", "mcp_server": server_entry},
            {"function": scroll_js},
        )
        if not scroll_result.get("success"):
            return scroll_result

        # Parse metrics from scroll evaluation
        metrics = {}
        try:
            import json
            raw_res = scroll_result.get("result", [])
            for part in (raw_res if isinstance(raw_res, list) else [raw_res]):
                text_val = part.get("text") if isinstance(part, dict) else str(part)
                if text_val and "{" in text_val and "currY" in text_val:
                    # Strip any surrounding formatting
                    start = text_val.find("{")
                    end = text_val.rfind("}") + 1
                    metrics = json.loads(text_val[start:end])
                    break
        except Exception as parse_err:
            logger.debug(f"[ BrowserScroll ] Could not parse scroll metrics: {parse_err}")

        # If on about:blank, fail fast and give clear actionable recovery instructions
        if metrics.get("isBlank") or metrics.get("url") == "about:blank":
            logger.warning("[ BrowserScroll ] Attempted to scroll on about:blank. Halting with actionable error.")
            return {
                "success": False,
                "error": (
                    "The browser is currently on 'about:blank' (no website is loaded). "
                    "You must call browser_navigate(url=...) to open a website before you can scroll or inspect content."
                ),
            }

        moved = metrics.get("moved", True)
        at_bottom = metrics.get("atBottom", False)
        at_top = metrics.get("atTop", False)
        curr_y = metrics.get("currY", 0)
        max_y = metrics.get("maxY", 0)

        is_at_boundary = at_bottom if direction == "down" else at_top
        if is_at_boundary or not moved:
            self._stationary_counts[direction] = self._stationary_counts.get(direction, 0) + 1
        else:
            self._stationary_counts[direction] = 0

        # If already at boundary twice, stop now
        if self._stationary_counts[direction] >= 2:
            boundary_name = "bottom" if direction == "down" else "top"
            return {
                "success": True,
                "result": [
                    {
                        "type": "text",
                        "text": (
                            f"[PAGE BOUNDARY REACHED / SCROLL SATIATED]: The page is already at the "
                            f"{boundary_name} (scrollY={curr_y}/{max_y}). "
                            f"Scrolling {direction} has not revealed new elements. "
                            "Stop scrolling and proceed with your task using the elements already visible."
                        ),
                    }
                ],
            }

        # Let lazy-loaded content settle before re-reading the DOM.
        await executor.execute(
            {"name": "browser_wait_for", "mcp_server": server_entry},
            {"time": 0.5},
        )

        snapshot_result = await executor.execute(
            {"name": "browser_snapshot", "mcp_server": server_entry},
            {},
        )

        # Prepend boundary warning if hit for the first time
        if is_at_boundary and isinstance(snapshot_result.get("result"), list):
            boundary_name = "bottom" if direction == "down" else "top"
            banner = (
                f"[PAGE BOUNDARY REACHED]: The page is at the {boundary_name} (scrollY={curr_y}/{max_y}). "
                "Stop scrolling and proceed to act on the visible elements.\n\n"
            )
            res_list = snapshot_result["result"]
            if res_list and isinstance(res_list[0], dict) and "text" in res_list[0]:
                res_list[0]["text"] = banner + res_list[0]["text"]
            else:
                res_list.insert(0, {"type": "text", "text": banner})

        return snapshot_result
