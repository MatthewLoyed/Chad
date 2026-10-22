"""Render statistical charts (donut, bar, line, radar, etc.) via QuickChart / Chart.js.

Produces previewable image artifacts (PNG, SVG) and cloud chart URLs that integrate
seamlessly into Chad's Web UI, Artifacts viewer, and Telegram photo delivery.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional, Union

import httpx

from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger
from src.tools.base_tool import BaseTool
from src.tools.display_helpers import truncate_label
from src.tools.visual._artifact import deliver_artifact, unique_filename

logger = setup_logger("RenderChart")

SUPPORTED_FORMATS = {"png", "svg", "webp", "pdf"}


class RenderChartTool(BaseTool):
    name = "render_chart"
    description = (
        "Render statistical charts (donut, bar, line, radar, sparkline) via QuickChart/Chart.js "
        "to PNG/SVG image artifacts and shareable URLs. Use when visualizing data such as "
        "personal finances, spending breakdowns, monthly cashflows, net worth, balances, or trends. "
        "Provide chart_config as a Chart.js JSON dict or JSON string."
    )
    category = "visual"
    is_side_effecting = True

    def format_action(self, params: Dict[str, Any]) -> str:
        fmt = str(params.get("output_format") or "png").upper()
        title = truncate_label(str(params.get("title") or "chart"))
        return f'Rendering chart "{title}" as {fmt}'

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": (
                "Render statistical charts (donut, bar, line, radar, etc.) to image artifacts and shareable URLs. "
                "You can EITHER pass simple convenience parameters (chart_type, labels, data) OR an advanced full chart_config dict."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "chart_type": {
                        "type": "string",
                        "enum": ["doughnut", "bar", "horizontalBar", "line", "pie", "radar"],
                        "description": "Chart style (e.g. 'doughnut', 'bar', 'line'). Default: 'doughnut'.",
                    },
                    "labels": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Category labels (e.g. ['Cash & Savings', 'Investments', 'Debt']).",
                    },
                    "data": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "Numeric data points corresponding to labels (e.g. [14040.40, 5768.35, 126.83]).",
                    },
                    "title": {
                        "type": "string",
                        "description": "Chart title and filename label (e.g. 'Financial Composition', 'Monthly Spending').",
                    },
                    "colors": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional list of hex colors (e.g. ['#4CAF50', '#2196F3', '#F44336']).",
                    },
                    "chart_config": {
                        "type": ["object", "string"],
                        "description": (
                            "Optional advanced Chart.js configuration dictionary or JSON string. "
                            "If omitted, chart_config is automatically generated from chart_type, labels, and data."
                        ),
                    },
                    "output_format": {
                        "type": "string",
                        "enum": ["png", "svg", "webp", "pdf"],
                        "description": "Image format. Default: 'png'.",
                    },
                    "width": {
                        "type": "integer",
                        "description": "Chart image width in pixels. Default: 600.",
                    },
                    "height": {
                        "type": "integer",
                        "description": "Chart image height in pixels. Default: 400.",
                    },
                    "background_color": {
                        "type": "string",
                        "description": "Chart background color (e.g. 'transparent', '#ffffff', '#1e1e2e'). Default: 'transparent'.",
                    },
                },
                "required": [],
            },
        }

    def _quickchart_base(self) -> str:
        return str(
            ConfigManager.get("visual.quickchart_url", "https://quickchart.io")
        ).rstrip("/")

    async def execute(
        self,
        chart_config: Optional[Union[Dict[str, Any], str]] = None,
        chart_type: str = "doughnut",
        labels: Optional[list] = None,
        data: Optional[list] = None,
        colors: Optional[list] = None,
        title: str = "chart",
        output_format: str = "png",
        width: int = 600,
        height: int = 400,
        background_color: str = "transparent",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        output_format = (output_format or "png").strip().lower()
        if output_format not in SUPPORTED_FORMATS:
            return {
                "success": False,
                "error": f"Unsupported output_format '{output_format}'. Supported: {sorted(SUPPORTED_FORMATS)}",
            }

        chart_obj: Optional[Dict[str, Any]] = None

        if chart_config:
            if isinstance(chart_config, str):
                clean_str = chart_config.strip()
                # Strip outer wrapper quotes if model escaped the entire JSON: '{"type": ...}' or "'{...}'"
                while len(clean_str) >= 2 and ((clean_str[0] == "'" and clean_str[-1] == "'") or (clean_str[0] == '"' and clean_str[-1] == '"')):
                    clean_str = clean_str[1:-1].strip()
                try:
                    chart_obj = json.loads(clean_str)
                except Exception as e:
                    import ast
                    try:
                        chart_obj = ast.literal_eval(clean_str)
                        if not isinstance(chart_obj, dict):
                            raise ValueError("Evaluated expression is not a dict")
                    except Exception:
                        return {
                            "success": False,
                            "error": f"Invalid JSON in chart_config: {e}",
                            "hint": "Pass a valid Chart.js JSON dict or JSON string.",
                        }
            elif isinstance(chart_config, dict):
                chart_obj = chart_config
            else:
                return {
                    "success": False,
                    "error": "chart_config must be a dictionary or JSON string.",
                }
        elif labels and data:
            ctype = (chart_type or "doughnut").strip()
            # Default modern aesthetic palette
            default_palette = [
                "#4CAF50", "#2196F3", "#FF9800", "#9C27B0", "#F44336",
                "#00BCD4", "#FFC107", "#795548", "#607D8B", "#E91E63"
            ]
            palette = colors or default_palette
            is_bar_or_line = ctype in ("bar", "horizontalBar", "line")

            dataset: Dict[str, Any] = {
                "data": list(data),
                "backgroundColor": palette[0] if (ctype == "line") else palette[:len(labels)],
            }
            if ctype == "line":
                dataset["fill"] = False
                dataset["borderColor"] = palette[0]
                dataset["tension"] = 0.3
            else:
                dataset["borderWidth"] = 0

            if title:
                dataset["label"] = title

            chart_obj = {
                "type": ctype,
                "data": {
                    "labels": list(labels),
                    "datasets": [dataset],
                },
                "options": {
                    "responsive": True,
                    "plugins": {
                        "legend": {
                            "display": not is_bar_or_line,
                            "position": "bottom",
                        },
                    },
                },
            }
            if ctype == "doughnut":
                chart_obj["options"]["cutout"] = "60%"
        else:
            return {
                "success": False,
                "error": "Either 'chart_config' OR ('labels' and 'data') must be provided.",
                "hint": "E.g. render_chart(chart_type='doughnut', labels=['Cash', 'Debt'], data=[14000, 100], title='Finances')",
            }

        payload_data = {
            "chart": chart_obj,
            "width": max(200, min(int(width or 600), 2000)),
            "height": max(150, min(int(height or 400), 2000)),
            "format": output_format,
            "backgroundColor": background_color or "transparent",
        }

        base_url = self._quickchart_base()
        render_url = f"{base_url}/chart"
        create_url = f"{base_url}/chart/create"

        image_bytes: Optional[bytes] = None
        short_url: Optional[str] = None

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # 1. Fetch raw image bytes for local artifact storage and preview
                resp = await client.post(render_url, json=payload_data)
                resp.raise_for_status()
                image_bytes = resp.content

                # 2. Also try creating a hosted short URL for easy sharing/embedding
                try:
                    create_resp = await client.post(create_url, json=payload_data, timeout=5.0)
                    if create_resp.status_code == 200:
                        create_json = create_resp.json()
                        if create_json.get("success"):
                            short_url = create_json.get("url")
                except Exception as ex:
                    logger.debug(f"QuickChart create_url optional call failed: {ex}")

        except httpx.HTTPStatusError as exc:
            detail = exc.response.text[:400] if exc.response is not None else str(exc)
            logger.error(f"QuickChart HTTP error: {detail}")
            return {
                "success": False,
                "error": f"QuickChart render failed ({exc.response.status_code}): {detail}",
                "hint": "Verify Chart.js configuration structure (type, data.labels, data.datasets).",
            }
        except Exception as exc:
            logger.error(f"QuickChart request failed: {exc}")
            return {
                "success": False,
                "error": f"Could not reach QuickChart at {base_url}: {exc}",
                "hint": "Check network connectivity or configure local visual.quickchart_url.",
            }

        filename = unique_filename(title or "chart", output_format)
        caption = f"Rendered {chart_obj.get('type', 'chart').title()} ({output_format.upper()})"

        artifact_payload = deliver_artifact(
            image_bytes,
            filename,
            caption=caption,
            auto_preview=output_format in {"png", "svg", "webp", "pdf"},
        )

        artifact_payload["chart_type"] = chart_obj.get("type", "chart")
        artifact_payload["output_format"] = output_format
        if short_url:
            artifact_payload["chart_url"] = short_url

        return artifact_payload
