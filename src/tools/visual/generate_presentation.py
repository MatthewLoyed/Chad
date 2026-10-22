"""Generate branded slide decks via self-hosted Presenton (issue #350)."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, Optional
from urllib.parse import urljoin

import httpx

from src.core.orchestration.context import await_with_progress_heartbeat, emit_node_progress
from src.shared.config_manager import ConfigManager
from src.shared.logger import setup_logger
from src.infrastructure.services.presenton_sidecar import resolve_presenton_auth
from src.tools.base_tool import BaseTool
from src.tools.display_helpers import truncate_label
from src.tools.visual._artifact import deliver_artifact, sanitize_basename, unique_filename

logger = setup_logger("GeneratePresentation")

# Presenton v8+ templates (see GET /api/v1/ppt/template/all on the sidecar).
SUPPORTED_TEMPLATES = (
    "general",
    "modern",
    "standard",
    "swift",
    "code",
    "education",
    "product-overview",
    "report",
    "pitch-deck",
    "neo-general",
    "neo-standard",
    "neo-modern",
    "neo-swift",
)
# Legacy names the model may still pick from older docs/skills.
TEMPLATE_ALIASES = {
    "classic": "general",
    "professional": "standard",
    "default": "general",
}
DEFAULT_TEMPLATE = "general"
SUPPORTED_EXPORTS = {"pptx", "pdf"}


def normalize_template(template: Optional[str]) -> str:
    cleaned = (template or DEFAULT_TEMPLATE).strip().lower()
    return TEMPLATE_ALIASES.get(cleaned, cleaned)


def _presenton_model_name() -> str:
    model = ConfigManager.get("visual.presenton_model")
    if model:
        return str(model)
    try:
        return str(ConfigManager.get_active_model() or "unknown")
    except Exception:
        return "unknown"


def _parse_presenton_detail(body: str) -> str:
    text = (body or "").strip()
    if not text:
        return ""
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            detail = parsed.get("detail") or parsed.get("message") or parsed.get("error")
            if detail:
                return str(detail)
    except json.JSONDecodeError:
        pass
    return text[:500]


def _presenton_http_error(
    *,
    status_code: int,
    body: str,
    template: str,
    n_slides: int,
    base_url: str,
) -> Dict[str, Any]:
    """Turn Presenton HTTP failures into model-actionable tool output."""
    detail = _parse_presenton_detail(body)
    detail_lower = detail.lower()
    model = _presenton_model_name()
    diagnostics = {
        "presenton_url": base_url,
        "presenton_model": model,
        "template": template,
        "n_slides": n_slides,
        "presenton_connected": True,
    }

    if status_code == 428:
        return {
            "success": False,
            "failure_stage": "auth",
            "error": "Presenton requires admin login before the API can generate decks.",
            "hint": (
                "Presenton is reachable but not authenticated. Open Settings → Services → "
                "Presenton and click Connect / set up Presenton."
            ),
            "diagnostics": diagnostics,
        }

    if status_code == 400 and "template" in detail_lower and "not found" in detail_lower:
        return {
            "success": False,
            "failure_stage": "template",
            "error": f"Presenton rejected template '{template}'.",
            "hint": (
                f"Use a v8 template such as general, modern, standard, report, or pitch-deck. "
                f"Valid values: {', '.join(SUPPORTED_TEMPLATES)}."
            ),
            "presenton_detail": detail,
            "diagnostics": diagnostics,
        }

    if status_code == 400 and (
        "outline" in detail_lower or "presentation outlines" in detail_lower
    ):
        return {
            "success": False,
            "failure_stage": "llm_outline",
            "error": (
                "Presenton is connected, but its LLM failed to produce valid slide outlines."
            ),
            "hint": (
                f"The sidecar is running; this is not a Docker/connectivity issue. "
                f"Presenton model '{model}' returned output Presenton could not parse. "
                "Try Settings → Services → Presenton → Deck generation model "
                "(e.g. google/gemma-4-26b-a4b-qat), reduce n_slides, simplify the brief, "
                "or use load_skill('marp') → render_marp for quick markdown slides."
            ),
            "presenton_detail": detail,
            "diagnostics": diagnostics,
            "caption": (
                "Presenton deck generation failed during LLM outline planning — "
                "sidecar is up, model output was invalid."
            ),
        }

    if status_code in {401, 403}:
        return {
            "success": False,
            "failure_stage": "auth",
            "error": f"Presenton rejected the request ({status_code}).",
            "hint": (
                "Presenton is reachable but credentials were rejected. Reconnect Presenton "
                "in Settings → Services or verify AUTH credentials in docker/presenton/.env."
            ),
            "presenton_detail": detail,
            "diagnostics": diagnostics,
        }

    return {
        "success": False,
        "failure_stage": "api",
        "error": f"Presenton API failed ({status_code}): {detail or body[:200]}",
        "hint": (
            "Presenton responded over HTTP, so the sidecar is likely running. "
            "Check Settings → Services → Presenton, docker logs chad-presenton, "
            "and the configured deck model."
        ),
        "presenton_detail": detail or None,
        "diagnostics": diagnostics,
    }


V1_GENERATE_PATH = "/api/v1/ppt/presentation/generate"
V3_ASYNC_GENERATE_PATH = "/api/v3/presentation/generate/async"
V3_ASYNC_STATUS_PATH = "/api/v3/async-task/status/{task_id}"


class GeneratePresentationTool(BaseTool):
    name = "generate_presentation"
    description = (
        "Generate a branded AI slide deck (PPTX or PDF) via self-hosted Presenton "
        "after load_skill('presenton'). "
        "Not for quick markdown slides (use render_marp) or surgical .pptx edits "
        "(use load_skill('pptx')). "
        "Requires visual.presenton_url and a running Presenton container."
    )
    category = "visual"
    is_side_effecting = True

    def format_action(self, params: Dict[str, Any]) -> str:
        export_as = str(params.get("export_as") or "pptx").upper()
        template = str(params.get("template") or "general")
        topic = truncate_label(str(params.get("content") or params.get("title") or ""))
        if topic:
            return f'Generating Presenton deck "{topic}" ({template}, {export_as})'
        return f"Generating Presenton deck ({template}, {export_as})"

    def get_schema(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {
                        "type": "string",
                        "description": (
                            "Topic, outline, or source text for the deck. Can be bullets, "
                            "a paragraph, or a detailed brief."
                        ),
                    },
                    "n_slides": {
                        "type": "integer",
                        "description": "Target slide count (default 8).",
                        "minimum": 3,
                        "maximum": 30,
                    },
                    "template": {
                        "type": "string",
                        "enum": list(SUPPORTED_TEMPLATES),
                        "description": (
                            "Presenton v8 layout template. Default general. "
                            "Common picks: general, modern, standard, swift, "
                            "report, pitch-deck."
                        ),
                    },
                    "export_as": {
                        "type": "string",
                        "enum": sorted(SUPPORTED_EXPORTS),
                        "description": "Output format. Default pptx.",
                    },
                    "language": {
                        "type": "string",
                        "description": "Deck language (default English).",
                    },
                    "theme": {
                        "type": "string",
                        "description": (
                            "Optional theme id (e.g. edge-yellow, professional-blue). "
                            "Omit to use template defaults."
                        ),
                    },
                    "title": {
                        "type": "string",
                        "description": "Short label for the downloaded artifact filename.",
                    },
                },
                "required": ["content"],
            },
        }

    def _base_url(self) -> Optional[str]:
        raw = ConfigManager.get("visual.presenton_url")
        if not raw:
            return None
        return str(raw).rstrip("/")

    def _api_key(self) -> Optional[str]:
        key = ConfigManager.get("PRESENTON_API_KEY") or ConfigManager.get(
            "visual.presenton_api_key"
        )
        return str(key).strip() if key else None

    def _auth_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        key = self._api_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _http_auth(self) -> Optional[tuple[str, str]]:
        """HTTP Basic auth for self-hosted Presenton v8+ (skipped when Bearer key is set)."""
        if self._api_key():
            return None
        user, password = resolve_presenton_auth()
        if user and password:
            return user, password
        return None

    def _use_async(self) -> bool:
        return bool(ConfigManager.get("visual.presenton_use_async", False))

    def _poll_interval(self) -> float:
        return float(ConfigManager.get("visual.presenton_poll_interval_seconds", 5))

    def _generation_timeout(self) -> float:
        return float(ConfigManager.get("visual.presenton_generation_timeout_seconds", 300))

    def _resolve_url(self, base: str, path: str) -> str:
        if not path:
            return ""
        if path.startswith("http://") or path.startswith("https://"):
            return path
        return urljoin(f"{base}/", path.lstrip("/"))

    def _build_v1_payload(
        self,
        content: str,
        n_slides: int,
        template: str,
        export_as: str,
        language: str,
        theme: Optional[str],
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "content": content,
            "n_slides": n_slides,
            "language": language,
            "template": template,
            "export_as": export_as,
        }
        if theme:
            payload["theme"] = theme
        return payload

    def _build_v3_payload(
        self,
        content: str,
        n_slides: int,
        template: str,
        export_as: str,
        language: str,
        theme: Optional[str],
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "content": content,
            "n_slides": n_slides,
            "language": language,
            "standard_template": template,
            "export_as": export_as,
        }
        if theme:
            payload["theme"] = theme
        return payload

    async def _download_file(self, client: httpx.AsyncClient, url: str) -> bytes:
        response = await client.get(url)
        response.raise_for_status()
        return response.content

    async def _generate_sync_v1(
        self,
        client: httpx.AsyncClient,
        base: str,
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        # Presenton's v1 endpoint holds the connection open for the WHOLE generation (can run
        # several minutes) with no incremental response — nothing reaches the graph's event
        # stream in the meantime, so without a heartbeat the idle-watchdog (which only sees
        # silence, not real progress) kills a perfectly healthy generation. Heartbeat resets it.
        response = await await_with_progress_heartbeat(
            client.post(
                f"{base}{V1_GENERATE_PATH}",
                json=payload,
                headers=self._auth_headers(),
            )
        )
        response.raise_for_status()
        return response.json()

    async def _poll_async_v3(
        self,
        client: httpx.AsyncClient,
        base: str,
        task_id: str,
        timeout: float,
        interval: float,
    ) -> Dict[str, Any]:
        deadline = time.monotonic() + timeout
        status_url = f"{base}{V3_ASYNC_STATUS_PATH.format(task_id=task_id)}"
        headers = self._auth_headers()
        while time.monotonic() < deadline:
            response = await client.get(status_url, headers=headers)
            response.raise_for_status()
            body = response.json()
            status = str(body.get("status") or "").lower()
            if status == "completed":
                return body
            if status == "error":
                err = body.get("error") or body.get("message") or "Presenton async task failed"
                raise RuntimeError(str(err))
            # Each poll IS progress (a live status check), but it never reaches the graph's
            # event stream on its own — tell the idle-watchdog explicitly so a long, healthy
            # generation isn't mistaken for a hang.
            emit_node_progress()
            await asyncio.sleep(interval)
        raise TimeoutError(
            f"Presenton generation timed out after {int(timeout)}s (task {task_id})"
        )

    async def _generate_async_v3(
        self,
        client: httpx.AsyncClient,
        base: str,
        payload: Dict[str, Any],
        timeout: float,
        interval: float,
    ) -> Dict[str, Any]:
        response = await client.post(
            f"{base}{V3_ASYNC_GENERATE_PATH}",
            json=payload,
            headers=self._auth_headers(),
        )
        response.raise_for_status()
        task = response.json()
        task_id = task.get("id")
        if not task_id:
            raise RuntimeError("Presenton async response missing task id")
        completed = await self._poll_async_v3(client, base, task_id, timeout, interval)
        data = completed.get("data") or {}
        if isinstance(data, dict) and data.get("path"):
            return data
        return completed

    def _extract_paths(self, result: Dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
        download_path = result.get("path")
        edit_path = result.get("edit_path")
        if isinstance(result.get("data"), dict):
            data = result["data"]
            download_path = download_path or data.get("path")
            edit_path = edit_path or data.get("edit_path")
        return (
            str(download_path) if download_path else None,
            str(edit_path) if edit_path else None,
        )

    async def execute(
        self,
        content: str,
        n_slides: int = 8,
        template: str = "general",
        export_as: str = "pptx",
        language: str = "English",
        theme: Optional[str] = None,
        title: str = "presentation",
        **kwargs: Any,
    ) -> Dict[str, Any]:
        base = self._base_url()
        if not base:
            return {
                "success": False,
                "error": "Presenton is not configured (visual.presenton_url is unset).",
                "hint": (
                    "Open Settings → Services → Presenton to configure the sidecar, "
                    "or run: docker compose -f docker/chad-services.yml --profile presenton up -d"
                ),
            }

        text = (content or "").strip()
        if not text:
            return {"success": False, "error": "content is required."}

        export_as = (export_as or "pptx").strip().lower()
        requested_template = (template or DEFAULT_TEMPLATE).strip().lower()
        template = normalize_template(template)
        if export_as not in SUPPORTED_EXPORTS:
            return {
                "success": False,
                "error": f"Unsupported export_as '{export_as}'. Use pptx or pdf.",
            }
        if template not in SUPPORTED_TEMPLATES:
            return {
                "success": False,
                "error": f"Unsupported template '{requested_template}'.",
                "hint": (
                    f"Choose one of: {', '.join(SUPPORTED_TEMPLATES)}. "
                    "Legacy aliases: classic→general, professional→standard."
                ),
            }

        n_slides = max(3, min(int(n_slides or 8), 30))
        timeout = self._generation_timeout()
        interval = self._poll_interval()

        try:
            auth = self._http_auth()
            async with httpx.AsyncClient(timeout=timeout, auth=auth) as client:
                if self._use_async():
                    payload = self._build_v3_payload(
                        text, n_slides, template, export_as, language, theme
                    )
                    result = await self._generate_async_v3(
                        client, base, payload, timeout, interval
                    )
                else:
                    payload = self._build_v1_payload(
                        text, n_slides, template, export_as, language, theme
                    )
                    result = await self._generate_sync_v1(client, base, payload)

                download_path, edit_path = self._extract_paths(result)
                if not download_path:
                    return {
                        "success": False,
                        "error": "Presenton response did not include a download path.",
                        "raw": result,
                    }

                file_url = self._resolve_url(base, download_path)
                file_bytes = await self._download_file(client, file_url)
        except httpx.HTTPStatusError as exc:
            detail = ""
            status_code = exc.response.status_code if exc.response is not None else 0
            if exc.response is not None:
                detail = exc.response.text[:500]
            logger.error(f"Presenton HTTP error ({status_code}): {detail}")
            if exc.response is not None:
                return _presenton_http_error(
                    status_code=status_code,
                    body=detail,
                    template=template,
                    n_slides=n_slides,
                    base_url=base,
                )
            return {
                "success": False,
                "failure_stage": "connectivity",
                "error": "Presenton request failed before an HTTP response was received.",
                "hint": (
                    "Check Settings → Services → Presenton and docker ps --filter name=chad-presenton."
                ),
            }
        except TimeoutError as exc:
            return {"success": False, "error": str(exc)}
        except Exception as exc:
            logger.error(f"Presenton request failed: {exc}", exc_info=True)
            return {
                "success": False,
                "failure_stage": "connectivity",
                "error": f"Could not reach Presenton at {base}: {exc}",
                "hint": (
                    "Presenton did not respond. Check Settings → Services → Presenton, "
                    "docker ps --filter name=chad-presenton, and http://127.0.0.1:5000."
                ),
                "diagnostics": {
                    "presenton_url": base,
                    "presenton_model": _presenton_model_name(),
                    "presenton_connected": False,
                },
            }

        stem = sanitize_basename(title or "presentation", "presentation")
        filename = unique_filename(stem, f".{export_as}")
        caption = f"Generated presentation ({export_as.upper()}): {filename}"
        payload = deliver_artifact(
            file_bytes,
            filename,
            caption=caption,
            auto_preview=export_as in {"pptx", "pdf"},
        )
        payload["presentation_id"] = result.get("presentation_id")
        if edit_path:
            payload["edit_url"] = self._resolve_url(base, edit_path)
        payload["export_as"] = export_as
        payload["template"] = template
        return payload
