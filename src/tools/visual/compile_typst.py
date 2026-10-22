"""Compile Typst source to designed PDFs."""

from __future__ import annotations

import asyncio
import os
import shutil
from typing import Any, Dict, Optional

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

logger = setup_logger("CompileTypst")


class CompileTypstTool(BaseTool):
    name = "compile_typst"
    description = (
        "Compile Typst markup to a designed PDF. "
        "Use after load_skill('typst') for reports, memos, letters, and formatted documents. "
        "For reading/merging/OCR on existing PDFs use load_skill('pdf') instead."
    )
    category = "visual"
    is_side_effecting = True

    def format_action(self, params: Dict[str, Any]) -> str:
        title = truncate_label(str(params.get("title") or ""))
        if title:
            return f'Compiling Typst PDF "{title}"'
        return "Compiling Typst document to PDF"

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "Full Typst source (.typ content).",
                    },
                    "title": {
                        "type": "string",
                        "description": "Short label for output filenames.",
                    },
                },
                "required": ["source"],
            },
        }

    def _typst_bin(self) -> str:
        return str(ConfigManager.get("visual.typst_bin", "typst"))

    async def execute(
        self,
        source: str,
        title: str = "document",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        text = (source or "").strip()
        if not text:
            return {"success": False, "error": "source is required."}

        typst_bin = self._typst_bin()
        if typst_bin != "typst" and not os.path.isfile(typst_bin):
            return {
                "success": False,
                "error": f"Typst binary not found at {typst_bin}.",
                "hint": "Install: winget install Typst.Typst",
            }
        if typst_bin == "typst" and not shutil.which("typst"):
            return {
                "success": False,
                "error": "Typst CLI not found on PATH.",
                "hint": "Install: winget install Typst.Typst",
            }

        base = unique_filename(title, ".typ")
        pdf_name = os.path.splitext(base)[0] + ".pdf"
        typ_path = write_text_artifact(text, base)[0]
        pdf_path = artifact_path(pdf_name)

        cmd = [typst_bin, "compile", typ_path, pdf_path]
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
                "error": f"Could not execute {typst_bin}.",
            }

        if proc.returncode != 0:
            err = (stderr or stdout or b"").decode("utf-8", errors="replace")[:2000]
            return {
                "success": False,
                "error": f"Typst compile failed:\n{err}",
                "hint": "Call load_skill('typst') and fix syntax errors.",
                "source_path": typ_path,
            }

        with open(pdf_path, "rb") as handle:
            pdf_bytes = handle.read()

        caption = f"Compiled Typst PDF: {pdf_name}"
        payload = deliver_artifact(
            pdf_bytes,
            pdf_name,
            caption=caption,
            auto_preview=True,
        )
        payload["source_path"] = typ_path
        return payload
