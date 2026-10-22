"""Render text-based diagrams via Kroki (Mermaid, PlantUML, D2, GraphViz, etc.)."""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

import httpx

from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger
from src.tools.base_tool import BaseTool
from src.tools.display_helpers import truncate_label
from src.tools.visual._artifact import deliver_artifact, unique_filename

logger = setup_logger("RenderDiagram")

SUPPORTED_TYPES = {
    "mermaid",
    "plantuml",
    "graphviz",
    "d2",
    "erd",
    "blockdiag",
    "bpmn",
    "c4plantuml",
}
SUPPORTED_FORMATS = {"svg", "png", "pdf"}


class RenderDiagramTool(BaseTool):
    name = "render_diagram"
    description = (
        "Render a text-based diagram to SVG, PNG, or PDF using Kroki. "
        "Use after load_skill('kroki') when the user wants flowcharts, sequence diagrams, "
        "architecture diagrams, ER diagrams, or other Kroki-supported diagram types. "
        "Provide diagram_source in the target language (e.g. Mermaid syntax for diagram_type=mermaid)."
    )
    category = "visual"
    is_side_effecting = True

    def format_action(self, params: Dict[str, Any]) -> str:
        dtype = str(params.get("diagram_type") or "diagram")
        fmt = str(params.get("output_format") or "svg")
        title = truncate_label(str(params.get("title") or ""))
        if title:
            return f'Rendering {dtype} diagram "{title}" as {fmt.upper()}'
        return f"Rendering {dtype} diagram as {fmt.upper()}"

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "diagram_source": {
                        "type": "string",
                        "description": "Diagram source code (Mermaid, PlantUML, D2, etc.).",
                    },
                    "diagram_type": {
                        "type": "string",
                        "enum": sorted(SUPPORTED_TYPES),
                        "description": "Kroki diagram engine.",
                    },
                    "output_format": {
                        "type": "string",
                        "enum": sorted(SUPPORTED_FORMATS),
                        "description": "Output format. Default svg.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Short label for the output filename.",
                    },
                },
                "required": ["diagram_source", "diagram_type"],
            },
        }

    def _kroki_base(self) -> str:
        return str(
            ConfigManager.get("visual.kroki_url", "https://kroki.io")
        ).rstrip("/")

    async def execute(
        self,
        diagram_source: str,
        diagram_type: str,
        output_format: str = "svg",
        title: str = "diagram",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        diagram_type = (diagram_type or "").strip().lower()
        output_format = (output_format or "svg").strip().lower()
        source = (diagram_source or "").strip()

        if diagram_type not in SUPPORTED_TYPES:
            return {
                "success": False,
                "error": f"Unsupported diagram_type '{diagram_type}'.",
            }
        if output_format not in SUPPORTED_FORMATS:
            return {
                "success": False,
                "error": f"Unsupported output_format '{output_format}'.",
            }
        if not source:
            return {"success": False, "error": "diagram_source is required."}

        url = f"{self._kroki_base()}/{diagram_type}/{output_format}"
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    url,
                    content=source.encode("utf-8"),
                    headers={"Content-Type": "text/plain"},
                )
                response.raise_for_status()
                body = response.content
        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:500] if exc.response is not None else str(exc)
            logger.error(f"Kroki HTTP error: {detail}")
            return {
                "success": False,
                "error": f"Kroki render failed ({exc.response.status_code}): {detail}",
                "hint": "Call load_skill('kroki') and fix diagram syntax, then retry.",
            }
        except Exception as exc:
            logger.error(f"Kroki request failed: {exc}")
            return {
                "success": False,
                "error": f"Could not reach Kroki at {self._kroki_base()}: {exc}",
                "hint": "Self-host: docker run -p 8000:8000 yuzutech/kroki and set visual.kroki_url.",
            }

        filename = unique_filename(title or diagram_type, output_format)
        caption = f"Rendered {diagram_type} diagram ({output_format.upper()})"
        payload = deliver_artifact(
            body,
            filename,
            caption=caption,
            auto_preview=output_format in {"svg", "png", "pdf"},
        )
        payload["diagram_type"] = diagram_type
        payload["output_format"] = output_format
        return payload
