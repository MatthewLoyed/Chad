"""Read a local PDF's text — in-process, Brain-side (issue: PDF capability gap, #358 eval).

The gap this closes: Chad could *find* a local PDF (open_file / discovery) and *process* one in the
sandbox (the `pdf` skill, which `requires: code_execution`), but the E2B sandbox is isolated from
the user's local filesystem — so a PDF sitting in Downloads could be located but never actually
read. This tool extracts text with pypdf (already a Brain dependency) running in the Brain process,
which does have local-filesystem access. It reuses the same DiscoveryEngine fuzzy-match that
open_file uses, so "read my smart annual report" resolves a real path the same way.

Text-only by design: scanned/image PDFs need OCR (the `pdf` skill's pytesseract path) and forms
need the field extractors under src/skills/pdf/scripts/ — this tool says so plainly instead of
returning empty, rather than pretending it read a scan.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from src.tools.base_tool import BaseTool
from src.shared.logger import setup_logger

logger = setup_logger("PdfReader")

# Guardrails so a 500-page PDF can't blow up the turn's context. Overridable per call.
_DEFAULT_MAX_PAGES = 50
_DEFAULT_MAX_CHARS = 40_000


def extract_pdf_text(
    path: str | Path, *, max_pages: int = _DEFAULT_MAX_PAGES, max_chars: int = _DEFAULT_MAX_CHARS,
) -> Dict[str, Any]:
    """Extract text from a PDF at ``path``. Pure + side-effect-free (no discovery, no config) so it
    unit-tests without the tool/registry machinery.

    Returns ``{ok, text, n_pages, pages_read, truncated, image_only}`` on success, or
    ``{ok: False, error}`` if the file is missing / not a PDF / unreadable. ``image_only`` flags a
    PDF that parsed fine but yielded no text (almost always a scan needing OCR)."""
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": f"file not found: {p}"}
    if p.suffix.lower() != ".pdf":
        return {"ok": False, "error": f"not a PDF (suffix {p.suffix!r}): {p}"}

    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover - pypdf is a hard dependency
        return {"ok": False, "error": "pypdf not installed in the Brain environment"}

    try:
        reader = PdfReader(str(p))
    except Exception as e:  # noqa: BLE001 - surface a readable error, don't crash the turn
        return {"ok": False, "error": f"could not open PDF: {type(e).__name__}: {e}"}

    n_pages = len(reader.pages)
    chunks: List[str] = []
    total = 0
    pages_read = 0
    truncated = False
    for i, page in enumerate(reader.pages):
        if i >= max_pages:
            truncated = True
            break
        try:
            text = page.extract_text() or ""
        except Exception as e:  # noqa: BLE001 - a single bad page shouldn't lose the rest
            logger.debug(f"[ PDF ] page {i} extract failed: {e}")
            text = ""
        if total + len(text) > max_chars:
            chunks.append(text[: max_chars - total])
            truncated = True
            pages_read += 1
            break
        chunks.append(text)
        total += len(text)
        pages_read += 1

    joined = "\n".join(c for c in chunks if c).strip()
    return {
        "ok": True,
        "text": joined,
        "n_pages": n_pages,
        "pages_read": pages_read,
        "truncated": truncated,
        "image_only": not joined,  # parsed but no extractable text -> almost certainly a scan
    }


class ReadPdf(BaseTool):
    """Read the text of a local PDF file (Brain-side, so it works on the user's real filesystem)."""

    name = "read_pdf"
    description = (
        "Extract and return the text of a local PDF file so you can actually read its contents "
        "(e.g. to find a value inside a report a user asked about). Give either a file name/keyword "
        "(fuzzy-matched against the user's Desktop/Documents/Downloads like open_file) or a full "
        "path. Text-only: scanned/image PDFs need OCR and won't return text here."
    )
    category = "filesystem"
    is_side_effecting = False

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        super().__init__(config)
        # Lazy: only construct the discovery engine when a fuzzy name (not a full path) is given.
        self._engine = None

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "file": {
                        "type": "string",
                        "description": (
                            "A full path to the PDF, OR a name/keyword to fuzzy-match "
                            "(e.g. 'smart annual report')."
                        ),
                    },
                    "max_pages": {
                        "type": "integer",
                        "description": f"Max pages to read (default {_DEFAULT_MAX_PAGES}).",
                    },
                },
                "required": ["file"],
            },
        }

    def format_action(self, params: Dict[str, Any]) -> str:
        f = (params or {}).get("file", "")
        return f"Reading PDF: {f}" if f else "Reading PDF"

    def validate_params(self, file: Optional[str] = None, **kwargs: Any) -> Tuple[bool, str | None]:
        if not file or not str(file).strip():
            return False, "file cannot be empty"
        return True, None

    def _resolve_path(self, file: str) -> Optional[str]:
        """A full path if ``file`` is one; else fuzzy-match it against the configured search paths
        (the same DiscoveryEngine open_file uses)."""
        p = Path(file)
        if p.is_absolute() and p.exists():
            return str(p)

        from src.core.filesystem.discovery_engine import DiscoveryEngine
        from src.shared.config_manager import ConfigManager

        if self._engine is None:
            self._engine = DiscoveryEngine()
        best = None
        for root in ConfigManager.get_search_paths():
            matches = self._engine.find(file, root)
            if matches and "error" not in matches[0]:
                m = matches[0]
                if not best or m["score"] > best["score"]:
                    best = m
        return best["path"] if best else None

    async def execute(self, file: str, max_pages: Optional[int] = None, **kwargs: Any) -> Dict[str, Any]:
        path = self._resolve_path(file)
        if not path:
            return {"success": False,
                    "error": f"Could not find a PDF matching '{file}' in your Desktop, Documents, "
                             f"or Downloads. Try a full path or use list_directory to locate it."}

        result = extract_pdf_text(path, max_pages=int(max_pages or _DEFAULT_MAX_PAGES))
        if not result["ok"]:
            return {"success": False, "error": result["error"], "path": path}

        if result["image_only"]:
            return {"success": False, "path": path,
                    "error": f"'{Path(path).name}' has no extractable text — it's likely a scanned/"
                             f"image PDF. Reading it needs OCR (the pdf skill's pytesseract path), "
                             f"which this tool doesn't do."}

        note = " (truncated)" if result["truncated"] else ""
        return {
            "success": True,
            "result": result["text"],
            "path": path,
            "pages_read": result["pages_read"],
            "n_pages": result["n_pages"],
            "truncated": result["truncated"],
            "summary": f"Read {result['pages_read']}/{result['n_pages']} page(s) of "
                       f"{Path(path).name}{note}.",
        }
