"""Render Marp markdown slide decks to PDF, PPTX, or HTML."""

from __future__ import annotations

import asyncio
import os
import shutil
from typing import Any, Dict, List

from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger
from src.tools.base_tool import BaseTool
from src.tools.display_helpers import truncate_label
from src.tools.visual._artifact import (
    artifact_path,
    deliver_artifact,
    unique_filename,
    write_text_artifact,
)

logger = setup_logger("RenderMarp")

SUPPORTED_EXPORTS = {"pdf", "pptx", "html"}


class RenderMarpTool(BaseTool):
    name = "render_marp"
    description = (
        "Render a Marp markdown slide deck to PDF, PPTX, or HTML. "
        "Use after load_skill('marp') when the user wants quick slides from markdown. "
        "For editing existing .pptx files use load_skill('pptx') instead."
    )
    category = "visual"
    is_side_effecting = True

    def format_action(self, params: Dict[str, Any]) -> str:
        fmt = str(params.get("export_format") or "pdf").upper()
        title = truncate_label(str(params.get("title") or ""))
        if title:
            return f'Rendering Marp deck "{title}" as {fmt}'
        return f"Rendering Marp slides as {fmt}"

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "markdown": {
                        "type": "string",
                        "description": (
                            "Marp markdown with YAML frontmatter (marp: true) and --- slide breaks."
                        ),
                    },
                    "export_format": {
                        "type": "string",
                        "enum": sorted(SUPPORTED_EXPORTS),
                        "description": "Output format. Default pdf.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Short label for output filenames.",
                    },
                },
                "required": ["markdown"],
            },
        }

    def _marp_command(self) -> List[str]:
        configured = ConfigManager.get("visual.marp_command")
        if isinstance(configured, list) and configured:
            return [str(part) for part in configured]
        if isinstance(configured, str) and configured.strip():
            return configured.strip().split()
        return ["npx", "--yes", "@marp-team/marp-cli"]

    def _ensure_marp_frontmatter(self, markdown: str) -> str:
        text = markdown.strip()
        if text.startswith("---"):
            return text + "\n"
        return "---\nmarp: true\ntheme: default\npaginate: true\n---\n\n" + text + "\n"

    async def execute(
        self,
        markdown: str,
        export_format: str = "pdf",
        title: str = "slides",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        export_format = (export_format or "pdf").strip().lower()
        if export_format not in SUPPORTED_EXPORTS:
            return {
                "success": False,
                "error": f"Unsupported export_format '{export_format}'.",
            }

        body = self._ensure_marp_frontmatter(markdown or "")
        if not body.strip():
            return {"success": False, "error": "markdown is required."}

        md_name = unique_filename(title, ".md")
        md_path = write_text_artifact(body, md_name)[0]
        out_base = os.path.splitext(md_name)[0]
        out_path = artifact_path(f"{out_base}.{export_format}")

        base_cmd = self._marp_command()
        if base_cmd[:1] == ["npx"] and not shutil.which("npx"):
            return {
                "success": False,
                "error": "npx not found — install Node.js or set visual.marp_command to marp CLI path.",
            }

        flag = f"--{export_format}"
        cmd = [*base_cmd, md_path, flag, "-o", out_path]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
        except FileNotFoundError:
            return {
                "success": False,
                "error": f"Could not execute Marp command: {' '.join(base_cmd)}",
                "hint": "npm install -g @marp-team/marp-cli",
            }

        if proc.returncode != 0:
            err = (stderr or stdout or b"").decode("utf-8", errors="replace")[:2000]
            return {
                "success": False,
                "error": f"Marp render failed:\n{err}",
                "hint": "Call load_skill('marp') and fix markdown/frontmatter.",
                "source_path": md_path,
            }

        if not os.path.isfile(out_path):
            return {
                "success": False,
                "error": f"Marp did not produce {out_path}.",
                "source_path": md_path,
            }

        with open(out_path, "rb") as handle:
            out_bytes = handle.read()

        filename = os.path.basename(out_path)
        caption = f"Rendered Marp deck ({export_format.upper()}): {filename}"
        payload = deliver_artifact(
            out_bytes,
            filename,
            caption=caption,
            auto_preview=export_format in {"pdf", "html", "pptx"},
        )
        payload["source_path"] = md_path
        payload["export_format"] = export_format
        return payload
